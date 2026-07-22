#!/usr/bin/env python3
"""Example CLI built with argparse_wizard.

Demonstrates a top-level command, nested subcommands, per-command arguments,
and clean error handling via CliError.

Try it:
    python examples/greet.py hello --name Ada
    python examples/greet.py counter increment 5
    python examples/greet.py counter increment --by=-2
    python examples/greet.py hello --name ""
"""

from __future__ import annotations

import sys

from typing_extensions import Self

from argparse_wizard import CliBase, CliCommand, CliError, OptCmdFunc, cli_command


class GreetCli(CliBase):
    """A small example CLI: a greeting command and a counter command group."""

    @cli_command("Greet someone by name.")
    async def cmd_hello(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            name: str = self.args.name
            if not name:
                raise CliError("--name must not be empty")
            # Plain print() works because --output-file (if given) reopens sys.stdout for the
            # duration of the command; self.orig_stdout is still there if you need to bypass that.
            print(f"Hello, {name}!")

        p = cmd.get_parser()
        p.add_argument("--name", "-n", default="world", help="Name to greet (default: world).")
        return handler

    @cli_command("Counter commands (group with no bare handler; a subcommand is required).")
    async def cmd_counter(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None

    @cli_command("Print BY added to zero (a stand-in for updating persistent state).")
    async def cmd_counter__increment(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            print(self.args.by)

        p = cmd.get_parser()
        p.add_argument("by", nargs="?", type=int, default=1, help="Amount to increment by (default: 1).")
        return handler

    @cli_command("Example CLI for argparse-wizard.")
    async def main(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        # No handler for the main command: a subcommand is required.
        return None


def main(args: list[str] | None = None) -> int:
    return GreetCli(args).run()


if __name__ == "__main__":
    sys.exit(main())
