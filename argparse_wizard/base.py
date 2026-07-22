"""The CliBase class: base class for CLIs with auto-registration of @cli_command subcommands."""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from argparse import ArgumentParser
from types import TracebackType
from typing import Any, BinaryIO, ClassVar, TextIO, cast

from typing_extensions import Never, Self

from .commands import CLI_MAIN_COMMAND_NAME, CliCommand, CliCommandWrapper
from .exceptions import CliError, CliExit

__all__ = [
    "CliBase",
]

logger = logging.getLogger(__name__)


class CliBase:
    """Base class for CLIs with auto-registration of @cli_command subcommands.

       Not generic: command methods reference the concrete subclass via `typing.Self` (e.g.
       `cmd: CliCommand[Self]`) rather than requiring subclasses to write `CliBase["MyCli"]`.
    """

    _command_wrappers: ClassVar[list[CliCommandWrapper[Any]]] = []  # 'Any' required here because classvars can't reference Self.
    """Class-scoped registered command wrappers, built up by the @cli_command decorator. In source code order."""

    _commands: list[CliCommand[Self]]
    """Per-instance command metadata/state, including parsers, etc. In breaddth-first source code order--top-level
       commands first, then their subcommands."""

    _commands_by_name: dict[str, CliCommand[Self]]
    """Index of commands by their full name, for quick lookup. Keys are the full command name (e.g. "test list")."""

    subcommand_required: bool = True
    """Whether a top-level subcommand is required. Override in subclasses. Defaults to True."""

    prog_name: str
    """Program name, used in the top-level parser help message, as passed to the CLI
       constructor. If not provided, defaults to the basename of sys.argv[0]."""

    raw_args: list[str]
    """Raw command-line arguments, as passed to the CLI constructor. Defaults to sys.argv[1:]."""

    _args: argparse.Namespace | None = None
    """Parsed command-line arguments, as returned by parse_args()."""

    _parser: ArgumentParser | None = None
    """Top-level parser, created by get_main_parser()."""

    logger: logging.Logger
    """The logger used by this class. By default, this is the logger for the module that defined the CLI subclass."""

    logging_format: str = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    """The logging format string to use if logging must be initialized. Override in subclasses to customize."""

    orig_stdin: TextIO
    """sys.stdin as it was before --input-file redirection, captured in __aenter__. Use this to
       read from the real console/pipe regardless of --input-file."""

    orig_stdout: TextIO
    """sys.stdout as it was before --output-file redirection, captured in __aenter__. Use this to
       write to the real console/pipe regardless of --output-file (e.g. status messages that
       should always reach the terminal, or console-capability probes that must reflect the true
       terminal rather than a redirected file)."""

    _opened_input_file: TextIO | None = None
    """If --input-file was provided, the file object opened for it (and currently installed as
       sys.stdin). Closed and cleared in __aexit__."""

    _opened_output_file: TextIO | None = None
    """If --output-file was provided, the file object opened for it (and currently installed as
       sys.stdout). Closed and cleared in __aexit__."""

    def __init__(self, args: list[str] | None = None, prog_name: str | None = None) -> None:
        self._commands = []
        self._commands_by_name = {}
        if args is None:
            args = sys.argv[1:]
        if prog_name is None:
            prog_name = os.path.basename(sys.argv[0])
        self.raw_args = args
        self.prog_name = prog_name
        self.logger = logging.getLogger(type(self).__module__.split(".")[0])

    @property
    def args(self) -> argparse.Namespace:
        if self._args is None:
            raise ValueError("Arguments not yet parsed")
        return self._args

    @args.setter
    def args(self, value: argparse.Namespace) -> None:
        if self._args is not None:
            raise ValueError("Arguments already parsed")
        self._args = value

    @property
    def parser(self) -> ArgumentParser:
        if self._parser is None:
            raise ValueError("Parser not yet initialized")
        return self._parser

    @parser.setter
    def parser(self, value: ArgumentParser) -> None:
        if self._parser is not None:
            raise ValueError("Parser already initialized")
        self._parser = value

    @property
    def main_command(self) -> CliCommand[Self]:
        """Get the <main> command for this CLI instance."""
        return self._commands_by_name[CLI_MAIN_COMMAND_NAME]

    @classmethod
    def _register_command_wrapper(cls, cmd: CliCommandWrapper[Self]) -> None:
        """Called by command decorator to record a command ro be registered."""
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
        # mypy currently widens `Self` to its bound (CliBase) when a classmethod-returned,
        # Self-parameterized generic is consumed from within a method of this same class (as
        # opposed to from a subclass or external caller, where it resolves correctly) -- so the
        # casts below just restate what's already true at runtime for mypy's benefit.
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
            # We add standard top-level arguments after initializing the main command,
            # so that it has a chance to customize the main parser before we add the standard arguments.
            if cmd.is_main_command:
                self.add_standard_top_level_arguments()

    def create_main_parser(self, description: str) -> ArgumentParser:
        """Creates the top-level parser for the CLI. Override in subclasses to customize the main parser."""
        return ArgumentParser(prog=self.prog_name, description=description, exit_on_error=False)

    def get_main_parser(self, description: str | None = None) -> ArgumentParser:
        """Get or create the top-level parser for the CLI. If description is provided, it will be used for the parser's description.
           description is ignored if the parser has already been created.
        """
        if self._parser is None:
            self._parser = self.create_main_parser(description or "Command-line interface")
        return self.parser

    def add_standard_top_level_arguments(self) -> None:
        """Add standard top-level arguments to the top-level parser.
           By default, this adds --log-level, --tb, --input-file, and --output-file.
           Override in subclasses to customize.
        """
        self.parser.add_argument(
            "--log-level", "-l", default="WARNING",
            type=str.upper,
            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            help="Logging level (default: WARNING).",
        )
        self.parser.add_argument(
            "--tb", default=False, action="store_true",
            help="Display full traceback on error.",
        )
        self.parser.add_argument(
            "--input-file", "-i", default=None, type=str,
            help="Read from the given file instead of stdin. Reopens sys.stdin for the duration of the command.",
        )
        self.parser.add_argument(
            "--output-file", "-o", default=None, type=str,
            help="Write to the given file instead of stdout. Reopens sys.stdout for the duration of the command.",
        )

    async def init_parser(self) -> None:
        """Build the parser: create it, add arguments, create subparsers, register commands."""
        self.register_commands()
        await self.initialize_commands()

    def parse_args(self) -> argparse.Namespace:
        """Parse the command-line arguments and return the namespace. If self.args is already set, return it."""
        if self._args is None:
            self._args = self.get_main_parser().parse_args(self.raw_args)
        return self._args

    def get_logging_format(self) -> str:
        """Return the logging format string to use for the CLI. This call is
           made after arguments are parsed. Override in subclasses to customize."""
        return self.logging_format

    def redirect_io(self) -> None:
        """If --input-file/--output-file were given, reopen sys.stdin/sys.stdout to point at them
           for the duration of command dispatch, so ordinary print()/input()/sys.stdin calls made
           by command handlers (and any libraries they use) transparently honor the redirection --
           the same way shell redirection would. Called from standard_predispatch(), i.e. after
           arguments are parsed but before any command or pre-dispatch hook runs, so that anything
           which inspects sys.stdout to decide on terminal capabilities (color, width, isatty, ...)
           sees the real redirection target rather than the console.

           The pre-redirection streams remain available as self.orig_stdin / self.orig_stdout for
           code that specifically wants the real console regardless of --input-file/--output-file
           (e.g. interactive prompts or status messages that should always reach the terminal).

           Commands that need binary-safe I/O on whichever stream is currently in effect should use
           get_binary_stdin() / get_binary_stdout() rather than assuming text mode.
        """
        input_file: str | None = self.args.input_file
        if input_file is not None:
            self._opened_input_file = io.TextIOWrapper(open(input_file, "rb"), encoding="utf-8", newline="")  # noqa: SIM115 -- closed in __aexit__
            sys.stdin = self._opened_input_file
        output_file: str | None = self.args.output_file
        if output_file is not None:
            self._opened_output_file = io.TextIOWrapper(open(output_file, "wb"), encoding="utf-8", newline="")  # noqa: SIM115 -- closed in __aexit__
            sys.stdout = self._opened_output_file

    def get_binary_stdout(self) -> BinaryIO:
        """Return the binary buffer behind the *current* sys.stdout (the console, or an
           --output-file target if one is in effect), flushing any pending text output first so
           it isn't interleaved incorrectly with the binary write that's about to happen. Use this
           for commands whose output is (or may be) raw bytes rather than text.
        """
        sys.stdout.flush()
        return sys.stdout.buffer

    def get_binary_stdin(self) -> BinaryIO:
        """Return the binary buffer behind the *current* sys.stdin (the console, or an
           --input-file source if one is in effect). Use this for commands that need to read raw
           bytes rather than text.
        """
        return sys.stdin.buffer

    def eprint(
        self,
        *values: object,
        sep: str | None = " ",
        end: str | None = "\n",
        flush: bool = False,
    ) -> None:
        """Print text to stderr."""
        print(*values, sep=sep, end=end, flush=flush, file=sys.stderr)

    async def init_logging(self) -> None:
        """Initialize logging based on the --log-level argument."""
        log_level = self.args.log_level.upper()
        if not logging.root.handlers:
            logging.basicConfig(level=log_level, format=self.get_logging_format())
        else:
            self.logger.setLevel(log_level)

    async def standard_predispatch(self) -> None:
        """Perform standard pre-dispatch setup: apply --input-file/--output-file redirection, then
           initialize logging. Override in subclasses to customize.
        """
        self.redirect_io()
        await self.init_logging()

    async def perform_predispatch(self, cmd: CliCommand[Self]) -> None:
        """Perform pre-dispatch setup for the given command and its parents, in order from root to leaf."""
        # Build the list of commands from root to leaf.
        commands: list[CliCommand[Self]] = []
        next_cmd: CliCommand[Self] | None = cmd
        while next_cmd is not None:
            commands.append(next_cmd)
            next_cmd = next_cmd.parent_cmd
        commands.reverse()
        await self.standard_predispatch()
        for c in commands:
            if c.pre_dispatch_handler is not None:
                await c.pre_dispatch_handler()

    def tracebacks_enabled(self) -> bool:
        """Return True if tracebacks should be displayed on error, based on the --tb argument.
           Override in subclasses to customize."""
        return getattr(self.args, "tb", False)

    def __enter__(self) -> Never:
        raise RuntimeError("CliBase does not support synchronous context management; use 'async with' instead")

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        raise RuntimeError("CliBase does not support synchronous context management; use 'async with' instead")

    async def __aenter__(self) -> Self:
        """Async context manager entry. This context is used while processing the command, and is exited before returning from async_run().
           Captures the pre-redirection sys.stdin/sys.stdout as self.orig_stdin/self.orig_stdout.
           Override in subclasses to customize for cleanup; subclasses should call super().__aenter__() first.
        """
        self.orig_stdin = sys.stdin
        self.orig_stdout = sys.stdout
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Async context manager exit. This context is used while processing the command, and is exited before returning from async_run().
           Closes any file opened for --input-file/--output-file and restores sys.stdin/sys.stdout.
           Override in subclasses to customize for cleanup. Subclasses should call super().__aexit__() to ensure that
           this restoration happens.
        """
        if self._opened_output_file is not None:
            try:
                self._opened_output_file.close()
            finally:
                self._opened_output_file = None
        if self._opened_input_file is not None:
            try:
                self._opened_input_file.close()
            finally:
                self._opened_input_file = None
        sys.stdout = self.orig_stdout
        sys.stdin = self.orig_stdin

    async def preinit(self) -> None:
        """Perform any pre-initialization setup before the parser is built. Override in subclasses to customize."""
        pass

    def run(self) -> int:
        """Run the CLI synchronously. Returns exit code.

           This is the entry point for apps with no async code of their own: it drives async_run()
           via asyncio.run() internally, so callers never need to touch asyncio themselves. It's the
           right default even for apps whose command handlers happen to be async -- that's a
           per-handler concern, not a per-entry-point one.

           Do not call this from code that already has an event loop running (e.g. from inside an
           async function, or in a Jupyter notebook); use 'await cli.async_run()' there instead.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # no running loop -- safe to proceed
        else:
            raise RuntimeError(
                "CliBase.run() cannot be called from within a running event loop "
                "(e.g. from an async function, or in a Jupyter notebook). "
                "Use 'await cli.async_run()' instead."
            )
        return asyncio.run(self.async_run())

    async def async_run(self) -> int:
        """Run the CLI. Returns exit code.

           This is the entry point for apps that already have (or want to control) their own event
           loop: call 'await cli.async_run()' from within a running loop, or wrap it yourself with
           'asyncio.run(cli.async_run())'. Most apps should prefer run() instead, which does the
           asyncio.run() wrapping for you.
        """
        rc = 0
        tb = True
        try:
            async with self:
                try:
                    await self.preinit()
                    await self.init_parser()
                    args = self.parse_args()
                    tb = self.tracebacks_enabled()
                    cmd: CliCommand[Self] | None = getattr(args, "cmd_meta", None)
                    if cmd is None:
                        raise CliError("Internal error: CliCommand associated with parsed arguments is missing")
                    # Same Self-widening quirk as in register_commands(): mypy resolves
                    # perform_predispatch's own `Self` to the CliBase bound here, even though cmd
                    # is already correctly typed CliCommand[Self].
                    await self.perform_predispatch(cmd)  # type: ignore[arg-type]
                    handler = cmd.handler
                    if handler is not None:
                        self.logger.debug(f"dispatching command: {cmd}")
                        await handler()
                except CliExit as e:
                    rc = e.code
        except Exception as e:
            if tb:
                # The standard unhandled exception hook will print the traceback and exit with code 1; there
                # is no way to make it exit with a different code, so in the rare case that a CliError is
                # raised with a non-1 code, we need to override the excepthook to exit with the correct code.
                if isinstance(e, CliError) and e.code != 1:
                    code = e.code
                    old_hook = sys.excepthook

                    def excepthook(exc_type: type[BaseException], exc: BaseException, exc_tb: TracebackType | None) -> None:
                        old_hook(exc_type, exc, exc_tb)
                        # os._exit is correct here: Python calls os._exit(1) after excepthook returns anyway
                        os._exit(code)

                    sys.excepthook = excepthook
                raise
            print(f"Error: {e}", file=sys.stderr)
            rc = e.code if isinstance(e, CliError) else 1
        return rc
