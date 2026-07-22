"""
argparse-wizard package.

Cleaner object-oriented argparse-driven command-line interfaces
"""

from __future__ import annotations

from importlib.metadata import version

__version__ = version(__name__.split(".", 1)[0])

__all__ = [
    "__version__",
]
