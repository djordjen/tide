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
    "TideQtWindow",
    "TideQtWorkspaceWindow",
    "run_qt_application",
]


def __getattr__(name: str) -> Any:
    if name in {
        "TideQtDetailDialog",
        "TideQtCollectionEditor",
        "TideQtConflictDialog",
        "TideQtEditDialog",
        "TideQtLookupDialog",
        "TideQtReferenceEditor",
        "TideQtReportDialog",
        "TideQtWindow",
        "TideQtWorkspaceWindow",
        "run_qt_application",
    }:
        from .app import (
            TideQtCollectionEditor,
            TideQtConflictDialog,
            TideQtDetailDialog,
            TideQtEditDialog,
            TideQtLookupDialog,
            TideQtReferenceEditor,
            TideQtReportDialog,
            TideQtWindow,
            TideQtWorkspaceWindow,
            run_qt_application,
        )

        return {
            "TideQtCollectionEditor": TideQtCollectionEditor,
            "TideQtConflictDialog": TideQtConflictDialog,
            "TideQtDetailDialog": TideQtDetailDialog,
            "TideQtEditDialog": TideQtEditDialog,
            "TideQtLookupDialog": TideQtLookupDialog,
            "TideQtReferenceEditor": TideQtReferenceEditor,
            "TideQtReportDialog": TideQtReportDialog,
            "TideQtWindow": TideQtWindow,
            "TideQtWorkspaceWindow": TideQtWorkspaceWindow,
            "run_qt_application": run_qt_application,
        }[name]
    raise AttributeError(name)
