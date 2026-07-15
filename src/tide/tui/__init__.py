"""Textual adapter for TIDE application services.

Imports stay lazy so headless installations can use the compiler and services
without installing the optional ``tui`` dependency.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DemoDataError", "TideApp", "seed_demo_data"]


def __getattr__(name: str) -> Any:
    if name == "TideApp":
        from tide.tui.app import TideApp

        return TideApp
    if name in {"DemoDataError", "seed_demo_data"}:
        from tide.tui.demo import DemoDataError, seed_demo_data

        return {"DemoDataError": DemoDataError, "seed_demo_data": seed_demo_data}[name]
    raise AttributeError(name)
