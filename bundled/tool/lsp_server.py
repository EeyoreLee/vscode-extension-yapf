# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Implementation of tool support over LSP."""
from __future__ import annotations

import ast
import copy
import importlib
import json
import os
import pathlib
import re
import sys
import sysconfig
import traceback
from typing import Any, Optional, Sequence


# **********************************************************
# Update sys.path before importing any bundled libraries.
# **********************************************************
def update_sys_path(libs_path: str, tool_libs_path: str, strategy: str) -> None:
    """Add given path to `sys.path`."""
    if tool_libs_path not in sys.path and os.path.isdir(tool_libs_path):
        if strategy == "useBundled":
            sys.path.insert(0, tool_libs_path)
        elif strategy == "fromEnvironment":
            sys.path.append(tool_libs_path)
    if libs_path not in sys.path:
        sys.path.insert(0, libs_path)


# Ensure that we can import LSP libraries, and other bundled libraries.
update_sys_path(
    os.fspath(pathlib.Path(__file__).parent.parent / "libs"),
    os.fspath(pathlib.Path(__file__).parent.parent / "tool-libs"),
    os.getenv("LS_IMPORT_STRATEGY", "useBundled"),
)

# **********************************************************
# Imports needed for the language server goes below this.
# **********************************************************
# pylint: disable=wrong-import-position,import-error
import lsp_jsonrpc as jsonrpc
import lsp_utils as utils
import lsprotocol.types as lsp
from pygls import server, uris, workspace

WORKSPACE_SETTINGS = {}
GLOBAL_SETTINGS = {}
RUNNER = pathlib.Path(__file__).parent / "lsp_runner.py"

MAX_WORKERS = 5
LSP_SERVER = server.LanguageServer(name="yapf", version="v0.1.0", max_workers=MAX_WORKERS)

TOOL_MODULE = "yapf"

TOOL_DISPLAY = "yapf"

TOOL_ARGS = []  # default arguments always passed to your tool.


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_ON_TYPE_FORMATTING,
                    lsp.DocumentOnTypeFormattingOptions(first_trigger_character="\n"))
def on_type_formatting(params: lsp.DocumentFormattingParams) -> Optional[list[lsp.TextEdit]]:
    document = LSP_SERVER.workspace.get_document(params.text_document.uri)
    source = document.source.replace("\r\n", "\n")
    if not document.lines[params.position.line - 1].rstrip(" \t\n") or \
        document.lines[params.position.line -1].rstrip("\n").endswith(","):
        return None
    if str(document.uri).startswith("vscode-notebook-cell"):
        settings = copy.deepcopy(_get_settings_by_document(document))
        source = utils.encode_cell_magic(source, settings.get("cellMagics", []))
    try:
        ast_tree = ast.parse(source)
    except SyntaxError:
        return None
    position_lineno = params.position.line
    for node in ast_tree.body:
        lineno = node.lineno
        end_lineno = node.end_lineno
        if position_lineno >= lineno and position_lineno <= end_lineno:
            break
    start = lineno
    end = end_lineno
    extra_args = ["-l", f"{start}-{end}"]
    edits = _formatting_helper(document, extra_args=extra_args)
    if edits:
        return edits

    return None


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_RANGE_FORMATTING)
def range_formatting(params: lsp.DocumentFormattingParams) -> list[lsp.TextEdit] | None:
    document = LSP_SERVER.workspace.get_document(params.text_document.uri)
    start = params.range.start.line + 1
    end = params.range.end.line + 1
    extra_args = ["-l", f"{start}-{end}"]
    edits = _formatting_helper(document, extra_args=extra_args)
    if edits:
        return edits

    # NOTE: If you provide [] array, VS Code will clear the file of all contents.
    # To indicate no changes to file return None.
    return None


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_FORMATTING)
def formatting(params: lsp.DocumentFormattingParams) -> list[lsp.TextEdit] | None:
    """LSP handler for textDocument/formatting request."""
    # If your tool is a formatter you can use this handler to provide
    # formatting support on save. You have to return an array of lsp.TextEdit
    # objects, to provide your formatted results.
    document = LSP_SERVER.workspace.get_document(params.text_document.uri)
    edits = _formatting_helper(document)
    if edits:
        return edits

    # NOTE: If you provide [] array, VS Code will clear the file of all contents.
    # To indicate no changes to file return None.
    return None


def _formatting_helper(document: workspace.Document,
                       extra_args: Optional[Sequence[str]] = None) -> list[lsp.TextEdit] | None:
    if _is_ignored(document) is True:
        return None
    result = _run_tool_on_document(document, use_stdin=True, extra_args=extra_args)
    if result.stdout:
        new_source = _match_line_endings(document, result.stdout)
        return [
            lsp.TextEdit(
                range=lsp.Range(
                    start=lsp.Position(line=0, character=0),
                    end=lsp.Position(line=len(document.lines), character=0),
                ),
                new_text=new_source,
            )
        ]
    return None


def _is_ignored(document: workspace.Document) -> bool:
    try:
        yapf_module = importlib.import_module("yapf")
        # deep copy here to prevent accidentally updating global settings.
        settings = copy.deepcopy(_get_settings_by_document(document))
        code_workspace = settings["workspaceFS"]
        path = document.path[len(code_workspace):] if document.path.startswith(code_workspace) else document.path
        path = path.lstrip("/")
        exclude: list[str] = yapf_module.file_resources.GetExcludePatternsForDir(code_workspace)
        if exclude and any(e.startswith('./') for e in exclude):
            log_error("path in '--exclude' should not start with ./")
            return False
        exclude = exclude and [e.rstrip('/' + os.path.sep) for e in exclude]
        exclude_from_args = _get_exclude_from_args(settings=settings)
        exclude = (exclude_from_args or []) + exclude
        return yapf_module.file_resources.IsIgnored(path, exclude)
    except Exception as e:
        log_error(str(e))
        return False


def _get_exclude_from_args(settings: dict) -> list[str]:
    exclude = []
    args = settings.get("args", [])
    if not args:
        return exclude
    for idx, i in enumerate(args[:-1]):
        if i in ("-e", "--exclude"):  # BUG: Exceptional situations need to be considered
            exclude.append(args[idx + 1])
    return exclude


def _get_line_endings(lines: list[str]) -> str:
    """Returns line endings used in the text."""
    try:
        if lines[0][-2:] == "\r\n":
            return "\r\n"
        return "\n"
    except Exception:  # pylint: disable=broad-except
        return None


def _match_line_endings(document: workspace.Document, text: str) -> str:
    """Ensures that the edited text line endings matches the document line endings."""
    expected = _get_line_endings(document.source.splitlines(keepends=True))
    actual = _get_line_endings(text.splitlines(keepends=True))
    if actual == expected or actual is None or expected is None:
        return text
    return text.replace(actual, expected)


@LSP_SERVER.feature(lsp.INITIALIZE)
def initialize(params: lsp.InitializeParams) -> None:
    """LSP handler for initialize request."""
    log_to_output(f"CWD Server: {os.getcwd()}")

    paths = "\r\n   ".join(sys.path)
    log_to_output(f"sys.path used to run Server:\r\n   {paths}")

    GLOBAL_SETTINGS.update(**params.initialization_options.get("globalSettings", {}))

    settings = params.initialization_options["settings"]
    _update_workspace_settings(settings)
    log_to_output(f"Settings used to run Server:\r\n{json.dumps(settings, indent=4, ensure_ascii=False)}\r\n")
    log_to_output(f"Global settings:\r\n{json.dumps(GLOBAL_SETTINGS, indent=4, ensure_ascii=False)}\r\n")


@LSP_SERVER.feature(lsp.EXIT)
def on_exit(_params: Optional[Any] = None) -> None:
    """Handle clean up on exit."""
    jsonrpc.shutdown_json_rpc()


@LSP_SERVER.feature(lsp.SHUTDOWN)
def on_shutdown(_params: Optional[Any] = None) -> None:
    """Handle clean up on shutdown."""
    jsonrpc.shutdown_json_rpc()


def _get_global_defaults():
    return {
        "path": GLOBAL_SETTINGS.get("path", []),
        "interpreter": GLOBAL_SETTINGS.get("interpreter", [sys.executable]),
        "args": GLOBAL_SETTINGS.get("args", []),
        "importStrategy": GLOBAL_SETTINGS.get("importStrategy", "useBundled"),
        "showNotifications": GLOBAL_SETTINGS.get("showNotifications", "off"),
        "showDebugLog": GLOBAL_SETTINGS.get("showDebugLog", False),
        "cellMagics": GLOBAL_SETTINGS.get("cellMagics", [])
    }


def _update_workspace_settings(settings):
    if not settings:
        key = os.getcwd()
        WORKSPACE_SETTINGS[key] = {
            "cwd": key,
            "workspaceFS": key,
            "workspace": uris.from_fs_path(key),
            **_get_global_defaults(),
        }
        return

    for setting in settings:
        key = uris.to_fs_path(setting["workspace"])
        WORKSPACE_SETTINGS[key] = {
            "cwd": key,
            **setting,
            "workspaceFS": key,
        }


def _get_settings_by_path(file_path: pathlib.Path):
    workspaces = {s["workspaceFS"] for s in WORKSPACE_SETTINGS.values()}

    while file_path != file_path.parent:
        str_file_path = str(file_path)
        if str_file_path in workspaces:
            return WORKSPACE_SETTINGS[str_file_path]
        file_path = file_path.parent

    setting_values = list(WORKSPACE_SETTINGS.values())
    return setting_values[0]


def _get_document_key(document: workspace.Document):
    if WORKSPACE_SETTINGS:
        document_workspace = pathlib.Path(document.path)
        workspaces = {s["workspaceFS"] for s in WORKSPACE_SETTINGS.values()}

        # Find workspace settings for the given file.
        while document_workspace != document_workspace.parent:
            if str(document_workspace) in workspaces:
                return str(document_workspace)
            document_workspace = document_workspace.parent

    return None


def _get_settings_by_document(document: workspace.Document | None):
    if document is None or document.path is None:
        return list(WORKSPACE_SETTINGS.values())[0]

    key = _get_document_key(document)
    if key is None:
        # This is either a non-workspace file or there is no workspace.
        key = os.fspath(pathlib.Path(document.path).parent)
        return {
            "cwd": key,
            "workspaceFS": key,
            "workspace": uris.from_fs_path(key),
            **_get_global_defaults(),
        }

    return WORKSPACE_SETTINGS[str(key)]


# *****************************************************
# Internal execution APIs.
# *****************************************************
def _run_tool_on_document(
    document: workspace.Document,
    use_stdin: bool = False,
    extra_args: Optional[Sequence[str]] = None,
) -> utils.RunResult | None:
    """Runs tool on the given document.

    if use_stdin is true then contents of the document is passed to the
    tool via stdin.
    """
    if extra_args is None:
        extra_args = []

    source = document.source
    has_magics = False
    blank_cell_trail = False
    if str(document.uri).startswith("vscode-notebook-cell"):
        try:
            ast.parse(source.replace("\r\n", "\n"))
        except SyntaxError:
            has_magics = True
        if source.replace("\r\n", "\n").rstrip(" \t").endswith("\n"):
            blank_cell_trail = True

    if utils.is_stdlib_file(document.path):
        # Skip standard library python files.
        return None

    # deep copy here to prevent accidentally updating global settings.
    settings = copy.deepcopy(_get_settings_by_document(document))

    code_workspace = settings["workspaceFS"]
    cwd = settings["cwd"]

    use_path = False
    use_rpc = False
    if settings["path"]:
        # 'path' setting takes priority over everything.
        use_path = True
        argv = settings["path"]
        source = source.replace("\r\n", "\n")
    elif settings["interpreter"] and not utils.is_current_interpreter(settings["interpreter"][0]):
        # If there is a different interpreter set use JSON-RPC to the subprocess
        # running under that interpreter.
        argv = [TOOL_MODULE]
        use_rpc = True
    else:
        # if the interpreter is same as the interpreter running this
        # process then run as module.
        argv = [TOOL_MODULE]

    argv += TOOL_ARGS + settings["args"] + extra_args

    if has_magics is True:
        log_debug(f"cellMagics: {settings.get('cellMagics', [])}", settings)
        source = utils.encode_cell_magic(source, settings.get("cellMagics", []))

    if use_stdin:
        argv += []
    else:
        argv += [document.path]

    if use_path:
        log_to_output(" ".join(argv))
        log_to_output(f"CWD Server: {cwd}")
        result = utils.run_path(
            argv=argv,
            use_stdin=use_stdin,
            cwd=cwd,
            source=source,
        )
        if result.stderr:
            log_to_output(result.stderr)
    elif use_rpc:
        log_to_output(" ".join(settings["interpreter"] + ["-m"] + argv))
        log_to_output(f"CWD Linter: {cwd}")

        result = jsonrpc.run_over_json_rpc(
            workspace=code_workspace,
            interpreter=settings["interpreter"],
            module=TOOL_MODULE,
            argv=argv,
            use_stdin=use_stdin,
            cwd=cwd,
            source=source,
        )
        if result.exception:
            log_error(result.exception)
            result = utils.RunResult(result.stdout, result.stderr)
        elif result.stderr:
            log_to_output(result.stderr)
    else:
        # In this mode the tool is run as a module in the same process as the language server.
        log_to_output(" ".join([sys.executable, "-m"] + argv))
        log_to_output(f"CWD Linter: {cwd}")
        # This is needed to preserve sys.path, in cases where the tool modifies
        # sys.path and that might not work for this scenario next time around.
        with utils.substitute_attr(sys, "path", sys.path[:]):
            try:
                result = utils.run_module(
                    module=TOOL_MODULE,
                    argv=argv,
                    use_stdin=use_stdin,
                    cwd=cwd,
                    source=source,
                )
            except Exception:
                log_error(traceback.format_exc(chain=True))
                raise
        if result.stderr:
            log_to_output(result.stderr)

    if has_magics:
        result.stdout = utils.decode_cell_magic(result.stdout)

    if str(document.uri).startswith("vscode-notebook-cell") and blank_cell_trail is not True:
        result.stdout = result.stdout.rstrip("\n")

    log_debug(f"{document.uri} :\r\n{result.stdout}", settings=settings)
    return result


def _run_tool(extra_args: Sequence[str]) -> utils.RunResult:
    """Runs tool."""
    # deep copy here to prevent accidentally updating global settings.
    settings = copy.deepcopy(_get_settings_by_document(None))

    code_workspace = settings["workspaceFS"]
    cwd = settings["workspaceFS"]

    use_path = False
    use_rpc = False
    if len(settings["path"]) > 0:
        # 'path' setting takes priority over everything.
        use_path = True
        argv = settings["path"]
    elif len(settings["interpreter"]) > 0 and not utils.is_current_interpreter(settings["interpreter"][0]):
        # If there is a different interpreter set use JSON-RPC to the subprocess
        # running under that interpreter.
        argv = [TOOL_MODULE]
        use_rpc = True
    else:
        # if the interpreter is same as the interpreter running this
        # process then run as module.
        argv = [TOOL_MODULE]

    argv += extra_args

    if use_path:
        # This mode is used when running executables.
        log_to_output(" ".join(argv))
        log_to_output(f"CWD Server: {cwd}")
        result = utils.run_path(argv=argv, use_stdin=True, cwd=cwd)
        if result.stderr:
            log_to_output(result.stderr)
    elif use_rpc:
        # This mode is used if the interpreter running this server is different from
        # the interpreter used for running this server.
        log_to_output(" ".join(settings["interpreter"] + ["-m"] + argv))
        log_to_output(f"CWD Linter: {cwd}")
        result = jsonrpc.run_over_json_rpc(
            workspace=code_workspace,
            interpreter=settings["interpreter"],
            module=TOOL_MODULE,
            argv=argv,
            use_stdin=True,
            cwd=cwd,
        )
        if result.exception:
            log_error(result.exception)
            result = utils.RunResult(result.stdout, result.stderr)
        elif result.stderr:
            log_to_output(result.stderr)
    else:
        # In this mode the tool is run as a module in the same process as the language server.
        log_to_output(" ".join([sys.executable, "-m"] + argv))
        log_to_output(f"CWD Linter: {cwd}")
        # This is needed to preserve sys.path, in cases where the tool modifies
        # sys.path and that might not work for this scenario next time around.
        with utils.substitute_attr(sys, "path", sys.path[:]):
            try:
                result = utils.run_module(module=TOOL_MODULE, argv=argv, use_stdin=True, cwd=cwd)
            except Exception:
                log_error(traceback.format_exc(chain=True))
                raise
        if result.stderr:
            log_to_output(result.stderr)

    log_debug(f"\r\n{result.stdout}\r\n", settings=settings)
    return result


# *****************************************************
# Logging and notification.
# *****************************************************
def log_to_output(message: str, msg_type: lsp.MessageType = lsp.MessageType.Log) -> None:
    LSP_SERVER.show_message_log(message, msg_type)


def log_debug(message: str, settings: dict):
    if settings.get("showDebugLog", False) is True:
        LSP_SERVER.show_message_log(message, lsp.MessageType.Log)


def log_error(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Error)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["onError", "onWarning", "always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Error)


def log_warning(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Warning)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["onWarning", "always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Warning)


def log_always(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Info)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Info)


# *****************************************************
# Start the server.
# *****************************************************
if __name__ == "__main__":
    LSP_SERVER.start_io()
