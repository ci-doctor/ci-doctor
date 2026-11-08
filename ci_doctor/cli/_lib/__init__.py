"""What every command shares, and no command in itself.

`command.py` is the base class every command derives from, `context.py` the config
files a command reads and writes, `terminal.py` the global `--plain`/`--no-color`
and the log console. `outputs/` holds what a command prints with, one module per
kind of output, the way a UI keeps its components.
"""
