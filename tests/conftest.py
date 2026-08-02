"""Shared pytest configuration for the TIDE contract tests."""

from __future__ import annotations

import os

# Qt must never try to reach a real display. Setting this before any test module
# imports PySide6 keeps the optional GUI suite headless on developer machines and
# CI alike; a module-level assignment inside one test file only works while that
# file happens to be imported first.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
