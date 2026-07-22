"""Command registration machinery: the `@cli_command` decorator and the descriptor/instance
   classes that back it.
"""

from __future__ import annotations

from argparse import ArgumentParser, _SubParsersAction
from collections.abc import Callable
from typing import TYPE_CHECKING, Generic, Protocol, TypeAlias, TypeVar

if TYPE_CHECKING:
    from .cli_tree import CliTree

__all__ = [
    "CLI_MAIN_COMMAND_NAME",
    "SubParsersAction",
    "CmdFunc",
    "OptCmdFunc",
    "CmdRegisterFunc",
    "CmdPreDispatchFunc",
    "CliCommandWrapper",
    "CliCommand",
    "cli_command",
]

_ArgumentParserT = TypeVar("_ArgumentParserT", bound=ArgumentParser)
SubParsersAction: TypeAlias = "_SubParsersAction[_ArgumentParserT]"

_CliTreeType = TypeVar("_CliTreeType", bound="CliTree")

CLI_MAIN_COMMAND_NAME: str = "<main>"
"""The special name of the top-level command, used for the main command in a CLI with subcommands."""


class CmdFunc(Protocol):
    """An async command handler. Captures cli/cmd from the enclosing @cli_command closure."""

    async def __call__(self) -> None: ...


OptCmdFunc: TypeAlias = "CmdFunc | None"
"""Return type of a @cli_command registration method: the handler to dispatch to, or None if this
   command has no bare handler (e.g. it exists only to group subcommands)."""


class CmdRegisterFunc(Protocol, Generic[_CliTreeType]):
    """An async command registration function. Sets up a command's subparser and returns its handler."""

    async def __call__(self, __cli: _CliTreeType, __cmd: CliCommand[_CliTreeType]) -> OptCmdFunc: ...


class CmdPreDispatchFunc(Protocol):
    """An async predispatch function. Captures cli/cmd from the enclosing @cli_command closure.
       Called in order from the root command to the leaf command before dispatch.
    """

    async def __call__(self) -> None: ...


class CliCommandWrapper(Generic[_CliTreeType]):
    """Descriptor wrapping a CmdRegisterFunc; auto-registers itself via __set_name__.
       This is used to implement the @cli_command decorator.
       All state in this class is per-class; per-instance state is stored in CliCommand.
    """

    register_func: CmdRegisterFunc[_CliTreeType]
    """The wrapped function that sets up the command's subparser and returns its handler."""

    description: str
    """The command description, used in the help message for the command."""

    help: str | None = None
    """The command help message, used in the help message for the command. If None, the description is used as the help message."""

    _names: list[str] | None
    """The full command name, split into keywords (e.g. ["test", "list"] for a method named "cmd_test__list").
       Populated by __set_name__ if not provided in constructor.
       As a special cast, if an empty list, this is the <main> command for the CLI (i.e. the top-level command with no keywords).
       """

    def __init__(
        self,
        func: CmdRegisterFunc[_CliTreeType],
        description: str,
        *,
        name: str | list[str] | None = None,
        help: str | None = None,
    ) -> None:
        """Create a new CliCommand descriptor wrapping the given function.
           If name is provided, it will be used as the command name; otherwise the name will
           be derived from the function name in __set_name__. If name containes spaces,
           it will be split into multiple keywords for subcommands.
        """
        self.register_func = func
        self.description = description
        self.help = help
        if isinstance(name, list):
            self._names = name
        else:
            if name is None or name == "":
                self._names = None
            elif name == CLI_MAIN_COMMAND_NAME:
                self._names = []
            else:
                self._names = name.split()

    @property
    def names(self) -> list[str]:
        if self._names is None:
            raise ValueError("Command name not yet set; __set_name__ has not been called")
        return self._names

    @property
    def short_name(self) -> str:
        if len(self.names) == 0:
            return CLI_MAIN_COMMAND_NAME
        return self.names[-1]

    @property
    def name(self) -> str:
        if len(self.names) == 0:
            return CLI_MAIN_COMMAND_NAME
        return " ".join(self.names)

    @property
    def parent_name(self) -> str:
        if len(self.names) == 0:
            return ""
        elif len(self.names) == 1:
            return CLI_MAIN_COMMAND_NAME
        return " ".join(self.names[:-1])

    @property
    def is_main_command(self) -> bool:
        return self.name == CLI_MAIN_COMMAND_NAME

    def create_instance(self, cli: _CliTreeType, i_source: int) -> CliCommand[_CliTreeType]:
        """Create a CliCommand instance for this command, bound to the given CLI instance."""
        return CliCommand(cli, self, i_source)

    async def __call__(self, cli: _CliTreeType, cmd: CliCommand[_CliTreeType]) -> OptCmdFunc:
        return await self.register_func(cli, cmd)

    def __set_name__(self, owner: type, name: str) -> None:
        if self._names is None:
            if name == "main":
                # Special handling for the "main" top-level command: represented as an empty list.
                names = []
            else:
                names = name.removeprefix("cmd_").split("__")
                names = [n.replace("_", "-") for n in names]
                if len(names) == 0 or any(n == "" for n in names):
                    raise ValueError(f"Invalid command name derived from method name {name!r}")
            self._names = names
        register = getattr(owner, "_register_command_wrapper", None)
        if register is None:
            raise TypeError(f"Cannot register command {self.name}: {owner.__name__} has no _register_command_wrapper method")
        register(self)

    def __str__(self) -> str:
        return f"<CliCommandWrapper {self.name!r}>"

    def __repr__(self) -> str:
        return f"CliCommandWrapper({self.name!r}"


def cli_command(
    description: str, *, name: str | None = None
) -> Callable[[CmdRegisterFunc[_CliTreeType]], CliCommandWrapper[_CliTreeType]]:
    """Decorator factory: wraps a CmdRegisterFunc in a CliCommandWrapper.
       The description is used in the help message for the command.
       If name is provided, it will be used as the command name; otherwise the name will
       be derived from the function name in __set_name__. The function name should begin
       with "cmd_", and sould use "__" to represent command keyword separators.
       If name containes spaces, it will be split into multiple keywords for subcommands."""

    def decorator(func: CmdRegisterFunc[_CliTreeType]) -> CliCommandWrapper[_CliTreeType]:
        return CliCommandWrapper(func, description, name=name)

    return decorator


class CliCommand(Generic[_CliTreeType]):
    """
    Per-cli-instance state for a CliCommand. This is used to store the parser and subparsers action for each command,
    and acts as a node in the command tree.
    """

    cli: _CliTreeType
    """The CLI instance this command is bound to."""

    wrapper: CliCommandWrapper[_CliTreeType]
    """The per-class descriptor that created this command instance."""

    i_source: int
    """The 0-based index of this command in the source-code order of the CLI class.
       Used for sorting commands in breadth-first order."""

    description: str
    """The command description, used in the help message for the command. Initially copied
       from the wrapper, but can be modified per-instance if desired."""

    help: str | None = None
    """The command help message, used in the help message for the command. Initially None,
         but can be set per-instance if desired. If None, the description is used as the help message."""

    parent_cmd: CliCommand[_CliTreeType] | None = None
    """If this is a subcommand of another command, this is the parent command; otherwise None."""

    children_cmds: list[CliCommand[_CliTreeType]]
    """If this command has subcommands, this is the list of child commands; otherwise empty"""

    parser: ArgumentParser | None = None
    """The parser for this command, created by get_parser()."""

    subparsers_action: SubParsersAction[ArgumentParser] | None = None
    """If this command has subcommands, this is the subparsers action for them"""

    handler: CmdFunc | None = None
    """The handler function for this command, returned by the register_func of the wrapper. If None,
       the command cannot be used bare, but must have subcommands."""

    pre_dispatch_handler: CmdPreDispatchFunc | None = None
    """If provided, this function is called before dispatching this command or any of its subcommands,
       for pre-dispatch setup. This allows a parent command to provide commandline options that affect all subcommands.
    """

    def __init__(self, cli: _CliTreeType, wrapper: CliCommandWrapper[_CliTreeType], i_source: int) -> None:
        """Create a new CliCommand descriptor for the given command."""
        self.cli = cli
        self.wrapper = wrapper
        self.i_source = i_source
        self.description = wrapper.description
        self.help = wrapper.help
        self.children_cmds = []

    @property
    def names(self) -> list[str]:
        return self.wrapper.names

    @property
    def short_name(self) -> str:
        return self.wrapper.short_name

    @property
    def name(self) -> str:
        return self.wrapper.name

    @property
    def parent_name(self) -> str:
        return self.wrapper.parent_name

    @property
    def is_main_command(self) -> bool:
        return self.wrapper.is_main_command

    def set_parser(self, parser: ArgumentParser) -> None:
        if self.parser is not None:
            raise ValueError(f"Command {self.name!r} already has a parser")
        self.parser = parser

    def get_parent_subparsers_action(self) -> SubParsersAction[ArgumentParser]:
        if self.parent_cmd is None:
            # This is the top-level command; should never need this.
            raise ValueError(f"Command {self.name!r} has no parent command, can't get parent subparsers action")
        return self.parent_cmd.get_subparsers_action()

    def get_parser(self) -> ArgumentParser:
        parser = self.parser
        if parser is None:
            if self.parent_cmd is None:
                # This is the top-level command; return the CLI's main parser.
                parser = self.cli.get_main_parser(self.description)
            else:
                parent_subparsers = self.parent_cmd.get_subparsers_action()
                help = self.description if self.help is None else self.help
                parser = parent_subparsers.add_parser(self.short_name, description=self.description, help=help)
            self.parser = parser
        return parser

    def set_subparsers_action(self, action: SubParsersAction[ArgumentParser]) -> None:
        if self.subparsers_action is not None:
            raise ValueError(f"Command {self.name} already has a subparsers action")
        self.subparsers_action = action

    def get_subparsers_action(self) -> SubParsersAction[ArgumentParser]:
        result = self.subparsers_action
        if result is None:
            result = self.get_parser().add_subparsers(dest="command", metavar="COMMAND")
            self.set_subparsers_action(result)
        return result

    async def initialize(self) -> None:
        """Initialize this command: create its parser and subparsers action, and call the register_func to get its handler."""
        # Call the decorated function to set up the parser and get the handler.
        self.handler = await self.wrapper(self.cli, self)
        # ensure parser is created even if the decorated registration function didn't create one
        parser = self.get_parser()
        # When the command is parsed, this CliCommand instance will be stored in the namespace as cmd_meta,
        # so that the dispatcher knows what command to invoke.
        parser.set_defaults(cmd_meta=self)
        # If no handler is returned, the command cannot be used bare, but must have subcommands.
        if self.handler is None:
            subparsers = self.get_subparsers_action()
            subparsers.required = True

    def __str__(self) -> str:
        return f"<CliCommand {self.name!r}>"

    def __repr__(self) -> str:
        return f"CliCommand({self.name!r})"
