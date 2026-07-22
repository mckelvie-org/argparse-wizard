"""
argparse-wizard package.

Cleaner object-oriented argparse-driven command-line interfaces
"""

from __future__ import annotations

from .base import CliBase
from .cli_tree import CliTree
from .commands import (
    CLI_MAIN_COMMAND_NAME,
    CliCommand,
    CliCommandWrapper,
    CmdFunc,
    CmdPreDispatchFunc,
    CmdRegisterFunc,
    OptCmdFunc,
    SubParsersAction,
    cli_command,
)
from .exceptions import CliError, CliExit
from .version import __version__

__all__ = [
    "__version__",
    "CLI_MAIN_COMMAND_NAME",
    "CliBase",
    "CliCommand",
    "CliCommandWrapper",
    "CliError",
    "CliExit",
    "CliTree",
    "CmdFunc",
    "CmdPreDispatchFunc",
    "CmdRegisterFunc",
    "OptCmdFunc",
    "SubParsersAction",
    "cli_command",
]
