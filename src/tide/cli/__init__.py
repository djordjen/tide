"""The `tide` command line, one module per command family.

This was a single 3,035-line module whose parser alone ran to 985 lines,
so every command's arguments sat about a thousand lines from the handler
that read them. It stays importable as `tide.cli` -- `pyproject.toml`
names `tide.cli:main` as the entry point.
"""

from __future__ import annotations

from .main import main

__all__ = ["main"]
