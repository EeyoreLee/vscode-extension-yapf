# yapf
Because yapf setting will soon be deprecated, I build a sample extension to support yapf. Feel free to open an issue to tell me what feature else do you need.

This is a preview version now. Support all mode('file', 'modification'). And will will search for the formatting style in `.style.yapf`, `setup.cfg`, `pyproject.toml`, `~/.config/yapf/style`, else the default style PEP8 is used if none of those files are found.

Usage:
* Install `yapf` package since this extension didn't include it, it will call `yapf` you installed.
```
pip install yapf
```
* Setup vscode settings by editting `settings.json` in User scope or Workspace scope in following:
Example of `settings`
```
  "[python]": {
    "editor.formatOnSaveMode": "file",
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "eeyore.yapf"  # choose this extension
  },
```
Don't support formatting on type now. Tell me if you need it.  