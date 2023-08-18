# yapf
Because vscode is about to give up support for yapf, this extension will release `v1.0.0` version before vscode officially gives up support for yapf.

This `v0.1.2` is a preview version.
Currently only `file` mode is supported. And there are currently some bebug logs.

Example of `settings`
```
  "[python]": {
    "editor.formatOnSaveMode": "file",
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "eeyore.yapf"
  },
```
There are some instability, and the parallel format has not yet been implemented, and will gradually improve in subsequent versions.