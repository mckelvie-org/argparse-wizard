"""CliTree: shared command-tree registration machinery for CliBase and (in the future) mountable
   subtrees -- reusable command groups that can be inserted into more than one CliBase app.
"""

from __future__ import annotations

import argparse
import logging
from argparse import ArgumentParser
from typing import TYPE_CHECKING, Any, BinaryIO, ClassVar, TextIO, cast

from typing_extensions import Self

from .commands import CLI_MAIN_COMMAND_NAME, CliCommand, CliCommandWrapper

if TYPE_CHECKING:
    from .base import CliBase

__all__ = [
    "CliTree",
]


class CliTree:
    """Base class for command-tree nodes: anything that can declare @cli_command methods and become
       part of a CLI's command tree. CliBase (the runnable, top-level CLI) is itself a CliTree.

       This class holds the machinery that's agnostic to where in the tree a node ends up: command
       registration/dispatch, per-node parser construction, and read/write access to state that's
       shared tree-wide but actually owned by the root (parsed args, raw args, logger, the
       pre-redirection stdio streams) or to root behavior a handler might call from anywhere in the
       tree (binary stdio access, eprint). Each such property/method here delegates to the *root's
       own* public property/method (e.g. `self._root_tree.args`, never a private backing field), and
       CliBase provides the real, terminating implementation as its own distinct override -- so a
       subclass overriding any of these on CliBase is honored tree-wide, and there's no infinite
       recursion for the root's own case (see CliBase.prog_name's docstring for why that matters).

       One-time lifecycle/orchestration concerns that only the root ever invokes (redirect_io,
       init_logging, standard_predispatch, perform_predispatch, preinit, parse_args, run/async_run,
       the context manager protocol) are not delegated here -- they stay CliBase-only.
    """

    _root_tree: CliBase
    """The root CliBase instance that owns this command tree. For CliBase itself, this is self."""

    _command_wrappers: ClassVar[list[CliCommandWrapper[Any]]] = []  # 'Any' required here because classvars can't reference Self.
    """Class-scoped registered command wrappers, built up by the @cli_command decorator. In source code order."""

    _commands: list[CliCommand[Self]]
    """Per-instance command metadata/state, including parsers, etc. In breaddth-first source code order--top-level
       commands first, then their subcommands."""

    _commands_by_name: dict[str, CliCommand[Self]]
    """Index of commands by their full name, for quick lookup. Keys are the full command name (e.g. "test list")."""

    _parser: ArgumentParser | None = None
    """This node's own top-level parser, created by get_main_parser(). Local to each tree node --
       not shared with/delegated to the root, since a mounted subtree's parser will eventually be a
       different (sub)parser object than the root's."""

    @property
    def prog_name(self) -> str:
        """Program name, used in parser help messages. Shared by the whole tree; delegates to the
           root's own prog_name property (not its private backing field), so a subclass override
           of CliBase.prog_name is honored everywhere in the tree, not just for the root itself.
        """
        return self._root_tree.prog_name

    @property
    def raw_args(self) -> list[str]:
        """Raw command-line arguments, as passed to the CLI constructor. Shared by the whole tree."""
        return self._root_tree.raw_args

    @property
    def logger(self) -> logging.Logger:
        """The logger used by this CLI. Shared by the whole tree."""
        return self._root_tree.logger

    @property
    def args(self) -> argparse.Namespace:
        """The parsed command-line arguments. Shared by the whole tree."""
        return self._root_tree.args

    @args.setter
    def args(self, value: argparse.Namespace) -> None:
        self._root_tree.args = value

    @property
    def orig_stdin(self) -> TextIO:
        """sys.stdin as it was before --input-file redirection. Use this to read from the real
           console/pipe regardless of --input-file."""
        return self._root_tree.orig_stdin

    @property
    def orig_stdout(self) -> TextIO:
        """sys.stdout as it was before --output-file redirection. Use this to write to the real
           console/pipe regardless of --output-file."""
        return self._root_tree.orig_stdout

    def get_binary_stdout(self) -> BinaryIO:
        """Return the binary buffer behind the *current* sys.stdout (the console, or an
           --output-file target if one is in effect). Use this for commands whose output is (or
           may be) raw bytes rather than text.
        """
        return self._root_tree.get_binary_stdout()

    def get_binary_stdin(self) -> BinaryIO:
        """Return the binary buffer behind the *current* sys.stdin (the console, or an
           --input-file source if one is in effect). Use this for commands that need to read raw
           bytes rather than text.
        """
        return self._root_tree.get_binary_stdin()

    def eprint(
        self,
        *values: object,
        sep: str | None = " ",
        end: str | None = "\n",
        flush: bool = False,
    ) -> None:
        """Print text to stderr."""
        self._root_tree.eprint(*values, sep=sep, end=end, flush=flush)

    def __init__(self, root_tree: CliBase | None = None) -> None:
        """Create a new CliTree node. root_tree is the owning CliBase instance; pass None only when
           self *is* the root -- CliBase.__init__ does this via super().__init__().
        """
        self._root_tree = root_tree if root_tree is not None else cast("CliBase", self)
        self._commands = []
        self._commands_by_name = {}

    @property
    def main_command(self) -> CliCommand[Self]:
        """Get the <main> command for this tree node."""
        return self._commands_by_name[CLI_MAIN_COMMAND_NAME]

    @classmethod
    def _register_command_wrapper(cls, cmd: CliCommandWrapper[Self]) -> None:
        """Called by command decorator to record a command to be registered."""
        if "_command_wrappers" not in cls.__dict__:
            cls._command_wrappers = list(cls._command_wrappers)  # fork from inherited list
        cls._command_wrappers.append(cmd)

    @classmethod
    def _iter_command_wrappers(cls) -> list[CliCommandWrapper[Self]]:
        """Iterate over all registered command wrappers, in source code order."""
        return cls._command_wrappers

    def create_default_wrapper(self, names: list[str]) -> CliCommandWrapper[Self]:
        """Create a default command wrapper for a command that has no registration function."""

        async def stub_func(cli: Self, cmd: CliCommand[Self]) -> None:
            return None

        result: CliCommandWrapper[Self] = CliCommandWrapper(stub_func, description="Stub command", name=names)
        if result.is_main_command:
            result.description = "Command-line interface"
        else:
            result.description = f"{result.name!r} command"
        return result

    def register_commands(self) -> None:
        """Create instance state for all registered commands, and build the command tree."""
        # mypy/pyright currently widen `Self` to its bound when a classmethod-returned,
        # Self-parameterized generic is consumed from within a method of this same class (as
        # opposed to from a subclass or external caller, where it resolves correctly) -- so the
        # casts below just restate what's already true at runtime for the type checker's benefit.
        for wrapper in self._iter_command_wrappers():
            name = wrapper.name
            if name in self._commands_by_name:
                raise ValueError(f"Duplicate command name: {name!r}")
            cmd = cast("CliCommand[Self]", wrapper.create_instance(self, i_source=len(self._commands)))
            self._commands.append(cmd)
            self._commands_by_name[name] = cmd

        # Build the command tree: set parent_cmd for each command based on its name.
        # If any command is missing, create a default command that has no handler and a default description.
        # We use a while loop here because we may add new commands to the list as we go, and we want to process them all.
        i_command = 0
        while i_command < len(self._commands):
            cmd = self._commands[i_command]
            if not cmd.is_main_command:
                parent_name = cmd.parent_name
                parent_cmd = self._commands_by_name.get(parent_name)
                if parent_cmd is None:
                    # Create a default command for the missing parent.
                    parent_wrapper = self.create_default_wrapper(parent_name.split())
                    parent_cmd = cast("CliCommand[Self]", parent_wrapper.create_instance(self, i_source=i_command))
                    self._commands.append(parent_cmd)
                    self._commands_by_name[parent_name] = parent_cmd
                cmd.parent_cmd = parent_cmd
                parent_cmd.children_cmds.append(cmd)
            i_command += 1

        # For completeness, if there is still no <main> command, create a default one. This will only
        # happen if the CLI has no @cli_command-decorated methods at all.
        if CLI_MAIN_COMMAND_NAME not in self._commands_by_name:
            main_wrapper = self.create_default_wrapper([])
            main_cmd = cast("CliCommand[Self]", main_wrapper.create_instance(self, i_source=len(self._commands)))
            self._commands.append(main_cmd)
            self._commands_by_name[CLI_MAIN_COMMAND_NAME] = main_cmd

        # sort commands by depth (len(cmd.names)), then source-code order. That will ensure
        # that parent commands are registered before their subcommands, so they have access to the parent
        # parser.
        self._commands.sort(key=lambda c: (len(c.names), c.i_source))

    async def initialize_commands(self) -> None:
        """Initialize all registered commands in breadth-first order, creating their parsers and subparsers."""
        for cmd in self._commands:
            await cmd.initialize()
            # We add standard top-level arguments after initializing the main command, so that it
            # has a chance to customize the main parser before we add the standard arguments.
            # add_standard_top_level_arguments() only exists on CliBase -- it's the root's own
            # process-wide flags (--log-level, --tb, --input-file, --output-file), not something a
            # subtree gets its own copy of -- so this reaches into the root directly rather than
            # going through a same-named virtual method on self.
            if cmd.is_main_command:
                self._root_tree.add_standard_top_level_arguments()

    def create_main_parser(self, description: str) -> ArgumentParser:
        """Creates this node's top-level parser. Override to customize."""
        return ArgumentParser(prog=self.prog_name, description=description, exit_on_error=False)

    def get_main_parser(self, description: str | None = None) -> ArgumentParser:
        """Get or create this node's top-level parser. If description is provided, it will be used
           for the parser's description. description is ignored if the parser has already been created.
        """
        if self._parser is None:
            self._parser = self.create_main_parser(description or "Command-line interface")
        return self._parser

    @property
    def parser(self) -> ArgumentParser:
        """This node's top-level parser. Raises if get_main_parser() hasn't created it yet."""
        if self._parser is None:
            raise ValueError("Parser not yet initialized")
        return self._parser

    @parser.setter
    def parser(self, value: ArgumentParser) -> None:
        if self._parser is not None:
            raise ValueError("Parser already initialized")
        self._parser = value
