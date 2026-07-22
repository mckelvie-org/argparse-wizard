# CHANGELOG

## 1.0.0 (2026-07-22)

- Initial release: `CliBase`, a cleaner object-oriented alternative to hand-wiring `argparse`
  subparsers. Declare subcommands (including nested subcommands) as `@cli_command`-decorated async
  methods; command names and hierarchy are derived automatically from method names, or can be given
  explicitly.
- Async-first command dispatch: `run()` is the recommended entry point and drives an internal
  `asyncio` event loop for you, so callers never need to touch `asyncio` themselves. `async_run()` is
  available for apps that already control their own event loop.
- `CliError`/`CliExit` for clean, exit-code-driven error handling instead of raw `sys.exit()` calls.
- `--input-file`/`--output-file` support that transparently reopens `sys.stdin`/`sys.stdout` for the
  duration of a command, so plain `print()`/`input()` (and libraries that inspect `sys.stdout`)
  transparently honor the redirection. `self.orig_stdin`/`self.orig_stdout` remain available to reach
  the real console regardless, and `get_binary_stdin()`/`get_binary_stdout()` give binary-safe access
  to whichever stream is currently in effect.
- Standard `--log-level`/`--tb` arguments.
- Fully typed (`py.typed`), `mypy --strict` clean. Subclasses are plain `class MyCli(CliBase):` with
  no self-referential generics, via `typing.Self`.
