"""Optional Qt desktop renderer for TIDE applications.

The presenter is importable without PySide6. The concrete widget adapter stays
lazy so compiler, service, and server installations do not acquire GUI
dependencies.
"""

from __future__ import annotations

from typing import Any

from .presenter import (
    QtBrowseBatch,
    QtBrowseColumn,
    QtBrowseController,
    QtBrowseQuery,
    QtDetailCollection,
    QtDetailField,
    QtDetailGroup,
    QtDetailRecord,
    QtEditField,
    QtEditForm,
    QtEditGroup,
)

__all__ = [
    "QtBrowseBatch",
    "QtBrowseColumn",
    "QtBrowseController",
    "QtBrowseQuery",
    "QtDetailCollection",
    "QtDetailField",
    "QtDetailGroup",
    "QtDetailRecord",
    "QtEditField",
    "QtEditForm",
    "QtEditGroup",
    "TideQtEditDialog",
    "TideQtDetailDialog",
    "TideQtWindow",
    "run_qt_application",
]


def __getattr__(name: str) -> Any:
    if name in {
        "TideQtDetailDialog",
        "TideQtEditDialog",
        "TideQtWindow",
        "run_qt_application",
    }:
        from .app import (
            TideQtDetailDialog,
            TideQtEditDialog,
            TideQtWindow,
            run_qt_application,
        )

        return {
            "TideQtDetailDialog": TideQtDetailDialog,
            "TideQtEditDialog": TideQtEditDialog,
            "TideQtWindow": TideQtWindow,
            "run_qt_application": run_qt_application,
        }[name]
    raise AttributeError(name)
