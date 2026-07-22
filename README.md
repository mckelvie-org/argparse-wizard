# argparse-wizard

[![CI](https://img.shields.io/badge/CI-passing-brightgreen.svg)](https://github.com/mckelvie-org/argparse-wizard/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/badge/pypi-v2.1.0-blue.svg)](https://pypi.org/project/argparse-wizard/2.1.0/)
[![Python versions](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13%20|%203.14-blue.svg)](https://pypi.org/project/argparse-wizard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`argparse-wizard`: Cleaner object-oriented argparse-driven command-line interfaces

Writing a CLI with plain `argparse` usually means two things that drift apart over time: a block
of `add_argument()`/`add_subparsers()` calls somewhere, and the functions that actually handle each
command somewhere else. `argparse-wizard` puts them in the same place — each command is just a
method on a class, so the code that declares an argument and the code that uses it can never fall
out of sync.

## Highlights

- **One method per command.** Subclass `CliBase`, decorate a method, and it becomes a command —
  nested subcommands too. Names fall out of your method names automatically, or you can give them
  explicitly if you'd rather.
- **Arguments live next to the code that reads them.** No separate parser-setup step to keep in
  sync by hand — add, rename, or remove an argument in one place.
- **Sync or async, your choice.** Write handlers as plain functions or `async` functions, freely
  mixed in the same CLI — a command that just validates some input doesn't need `async` ceremony it
  doesn't use, and a command that talks to a network can still `await` freely.
- **Redirecting input or output behaves like a real shell.** Your code just calls `print()` or
  reads from stdin normally; whether that's actually going to the terminal or to a file someone
  redirected it to is handled for you, transparently.
- **Shared setup lives in one place.** A parent command can run something before any of its
  subcommands — open a connection, validate a global flag — without every subcommand repeating it.
- **Errors are just exceptions with an exit code attached.** No scattered `sys.exit()` calls buried
  in handler logic.
- **Fully typed**, and designed to work cleanly with `mypy --strict` and Pylance/pyright alike, so
  your editor understands your CLI's structure as you build it.

## Installation

```bash
pip install argparse-wizard
```

## Quick Start

```python
import sys

from typing_extensions import Self

from argparse_wizard import CliBase, CliCommand, CliError, OptCmdFunc, cli_command


class GreetCli(CliBase):
    @cli_command("Greet someone by name.")
    def cmd_hello(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        def handler() -> None:
            name: str = self.args.name
            if not name:
                raise CliError("--name must not be empty")
            print(f"Hello, {name}!")

        p = cmd.get_parser()
        p.add_argument("--name", "-n", default="world")
        return handler

    @cli_command("Example CLI.")
    def main(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # no bare handler: a subcommand is required


def main() -> int:
    return GreetCli(sys.argv[1:]).run()


if __name__ == "__main__":
    sys.exit(main())
```

```bash
$ python greet.py hello --name Ada
Hello, Ada!
```

```bash
$ python greet.py --help
usage: greet.py [-h] [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [--tb]
                [--input-file INPUT_FILE] [--output-file OUTPUT_FILE]
                COMMAND ...

Example CLI.

positional arguments:
  COMMAND
    hello               Greet someone by name.

options:
  -h, --help            show this help message and exit
  --log-level, -l {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Logging level (default: WARNING).
  --tb                  Display full traceback on error.
  --input-file, -i INPUT_FILE
                        Read from the given file instead of stdin. Reopens
                        sys.stdin for the duration of the command.
  --output-file, -o OUTPUT_FILE
                        Write to the given file instead of stdout. Reopens
                        sys.stdout for the duration of the command.
```

See [examples/greet.py](examples/greet.py) for a fuller example with nested subcommands.

## Guide

### Commands and subcommands

Every command is a method decorated with `@cli_command(description)`. The method's job is to set
up that command's arguments and hand back the function that should run when the command is invoked
— or `None`, for a command that only exists to group subcommands (more on that below).

Command names are derived from method names by default: `cmd_test` becomes `test`, and
`cmd_test__list` (double underscore as the separator) becomes the nested subcommand `test list`.
You can also give a command an explicit name via `@cli_command(description, name="test list")` if
you'd rather not rely on the naming convention. Every `CliBase` subclass also needs exactly one
`main` method — decorated the same way — which becomes the top-level command itself.

### Sync or async — your choice

A command's setup method and the handler it returns can each independently be a plain function or
an `async` one. Most commands don't need `async` at all — it only earns its keep once a handler
actually has something to `await`:

```python
@cli_command("Fetch a URL and print its status.")
def cmd_check(self, cmd: CliCommand[Self]) -> OptCmdFunc:
    async def handler() -> None:
        status = await fetch_status(self.args.url)
        print(status)

    cmd.get_parser().add_argument("url")
    return handler
```

Here the setup method is a plain `def` (it's just calling `add_argument()`), while the handler is
`async def` because it awaits a network call. Both shapes — and any mix of them across your
commands — work interchangeably.

### Commands that only group subcommands

Returning `None` instead of a handler tells `argparse-wizard` that this command exists purely to
organize its subcommands: it can't be invoked on its own, and a subcommand becomes required. This
is exactly how `main` methods with no top-level behavior work (as in the Quick Start above), and
it's the same pattern for any intermediate "group" command:

```python
@cli_command("Database commands.")
def cmd_db(self, cmd: CliCommand[Self]) -> OptCmdFunc:
    return None  # 'mycli db' by itself isn't runnable; a subcommand is required

@cli_command("Apply pending migrations.")
def cmd_db__migrate(self, cmd: CliCommand[Self]) -> OptCmdFunc:
    def handler() -> None:
        print("migrating...")
    return handler
```

### Pre-dispatch hooks

A command can set `cmd.pre_dispatch_handler` to a function that runs before that command (or any
of its subcommands) is dispatched — the natural place for setup that every subcommand underneath it
should share. Hooks run in order from the outermost command down to the one actually being invoked,
and can be sync or async just like handlers:

```python
class DbCli(CliBase):
    @cli_command("Database commands.")
    def cmd_db(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        def setup() -> None:
            print("connecting to the database...")

        cmd.pre_dispatch_handler = setup
        return None

    @cli_command("Apply pending migrations.")
    def cmd_db__migrate(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        def handler() -> None:
            print("migrating...")
        return handler

    @cli_command("Load seed data.")
    def cmd_db__seed(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        def handler() -> None:
            print("seeding...")
        return handler
```

```bash
$ mycli db migrate
connecting to the database...
migrating...
$ mycli db seed
connecting to the database...
seeding...
```

The hook only runs once, right before dispatch, regardless of how deep the subcommand that was
actually invoked is nested underneath it.

### Redirecting input and output

`--input-file`/`--output-file` (and their short forms `-i`/`-o`) reopen `sys.stdin`/`sys.stdout` for
the duration of the command, the same way shell redirection would — so plain `print()`, `input()`,
and even third-party libraries that inspect `sys.stdout` all just do the right thing without your
handler needing to know or care whether it's talking to a terminal or a file.

If a handler specifically needs the real console regardless of redirection (a progress indicator,
an interactive prompt), `self.orig_stdin`/`self.orig_stdout` are always available. And if a handler
needs to read or write raw bytes rather than text, `self.get_binary_stdin()`/`self.get_binary_stdout()`
give binary-safe access to whichever stream — console or redirected file — is currently in effect.

### Errors and exit codes

Raise `CliError(message, code=1)` to exit with an error message and a specific exit code, or
`CliExit(code)` to exit cleanly (no message) with a given code — both are just exceptions, so they
work naturally from anywhere in a handler's call stack, no need to thread a return code back up
manually.

### Debugging: --tb

By default, any exception a handler raises — a `CliError`, or a genuine bug — is caught, printed as
a short `Error: ...` message, and turned into an exit code, so people running your CLI see a clean
one-line message instead of a raw Python traceback.

Pass `--tb` to turn that off. Instead of being caught and reported, the exception is left to
propagate uncaught, which matters for more than just "you get a traceback":

- Because it's a genuinely *unhandled* exception rather than one that was caught and dealt with,
  a debugger will actually stop there — `python -m pdb`, an IDE's "break on uncaught exception,"
  post-mortem debugging — with the full stack still live to inspect. With the default behavior,
  there's nothing for a debugger to catch, since `argparse-wizard` already handled it.
- The process still exits with the same code either way — a `CliError`'s specific exit code is
  always honored. `--tb` changes how the failure is reported and whether a debugger can see it, not
  what the process ultimately exits with.

Reach for `--tb` while developing or chasing down a bug; leave it off for the polished,
end-user-facing experience.

### Running the CLI

Call `.run()` on your `CliBase` instance — it's the right choice for almost every app, regardless of
whether your own handlers happen to be sync or async, since it takes care of the event loop for you
and your code never needs to import `asyncio` at all. The exception is an app that already has (or
wants to control) its own event loop, e.g. one embedding the CLI inside a larger async application —
that can `await cli.async_run()` instead.

## Supported Python Versions

Python 3.10 through 3.14.

## License

MIT. See [LICENSE](LICENSE).

---

For development and release workflow documentation, see [CONTRIBUTING.md](CONTRIBUTING.md).
