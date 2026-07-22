# CHANGELOG

## {{UNRELEASED}}

- _Add release notes here._

## 2.1.0 (2026-07-22)

- Added `ctx_enter()`/`ctx_exit()` as the recommended extension points for setup/cleanup around a
  command's execution, replacing direct `__aenter__`/`__aexit__` overrides. Both can be plain `def`
  or `async def`, and the base class's own setup/cleanup always wraps them in the conventional order
  (base first on entry, base last on exit), so a subclass never needs to call `super()` or reason
  about ordering. `ctx_exit()` cannot suppress an exception — by design, its return value is always
  ignored, unlike a real `__exit__` — it's for cleanup, not error handling.
- `preinit()` can now also be a plain `def`, not just `async def`.
- Documented that `ctx_enter()`/`preinit()` intentionally run before argument parsing and before
  `--input-file`/`--output-file` redirection (so `self.args` isn't available yet there, and their
  output always goes to the real console) — that's what lets cleanup run reliably even if something
  fails during parsing itself. Setup that needs parsed arguments or should honor redirection belongs
  in a command's `pre_dispatch_handler` instead.

## 2.0.0 (2026-07-22)

- Command registration methods and the handlers they return can now be written as plain `def`
  functions, not just `async def` — sync and async can be freely mixed, per command, within the same
  CLI. Most commands don't need `async` at all now; it's still there for the ones that do real I/O.
- Added `CliTree`, a new base class that `CliBase` itself now extends — structural groundwork for
  upcoming support for reusable, mountable command subtrees. No behavior change for existing
  `CliBase` subclasses.
- README substantially expanded: pre-dispatch hooks, group-only commands (registration methods that
  return `None`), `--tb` and its debugger interop, and stdio redirection are now documented, along
  with a friendlier overview and highlights.

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
