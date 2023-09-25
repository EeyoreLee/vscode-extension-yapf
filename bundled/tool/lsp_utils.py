# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Utility functions and classes for use with running tools over LSP."""
from __future__ import annotations

import contextlib
import io
import os
import os.path
import re
import runpy
import site
import subprocess
import sys
import threading
from itertools import chain
from typing import Any, Callable, List, Sequence, Tuple, Union

from constants import PYTHON_CELL_MAGICS

# Save the working directory used when loading this module
SERVER_CWD = os.getcwd()
CWD_LOCK = threading.Lock()


def as_list(content: Union[Any, List[Any], Tuple[Any]]) -> Union[List[Any], Tuple[Any]]:
    """Ensures we always get a list"""
    if isinstance(content, (list, tuple)):
        return content
    return [content]


# pylint: disable-next=consider-using-generator
_site_paths = tuple([
    os.path.normcase(os.path.normpath(p))
    for p in (as_list(site.getsitepackages()) + as_list(site.getusersitepackages()))
])


def is_cell_magic(code_line: str, python_cell_magics: list = None) -> bool:
    if python_cell_magics is None:
        python_cell_magics = []
    p = re.compile(f"^%{{1,2}}({'|'.join(chain(PYTHON_CELL_MAGICS, python_cell_magics))})")
    r = p.search(code_line)
    if r:
        return True
    return False


def encode_line_magic(code_line: str) -> str:
    return code_line.replace("%", "#\x00%\x00#")


def encode_cell_magic(code: str, python_cell_magics: list = None) -> str:
    if python_cell_magics is None:
        python_cell_magics = []
    masked_code_lines = []
    for code_line in code.split("\n"):
        if is_cell_magic(code_line=code_line, python_cell_magics=python_cell_magics):
            masked_code_lines.append(encode_line_magic(code_line=code_line))
        else:
            masked_code_lines.append(code_line)
    code = "\n".join(masked_code_lines)
    return code


def decode_line_magic(code_line: str) -> str:
    return code_line.replace("#\x00%\x00#", "%")


def decode_cell_magic(code: str) -> str:
    return decode_line_magic(code)


def is_same_path(file_path1, file_path2) -> bool:
    """Returns true if two paths are the same."""
    return os.path.normcase(os.path.normpath(file_path1)) == os.path.normcase(os.path.normpath(file_path2))


def is_current_interpreter(executable) -> bool:
    """Returns true if the executable path is same as the current interpreter."""
    return is_same_path(executable, sys.executable)


def is_stdlib_file(file_path) -> bool:
    """Return True if the file belongs to standard library."""
    return os.path.normcase(os.path.normpath(file_path)).startswith(_site_paths)


# pylint: disable-next=too-few-public-methods
class RunResult:
    """Object to hold result from running tool."""

    def __init__(self, stdout: str, stderr: str):
        self.stdout: str = stdout
        self.stderr: str = stderr


class CustomIO(io.TextIOWrapper):
    """Custom stream object to replace stdio."""

    name = None

    def __init__(self, name, encoding="utf-8", newline=None):
        self._buffer = io.BytesIO()
        self._buffer.name = name
        super().__init__(self._buffer, encoding=encoding, newline=newline)

    def close(self):
        """Provide this close method which is used by some tools."""
        # This is intentionally empty.

    def get_value(self) -> str:
        """Returns value from the buffer as string."""
        self.seek(0)
        return self.read()


class YapfIO(io.TextIOWrapper):

    name = None

    def __init__(self, name, encoding="utf-8", newline=None):
        raw = io.RawIOBase()
        raw.readall = self._raw_readall
        raw.write = self.write
        self._buffer = io.BytesIO()
        self._buffer.name = name
        self._buffer.raw = raw
        super().__init__(self._buffer, encoding=encoding, newline=newline)

    def close(self):
        """Provide this close method which is used by some tools."""
        # This is intentionally empty.

    def get_value(self) -> str:
        """Returns value from the buffer as string."""
        self.seek(0)
        return self.read()

    def _raw_readall(self) -> bytes:
        self.seek(0)
        return self.read().encode("utf-8")

    def write(self, s) -> None:
        self.buffer.write(s)


@contextlib.contextmanager
def substitute_attr(obj: Any, attribute: str, new_value: Any):
    """Manage object attributes context when using runpy.run_module()."""
    old_value = getattr(obj, attribute)
    setattr(obj, attribute, new_value)
    yield
    setattr(obj, attribute, old_value)


@contextlib.contextmanager
def redirect_io(stream: str, new_stream):
    """Redirect stdio streams to a custom stream."""
    old_stream = getattr(sys, stream)
    setattr(sys, stream, new_stream)
    yield
    setattr(sys, stream, old_stream)


@contextlib.contextmanager
def change_cwd(new_cwd):
    """Change working directory before running code."""
    os.chdir(new_cwd)
    yield
    os.chdir(SERVER_CWD)


def _run_module(module: str, argv: Sequence[str], use_stdin: bool, source: str = None) -> RunResult:
    """Runs as a module."""
    str_output = CustomIO("<stdout>", encoding="utf-8")
    str_error = CustomIO("<stderr>", encoding="utf-8")

    try:
        with substitute_attr(sys, "argv", argv):
            with redirect_io("stdout", str_output):
                with redirect_io("stderr", str_error):
                    if use_stdin and source is not None:
                        str_input = YapfIO("<stdin>", encoding="utf-8", newline="\n")
                        with redirect_io("stdin", str_input):
                            str_input.write(source.encode("utf-8"))
                            str_input.seek(0)
                            runpy.run_module(module, run_name="__main__")
                    else:
                        runpy.run_module(module, run_name="__main__")
    except SystemExit:
        pass

    return RunResult(str_output.get_value(), str_error.get_value())


def run_module(module: str, argv: Sequence[str], use_stdin: bool, cwd: str, source: str = None) -> RunResult:
    """Runs as a module."""
    with CWD_LOCK:
        if is_same_path(os.getcwd(), cwd):
            return _run_module(module, argv, use_stdin, source)
        with change_cwd(cwd):
            return _run_module(module, argv, use_stdin, source)


def run_path(argv: Sequence[str], use_stdin: bool, cwd: str, source: str = None) -> RunResult:
    """Runs as an executable."""
    if use_stdin:
        with subprocess.Popen(
                argv,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                cwd=cwd,
        ) as process:
            return RunResult(*process.communicate(input=source))
    else:
        result = subprocess.run(
            argv,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            cwd=cwd,
        )
        return RunResult(result.stdout, result.stderr)


def run_api(
    callback: Callable[[Sequence[str], CustomIO, CustomIO, CustomIO | None], None],
    argv: Sequence[str],
    use_stdin: bool,
    cwd: str,
    source: str = None,
) -> RunResult:
    """Run a API."""
    with CWD_LOCK:
        if is_same_path(os.getcwd(), cwd):
            return _run_api(callback, argv, use_stdin, source)
        with change_cwd(cwd):
            return _run_api(callback, argv, use_stdin, source)


def _run_api(
    callback: Callable[[Sequence[str], CustomIO, CustomIO, CustomIO | None], None],
    argv: Sequence[str],
    use_stdin: bool,
    source: str = None,
) -> RunResult:
    str_output = CustomIO("<stdout>", encoding="utf-8")
    str_error = CustomIO("<stderr>", encoding="utf-8")

    try:
        with substitute_attr(sys, "argv", argv):
            with redirect_io("stdout", str_output):
                with redirect_io("stderr", str_error):
                    if use_stdin and source is not None:
                        str_input = CustomIO("<stdin>", encoding="utf-8", newline="\n")
                        with redirect_io("stdin", str_input):
                            str_input.write(source)
                            str_input.seek(0)
                            callback(argv, str_output, str_error, str_input)
                    else:
                        callback(argv, str_output, str_error)
    except SystemExit:
        pass

    return RunResult(str_output.get_value(), str_error.get_value())
