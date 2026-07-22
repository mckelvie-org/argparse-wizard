# argparse-wizard

[![CI](https://github.com/mckelvie-org/argparse-wizard/actions/workflows/ci.yml/badge.svg)](https://github.com/mckelvie-org/argparse-wizard/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/argparse-wizard.svg)](https://pypi.org/project/argparse-wizard/)
[![Python versions](https://img.shields.io/pypi/pyversions/argparse-wizard.svg)](https://pypi.org/project/argparse-wizard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`argparse-wizard`: Cleaner object-oriented argparse-driven command-line interfaces

## Highlights

- Declare subcommands (including nested subcommands) as decorated async methods on a `CliBase`
  subclass instead of hand-wiring `argparse` subparsers. Command names and hierarchy are derived
  automatically from method names (`cmd_test__list` → `test list`), or can be given explicitly.
- A command's `add_argument()` calls live in the same method as the handler that reads them back
  off `self.args` — not in a separate parser-setup block you have to remember to keep in sync as
  the handler evolves. Add, rename, or remove an argument and its usage in one place.
- Built-in `--log-level`, `--tb`, `--input-file`/`--output-file` handling. `-i`/`-o` reopen
  `sys.stdin`/`sys.stdout` for the duration of the command, so plain `print()`/`input()` and any
  library that inspects `sys.stdout` (colorizers, `rich`, ...) transparently honor the redirection,
  the same way shell redirection would. `self.orig_stdin`/`self.orig_stdout` reach the real
  console/pipe regardless, and `get_binary_stdin()`/`get_binary_stdout()` give binary-safe access to
  whichever stream is currently in effect.
- `CliError`/`CliExit` for clean, exit-code-driven error handling instead of raw `sys.exit()` calls.
- Commands and pre-dispatch hooks are `async def`. Call `cli.run()` and it drives an event loop for
  you — no `asyncio` import needed in your own code. Apps that already have (or want to control)
  their own event loop can `await cli.async_run()` instead.
- Fully typed (`py.typed`), works under `mypy --strict`.

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
    async def cmd_hello(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            name: str = self.args.name
            if not name:
                raise CliError("--name must not be empty")
            print(f"Hello, {name}!")

        p = cmd.get_parser()
        p.add_argument("--name", "-n", default="world")
        return handler

    @cli_command("Example CLI.")
    async def main(self, cmd: CliCommand[Self]) -> OptCmdFunc:
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

## Supported Python Versions

Python 3.10 through 3.14.

## License

MIT. See [LICENSE](LICENSE).

---

For development and release workflow documentation, see [CONTRIBUTING.md](CONTRIBUTING.md).
