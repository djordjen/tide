"""Optional Qt desktop renderer for TIDE applications.

The presenter is importable without PySide6. The concrete widget adapter stays
lazy so compiler, service, and server installations do not acquire GUI
dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .presenter import (
    QtBrowseBatch,
    QtBrowseColumn,
    QtBrowseController,
    QtBrowseQuery,
    QtRecordReport,
    QtSummaryReport,
    QtDetailCollection,
    QtDetailField,
    QtDetailGroup,
    QtDetailRecord,
    QtEditCollection,
    QtEditAction,
    QtEditActionError,
    QtEditConflict,
    QtEditField,
    QtEditForm,
    QtEditGroup,
    QtEditRebase,
    QtLookupRecord,
    QtLookupSelection,
    QtLookupSpec,
)

__all__ = [
    "QtBrowseBatch",
    "QtBrowseColumn",
    "QtBrowseController",
    "QtBrowseQuery",
    "QtRecordReport",
    "QtSummaryReport",
    "QtDetailCollection",
    "QtDetailField",
    "QtDetailGroup",
    "QtDetailRecord",
    "QtEditCollection",
    "QtEditAction",
    "QtEditActionError",
    "QtEditConflict",
    "QtEditField",
    "QtEditForm",
    "QtEditGroup",
    "QtEditRebase",
    "QtLookupRecord",
    "QtLookupSelection",
    "QtLookupSpec",
    "TideQtCollectionEditor",
    "TideQtConflictDialog",
    "TideQtEditDialog",
    "TideQtDetailDialog",
    "TideQtLookupDialog",
    "TideQtReferenceEditor",
    "TideQtReportDialog",
    "TideQtTableModel",
    "TideQtWindow",
    "TideQtWorkspaceWindow",
    "run_qt_application",
]


#: Which module holds each widget. This is the one place that knows, so a
#: screen can move without every caller following it, and importing one screen
#: does not drag in the rest of the renderer.
_WIDGETS = {
    "TideQtCollectionEditor": "collection",
    "TideQtConflictDialog": "conflict",
    "TideQtDetailDialog": "detail",
    "TideQtEditDialog": "form",
    "TideQtLookupDialog": "lookup",
    "TideQtReferenceEditor": "editors",
    "TideQtReportDialog": "report",
    "TideQtTableModel": "table",
    "TideQtWindow": "app",
    "TideQtWorkspaceWindow": "app",
    "run_qt_application": "app",
}


def __getattr__(name: str) -> Any:
    """Load a widget on first use, so PySide6 stays optional until then."""

    module = _WIDGETS.get(name)
    if module is None:
        raise AttributeError(name)
    return getattr(import_module(f".{module}", __name__), name)
