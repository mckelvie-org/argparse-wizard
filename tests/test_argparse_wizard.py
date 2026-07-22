"""
General pytest tests for this package.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import pytest
from typing_extensions import Self

from argparse_wizard import (
    CliBase,
    CliCommand,
    CliError,
    CliExit,
    OptCmdFunc,
    __version__,
    cli_command,
)


class SampleCli(CliBase):
    """A small CLI used to exercise the framework: a greeting command, a
       "test list"/"test show" nested command group, error/exit commands, and
       commands that exercise stdio redirection.
    """

    @cli_command("Greet someone by name.")
    async def cmd_hello(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            name: str = self.args.name
            print(f"Hello, {name}!")

        p = cmd.get_parser()
        p.add_argument("--name", "-n", default="world")
        return handler

    @cli_command("Test commands.")
    async def cmd_test(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None  # group only; no bare handler

    @cli_command("List tests.")
    async def cmd_test__list(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            print("a", "b", "c")

        return handler

    @cli_command("Show a test.")
    async def cmd_test__show(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            print(self.args.number)

        p = cmd.get_parser()
        p.add_argument("number", type=int)
        return handler

    @cli_command("Fail with a CliError.")
    async def cmd_fail(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            raise CliError("something went wrong", code=self.args.code)

        p = cmd.get_parser()
        p.add_argument("--code", type=int, default=1)
        return handler

    @cli_command("Exit cleanly with a specific code.")
    async def cmd_bail(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            raise CliExit(self.args.code)

        p = cmd.get_parser()
        p.add_argument("--code", type=int, default=0)
        return handler

    @cli_command("Echo stdin to stdout as text.")
    async def cmd_cat(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            sys.stdout.write(sys.stdin.read())

        return handler

    @cli_command("Echo stdin to stdout as raw bytes.")
    async def cmd_binary_cat(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            self.get_binary_stdout().write(self.get_binary_stdin().read())

        return handler

    @cli_command("Write to the real console, bypassing --output-file.")
    async def cmd_realout(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            self.orig_stdout.write("to the console\n")
            self.orig_stdout.flush()

        return handler

    @cli_command("Report whether stdout is currently a terminal.")
    async def cmd_isatty(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            print("TTY" if sys.stdout.isatty() else "NOTTY")

        return handler

    @cli_command("Sync registration method, sync handler.")
    def cmd_sync(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        def handler() -> None:
            print("sync registration, sync handler")

        return handler

    @cli_command("Sync registration method, async handler.")
    def cmd_sync_async(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        async def handler() -> None:
            print("sync registration, async handler")

        return handler

    @cli_command("Async registration method, sync handler.")
    async def cmd_async_sync(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        def handler() -> None:
            print("async registration, sync handler")

        return handler

    @cli_command("Sample CLI.")
    async def main(self, cmd: CliCommand[Self]) -> OptCmdFunc:
        return None


def run_cli(args: list[str]) -> int:
    return SampleCli(args, prog_name="sample").run()


def test_version_is_exposed() -> None:
    assert isinstance(__version__, str)
    assert __version__ != ""


def test_hello_default(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "hello"])
    assert rc == 0
    assert out.read_text() == "Hello, world!\n"


def test_hello_custom_name(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "hello", "--name", "Ada"])
    assert rc == 0
    assert out.read_text() == "Hello, Ada!\n"


def test_nested_subcommand_name_derivation(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "test", "list"])
    assert rc == 0
    assert out.read_text() == "a b c\n"


def test_nested_subcommand_with_arg(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "test", "show", "7"])
    assert rc == 0
    assert out.read_text() == "7\n"


def test_group_command_without_subcommand_fails() -> None:
    with pytest.raises(SystemExit):
        run_cli(["test"])


def test_no_subcommand_at_all_fails() -> None:
    # The top-level parser is created with exit_on_error=False, so a missing
    # required subcommand raises ArgumentError instead of calling sys.exit.
    with pytest.raises(argparse.ArgumentError):
        run_cli([])


def test_cli_error_sets_exit_code_and_prints_message(capsys: pytest.CaptureFixture[str]) -> None:
    rc = run_cli(["fail", "--code", "3"])
    assert rc == 3
    captured = capsys.readouterr()
    assert "something went wrong" in captured.err


def test_cli_error_default_code_is_one() -> None:
    rc = run_cli(["fail"])
    assert rc == 1


def test_cli_error_with_tb_reraises() -> None:
    with pytest.raises(CliError):
        run_cli(["--tb", "fail"])


def test_cli_exit_sets_exit_code() -> None:
    assert run_cli(["bail", "--code", "0"]) == 0
    assert run_cli(["bail", "--code", "5"]) == 5


def test_input_and_output_file_roundtrip_text(tmp_path: Path) -> None:
    infile = tmp_path / "in.txt"
    outfile = tmp_path / "out.txt"
    infile.write_text("hello text world")
    rc = run_cli(["-i", str(infile), "-o", str(outfile), "cat"])
    assert rc == 0
    assert outfile.read_text() == "hello text world"


def test_input_and_output_file_roundtrip_binary(tmp_path: Path) -> None:
    infile = tmp_path / "in.bin"
    outfile = tmp_path / "out.bin"
    infile.write_bytes(b"\x00\x01hello binary world\xff")
    rc = run_cli(["-i", str(infile), "-o", str(outfile), "binary-cat"])
    assert rc == 0
    assert outfile.read_bytes() == b"\x00\x01hello binary world\xff"


def test_orig_stdout_bypasses_output_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "realout"])
    assert rc == 0
    assert out.read_text() == ""
    captured = capsys.readouterr()
    assert captured.out == "to the console\n"


def test_stdout_reflects_output_file_redirection(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "isatty"])
    assert rc == 0
    assert out.read_text() == "NOTTY\n"


def test_stdio_restored_after_call(tmp_path: Path) -> None:
    orig_stdout = sys.stdout
    orig_stdin = sys.stdin
    out = tmp_path / "out.txt"
    run_cli(["-o", str(out), "hello"])
    assert sys.stdout is orig_stdout
    assert sys.stdin is orig_stdin


def test_standard_top_level_arguments_present() -> None:
    cli = SampleCli(["hello"], prog_name="sample")
    asyncio.run(cli.init_parser())
    help_text = cli.parser.format_help()
    assert "--log-level" in help_text
    assert "--tb" in help_text
    assert "--input-file" in help_text
    assert "--output-file" in help_text


def test_duplicate_command_name_raises() -> None:
    class DupCli(CliBase):
        @cli_command("first", name="dup")
        async def cmd_first(self, cmd: CliCommand[Self]) -> OptCmdFunc:
            return None

        @cli_command("second", name="dup")
        async def cmd_second(self, cmd: CliCommand[Self]) -> OptCmdFunc:
            return None

    with pytest.raises(ValueError, match="Duplicate command name"):
        DupCli([]).register_commands()


def test_sync_context_manager_not_supported() -> None:
    cli = SampleCli([])
    with pytest.raises(RuntimeError), cli:
        pass


def test_async_run_works_directly(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = asyncio.run(SampleCli(["-o", str(out), "hello"], prog_name="sample").async_run())
    assert rc == 0
    assert out.read_text() == "Hello, world!\n"


def test_run_rejects_call_from_within_running_loop() -> None:
    async def call_run_from_inside_a_loop() -> int:
        return SampleCli(["hello"], prog_name="sample").run()

    with pytest.raises(RuntimeError, match="running event loop"):
        asyncio.run(call_run_from_inside_a_loop())


def test_sync_registration_sync_handler(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "sync"])
    assert rc == 0
    assert out.read_text() == "sync registration, sync handler\n"


def test_sync_registration_async_handler(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "sync-async"])
    assert rc == 0
    assert out.read_text() == "sync registration, async handler\n"


def test_async_registration_sync_handler(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    rc = run_cli(["-o", str(out), "async-sync"])
    assert rc == 0
    assert out.read_text() == "async registration, sync handler\n"
