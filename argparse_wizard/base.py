"""The CliBase class: the runnable, top-level CLI. Adds process-level concerns (parsed args, prog
   name, stdio redirection, logging, dispatch) on top of the command-tree machinery in CliTree.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from collections.abc import Awaitable
from types import TracebackType
from typing import BinaryIO, TextIO

from typing_extensions import Never, Self

from .cli_tree import CliTree
from .commands import CliCommand, _call_maybe_async
from .exceptions import CliError, CliExit

__all__ = [
    "CliBase",
]

logger = logging.getLogger(__name__)


class CliBase(CliTree):
    """The runnable, top-level command-line interface. Subclass this and declare @cli_command
       methods; run with cli.run() (or cli.async_run() / await it, from your own event loop).

       Not generic: command methods reference the concrete subclass via `typing.Self` (e.g.
       `cmd: CliCommand[Self]`) rather than requiring subclasses to write `CliBase["MyCli"]`.
    """

    subcommand_required: bool = True
    """Whether a top-level subcommand is required. Override in subclasses. Defaults to True."""

    _prog_name: str
    """Program name, used in the top-level parser help message, as passed to the CLI
       constructor. If not provided, defaults to the basename of sys.argv[0]. Exposed tree-wide
       via CliTree.prog_name."""

    _raw_args: list[str]
    """Raw command-line arguments, as passed to the CLI constructor. Defaults to sys.argv[1:].
       Exposed tree-wide via CliTree.raw_args."""

    _args: argparse.Namespace | None = None
    """Parsed command-line arguments, as returned by parse_args(). Exposed tree-wide via
       CliTree.args."""

    _logger: logging.Logger
    """The logger used by this class. By default, this is the logger for the module that defined
       the CLI subclass. Exposed tree-wide via CliTree.logger."""

    logging_format: str = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    """The logging format string to use if logging must be initialized. Override in subclasses to customize."""

    _orig_stdin: TextIO
    """sys.stdin as it was before --input-file redirection, captured in __aenter__. Exposed
       tree-wide via CliTree.orig_stdin, to read from the real console/pipe regardless of
       --input-file."""

    _orig_stdout: TextIO
    """sys.stdout as it was before --output-file redirection, captured in __aenter__. Exposed
       tree-wide via CliTree.orig_stdout, to write to the real console/pipe regardless of
       --output-file (e.g. status messages that should always reach the terminal, or
       console-capability probes that must reflect the true terminal rather than a redirected file)."""

    _opened_input_file: TextIO | None = None
    """If --input-file was provided, the file object opened for it (and currently installed as
       sys.stdin). Closed and cleared in __aexit__."""

    _opened_output_file: TextIO | None = None
    """If --output-file was provided, the file object opened for it (and currently installed as
       sys.stdout). Closed and cleared in __aexit__."""

    def __init__(self, args: list[str] | None = None, prog_name: str | None = None) -> None:
        super().__init__()
        if args is None:
            args = sys.argv[1:]
        if prog_name is None:
            prog_name = os.path.basename(sys.argv[0])
        self._raw_args = args
        self._prog_name = prog_name
        self._logger = logging.getLogger(type(self).__module__.split(".")[0])

    # The following properties are CliBase's own overrides of the corresponding CliTree
    # delegating properties -- distinct definitions, not inherited ones -- so that CliTree's
    # delegation to self._root_tree.<name> resolves here instead of recursing back into itself,
    # and so subclasses can override any of them and have that override picked up tree-wide.

    @property
    def prog_name(self) -> str:
        """Program name, used in parser help messages."""
        return self._prog_name

    @property
    def raw_args(self) -> list[str]:
        """Raw command-line arguments, as passed to the CLI constructor."""
        return self._raw_args

    @property
    def logger(self) -> logging.Logger:
        """The logger used by this CLI."""
        return self._logger

    @property
    def orig_stdin(self) -> TextIO:
        """sys.stdin as it was before --input-file redirection."""
        return self._orig_stdin

    @property
    def orig_stdout(self) -> TextIO:
        """sys.stdout as it was before --output-file redirection."""
        return self._orig_stdout

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

    async def init_parser(self) -> None:
        """Build the parser: create it, add arguments, create subparsers, register commands."""
        self.register_commands()
        await self.initialize_commands()

    def add_standard_top_level_arguments(self) -> None:
        """Add standard top-level arguments to the top-level parser.
           By default, this adds --log-level, --tb, --input-file, and --output-file.
           Override in subclasses to customize.

           This only exists on CliBase, not CliTree -- these are the root's own process-wide
           flags, not something a mounted subtree gets its own copy of. (Making subtrees more
           autonomous about their own arguments is future work, not handled here.)
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
                await _call_maybe_async(c.pre_dispatch_handler)

    def tracebacks_enabled(self) -> bool:
        """Return True if tracebacks should be displayed on error, based on the --tb argument.
           Override in subclasses to customize."""
        return getattr(self.args, "tb", False)

    def __enter__(self) -> Never:
        raise RuntimeError("CliBase does not support synchronous context management; use 'async with' instead")

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        raise RuntimeError("CliBase does not support synchronous context management; use 'async with' instead")

    async def __aenter__(self) -> Self:
        """Async context manager entry, used internally by async_run(). Not meant to be overridden
           directly -- override ctx_enter() instead. Captures the pre-redirection sys.stdin/
           sys.stdout as self.orig_stdin/self.orig_stdout, then calls ctx_enter(): base setup runs
           first, conventional for initialization in a subclass hierarchy, and there's no super()
           chain for a subclass to get wrong since ctx_enter() is the only extension point.
        """
        self._orig_stdin = sys.stdin
        self._orig_stdout = sys.stdout
        await _call_maybe_async(self.ctx_enter)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Async context manager exit, used internally by async_run(). Not meant to be overridden
           directly -- override ctx_exit() instead. Calls ctx_exit() first, then closes any file
           opened for --input-file/--output-file and restores sys.stdin/sys.stdout -- the reverse
           order from __aenter__, matching how nested context managers normally unwind (last
           acquired, first released), so ctx_exit() still sees the environment exactly as the
           command left it rather than already-restored state.
        """
        await _call_maybe_async(self.ctx_exit, exc_type, exc_value, traceback)
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

    def ctx_enter(self) -> None | Awaitable[None]:
        """Override to run setup logic when the CLI starts processing a command -- the extension
           point for __aenter__(). Called after the base class's own setup, so self.orig_stdin/
           self.orig_stdout are already available if you need them. Can be a plain function or an
           async function; no need to call super() or to know when the base class's own setup runs
           relative to yours -- that ordering is fixed by __aenter__() itself.

           Deliberately by design: this runs early, before preinit(), before arguments are parsed,
           and before --input-file/--output-file redirection happens. self.args isn't available
           yet, and anything you print here goes to the real console regardless of --output-file.
           The reason is the same reason this hook exists at all -- __aenter__/__aexit__ wrap the
           *entire* body of async_run(), including preinit() and argument parsing themselves, so
           that ctx_exit() reliably runs for cleanup even if something fails during parsing, not
           just during command dispatch. For setup that needs parsed arguments or should honor
           redirection, use a command's pre_dispatch_handler instead (see
           CliBase.perform_predispatch()), which runs after both.

           Return type deliberately differs from the standard __enter__ convention: a real
           __enter__ returns whatever value should bind to `with ... as x:`, but nothing ever binds
           the result of ctx_enter() to anything, so unlike __enter__ there's nothing meaningful to
           return -- whether you implement this as sync or async, the value produced is always None.
        """
        return None

    def ctx_exit(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None | Awaitable[None]:
        """Override to run cleanup logic when the CLI finishes processing a command -- the
           extension point for __aexit__(). Called before the base class's own cleanup (closing any
           redirected file, restoring sys.stdin/sys.stdout), so the environment still looks exactly
           as it did during the command. Can be a plain function or an async function; no need to
           call super().

           The parameters match __aexit__'s so you can inspect whether (and how) the command
           failed. But the return type deliberately differs from the standard __exit__ convention:
           a real __exit__ can return a truthy value to suppress the propagating exception, but
           ctx_exit()'s return value is always ignored -- there is no way to suppress an exception
           from here, by design, so this always returns None. If you need to handle an exception
           rather than just clean up after it, catch it inside your own command handling instead;
           this hook is for cleanup, not error handling.
        """
        return None

    def preinit(self) -> None | Awaitable[None]:
        """Perform any pre-initialization setup before the parser is built. Override in subclasses
           to customize. Can be a plain function or an async function.

           Like ctx_enter() (which runs just before this), this is called before arguments are
           parsed and before --input-file/--output-file redirection happens -- self.args isn't
           available yet, and output goes to the real console regardless of --output-file.
        """
        return None

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
                    await _call_maybe_async(self.preinit)
                    await self.init_parser()
                    args = self.parse_args()
                    tb = self.tracebacks_enabled()
                    cmd: CliCommand[Self] | None = getattr(args, "cmd_meta", None)
                    if cmd is None:
                        raise CliError("Internal error: CliCommand associated with parsed arguments is missing")
                    # Same Self-widening quirk noted in CliTree.register_commands(): the type
                    # checker resolves perform_predispatch's own `Self` to the CliBase bound here,
                    # even though cmd is already correctly typed CliCommand[Self].
                    await self.perform_predispatch(cmd)  # type: ignore[arg-type]
                    handler = cmd.handler
                    if handler is not None:
                        self.logger.debug(f"dispatching command: {cmd}")
                        await _call_maybe_async(handler)
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
