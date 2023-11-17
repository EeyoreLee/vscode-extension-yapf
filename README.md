# Vscode formatter extension support for python files using yapf

A formatter extension with support for python files and notebook cell. Feel free to open an issue to tell me what feature else do you need since it's a preview version.  
Note:  
* This extension is supported for all [actively supported versions](https://devguide.python.org/versions/#supported-versions) of the python language (i.e., python >= 3.8(EOL: 2024-10)).

## Quick Start
Setting the following can enable this formatter quickly.
```
  "[python]": {
    "editor.formatOnSaveMode": "file",
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "eeyore.yapf",
    "editor.formatOnType": false
  }
```


## Usage
* Install `yapf` package from pip in following. This is optional but recommended way, else it will use the bundled `yapf=0.40.2`
```
pip install yapf
```
* Select this fotmatter `eeyore.yapf` by adding the following to your vscode settings 
```
  "[python]": {
    "editor.defaultFormatter": "eeyore.yapf"
  }
```
* Enable format on save by adding the following
```
  "[python]": {
    "editor.formatOnSave": true
  }
```

## Address crash for python3.7 or lower
Use `yapf.interpreter` property to select a python interpreter that 3.8 or higher to run this tool by subprocess


## file mode & modifications mode
Choose the mode by the following
* Use file mode
```
  "[python]": {
    "editor.formatOnSaveMode": "file"
  }
```
* Use modifications mode
```
  "[python]": {
    "editor.formatOnSaveMode": "modifications"
  }
```

## Format for notebook
* Format on cell execution
```
  "notebook.formatOnCellExecution": true
```
* Format on save
```
  "notebook.formatOnSave.enabled": true
```

## Set your own yapf style
* Set style by the following vscode settings which is equal to `yapf --style '{based_on_style: pep8, indent_width: 2}'`
```
  "yapf.args": ["--style", "{based_on_style: pep8, indent_width: 2}"]
```
* Use a style file, like `.style.yapf`, `setup.cfg`, `pyproject.toml`, `~/.config/yapf/style`. For details, see [google/yapf](https://github.com/google/yapf)

## Add extra magic function for jupyter
This extension supports the following magic functions by default
```
"capture",
"prun",
"pypy",
"python",
"python3",
"time",
"timeit"
```
Other magic functions like %matplotlib inline, you need to add it to the `yapf.cellMagics` property.
```
"yapf.cellMagics": ["matplotlib inline"]
```