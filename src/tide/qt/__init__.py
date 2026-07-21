"""Optional Qt desktop renderer for TIDE applications.

The presenter is importable without PySide6. The concrete widget adapter stays
lazy so compiler, service, and server installations do not acquire GUI
dependencies.
"""

from __future__ import annotations

from typing import Any

from .presenter import (
    QtBrowseColumn,
    QtBrowseController,
    QtBrowsePage,
    QtDetailCollection,
    QtDetailField,
    QtDetailGroup,
    QtDetailRecord,
)

__all__ = [
    "QtBrowseColumn",
    "QtBrowseController",
    "QtBrowsePage",
    "QtDetailCollection",
    "QtDetailField",
    "QtDetailGroup",
    "QtDetailRecord",
    "TideQtDetailDialog",
    "TideQtWindow",
    "run_qt_application",
]


def __getattr__(name: str) -> Any:
    if name in {"TideQtDetailDialog", "TideQtWindow", "run_qt_application"}:
        from .app import TideQtDetailDialog, TideQtWindow, run_qt_application

        return {
            "TideQtDetailDialog": TideQtDetailDialog,
            "TideQtWindow": TideQtWindow,
            "run_qt_application": run_qt_application,
        }[name]
    raise AttributeError(name)
