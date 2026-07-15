"""Textual adapter for TIDE application services.

Imports stay lazy so headless installations can use the compiler and services
without installing the optional ``tui`` dependency.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ApplicationRuntimeError",
    "DemoDataError",
    "TideApp",
    "configure_application_runtime",
    "seed_demo_data",
]


def __getattr__(name: str) -> Any:
    if name == "TideApp":
        from tide.tui.app import TideApp

        return TideApp
    if name in {"DemoDataError", "seed_demo_data"}:
        from tide.tui.demo import DemoDataError, seed_demo_data

        return {"DemoDataError": DemoDataError, "seed_demo_data": seed_demo_data}[name]
    if name in {"ApplicationRuntimeError", "configure_application_runtime"}:
        from tide.tui.application_runtime import (
            ApplicationRuntimeError,
            configure_application_runtime,
        )

        return {
            "ApplicationRuntimeError": ApplicationRuntimeError,
            "configure_application_runtime": configure_application_runtime,
        }[name]
    raise AttributeError(name)
