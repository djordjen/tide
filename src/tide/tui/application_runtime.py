"""Compatibility import for the UI-independent application runtime loader."""

from tide.runtime.application import (
    ApplicationRuntimeError,
    configure_application_runtime,
)

__all__ = ["ApplicationRuntimeError", "configure_application_runtime"]
