"""Exceptions used to control CLI exit behavior."""

from __future__ import annotations

__all__ = [
    "CliExit",
    "CliError",
]


class CliExit(Exception):
    """Exit the CLI cleanly with a given exit code (no error message printed)."""

    code: int

    def __init__(self, code: int = 0) -> None:
        self.code = code


class CliError(Exception):
    """Exit the CLI after printing an error message."""

    code: int

    def __init__(self, message: str | None = None, *, code: int = 1) -> None:
        if message is None:
            message = f"CLI exited with code {code}"
        super().__init__(str(message))
        self.code = code
