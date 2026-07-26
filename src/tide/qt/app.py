"""PySide6 Qt Widgets adapter for the initial remote browse/detail prototype."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping
from urllib.parse import quote
from uuid import uuid4

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QRunnable,
    QRegularExpression,
    QSettings,
    QSignalBlocker,
    QThreadPool,
    QTimer,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QKeyEvent,
    QKeySequence,
    QRegularExpressionValidator,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tide.api.contracts import TideSessionInfo
from tide.compiler.normalized import ApplicationModel
from tide.reporting import (
    ReportDocument,
    write_csv,
    write_html,
    write_pdf,
)
from tide.runtime import TideRuntimeError
from tide.security import PROTECTED
from tide.sessions import ConflictDisposition, ConflictValueChoice

from .presenter import (
    BrowseApiClient,
    QtBrowseBatch,
    QtBrowseColumn,
    QtBrowseController,
    QtBrowseQuery,
    QtDetailCollection,
    QtDetailGroup,
    QtDetailRecord,
    QtEditCollection,
    QtEditActionError,
    QtEditConflict,
    QtEditField,
    QtEditForm,
    QtLookupRecord,
    QtLookupSelection,
    QtLookupSpec,
)


class _CallSignals(QObject):
    completed = Signal(object, object)
    failed = Signal(object, object)


class _CallWorker(QRunnable):
    """Run one arbitrary blocking controller call outside Qt's GUI thread."""

    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = _CallSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as error:  # Qt worker boundary reports failures to the GUI.
            self.signals.failed.emit(error, self)
            return
        self.signals.completed.emit(result, self)


class TideQtReferenceEditor(QWidget):
    """Compact reference editor that opens a secured multi-column lookup."""

    lookupRequested = Signal()
    selectionChanged = Signal()

    def __init__(self, field: QtEditField, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.field = field
        self._identity = field.value
        self.setObjectName(f"edit-field-{field.name}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.display = QLineEdit(field.reference_display)
        self.display.setObjectName(f"reference-display-{field.name}")
        self.display.setReadOnly(True)
        self.select_button = QPushButton("Select…")
        self.select_button.setObjectName(f"lookup-{field.name}")
        self.select_button.clicked.connect(self.lookupRequested)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName(f"clear-reference-{field.name}")
        self.clear_button.setVisible(not field.required)
        self.clear_button.clicked.connect(self.clear)
        layout.addWidget(self.display, 1)
        layout.addWidget(self.select_button)
        layout.addWidget(self.clear_button)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self.select_button)
        self._lookup_shortcut = QShortcut(QKeySequence("F4"), self)
        self._lookup_shortcut.activated.connect(self.lookupRequested)

    @property
    def identity(self) -> Any:
        return self._identity

    def set_selection(self, identity: Any, display: str) -> None:
        self._identity = identity
        self.display.setText(display)
        self.selectionChanged.emit()

    def clear(self) -> None:
        self.set_selection(None, "")


class TideQtLookupDialog(QDialog):
    """Search and select a secured reference record through compiled metadata."""

    def __init__(
        self,
        controller: QtBrowseController,
        spec: QtLookupSpec,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.spec = spec
        self.selected_record: QtLookupRecord | None = None
        self._records: tuple[QtLookupRecord, ...] = ()
        self._workers: set[_CallWorker] = set()
        self._create_dialogs: set[TideQtEditDialog] = set()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._generation = 0
        self.setWindowTitle(f"{controller.model.name} — {spec.title}")
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        heading = QLabel(spec.title)
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)
        self.search = QLineEdit()
        self.search.setObjectName("lookup-search")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText(
            "Search "
            + ", ".join(
                controller.model.entity(spec.target_entity)
                .field(name)
                .metadata.get("label", name.replace("_", " ").title())
                for name in spec.search_fields
            )
            + "…"
        )
        layout.addWidget(self.search)
        self.table = QTableWidget(0, len(spec.columns))
        self.table.setObjectName("lookup-results")
        self.table.setHorizontalHeaderLabels(
            [column.label for column in spec.columns]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        _configure_interactive_header(self.table)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.new_button = QPushButton("New")
        self.new_button.setObjectName("create-lookup-record")
        self.new_button.setEnabled(spec.create_available)
        self.cancel_button = QPushButton("Cancel")
        self.select_button = QPushButton("Select")
        self.select_button.setObjectName("select-lookup")
        self.select_button.setEnabled(False)
        actions.addWidget(self.new_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.select_button)
        layout.addLayout(actions)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._reload)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.search.returnPressed.connect(self._select_current)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(
            lambda _row, _column: self._select_current()
        )
        self.new_button.clicked.connect(self._create_record)
        self.cancel_button.clicked.connect(self.reject)
        self.select_button.clicked.connect(self._select_current)
        self._create_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self._create_shortcut.activated.connect(
            self._create_record
        )
        self._reload()
        self.search.setFocus()

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        searches_done = self._thread_pool.waitForDone(milliseconds)
        creates_done = all(
            dialog.wait_for_done(milliseconds)
            for dialog in tuple(self._create_dialogs)
        )
        return searches_done and creates_done

    def _reload(self) -> None:
        self._search_timer.stop()
        self._generation += 1
        generation = self._generation
        search_text = self.search.text()
        self.status.setText("Searching through the TIDE API…")
        self.select_button.setEnabled(False)
        worker = _CallWorker(
            lambda: (
                generation,
                self.controller.search_lookup(self.spec, search_text),
                search_text,
            )
        )
        self._workers.add(worker)
        worker.signals.completed.connect(self._search_completed)
        worker.signals.failed.connect(self._search_failed)
        self._thread_pool.start(worker)

    @Slot(object, object)
    def _search_completed(
        self,
        payload: tuple[int, tuple[QtLookupRecord, ...], str],
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        generation, records, search_text = payload
        if generation != self._generation:
            return
        self._records = records
        self.table.setRowCount(len(records))
        for row_index, record in enumerate(records):
            for column_index, text in enumerate(record.cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    _qt_alignment(self.spec.columns[column_index].alignment)
                )
                self.table.setItem(row_index, column_index, item)
        if records:
            self.table.selectRow(0)
            _fit_interactive_columns(self.table, self.spec.columns)
        noun = "match" if len(records) == 1 else "matches"
        suffix = f" for {search_text!r}" if search_text else ""
        create_hint = "  ·  Ctrl+N creates" if self.spec.create_available else ""
        self.status.setText(
            f"{len(records)} {noun}{suffix}  ·  Enter selects{create_hint}"
        )
        self._selection_changed()

    @Slot(object, object)
    def _search_failed(self, error: Exception, worker: _CallWorker) -> None:
        self._workers.discard(worker)
        self._records = ()
        self.table.setRowCount(0)
        self.select_button.setEnabled(False)
        self.status.setText(f"Lookup failed: {error}")

    def _selection_changed(self) -> None:
        row = self.table.currentRow()
        self.select_button.setEnabled(0 <= row < len(self._records))

    def _select_current(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._records):
            self.selected_record = self._records[row]
            self.accept()

    def _create_record(self) -> None:
        if not self.spec.create_available:
            return
        try:
            target = self.controller.related_create_controller(self.spec)
            form = target.new_form()
        except ValueError as error:
            QMessageBox.critical(self, "TIDE Qt", str(error))
            return
        dialog = TideQtEditDialog(
            target,
            form,
            parent=self,
            save_label="Save & Select",
        )
        self._create_dialogs.add(dialog)
        dialog.recordSaved.connect(self._record_created)
        dialog.finished.connect(
            lambda _result, current=dialog: self._create_dialogs.discard(current)
        )
        dialog.show()

    @Slot(object)
    def _record_created(self, stored: Mapping[str, Any]) -> None:
        try:
            self.selected_record = self.controller.lookup_record(
                self.spec,
                stored,
            )
        except ValueError as error:
            QMessageBox.critical(self, "TIDE Qt", str(error))
            return
        self.accept()


class TideQtCollectionEditor(QGroupBox):
    """Editable inline collection with a table and explicit local line draft."""

    def __init__(
        self,
        dialog: TideQtEditDialog,
        collection: QtEditCollection,
    ) -> None:
        super().__init__(collection.label, dialog)
        self.dialog = dialog
        self.controller = dialog.controller
        self.collection = collection
        self.rows: list[dict[str, Any]] = [
            deepcopy(dict(record)) for record in collection.records
        ]
        self.editors: dict[str, QWidget] = {}
        self._fields = {
            field.name: field
            for field in collection.fields
        }
        self._selected_row: int | None = None
        self._field_labels: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(collection.columns))
        self.table.setObjectName(f"collection-{collection.name}")
        self.table.setHorizontalHeaderLabels(
            [column.label for column in collection.columns]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        _configure_interactive_header(self.table)
        layout.addWidget(self.table, 1)
        layout.addSpacing(8)

        focus_order: list[QWidget] = []
        for group in collection.groups:
            details = QGroupBox(group.label)
            grid = QGridLayout(details)
            positioned: dict[tuple[int, int], QWidget] = {}
            for row_index, row in enumerate(group.rows):
                for column_index, field in enumerate(row):
                    offset = column_index * 2
                    label = QLabel(
                        f"{field.label} *" if field.required else field.label
                    )
                    editor = dialog._field_editor(
                        field,
                        lookup_handler=(
                            lambda item=field: self._open_lookup(item)
                        ),
                    )
                    self.editors[field.name] = editor
                    self._field_labels[field.name] = label
                    positioned[row_index, column_index] = editor
                    if not field.editable:
                        label.setStyleSheet(
                            "color: palette(mid); font-style: italic;"
                        )
                    grid.addWidget(label, row_index, offset)
                    grid.addWidget(editor, row_index, offset + 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 1)
            layout.addWidget(details)
            column_count = max((len(row) for row in group.rows), default=0)
            for column_index in range(column_count):
                for row_index in range(len(group.rows)):
                    editor = positioned.get((row_index, column_index))
                    if editor is not None:
                        focus_order.append(editor)
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        actions = QHBoxLayout()
        self.action_buttons: dict[str, QPushButton] = {}
        labels = {
            "add": "Add line",
            "apply": "Apply line",
            "remove": "Remove line",
        }
        for action in collection.actions:
            if action not in labels:
                continue
            button = QPushButton(labels[action])
            button.setObjectName(f"{action}-{collection.name}")
            self.action_buttons[action] = button
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        if "add" in self.action_buttons:
            self.action_buttons["add"].clicked.connect(self.add_line)
        if "apply" in self.action_buttons:
            self.action_buttons["apply"].clicked.connect(self.apply_line)
        if "remove" in self.action_buttons:
            self.action_buttons["remove"].clicked.connect(self.remove_line)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        self._refresh(select=0 if self.rows else None)

    def replace_collection(self, collection: QtEditCollection) -> None:
        """Replace record rows and workflow state without rebuilding widgets."""

        if _collection_structure(collection) != _collection_structure(
            self.collection
        ):
            raise ValueError(
                f"{collection.label} layout changed while navigating"
            )
        self.collection = collection
        self._fields = {field.name: field for field in collection.fields}
        self.setTitle(collection.label)
        for name, editor in self.editors.items():
            field = self._fields[name]
            self.dialog._configure_field_editor(field, editor)
            self.dialog._configure_field_label(
                self._field_labels[name],
                field,
            )
        self.rows = [
            deepcopy(dict(record)) for record in collection.records
        ]
        self._refresh(
            select=0 if self.rows else None,
            fit_columns=False,
        )

    def values(self) -> list[dict[str, Any]]:
        return deepcopy(self.rows)

    def current_draft(
        self,
        *,
        enforce_required: bool,
    ) -> dict[str, Any]:
        if self._selected_row is None:
            raise ValueError("Add or select a line before choosing a value")
        draft = deepcopy(self.rows[self._selected_row])
        for name, editor in self.editors.items():
            draft[name] = _editor_value(
                self._fields[name],
                editor,
                enforce_required=enforce_required,
            )
        return draft

    def prepare_save(self) -> None:
        if self.collection.editable and self._selected_row is not None:
            self.apply_line()

    def add_line(self) -> None:
        if not self.collection.editable:
            return
        record = self.controller.new_collection_record(
            self.collection,
            tuple(self.rows),
        )
        self.rows.append(record)
        self._refresh(select=len(self.rows) - 1)
        self.dialog.message.setText(
            "Line added locally; Apply updates its preview and Save commits it."
        )
        self.dialog.refresh_action_state()

    def apply_line(self) -> None:
        if not self.collection.editable or self._selected_row is None:
            return
        draft = self.current_draft(enforce_required=True)
        self.rows[self._selected_row] = self.controller.preview_collection_record(
            self.collection,
            draft,
        )
        selected = self._selected_row
        self._refresh(select=selected)
        self.dialog.refresh_computed_preview()
        self.dialog.refresh_action_state()
        self.dialog.message.setText(
            "Line applied locally; Save commits the invoice."
        )

    def remove_line(self) -> None:
        if not self.collection.editable or self._selected_row is None:
            return
        removed = self._selected_row
        del self.rows[removed]
        self._selected_row = None
        select = min(removed, len(self.rows) - 1)
        self._refresh(select=select if select >= 0 else None)
        self.dialog.refresh_computed_preview()
        self.dialog.refresh_action_state()
        self.dialog.message.setText(
            "Line removed locally; Save commits the invoice."
        )

    def set_working(self, working: bool) -> None:
        self.setEnabled(not working)
        if not working:
            for name, editor in self.editors.items():
                self.dialog._configure_field_editor(
                    self._fields[name],
                    editor,
                )
            self._update_actions()

    def apply_lookup_selection(self, selection: QtLookupSelection) -> None:
        for name, value in selection.values.items():
            editor = self.editors.get(name)
            field = self._fields.get(name)
            if editor is None or field is None or not field.editable:
                continue
            _set_editor_value(
                field,
                editor,
                value,
                reference_display=(
                    selection.display if name == selection.field_name else None
                ),
            )

    def _open_lookup(self, field: QtEditField) -> None:
        if self._selected_row is None:
            self.dialog.message.setText(
                "Add or select a line before choosing a lookup value."
            )
            return
        self.dialog._open_lookup(field, collection_editor=self)

    def _table_selection_changed(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            self._select_row(row)

    def _select_row(self, row: int) -> None:
        self._selected_row = row
        values = self.rows[row]
        for name, editor in self.editors.items():
            field = self._fields[name]
            value = values.get(name)
            _set_editor_value(
                field,
                editor,
                value,
                reference_display=(
                    self.controller.reference_display(field, value)
                    if field.field_type == "reference"
                    else None
                ),
            )
        self._update_actions()

    def _refresh(
        self,
        *,
        select: int | None,
        fit_columns: bool = True,
    ) -> None:
        self.table.setRowCount(len(self.rows))
        for row_index, record in enumerate(self.rows):
            cells = self.controller.collection_cells(
                self.collection,
                record,
            )
            for column_index, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    _qt_alignment(
                        self.collection.columns[column_index].alignment
                    )
                )
                self.table.setItem(row_index, column_index, item)
        if self.rows and fit_columns:
            _fit_interactive_columns(self.table, self.collection.columns)
        if select is not None and 0 <= select < len(self.rows):
            self.table.selectRow(select)
            self._select_row(select)
        else:
            self._selected_row = None
            self._clear_editors()
        self._update_actions()

    def _clear_editors(self) -> None:
        for name, editor in self.editors.items():
            _set_editor_value(
                self._fields[name],
                editor,
                None,
                reference_display="",
            )

    def _update_actions(self) -> None:
        selected = self._selected_row is not None
        if "add" in self.action_buttons:
            self.action_buttons["add"].setEnabled(self.collection.editable)
        for action in ("apply", "remove"):
            if action in self.action_buttons:
                self.action_buttons[action].setEnabled(
                    self.collection.editable and selected
                )


class TideQtConflictDialog(QDialog):
    """Three-way review of a stale draft before reload or safe rebase."""

    def __init__(
        self,
        controller: QtBrowseController,
        conflict: QtEditConflict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.conflict = conflict
        self.action: str | None = None
        self.choices: dict[str, ConflictValueChoice] = {}
        self.choice_editors: dict[str, QComboBox] = {}
        self.setWindowTitle(f"{controller.model.name} — Record changed elsewhere")
        self.resize(980, 470)

        layout = QVBoxLayout(self)
        heading = QLabel("Record changed elsewhere")
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)

        conflicts = len(conflict.comparison.conflicting_fields)
        safe = len(conflict.comparison.rebase_fields)
        self.summary = QLabel(
            f"{conflicts} field(s) require a decision; "
            f"{safe} draft-only change(s) can be safely retained."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        guidance = QLabel(
            "Compare Original, Current server value, and Your draft. "
            "Choose Current or Mine for every conflicting row. Applying the "
            "resolution reopens a fresh form for review before saving."
        )
        guidance.setWordWrap(True)
        layout.addWidget(guidance)
        if conflict.locked_fields:
            locked = QLabel(
                "Current workflow rules now lock: "
                + ", ".join(
                    controller.conflict_field_label(name)
                    for name in conflict.locked_fields
                )
                + ". Those fields cannot be carried into the fresh draft."
            )
            locked.setWordWrap(True)
            locked.setStyleSheet(
                "background: palette(alternate-base); padding: 6px;"
            )
            layout.addWidget(locked)

        self.table = QTableWidget(
            len(conflict.comparison.fields),
            5,
        )
        self.table.setObjectName("conflict-fields")
        self.table.setHorizontalHeaderLabels(
            ("Field", "Original", "Current", "Your draft", "Resolution")
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        for row, field in enumerate(conflict.comparison.fields):
            values = (
                controller.conflict_field_label(field.name),
                controller.format_conflict_value(field.name, field.original),
                controller.format_conflict_value(field.name, field.current),
                controller.format_conflict_value(field.name, field.draft),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            if field.disposition is ConflictDisposition.CONFLICT:
                choice = QComboBox()
                choice.setObjectName(f"conflict-choice-{field.name}")
                choice.addItem("Choose…", None)
                choice.addItem("Use Current", ConflictValueChoice.CURRENT)
                if field.name not in conflict.locked_fields:
                    choice.addItem("Use Mine", ConflictValueChoice.DRAFT)
                choice.currentIndexChanged.connect(self._choices_changed)
                self.choice_editors[field.name] = choice
                self.table.setCellWidget(row, 4, choice)
            else:
                self.table.setItem(
                    row,
                    4,
                    QTableWidgetItem(
                        {
                            ConflictDisposition.YOUR_CHANGE: "Keep your change",
                            ConflictDisposition.CURRENT_CHANGE: "Use current",
                            ConflictDisposition.SAME_CHANGE: "Already same",
                        }[field.disposition]
                    ),
                )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.keep_editing = QPushButton("Continue Editing")
        self.keep_editing.setObjectName("keep-conflict-draft")
        self.reload_current = QPushButton("Reload Current")
        self.reload_current.setObjectName("reload-conflict-record")
        self.apply_resolution = QPushButton("Apply Resolution")
        self.apply_resolution.setObjectName("apply-conflict-resolution")
        self.apply_resolution.setDefault(True)
        actions.addWidget(self.keep_editing)
        actions.addStretch(1)
        actions.addWidget(self.reload_current)
        actions.addWidget(self.apply_resolution)
        layout.addLayout(actions)

        self.keep_editing.clicked.connect(self.reject)
        self.reload_current.clicked.connect(self._reload)
        self.apply_resolution.clicked.connect(self._rebase)
        self._choices_changed()

    @Slot()
    def _choices_changed(self) -> None:
        choices: dict[str, ConflictValueChoice] = {}
        for name, editor in self.choice_editors.items():
            raw_choice = editor.currentData()
            if raw_choice is None:
                continue
            choices[name] = ConflictValueChoice(raw_choice)
        self.choices = choices
        remaining = len(
            set(self.conflict.comparison.conflicting_fields) - set(self.choices)
        )
        conflicts = len(self.conflict.comparison.conflicting_fields)
        safe = len(self.conflict.comparison.rebase_fields)
        self.summary.setText(
            f"{conflicts} field(s) require a decision; {remaining} remain. "
            f"{safe} draft-only change(s) are safe."
        )
        self.apply_resolution.setEnabled(remaining == 0)

    @Slot()
    def _reload(self) -> None:
        self.action = "reload"
        self.accept()

    @Slot()
    def _rebase(self) -> None:
        if self.apply_resolution.isEnabled():
            self.action = "rebase"
            self.accept()


class TideQtEditDialog(QDialog):
    """Metadata-driven create/update dialog for supported form fields."""

    recordSaved = Signal(object)
    recordActionCompleted = Signal(str, object)
    reopenRequested = Signal(object, str)
    navigationRequested = Signal(int)

    def __init__(
        self,
        controller: QtBrowseController,
        form: QtEditForm,
        parent: QWidget | None = None,
        *,
        save_label: str = "Save",
        report_directory: Path | None = None,
        report_opener: Callable[[Path], bool] | None = None,
        navigation_enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.form = form
        self.editors: dict[str, QWidget] = {}
        self.collection_editors: dict[str, TideQtCollectionEditor] = {}
        self._field_labels: dict[str, QLabel] = {}
        self._fields = {field.name: field for field in form.fields}
        self._workers: set[_CallWorker] = set()
        self._lookup_targets: dict[
            _CallWorker,
            TideQtCollectionEditor | None,
        ] = {}
        self._lookup_dialogs: set[TideQtLookupDialog] = set()
        self._conflict_dialogs: set[TideQtConflictDialog] = set()
        self._save_attempts: dict[
            _CallWorker,
            tuple[QtEditForm, dict[str, Any]],
        ] = {}
        self._action_attempts: dict[
            _CallWorker,
            tuple[QtEditForm, dict[str, Any], str],
        ] = {}
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._saving = False
        self._save_label = save_label
        self._navigation_enabled = navigation_enabled
        self._can_previous = False
        self._can_next = False
        self.report_directory = report_directory
        self.report_opener = report_opener or _open_local_report
        self.setWindowTitle(f"{controller.model.name} — {form.title}")
        self.resize(1050 if form.collections else 760, 720 if form.collections else 360)

        layout = QVBoxLayout(self)
        self.heading = QLabel(form.title)
        self.heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(self.heading)
        if form.omitted_collections:
            collection_labels = ", ".join(
                controller.entity.field(name).metadata.get(
                    "label",
                    name.replace("_", " ").title(),
                )
                for name in form.omitted_collections
                if name in controller.entity.fields
            )
            note = QLabel(
                (
                    f"{collection_labels} are not editable in this Qt header "
                    "form and will start empty."
                    if form.operation == "create"
                    else (
                        f"{collection_labels} remain unchanged in this Qt "
                        "header form; use View to inspect them."
                    )
                )
            )
            note.setWordWrap(True)
            note.setStyleSheet(
                "background: palette(alternate-base); padding: 6px;"
            )
            layout.addWidget(note)
        focus_order: list[QWidget] = []
        for group in form.groups:
            container = QWidget()
            section_layout = QVBoxLayout(container)
            section_layout.setContentsMargins(0, 4, 0, 4)
            section_title = QLabel(group.label)
            section_title.setStyleSheet("font-weight: 600;")
            section_layout.addWidget(section_title)
            fields = QWidget()
            grid = QGridLayout(fields)
            grid.setContentsMargins(8, 0, 8, 0)
            positioned: dict[tuple[int, int], QWidget] = {}
            for row_index, row in enumerate(group.rows):
                for column_index, field in enumerate(row):
                    offset = column_index * 2
                    label = QLabel(
                        f"{field.label} *" if field.required else field.label
                    )
                    editor = self._field_editor(field)
                    self.editors[field.name] = editor
                    self._field_labels[field.name] = label
                    positioned[row_index, column_index] = editor
                    if not field.editable:
                        label.setStyleSheet("color: palette(mid); font-style: italic;")
                    grid.addWidget(label, row_index, offset)
                    grid.addWidget(editor, row_index, offset + 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 1)
            section_layout.addWidget(fields)
            layout.addWidget(container)
            column_count = max((len(row) for row in group.rows), default=0)
            for column_index in range(column_count):
                for row_index in range(len(group.rows)):
                    editor = positioned.get((row_index, column_index))
                    if editor is not None:
                        focus_order.append(editor)

        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        for collection in form.collections:
            editor = TideQtCollectionEditor(self, collection)
            self.collection_editors[collection.name] = editor
            layout.addWidget(editor, 1)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: palette(link-visited);")
        layout.addWidget(self.message)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button.clicked.connect(self._save)
        self.save_button.setText(save_label)
        self._save_available = any(field.editable for field in form.fields) or any(
            collection.editable for collection in form.collections
        )
        self.save_button.setVisible(self._save_available)
        self.cancel_button.clicked.connect(self.reject)
        self.preview_button: QPushButton | None = None
        if (
            form.operation == "update"
            and controller.record_report_available
            and report_directory is not None
        ):
            self.preview_button = QPushButton("Preview PDF")
            self.preview_button.setObjectName("preview-report")
            self.buttons.addButton(
                self.preview_button,
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            self.preview_button.clicked.connect(self._preview_report)
        self.action_buttons: dict[str, QPushButton] = {}
        for action in form.actions:
            button = QPushButton(action.label)
            button.setObjectName(f"record-action-{action.name}")
            if action.name == "post":
                button.setStyleSheet(
                    "font-weight: 600; color: palette(highlighted-text); "
                    "background: palette(highlight); padding: 5px 12px;"
                )
            self.buttons.addButton(
                button,
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            button.clicked.connect(
                lambda _checked=False, name=action.name: self._execute_action(
                    name
                )
            )
            self.action_buttons[action.name] = button
        footer = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.previous_button.setObjectName("previous-record")
        self.previous_button.setToolTip("Previous record (Page Up)")
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("next-record")
        self.next_button.setToolTip("Next record (Page Down)")
        self.previous_button.setVisible(navigation_enabled)
        self.next_button.setVisible(navigation_enabled)
        self.previous_button.clicked.connect(
            lambda: self._request_navigation(-1)
        )
        self.next_button.clicked.connect(lambda: self._request_navigation(1))
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        footer.addStretch(1)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        self.previous_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_PageUp),
            self,
        )
        self.previous_shortcut.setAutoRepeat(False)
        self.previous_shortcut.setEnabled(False)
        self.previous_shortcut.activated.connect(
            lambda: self._request_navigation(-1)
        )
        self.next_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_PageDown),
            self,
        )
        self.next_shortcut.setAutoRepeat(False)
        self.next_shortcut.setEnabled(False)
        self.next_shortcut.activated.connect(
            lambda: self._request_navigation(1)
        )
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self._save)

        self._connect_action_state_editors()
        self.refresh_action_state()
        first_editable = next(
            (
                editor
                for editor in focus_order
                if editor.isEnabled()
                and editor.focusPolicy() != Qt.FocusPolicy.NoFocus
            ),
            None,
        )
        if first_editable is not None:
            first_editable.setFocus()

    def replace_form(self, form: QtEditForm) -> None:
        """Replace an adjacent record without closing or moving this dialog."""

        if _form_structure(form) != _form_structure(self.form):
            raise ValueError("the adjacent record form layout is incompatible")
        self.form = form
        self._fields = {field.name: field for field in form.fields}
        self.setWindowTitle(f"{self.controller.model.name} — {form.title}")
        self.heading.setText(form.title)
        for name, editor in self.editors.items():
            field = self._fields[name]
            self._configure_field_editor(field, editor)
            self._configure_field_label(self._field_labels[name], field)
            blocker = QSignalBlocker(editor)
            _set_editor_value(
                field,
                editor,
                field.value,
                reference_display=field.reference_display,
            )
            del blocker
        for collection in form.collections:
            self.collection_editors[collection.name].replace_collection(
                collection
            )
        self._save_available = any(
            field.editable for field in form.fields
        ) or any(collection.editable for collection in form.collections)
        self.save_button.setVisible(self._save_available)
        for action in form.actions:
            button = self.action_buttons[action.name]
            button.setText(action.label)
        self.message.clear()
        self.refresh_computed_preview()
        self.refresh_action_state()

    def set_navigation_state(
        self,
        *,
        can_previous: bool,
        can_next: bool,
    ) -> None:
        """Update adjacent-record availability without changing form data."""

        self._can_previous = can_previous
        self._can_next = can_next
        self._apply_navigation_state()

    def set_navigation_loading(self, loading: bool) -> None:
        """Temporarily lock the form while an adjacent record is loaded."""

        self._set_saving(
            loading,
            label=self._save_label,
            message="Loading the adjacent record through the TIDE API…",
        )

    def _apply_navigation_state(self) -> None:
        available = self._navigation_enabled and not self._saving
        previous_available = available and self._can_previous
        next_available = available and self._can_next
        self.previous_button.setEnabled(previous_available)
        self.previous_shortcut.setEnabled(previous_available)
        self.next_button.setEnabled(next_available)
        self.next_shortcut.setEnabled(next_available)

    def _request_navigation(self, direction: int) -> None:
        if self._saving or direction not in {-1, 1}:
            return
        if (
            (direction < 0 and not self._can_previous)
            or (direction > 0 and not self._can_next)
        ):
            return
        try:
            values = self._collect_values(enforce_required=True)
        except ValueError as error:
            self.message.setText(str(error))
            return
        if self.controller.form_has_changes(self.form, values):
            self.message.setText(
                "Save or cancel your changes before navigating to another "
                "record."
            )
            return
        self.navigationRequested.emit(direction)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and watched in self.editors.values()
            and not isinstance(watched, QComboBox)
        ):
            self.focusNextChild()
            return True
        return super().eventFilter(watched, event)

    def reject(self) -> None:
        if not self._saving:
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._saving:
            event.ignore()
            return
        super().closeEvent(event)

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        work_done = self._thread_pool.waitForDone(milliseconds)
        lookups_done = all(
            dialog.wait_for_done(milliseconds)
            for dialog in tuple(self._lookup_dialogs)
        )
        return work_done and lookups_done

    def _field_editor(
        self,
        field: QtEditField,
        *,
        lookup_handler: Callable[[], None] | None = None,
    ) -> QWidget:
        if (
            field.field_type == "reference"
            and field.lookup_view is not None
        ):
            editor = TideQtReferenceEditor(field)
            editor.lookupRequested.connect(
                lookup_handler
                if lookup_handler is not None
                else (lambda item=field: self._open_lookup(item))
            )
            self._configure_field_editor(field, editor)
            return editor
        if field.field_type == "boolean":
            editor = QCheckBox()
            editor.setChecked(bool(field.value))
            editor.setObjectName(f"edit-field-{field.name}")
            editor.installEventFilter(self)
            self._configure_field_editor(field, editor)
            return editor
        if field.field_type == "choice":
            editor = QComboBox()
            editor.setObjectName(f"edit-field-{field.name}")
            if not field.required:
                editor.addItem("", None)
            for choice in field.choices:
                editor.addItem(
                    str(choice).replace("_", " ").title(),
                    choice,
                )
            current = editor.findData(field.value)
            editor.setCurrentIndex(max(current, 0))
            editor.installEventFilter(self)
            self._configure_field_editor(field, editor)
            return editor

        editor = QLineEdit(_edit_text(field))
        editor.setObjectName(f"edit-field-{field.name}")
        if field.numeric_mask is not None:
            editor.editingFinished.connect(
                lambda current=editor, item=field: _normalize_numeric_editor(
                    current,
                    item,
                )
            )
        editor.installEventFilter(self)
        self._configure_field_editor(field, editor)
        return editor

    def _configure_field_editor(
        self,
        field: QtEditField,
        editor: QWidget,
    ) -> None:
        editable = field.editable and not self._saving
        editor.setEnabled(editable)
        editor.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus
            if editable
            else Qt.FocusPolicy.NoFocus
        )
        if isinstance(editor, TideQtReferenceEditor):
            editor.field = field
            editor.select_button.setVisible(field.editable)
            editor.clear_button.setVisible(field.editable and not field.required)
            editor.display.setStyleSheet(
                ""
                if field.editable
                else (
                    "background: palette(alternate-base); "
                    "color: palette(mid);"
                )
            )
            return
        if isinstance(editor, QLineEdit):
            editor.setReadOnly(not field.editable)
            editor.setStyleSheet(
                ""
                if field.editable
                else (
                    "background: palette(alternate-base); "
                    "color: palette(mid);"
                )
            )
            editor.setMaxLength(field.max_length or 32_767)
            editor.setValidator(
                _field_validator(field, editor)
                if field.editable
                else None
            )
            editor.setPlaceholderText(
                "DD.MM.YYYY"
                if field.field_type == "date" and field.editable
                else ""
            )

    @staticmethod
    def _configure_field_label(
        label: QLabel,
        field: QtEditField,
    ) -> None:
        label.setText(f"{field.label} *" if field.required else field.label)
        label.setStyleSheet(
            ""
            if field.editable
            else "color: palette(mid); font-style: italic;"
        )

    def _save(self) -> None:
        if self._saving or not self._save_available:
            return
        try:
            values = self._collect_values(enforce_required=True)
        except ValueError as error:
            self.message.setText(str(error))
            return
        self._start_save(self.form, values)

    def _execute_action(self, action_name: str) -> None:
        if self._saving:
            return
        try:
            values = self._collect_values(enforce_required=True)
            actions = {
                action.name: action
                for action in self.controller.form_actions(self.form, values)
            }
            action = actions.get(action_name)
            if action is None or not action.visible or not action.enabled:
                raise ValueError(
                    f"{action_name.replace('_', ' ').title()} is unavailable"
                )
        except ValueError as error:
            self.message.setText(str(error))
            self.refresh_action_state()
            return
        self._set_saving(
            True,
            label=self._save_label,
            message=f"Saving the draft and running {action.label} through TIDE…",
        )
        draft = deepcopy(values)
        worker = _CallWorker(
            lambda: self.controller.execute_form_action(
                self.form,
                action_name,
                draft,
                idempotency_key=f"qt:{uuid4()}",
            )
        )
        self._workers.add(worker)
        self._action_attempts[worker] = (
            self.form,
            draft,
            action_name,
        )
        worker.signals.completed.connect(self._action_completed)
        worker.signals.failed.connect(self._action_failed)
        self._thread_pool.start(worker)

    def _preview_report(self) -> None:
        if (
            self._saving
            or self.preview_button is None
            or self.report_directory is None
        ):
            return
        try:
            values = self._collect_values(enforce_required=False)
            if self.controller.form_has_changes(self.form, values):
                raise ValueError("Save your changes before previewing the PDF.")
        except ValueError as error:
            self.message.setText(str(error))
            return
        self._set_saving(
            True,
            label=self._save_label,
            message="Building the secured PDF preview…",
        )
        worker = _CallWorker(self._build_report_pdf)
        self._workers.add(worker)
        worker.signals.completed.connect(self._report_pdf_ready)
        worker.signals.failed.connect(self._report_pdf_failed)
        self._thread_pool.start(worker)

    def _build_report_pdf(self) -> Path:
        assert self.report_directory is not None
        document = self.controller.load_record_report(self.form.identity)
        destination = self.report_directory / (
            f"{document.suggested_filename}-{uuid4().hex}.pdf"
        )
        return write_pdf(document, destination)

    @Slot(object, object)
    def _report_pdf_ready(
        self,
        path: Path,
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        if self.report_opener(path):
            self.message.setText(f"Opened temporary PDF preview: {path.name}")
        else:
            self.message.setText(
                f"PDF created at {path}, but no system viewer accepted it."
            )

    @Slot(object, object)
    def _report_pdf_failed(
        self,
        error: Exception,
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        self.message.setText(f"PDF preview failed: {error}")

    def _collect_values(
        self,
        *,
        enforce_required: bool,
    ) -> dict[str, Any]:
        if enforce_required:
            for collection in self.collection_editors.values():
                collection.prepare_save()
        values = self._editor_values(enforce_required=enforce_required)
        values.update(
            {
                name: collection.values()
                for name, collection in self.collection_editors.items()
                if collection.collection.editable
            }
        )
        return values

    def _start_save(
        self,
        form: QtEditForm,
        values: Mapping[str, Any],
    ) -> None:
        draft = deepcopy(dict(values))
        self._set_saving(True)
        worker = _CallWorker(lambda: self.controller.save_form(form, draft))
        self._workers.add(worker)
        self._save_attempts[worker] = (form, draft)
        worker.signals.completed.connect(self._save_completed)
        worker.signals.failed.connect(self._save_failed)
        self._thread_pool.start(worker)

    def _editor_values(
        self,
        *,
        enforce_required: bool = True,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name, editor in self.editors.items():
            field = self._fields[field_name]
            if not field.editable:
                continue
            values[field_name] = _editor_value(
                field,
                editor,
                enforce_required=enforce_required,
            )
        return values

    def refresh_computed_preview(self) -> None:
        try:
            values = self._editor_values(enforce_required=False)
        except ValueError:
            return
        values.update(
            {
                name: collection.values()
                for name, collection in self.collection_editors.items()
            }
        )
        preview = self.controller.preview_form(self.form, values)
        for name, editor in self.editors.items():
            if (
                name not in self.controller.entity.fields
                or not self.controller.entity.field(name).metadata.get("computed")
                or not isinstance(editor, QLineEdit)
            ):
                continue
            editor.setText(
                self.controller.format_form_value(name, preview.get(name))
            )

    def refresh_action_state(self) -> None:
        if not self.action_buttons:
            return
        try:
            values = self._collect_values(enforce_required=False)
            states = {
                action.name: action
                for action in self.controller.form_actions(self.form, values)
            }
        except ValueError:
            states = {}
        for name, button in self.action_buttons.items():
            state = states.get(name)
            button.setVisible(bool(state and state.visible))
            button.setEnabled(
                bool(state and state.visible and state.enabled and not self._saving)
            )

    def _connect_action_state_editors(self) -> None:
        for editor in self.editors.values():
            if isinstance(editor, QLineEdit):
                editor.textChanged.connect(self.refresh_action_state)
            elif isinstance(editor, QCheckBox):
                editor.toggled.connect(self.refresh_action_state)
            elif isinstance(editor, QComboBox):
                editor.currentIndexChanged.connect(self.refresh_action_state)
            elif isinstance(editor, TideQtReferenceEditor):
                editor.selectionChanged.connect(self.refresh_action_state)

    def _open_lookup(
        self,
        field: QtEditField,
        *,
        collection_editor: TideQtCollectionEditor | None = None,
    ) -> None:
        if self._saving:
            return
        try:
            spec = self.controller.lookup_spec(
                field.name,
                collection_name=(
                    collection_editor.collection.name
                    if collection_editor is not None
                    else None
                ),
            )
        except ValueError as error:
            self.message.setText(f"Lookup unavailable: {error}")
            return
        dialog = TideQtLookupDialog(self.controller, spec, parent=self)
        self._lookup_dialogs.add(dialog)
        dialog.finished.connect(
            lambda result, current=dialog, item=field: self._lookup_finished(
                current,
                item,
                result,
                collection_editor,
            )
        )
        dialog.show()

    def _lookup_finished(
        self,
        dialog: TideQtLookupDialog,
        field: QtEditField,
        result: int,
        collection_editor: TideQtCollectionEditor | None,
    ) -> None:
        if result != QDialog.DialogCode.Accepted or dialog.selected_record is None:
            return
        try:
            values = (
                collection_editor.current_draft(enforce_required=False)
                if collection_editor is not None
                else self._editor_values(enforce_required=False)
            )
        except ValueError as error:
            self.message.setText(str(error))
            return
        self._set_saving(
            True,
            label="Applying…",
            message="Applying the secured lookup selection…",
        )
        worker = _CallWorker(
            lambda: self.controller.apply_lookup_selection(
                self.form,
                field.name,
                values,
                dialog.selected_record,
                collection_name=(
                    collection_editor.collection.name
                    if collection_editor is not None
                    else None
                ),
            )
        )
        self._workers.add(worker)
        self._lookup_targets[worker] = collection_editor
        worker.signals.completed.connect(self._lookup_applied)
        worker.signals.failed.connect(self._lookup_failed)
        self._thread_pool.start(worker)

    @Slot(object, object)
    def _lookup_applied(
        self,
        selection: QtLookupSelection,
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        collection_editor = self._lookup_targets.pop(worker, None)
        if collection_editor is not None:
            collection_editor.apply_lookup_selection(selection)
        else:
            for name, value in selection.values.items():
                editor = self.editors.get(name)
                field = self._fields.get(name)
                if editor is None or field is None or not field.editable:
                    continue
                _set_editor_value(
                    field,
                    editor,
                    value,
                    reference_display=(
                        selection.display
                        if name == selection.field_name
                        else None
                    ),
                )
        self._set_saving(False)
        self.refresh_action_state()
        self.message.setText(
            f"{selection.display} selected; initial values applied."
        )

    @Slot(object, object)
    def _lookup_failed(self, error: Exception, worker: _CallWorker) -> None:
        self._workers.discard(worker)
        self._lookup_targets.pop(worker, None)
        self._set_saving(False)
        self.message.setText(f"Lookup selection failed: {error}")

    @Slot(object, object)
    def _action_completed(
        self,
        stored: Mapping[str, Any],
        worker: _CallWorker,
    ) -> None:
        attempt = self._action_attempts.pop(worker, None)
        self._workers.discard(worker)
        self._set_saving(False)
        action_name = attempt[2] if attempt is not None else "action"
        action = self.controller.entity.actions.get(action_name, {})
        label = str(
            action.get("label")
            or action_name.replace("_", " ").title()
        )
        self.recordActionCompleted.emit(label, dict(stored))
        super().accept()

    @Slot(object, object)
    def _action_failed(
        self,
        error: Exception,
        worker: _CallWorker,
    ) -> None:
        attempt = self._action_attempts.pop(worker, None)
        self._workers.discard(worker)
        if isinstance(error, QtEditActionError):
            form = error.form
            draft = dict(error.draft)
            action_name = error.action.name
        elif attempt is not None:
            form, draft, action_name = attempt
        else:
            self._set_saving(False)
            self.message.setText(f"Action failed: {error}")
            return
        if getattr(error, "code", None) == "stale_version":
            self._start_conflict_review(form, draft)
            return
        self._set_saving(False)
        label = str(
            self.controller.entity.actions.get(action_name, {}).get("label")
            or action_name.replace("_", " ").title()
        )
        if isinstance(error, QtEditActionError) and error.saved_before_action:
            self.reopenRequested.emit(
                error.form,
                f"Draft saved, but {label} failed: {error}. "
                "Correct the record and run the action again.",
            )
            super().reject()
            return
        self.message.setText(f"{label} failed: {error}")

    @Slot(object, object)
    def _save_completed(
        self,
        stored: Mapping[str, Any],
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._save_attempts.pop(worker, None)
        self._set_saving(False)
        self.recordSaved.emit(dict(stored))
        super().accept()

    @Slot(object, object)
    def _save_failed(self, error: Exception, worker: _CallWorker) -> None:
        self._workers.discard(worker)
        attempt = self._save_attempts.pop(worker, None)
        if (
            getattr(error, "code", None) == "stale_version"
            and attempt is not None
            and attempt[0].operation == "update"
        ):
            form, draft = attempt
            self._start_conflict_review(form, draft)
            return
        self._set_saving(False)
        self.message.setText(f"Save failed: {error}")

    def _start_conflict_review(
        self,
        form: QtEditForm,
        draft: Mapping[str, Any],
    ) -> None:
        self._set_saving(
            True,
            label="Reviewing…",
            message=(
                "The record changed elsewhere. Loading the current "
                "version for a three-way review…"
            ),
        )
        review_worker = _CallWorker(
            lambda: self.controller.review_edit_conflict(form, draft)
        )
        self._workers.add(review_worker)
        review_worker.signals.completed.connect(self._conflict_ready)
        review_worker.signals.failed.connect(self._conflict_failed)
        self._thread_pool.start(review_worker)

    @Slot(object, object)
    def _conflict_ready(
        self,
        conflict: QtEditConflict,
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        dialog = TideQtConflictDialog(self.controller, conflict, parent=self)
        self._conflict_dialogs.add(dialog)
        dialog.finished.connect(
            lambda result, current=dialog: self._conflict_review_closed(
                current,
                result,
            )
        )
        dialog.show()

    @Slot(object, object)
    def _conflict_failed(
        self,
        error: Exception,
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        self.message.setText(
            f"Unable to inspect the current record: {error}"
        )

    def _conflict_review_closed(
        self,
        dialog: TideQtConflictDialog,
        result: int,
    ) -> None:
        self._conflict_dialogs.discard(dialog)
        if result != QDialog.DialogCode.Accepted or dialog.action is None:
            self.message.setText(
                "Your stale draft remains open and unsaved. "
                "Reload or rebase before saving."
            )
            return
        if dialog.action == "reload":
            form = dialog.conflict.current_form
            message = "Current data reloaded; the stale draft was discarded."
        else:
            try:
                rebase = self.controller.rebase_edit_conflict(
                    dialog.conflict,
                    dialog.choices,
                )
            except ValueError as error:
                self.message.setText(str(error))
                return
            form = rebase.form
            if rebase.retained_fields:
                message = (
                    "Current data reloaded; resolved draft fields were "
                    "retained. Review, then save or run the action again."
                )
            else:
                message = "Current data reloaded; no draft fields were retained."
            if rebase.dropped_fields:
                message += (
                    " Workflow rules now lock: "
                    + ", ".join(
                        self.controller.conflict_field_label(name)
                        for name in rebase.dropped_fields
                    )
                    + "."
                )
        self.reopenRequested.emit(form, message)
        super().reject()

    def _set_saving(
        self,
        saving: bool,
        *,
        label: str = "Saving…",
        message: str = "Saving through the TIDE API…",
    ) -> None:
        self._saving = saving
        for field_name, editor in self.editors.items():
            if self._fields[field_name].editable:
                editor.setEnabled(not saving)
        for collection in self.collection_editors.values():
            collection.set_working(saving)
        self.save_button.setEnabled(not saving and self._save_available)
        self.cancel_button.setEnabled(not saving)
        for button in self.action_buttons.values():
            button.setEnabled(not saving)
        if self.preview_button is not None:
            self.preview_button.setEnabled(not saving)
        self._apply_navigation_state()
        self.save_button.setText(label if saving else self._save_label)
        if saving:
            self.message.setText(message)
        else:
            self.refresh_action_state()


class TideQtDetailDialog(QDialog):
    """Metadata-driven read-only record detail with nested collections."""

    def __init__(
        self,
        application_name: str,
        detail: QtDetailRecord,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.detail = detail
        self.field_editors: dict[str, QLineEdit] = {}
        self.collection_tables: dict[str, QTableWidget] = {}
        self.setWindowTitle(f"{application_name} — {detail.title}")
        self.resize(920, 680)

        layout = QVBoxLayout(self)
        heading = QLabel(detail.title)
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        for section in detail.sections:
            if isinstance(section, QtDetailGroup):
                content_layout.addWidget(self._detail_group(section))
            elif isinstance(section, QtDetailCollection):
                content_layout.addWidget(self._detail_collection(section))
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        for section in self.detail.sections:
            if isinstance(section, QtDetailCollection):
                table = self.collection_tables.get(section.name)
                if table is not None:
                    _fit_interactive_columns(table, section.columns)

    def _detail_group(self, section: QtDetailGroup) -> QGroupBox:
        group = QGroupBox(section.label)
        grid = QGridLayout(group)
        fields = tuple(field for row in section.rows for field in row)
        for index, field in enumerate(fields):
            row = index // 2
            offset = (index % 2) * 2
            label = QLabel(field.label)
            editor = QLineEdit(field.value)
            editor.setObjectName(f"detail-field-{field.name}")
            editor.setReadOnly(True)
            editor.setAlignment(_qt_alignment(field.alignment))
            self.field_editors[field.name] = editor
            grid.addWidget(label, row, offset)
            grid.addWidget(editor, row, offset + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        return group

    def _detail_collection(self, section: QtDetailCollection) -> QGroupBox:
        group = QGroupBox(section.label)
        layout = QVBoxLayout(group)
        if section.protected:
            layout.addWidget(QLabel("Protected"))
            return group
        table = QTableWidget(len(section.rows), len(section.columns))
        table.setObjectName(f"detail-collection-{section.name}")
        table.setHorizontalHeaderLabels(
            [column.label for column in section.columns]
        )
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        _configure_interactive_header(table)
        for row_index, row in enumerate(section.rows):
            for column_index, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    _qt_alignment(section.columns[column_index].alignment)
                )
                table.setItem(row_index, column_index, item)
        table.setMinimumHeight(190)
        self.collection_tables[section.name] = table
        layout.addWidget(table)
        return group


class TideQtReportDialog(QDialog):
    """Native preview and local export surface for a secured report document."""

    def __init__(
        self,
        document: ReportDocument,
        output_directory: str | Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.output_directory = Path(output_directory)
        self._workers: set[_CallWorker] = set()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._exporting = False
        self.setWindowTitle(f"{document.application} — {document.title}")
        self.resize(980, 720)

        layout = QVBoxLayout(self)
        heading = QLabel(document.title)
        heading.setObjectName("report-title")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(heading)

        context = QLabel(
            f"{document.application}  ·  {document.report}  ·  "
            f"{document.suggested_filename}"
        )
        context.setStyleSheet("color: palette(mid);")
        layout.addWidget(context)
        for text in document.header_text:
            header = QLabel(text)
            header.setWordWrap(True)
            layout.addWidget(header)

        if document.record_values:
            facts = QGroupBox("Record")
            facts_grid = QGridLayout(facts)
            for index, value in enumerate(document.record_values):
                row = index // 2
                offset = (index % 2) * 2
                label = QLabel(value.label)
                label.setStyleSheet("color: palette(mid);")
                editor = QLineEdit(value.text)
                editor.setObjectName(f"report-value-{index}")
                editor.setReadOnly(True)
                editor.setAlignment(_qt_alignment(value.alignment))
                facts_grid.addWidget(label, row, offset)
                facts_grid.addWidget(editor, row, offset + 1)
            facts_grid.setColumnStretch(1, 1)
            facts_grid.setColumnStretch(3, 1)
            layout.addWidget(facts)

        self.detail = QTableWidget(
            len(document.detail.rows),
            len(document.detail.columns),
        )
        self.detail.setObjectName("report-detail")
        self.detail.setHorizontalHeaderLabels(
            [column.label for column in document.detail.columns]
        )
        self.detail.setAlternatingRowColors(True)
        self.detail.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.detail.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.detail.verticalHeader().setVisible(False)
        _configure_interactive_header(self.detail)
        for row_index, row in enumerate(document.detail.rows):
            for column_index, cell in enumerate(row):
                item = QTableWidgetItem(cell.text)
                item.setTextAlignment(_qt_alignment(cell.alignment))
                self.detail.setItem(row_index, column_index, item)
        layout.addWidget(self.detail, 1)

        if document.footer_values:
            totals = QGroupBox("Totals")
            totals_grid = QGridLayout(totals)
            for row, value in enumerate(document.footer_values):
                label = QLabel(value.label)
                label.setStyleSheet("font-weight: 600;")
                result = QLineEdit(value.text)
                result.setObjectName(f"report-footer-{row}")
                result.setReadOnly(True)
                result.setAlignment(_qt_alignment(value.alignment))
                totals_grid.addWidget(label, row, 0)
                totals_grid.addWidget(result, row, 1)
            totals_grid.setColumnStretch(0, 1)
            totals_grid.setColumnStretch(1, 1)
            layout.addWidget(totals)

        self.message = QLabel(
            f"Exports will be written to {self.output_directory}"
        )
        self.message.setObjectName("report-export-status")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.message)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.export_csv = QPushButton("Export CSV")
        self.export_csv.setObjectName("report-export-csv")
        self.export_html = QPushButton("Export HTML")
        self.export_html.setObjectName("report-export-html")
        self.export_pdf = QPushButton("Export PDF")
        self.export_pdf.setObjectName("report-export-pdf")
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("report-close")
        actions.addWidget(self.export_csv)
        actions.addWidget(self.export_html)
        actions.addWidget(self.export_pdf)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)

        self.export_csv.clicked.connect(
            lambda: self._start_export("CSV", write_csv, ".csv")
        )
        self.export_html.clicked.connect(
            lambda: self._start_export("HTML", write_html, ".html")
        )
        self.export_pdf.clicked.connect(
            lambda: self._start_export("PDF", write_pdf, ".pdf")
        )
        self.close_button.clicked.connect(self.reject)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        _fit_interactive_columns(
            self.detail,
            tuple(
                QtBrowseColumn(
                    column.name,
                    column.label,
                    column.alignment,
                )
                for column in self.document.detail.columns
            ),
        )

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        """Wait for any active local report export."""

        return self._thread_pool.waitForDone(milliseconds)

    def _start_export(
        self,
        label: str,
        writer: Callable[[ReportDocument, Path], Path],
        suffix: str,
    ) -> None:
        if self._exporting:
            return
        path = self.output_directory / (
            f"{self.document.suggested_filename}{suffix}"
        )
        self._set_exporting(True, f"Exporting {label}…")
        worker = _CallWorker(lambda: writer(self.document, path))
        self._workers.add(worker)
        worker.signals.completed.connect(
            lambda result, current, name=label: self._export_completed(
                name,
                result,
                current,
            )
        )
        worker.signals.failed.connect(
            lambda error, current, name=label: self._export_failed(
                name,
                error,
                current,
            )
        )
        self._thread_pool.start(worker)

    def _export_completed(
        self,
        label: str,
        path: Path,
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_exporting(False, f"{label} exported to {path}")

    def _export_failed(
        self,
        label: str,
        error: Exception,
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_exporting(False, f"{label} export failed: {error}")

    def _set_exporting(self, exporting: bool, message: str) -> None:
        self._exporting = exporting
        self.export_csv.setEnabled(not exporting)
        self.export_html.setEnabled(not exporting)
        self.export_pdf.setEnabled(not exporting)
        self.close_button.setEnabled(not exporting)
        self.message.setText(message)


class _BatchSignals(QObject):
    completed = Signal(int, object, object, object)
    failed = Signal(int, object, object, object)


class _BatchWorker(QRunnable):
    """Run one blocking HTTP batch outside Qt's GUI thread."""

    def __init__(
        self,
        controller: QtBrowseController,
        generation: int,
        cursor: str | None,
        query: QtBrowseQuery,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.generation = generation
        self.cursor = cursor
        self.query = query
        self.signals = _BatchSignals()

    @Slot()
    def run(self) -> None:
        try:
            batch = self.controller.fetch_batch(
                self.cursor,
                query=self.query,
            )
        except Exception as error:  # Qt worker boundary reports failures to the GUI.
            self.signals.failed.emit(
                self.generation,
                self.cursor,
                error,
                self,
            )
            return
        self.signals.completed.emit(
            self.generation,
            self.cursor,
            batch,
            self,
        )


class TideQtTableModel(QAbstractTableModel):
    """Incremental read-only table over opaque FastAPI continuation cursors."""

    loadingChanged = Signal(bool)
    batchLoaded = Signal(int, bool)
    loadFailed = Signal(str)

    def __init__(
        self,
        controller: QtBrowseController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.columns = controller.columns
        self._rows: list[tuple[str, ...]] = []
        self._identities: list[Any] = []
        self._next_cursor: str | None = None
        self._started = False
        self._loading = False
        self._load_error: str | None = None
        self._generation = 0
        self._query = QtBrowseQuery()
        self._workers: set[_BatchWorker] = set()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)

    @property
    def loading(self) -> bool:
        return self._loading

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def has_more(self) -> bool:
        return not self._started or self._next_cursor is not None

    @property
    def query(self) -> QtBrowseQuery:
        return self._query

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _qt_alignment(self.columns[index.column()].alignment)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.columns)
        ):
            return self.columns[section].label
        return super().headerData(section, orientation, role)

    def identity_at(self, row: int) -> Any:
        if row < 0 or row >= len(self._identities):
            raise ValueError("Qt detail row is not available in the loaded records")
        return self._identities[row]

    def row_for_identity(self, identity: Any) -> int | None:
        """Return an identity's position in the currently loaded query rows."""

        try:
            return self._identities.index(identity)
        except ValueError:
            return None

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        return (
            not parent.isValid()
            and not self._loading
            and self._load_error is None
            and self.has_more
        )

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        if not self.canFetchMore(parent):
            return
        self._start_fetch(self._next_cursor)

    def reload(self) -> None:
        """Discard loaded rows and start a fresh first batch."""

        self._generation += 1
        self.beginResetModel()
        self._rows.clear()
        self._identities.clear()
        self._next_cursor = None
        self._started = False
        self._load_error = None
        self.endResetModel()
        self.controller.reset_browse()
        self._set_loading(False)
        self.fetchMore()

    def set_query(self, query: QtBrowseQuery) -> None:
        """Restart the cursor sequence when structured query inputs change."""

        if query == self._query:
            return
        self._query = query
        self.reload()

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        """Wait for this model's HTTP workers before their client is closed."""

        return self._thread_pool.waitForDone(milliseconds)

    def _start_fetch(self, cursor: str | None) -> None:
        self._set_loading(True)
        worker = _BatchWorker(
            self.controller,
            self._generation,
            cursor,
            self._query,
        )
        self._workers.add(worker)
        worker.signals.completed.connect(self._batch_ready)
        worker.signals.failed.connect(self._worker_failed)
        self._thread_pool.start(worker)

    @Slot(int, object, object, object)
    def _batch_ready(
        self,
        generation: int,
        requested_cursor: str | None,
        batch: QtBrowseBatch,
        worker: _BatchWorker,
    ) -> None:
        self._workers.discard(worker)
        if generation != self._generation:
            return
        if batch.next_cursor is not None and batch.next_cursor == requested_cursor:
            self._batch_failed(
                generation,
                requested_cursor,
                ValueError("server repeated the current continuation cursor"),
            )
            return
        if batch.rows:
            first = len(self._rows)
            last = first + len(batch.rows) - 1
            self.beginInsertRows(QModelIndex(), first, last)
            self._rows.extend(batch.rows)
            self._identities.extend(batch.identities)
            self.endInsertRows()
        self._started = True
        self._next_cursor = batch.next_cursor
        self._load_error = None
        self._set_loading(False)
        self.batchLoaded.emit(len(batch.rows), self.has_more)

    @Slot(int, object, object, object)
    def _worker_failed(
        self,
        generation: int,
        requested_cursor: str | None,
        error: Exception,
        worker: _BatchWorker,
    ) -> None:
        self._workers.discard(worker)
        self._batch_failed(generation, requested_cursor, error)

    @Slot(int, object, object)
    def _batch_failed(
        self,
        generation: int,
        _requested_cursor: str | None,
        error: Exception,
    ) -> None:
        if generation != self._generation:
            return
        self._load_error = str(error)
        self._set_loading(False)
        self.loadFailed.emit(self._load_error)

    def _set_loading(self, value: bool) -> None:
        if self._loading == value:
            return
        self._loading = value
        self.loadingChanged.emit(value)


class TideQtWindow(QMainWindow):
    """Remote Qt workspace that delegates all data access to TideApiClient."""

    def __init__(
        self,
        controller: QtBrowseController,
        *,
        source_label: str,
        layout_settings: QSettings | None = None,
        report_output_directory: str | Path | None = None,
        report_opener: Callable[[Path], bool] | None = None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.source_label = source_label
        self._layout_settings = (
            layout_settings
            if layout_settings is not None
            else QSettings("TIDE Framework", "TIDE Qt")
        )
        self._column_layout_key = _column_layout_key(controller)
        self._restoring_column_layout = False
        self._column_widths_initialized = False
        self._detail_dialogs: set[TideQtDetailDialog] = set()
        self._edit_dialogs: set[TideQtEditDialog] = set()
        self._operation_workers: set[_CallWorker] = set()
        self._navigation_workers: dict[
            _CallWorker,
            tuple[TideQtEditDialog, Any],
        ] = {}
        self._pending_navigation: dict[TideQtEditDialog, int] = {}
        self._operation_pool = QThreadPool(self)
        self._operation_pool.setMaxThreadCount(1)
        self._form_loading = False
        self._report_temp_directory = (
            None
            if report_output_directory is not None
            else TemporaryDirectory(
                prefix="tide-qt-report-",
                ignore_cleanup_errors=True,
            )
        )
        self.report_output_directory = (
            Path(report_output_directory)
            if report_output_directory is not None
            else Path(self._report_temp_directory.name)
        )
        self.report_opener = report_opener
        self._notice: str | None = None
        self.setWindowTitle(f"{controller.model.name} — {controller.title}")
        self.resize(1100, 650)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        heading = QLabel(controller.title)
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        context = QLabel(f"{controller.context_text}  ·  {source_label}")
        context.setStyleSheet("color: palette(mid);")
        layout.addWidget(heading)
        layout.addWidget(context)

        query_controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("browse-search")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText(
            (
                f"Search {controller.search_label}…"
                if controller.search_label is not None
                else "Search is not configured"
            )
        )
        self.search.setEnabled(controller.search_field is not None)
        self.named_filter = QComboBox()
        self.named_filter.setObjectName("browse-filter")
        self.named_filter.addItem("All records", None)
        for named_filter in controller.named_filters.values():
            self.named_filter.addItem(named_filter.label, named_filter.name)
        self.named_filter.setEnabled(bool(controller.named_filters))
        self.clear_query = QPushButton("Clear")
        self.clear_query.setObjectName("clear-query")
        query_controls.addWidget(self.search, 2)
        query_controls.addWidget(self.named_filter, 1)
        query_controls.addWidget(self.clear_query)
        layout.addLayout(query_controls)

        self.table = QTableView()
        self.table_model = TideQtTableModel(controller, self)
        self.table.setModel(self.table_model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerItem
        )
        self.table.verticalHeader().setVisible(False)
        _configure_interactive_header(self.table)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSectionsMovable(True)
        header.setSortIndicatorShown(False)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.status = QLabel()
        self.new = QPushButton("New")
        self.new.setObjectName("new-record")
        self.open = QPushButton("Open")
        self.open.setObjectName("open-record")
        self.best_fit = QPushButton("Best Fit")
        self.best_fit.setObjectName("best-fit-columns")
        self.best_fit.setToolTip("Best fit all columns to their current contents")
        self.reset_layout = QPushButton("Reset Layout")
        self.reset_layout.setObjectName("reset-column-layout")
        self.reset_layout.setToolTip(
            "Restore metadata column order and default fitted widths"
        )
        self.refresh = QPushButton("Refresh")
        close = QPushButton("Close")
        actions.addWidget(self.status, 1)
        actions.addWidget(self.new)
        actions.addWidget(self.open)
        actions.addWidget(self.best_fit)
        actions.addWidget(self.reset_layout)
        actions.addWidget(self.refresh)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.setCentralWidget(root)

        self.refresh.clicked.connect(self.table_model.reload)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._apply_query_controls)
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(250)
        self._layout_save_timer.timeout.connect(self._save_column_layout)
        self.search.textChanged.connect(self._queue_search)
        self.named_filter.currentIndexChanged.connect(self._apply_query_controls)
        self.clear_query.clicked.connect(self._clear_query)
        header.sectionClicked.connect(self._sort_by_section)
        header.sectionMoved.connect(self._queue_column_layout_save)
        header.sectionResized.connect(self._queue_column_layout_save)
        self.new.clicked.connect(self._open_new_form)
        self.open.clicked.connect(self._open_selected_form)
        self.best_fit.clicked.connect(self._best_fit_all_columns)
        self.reset_layout.clicked.connect(self._reset_column_layout)
        self.table.selectionModel().selectionChanged.connect(
            self._update_detail_action
        )
        self.table.activated.connect(lambda index: self._open_row(index.row()))
        self.table_model.loadingChanged.connect(self._loading_changed)
        self.table_model.batchLoaded.connect(self._batch_loaded)
        self.table_model.loadFailed.connect(self._load_failed)
        self.table.verticalScrollBar().valueChanged.connect(
            self._prefetch_if_near_end
        )
        close.clicked.connect(self.close)
        self._column_widths_initialized = self._restore_column_layout()
        self._update_detail_action()
        self.table_model.reload()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._column_widths_initialized:
            self._initialize_column_widths()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._layout_save_timer.isActive():
            self._layout_save_timer.stop()
            self._save_column_layout()
        self._layout_settings.sync()
        super().closeEvent(event)

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        """Wait for browse, form-load, and active form-save workers."""

        browse_done = self.table_model.wait_for_done(milliseconds)
        operations_done = self._operation_pool.waitForDone(milliseconds)
        edits_done = all(
            dialog.wait_for_done(milliseconds)
            for dialog in tuple(self._edit_dialogs)
        )
        return browse_done and operations_done and edits_done

    def _initialize_column_widths(self) -> None:
        """Fit once, then leave every section under direct user control."""

        if self.table_model.rowCount() == 0:
            return
        self._restoring_column_layout = True
        try:
            _fit_interactive_columns(self.table, self.controller.columns)
        finally:
            self._restoring_column_layout = False
        self._column_widths_initialized = True

    def _restore_column_layout(self) -> bool:
        raw = self._layout_settings.value(self._column_layout_key)
        if not isinstance(raw, str) or len(raw) > 65_536:
            return False
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return False

        current_names = tuple(column.name for column in self.controller.columns)
        configured_order = payload.get("order")
        configured_widths = payload.get("widths")
        if not isinstance(configured_order, list) or not isinstance(
            configured_widths,
            dict,
        ):
            return False
        has_known_order = any(
            isinstance(name, str) and name in current_names
            for name in configured_order
        )
        known_order = _known_column_order(configured_order, current_names)
        widths = _known_column_widths(configured_widths, current_names)
        if not has_known_order and not widths:
            return False

        self._restoring_column_layout = True
        try:
            _apply_column_order(
                self.table.horizontalHeader(),
                known_order,
                current_names,
            )
            minimum = self.table.horizontalHeader().minimumSectionSize()
            for logical_index, field_name in enumerate(current_names):
                width = widths.get(field_name)
                if width is not None:
                    self.table.setColumnWidth(
                        logical_index,
                        min(max(width, minimum), 2_000),
                    )
        finally:
            self._restoring_column_layout = False
        return True

    def _queue_column_layout_save(self, *_args: Any) -> None:
        if not self._restoring_column_layout:
            self._layout_save_timer.start()

    def _save_column_layout(self) -> None:
        if self._restoring_column_layout:
            return
        header = self.table.horizontalHeader()
        column_names = tuple(column.name for column in self.controller.columns)
        order = tuple(
            column_names[header.logicalIndex(visual_index)]
            for visual_index in range(header.count())
        )
        widths = {
            name: self.table.columnWidth(logical_index)
            for logical_index, name in enumerate(column_names)
        }
        self._layout_settings.setValue(
            self._column_layout_key,
            json.dumps(
                {
                    "version": 1,
                    "order": order,
                    "widths": widths,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        self._layout_settings.sync()

    def _best_fit_all_columns(self) -> None:
        self._layout_save_timer.stop()
        self._restoring_column_layout = True
        try:
            _fit_content_columns(self.table)
        finally:
            self._restoring_column_layout = False
        self._column_widths_initialized = True
        self._save_column_layout()

    def _reset_column_layout(self) -> None:
        self._layout_save_timer.stop()
        self._restoring_column_layout = True
        try:
            column_names = tuple(
                column.name for column in self.controller.columns
            )
            _apply_column_order(
                self.table.horizontalHeader(),
                column_names,
                column_names,
            )
            _fit_interactive_columns(self.table, self.controller.columns)
            self._layout_settings.remove(self._column_layout_key)
            self._layout_settings.sync()
        finally:
            self._restoring_column_layout = False
        self._column_widths_initialized = True

    def _update_detail_action(self, *_args: Any) -> None:
        selected = self.table.currentIndex().isValid()
        busy = self._form_loading
        self.new.setEnabled(
            self.controller.create_available and not busy
        )
        self.open.setEnabled(
            self.controller.open_available
            and selected
            and not busy
        )

    def _open_new_form(self) -> None:
        if self.controller.create_available:
            self._start_form_load(self.controller.new_form)

    def _open_selected_form(self) -> None:
        index = self.table.currentIndex()
        if not index.isValid() or not self.controller.open_available:
            return
        try:
            identity = self.table_model.identity_at(index.row())
        except ValueError as error:
            QMessageBox.critical(self, "TIDE Qt", str(error))
            return
        self._start_form_load(lambda: self.controller.edit_form(identity))

    def _open_row(self, row_index: int) -> None:
        if not self.controller.open_available:
            return
        try:
            identity = self.table_model.identity_at(row_index)
        except ValueError as error:
            QMessageBox.critical(self, "TIDE Qt", str(error))
            return
        self._start_form_load(lambda: self.controller.edit_form(identity))

    def _start_form_load(self, operation: Callable[[], QtEditForm]) -> None:
        if self._form_loading:
            return
        self._form_loading = True
        self._notice = None
        self._update_detail_action()
        self._update_status()
        worker = _CallWorker(operation)
        self._operation_workers.add(worker)
        worker.signals.completed.connect(self._form_ready)
        worker.signals.failed.connect(self._form_load_failed)
        self._operation_pool.start(worker)

    @Slot(object, object)
    def _form_ready(
        self,
        form: QtEditForm,
        worker: _CallWorker,
    ) -> None:
        self._operation_workers.discard(worker)
        self._form_loading = False
        self._update_detail_action()
        self._update_status()
        self._show_edit_form(form)

    def _show_edit_form(
        self,
        form: QtEditForm,
        message: str | None = None,
    ) -> None:
        navigation_enabled = (
            form.operation == "update"
            and self.table_model.row_for_identity(form.identity) is not None
        )
        dialog = TideQtEditDialog(
            self.controller,
            form,
            parent=self,
            report_directory=self.report_output_directory,
            report_opener=self.report_opener,
            navigation_enabled=navigation_enabled,
        )
        self._edit_dialogs.add(dialog)
        self._refresh_dialog_navigation(dialog)
        if message:
            dialog.message.setText(message)
        dialog.recordSaved.connect(self._record_saved)
        dialog.recordActionCompleted.connect(self._record_action_completed)
        dialog.reopenRequested.connect(self._reopen_edit_form)
        dialog.navigationRequested.connect(
            lambda direction, current=dialog: self._navigate_dialog(
                current,
                direction,
            )
        )
        dialog.finished.connect(
            lambda _result, current=dialog: self._edit_dialog_closed(current)
        )
        dialog.show()

    def _edit_dialog_closed(self, dialog: TideQtEditDialog) -> None:
        self._edit_dialogs.discard(dialog)
        self._pending_navigation.pop(dialog, None)

    def _refresh_dialog_navigation(
        self,
        dialog: TideQtEditDialog,
    ) -> None:
        row = self.table_model.row_for_identity(dialog.form.identity)
        dialog.set_navigation_state(
            can_previous=row is not None and row > 0,
            can_next=(
                row is not None
                and (
                    row + 1 < self.table_model.rowCount()
                    or self.table_model.has_more
                )
            ),
        )

    def _refresh_open_dialog_navigation(self) -> None:
        for dialog in tuple(self._edit_dialogs):
            self._refresh_dialog_navigation(dialog)

    def _navigate_dialog(
        self,
        dialog: TideQtEditDialog,
        direction: int,
    ) -> None:
        if dialog not in self._edit_dialogs or direction not in {-1, 1}:
            return
        row = self.table_model.row_for_identity(dialog.form.identity)
        if row is None:
            self._refresh_dialog_navigation(dialog)
            dialog.message.setText(
                "This record is no longer part of the current list query."
            )
            return
        target_row = row + direction
        if 0 <= target_row < self.table_model.rowCount():
            self._start_navigation_load(
                dialog,
                self.table_model.identity_at(target_row),
            )
            return
        if direction > 0 and self.table_model.has_more:
            self._pending_navigation[dialog] = direction
            dialog.set_navigation_loading(True)
            if self.table_model.canFetchMore():
                self.table_model.fetchMore()
            return
        self._refresh_dialog_navigation(dialog)

    def _continue_pending_navigation(self) -> None:
        for dialog, direction in tuple(self._pending_navigation.items()):
            if dialog not in self._edit_dialogs:
                self._pending_navigation.pop(dialog, None)
                continue
            row = self.table_model.row_for_identity(dialog.form.identity)
            if row is None:
                self._pending_navigation.pop(dialog, None)
                dialog.set_navigation_loading(False)
                self._refresh_dialog_navigation(dialog)
                dialog.message.setText(
                    "This record is no longer part of the current list query."
                )
                continue
            target_row = row + direction
            if 0 <= target_row < self.table_model.rowCount():
                self._pending_navigation.pop(dialog, None)
                self._start_navigation_load(
                    dialog,
                    self.table_model.identity_at(target_row),
                )
                continue
            if direction > 0 and self.table_model.has_more:
                if self.table_model.canFetchMore():
                    self.table_model.fetchMore()
                continue
            self._pending_navigation.pop(dialog, None)
            dialog.set_navigation_loading(False)
            self._refresh_dialog_navigation(dialog)
            dialog.message.setText(
                "There is no next record in the current list."
            )

    def _start_navigation_load(
        self,
        dialog: TideQtEditDialog,
        identity: Any,
    ) -> None:
        dialog.set_navigation_loading(True)
        worker = _CallWorker(lambda: self.controller.edit_form(identity))
        self._operation_workers.add(worker)
        self._navigation_workers[worker] = (dialog, identity)
        worker.signals.completed.connect(self._navigation_form_ready)
        worker.signals.failed.connect(self._navigation_form_failed)
        self._operation_pool.start(worker)

    @Slot(object, object)
    def _navigation_form_ready(
        self,
        form: QtEditForm,
        worker: _CallWorker,
    ) -> None:
        source, target_identity = self._navigation_workers.pop(worker)
        self._operation_workers.discard(worker)
        if source not in self._edit_dialogs:
            return
        try:
            source.replace_form(form)
        except ValueError as error:
            source.set_navigation_loading(False)
            self._refresh_dialog_navigation(source)
            source.message.setText(
                f"Unable to display adjacent record: {error}"
            )
            return
        source.set_navigation_loading(False)
        target_row = self.table_model.row_for_identity(target_identity)
        if target_row is not None:
            self.table.selectRow(target_row)
        self._refresh_dialog_navigation(source)

    @Slot(object, object)
    def _navigation_form_failed(
        self,
        error: Exception,
        worker: _CallWorker,
    ) -> None:
        source, _target_identity = self._navigation_workers.pop(worker)
        self._operation_workers.discard(worker)
        if source not in self._edit_dialogs:
            return
        source.set_navigation_loading(False)
        self._refresh_dialog_navigation(source)
        source.message.setText(f"Unable to open adjacent record: {error}")

    @Slot(object, str)
    def _reopen_edit_form(
        self,
        form: QtEditForm,
        message: str,
    ) -> None:
        QTimer.singleShot(
            0,
            lambda current=form, notice=message: self._show_edit_form(
                current,
                notice,
            ),
        )

    @Slot(object, object)
    def _form_load_failed(
        self,
        error: Exception,
        worker: _CallWorker,
    ) -> None:
        self._operation_workers.discard(worker)
        self._form_loading = False
        self._update_detail_action()
        self._update_status()
        QMessageBox.critical(
            self,
            "TIDE Qt",
            f"Unable to open record form: {error}",
        )

    @Slot(object)
    def _record_saved(self, _stored: Mapping[str, Any]) -> None:
        self._notice = "Record saved"
        self.table_model.reload()

    @Slot(str, object)
    def _record_action_completed(
        self,
        label: str,
        _stored: Mapping[str, Any],
    ) -> None:
        self._notice = f"{label} completed"
        self.table_model.reload()

    def _open_selected_detail(self) -> None:
        index = self.table.currentIndex()
        if index.isValid():
            self._open_detail(index.row())

    def _open_detail(self, row_index: int) -> None:
        try:
            identity = self.table_model.identity_at(row_index)
            detail = self.controller.load_detail(identity)
        except (TideRuntimeError, ValueError) as error:
            QMessageBox.critical(
                self,
                "TIDE Qt",
                f"Unable to load record detail: {error}",
            )
            return
        dialog = TideQtDetailDialog(
            self.controller.model.name,
            detail,
            parent=self,
        )
        self._detail_dialogs.add(dialog)
        dialog.finished.connect(
            lambda _result, current=dialog: self._detail_dialogs.discard(current)
        )
        dialog.show()

    def _loading_changed(self, loading: bool) -> None:
        self.refresh.setEnabled(not loading)
        self._update_status()

    def _batch_loaded(self, _count: int, _has_more: bool) -> None:
        if not self._column_widths_initialized and self.isVisible():
            self._initialize_column_widths()
        self._update_status()
        self._refresh_open_dialog_navigation()
        self._continue_pending_navigation()
        self._prefetch_if_near_end()

    def _load_failed(self, message: str) -> None:
        self._update_status()
        if self._pending_navigation:
            pending = tuple(self._pending_navigation)
            self._pending_navigation.clear()
            for dialog in pending:
                if dialog not in self._edit_dialogs:
                    continue
                dialog.set_navigation_loading(False)
                self._refresh_dialog_navigation(dialog)
                dialog.message.setText(
                    f"Unable to load the next list records: {message}"
                )
            return
        QMessageBox.critical(self, "TIDE Qt", f"Unable to load records: {message}")

    def _queue_search(self, _text: str) -> None:
        self._search_timer.start()

    def _apply_query_controls(self, *_args: Any) -> None:
        self._search_timer.stop()
        current = self.table_model.query
        self.table_model.set_query(
            QtBrowseQuery(
                search_text=self.search.text(),
                filter_name=self.named_filter.currentData(),
                sort_field=current.sort_field,
                sort_descending=current.sort_descending,
            )
        )

    def _sort_by_section(self, section: int) -> None:
        if section < 0 or section >= len(self.controller.columns):
            return
        field_name = self.controller.columns[section].name
        if field_name not in self.controller.sortable_fields:
            return
        current = self.table_model.query
        descending = (
            not current.sort_descending
            if current.sort_field == field_name
            else False
        )
        self.table.horizontalHeader().setSortIndicator(
            section,
            (
                Qt.SortOrder.DescendingOrder
                if descending
                else Qt.SortOrder.AscendingOrder
            ),
        )
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table_model.set_query(
            QtBrowseQuery(
                search_text=current.search_text,
                filter_name=current.filter_name,
                sort_field=field_name,
                sort_descending=descending,
            )
        )

    def _clear_query(self) -> None:
        self._search_timer.stop()
        search_blocker = QSignalBlocker(self.search)
        filter_blocker = QSignalBlocker(self.named_filter)
        self.search.clear()
        self.named_filter.setCurrentIndex(0)
        del search_blocker, filter_blocker
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table_model.set_query(QtBrowseQuery())

    def _update_status(self) -> None:
        count = self.table_model.rowCount()
        noun = "record" if count == 1 else "records"
        if self.table_model.loading:
            state = "Loading more records…" if count else "Loading records…"
        elif self.table_model.load_error:
            state = "Loading failed · Refresh to retry"
        elif self.table_model.has_more:
            state = "Scroll for more"
        else:
            state = "All available records loaded"
        summary = self.controller.query_summary(self.table_model.query)
        query_text = f"  ·  {summary}" if summary else ""
        if self._form_loading:
            state = "Loading record form…"
        notice = f"{self._notice}  ·  " if self._notice else ""
        self.status.setText(
            f"{notice}{count} {noun} loaded  ·  {state}  ·  "
            f"{self.source_label}{query_text}"
        )

    def _prefetch_if_near_end(self, *_args: Any) -> None:
        scrollbar = self.table.verticalScrollBar()
        if (
            scrollbar.maximum() - scrollbar.value() <= 2
            and self.table_model.canFetchMore()
        ):
            self.table_model.fetchMore()


def run_qt_application(
    model: ApplicationModel,
    client: BrowseApiClient,
    session: TideSessionInfo,
    *,
    view_name: str | None = None,
    page_size: int | None = None,
    source_label: str = "remote API",
    report_output_directory: str | Path | None = None,
) -> int:
    """Run the first remote Qt renderer and return Qt's process result."""

    application = QApplication.instance() or QApplication([model.name])
    application.setApplicationName(model.name)
    controller = QtBrowseController(
        model,
        client,
        session,
        view_name=view_name,
        page_size=page_size,
    )
    window = TideQtWindow(
        controller,
        source_label=source_label,
        report_output_directory=report_output_directory,
    )
    window.show()
    result = int(application.exec())
    window.wait_for_done()
    return result


def _qt_alignment(value: str) -> Any:
    horizontal = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }[value]
    return horizontal | Qt.AlignmentFlag.AlignVCenter


def _open_local_report(path: Path) -> bool:
    """Ask the operating system to open one generated temporary report."""

    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))


def _configure_interactive_header(table: QTableView | QTableWidget) -> None:
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setMinimumSectionSize(56)
    header.setStretchLastSection(False)


def _fit_interactive_columns(
    table: QTableView | QTableWidget,
    columns: tuple[QtBrowseColumn, ...],
) -> None:
    _fit_content_columns(table)
    column_count = _table_column_count(table)

    left_aligned = tuple(
        index for index, column in enumerate(columns) if column.alignment == "left"
    )
    if not left_aligned:
        return
    flexible = max(left_aligned, key=table.columnWidth)
    used = sum(table.columnWidth(index) for index in range(column_count))
    available = table.viewport().width()
    extra = max(available - used - 2, 0)
    flexible_limit = max(280, int(available * 0.55))
    table.setColumnWidth(
        flexible,
        min(table.columnWidth(flexible) + extra, flexible_limit),
    )


def _fit_content_columns(table: QTableView | QTableWidget) -> None:
    table.resizeColumnsToContents()
    for index in range(_table_column_count(table)):
        fitted = table.columnWidth(index)
        table.setColumnWidth(index, min(max(fitted, 72), 360))


def _table_column_count(table: QTableView | QTableWidget) -> int:
    model = table.model()
    return 0 if model is None else model.columnCount()


def _column_layout_key(controller: QtBrowseController) -> str:
    parts = (
        controller.model.name,
        controller.view.name,
        controller.session.principal,
    )
    encoded = "/".join(quote(part, safe="") for part in parts)
    return f"browse-column-layouts/{encoded}"


def _known_column_order(
    configured: list[Any],
    current: tuple[str, ...],
) -> tuple[str, ...]:
    known: list[str] = []
    for name in configured:
        if isinstance(name, str) and name in current and name not in known:
            known.append(name)
    known.extend(name for name in current if name not in known)
    return tuple(known)


def _known_column_widths(
    configured: dict[Any, Any],
    current: tuple[str, ...],
) -> dict[str, int]:
    return {
        name: value
        for name, value in configured.items()
        if name in current and isinstance(value, int) and not isinstance(value, bool)
    }


def _apply_column_order(
    header: QHeaderView,
    desired: tuple[str, ...],
    current: tuple[str, ...],
) -> None:
    logical_by_name = {name: index for index, name in enumerate(current)}
    for target_visual, field_name in enumerate(desired):
        logical_index = logical_by_name[field_name]
        current_visual = header.visualIndex(logical_index)
        if current_visual != target_visual:
            header.moveSection(current_visual, target_visual)


def _edit_text(field: QtEditField) -> str:
    if field.field_type == "reference":
        return field.reference_display
    return _value_text(field, field.value)


def _value_text(field: QtEditField, value: Any) -> str:
    if value is PROTECTED:
        return "Protected"
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def _field_validator(
    field: QtEditField,
    parent: QObject,
) -> QRegularExpressionValidator | None:
    pattern: str | None = None
    if field.regex is not None:
        pattern = rf"^(?:{field.regex})$"
    elif field.field_type == "integer":
        pattern = r"^-?\d*$"
    elif field.field_type == "decimal":
        pattern = _decimal_input_pattern(field)
    if pattern is None:
        return None
    expression = QRegularExpression(pattern)
    if not expression.isValid():
        return None
    return QRegularExpressionValidator(expression, parent)


def _decimal_input_pattern(field: QtEditField) -> str:
    mask = field.numeric_mask
    match = re.fullmatch(r"0(?:([.,])(0+))?", mask or "")
    scale = (
        len(match.group(2))
        if match is not None
        else int(field.scale or 0)
    )
    integer_digits = (
        max(1, field.precision - int(field.scale or 0))
        if field.precision is not None
        else None
    )
    integer = rf"\d{{0,{integer_digits}}}" if integer_digits else r"\d*"
    if scale <= 0:
        return rf"^-?{integer}$"
    separator = re.escape(match.group(1)) if match and match.group(1) else r"[.,]"
    return rf"^-?{integer}(?:{separator}\d{{0,{scale}}})?$"


def _normalize_numeric_editor(
    editor: QLineEdit,
    field: QtEditField,
) -> None:
    raw = editor.text().strip()
    match = re.fullmatch(r"0(?:([.,])(0+))?", field.numeric_mask or "")
    if not raw or match is None:
        return
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return
    places = len(match.group(2) or "")
    formatted = f"{value:.{places}f}"
    if match.group(1) == ",":
        formatted = formatted.replace(".", ",")
    editor.setText(formatted)


def _editor_value(
    field: QtEditField,
    editor: QWidget,
    *,
    enforce_required: bool = True,
) -> Any:
    if isinstance(editor, TideQtReferenceEditor):
        value = editor.identity
        if value is None and field.required and enforce_required:
            raise ValueError(f"{field.label} is required")
        return value
    if isinstance(editor, QCheckBox):
        return editor.isChecked()
    if isinstance(editor, QComboBox):
        value = editor.currentData()
        if value is None and field.required and enforce_required:
            raise ValueError(f"{field.label} is required")
        return value
    if not isinstance(editor, QLineEdit):
        raise ValueError(f"{field.label} uses an unsupported editor")
    raw = editor.text().strip()
    if not raw:
        if field.required and enforce_required:
            raise ValueError(f"{field.label} is required")
        return None
    if not editor.hasAcceptableInput():
        raise ValueError(f"{field.label} has an invalid format")
    try:
        if field.field_type == "integer":
            value: Any = int(raw)
        elif field.field_type == "decimal":
            value = Decimal(raw.replace(",", "."))
        elif field.field_type == "date":
            value = _parse_edit_date(raw)
        elif field.field_type == "datetime":
            value = datetime.fromisoformat(raw)
        else:
            value = raw
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field.label} has an invalid value") from error
    if field.minimum is not None and value < field.minimum:
        raise ValueError(f"{field.label} must be at least {field.minimum}")
    if field.maximum is not None and value > field.maximum:
        raise ValueError(f"{field.label} must be at most {field.maximum}")
    return value


def _set_editor_value(
    field: QtEditField,
    editor: QWidget,
    value: Any,
    *,
    reference_display: str | None = None,
) -> None:
    if isinstance(editor, TideQtReferenceEditor):
        editor.set_selection(value, reference_display or "")
        return
    if isinstance(editor, QCheckBox):
        editor.setChecked(bool(value))
        return
    if isinstance(editor, QComboBox):
        index = editor.findData(value)
        editor.setCurrentIndex(max(index, 0))
        return
    if isinstance(editor, QLineEdit):
        editor.setText(
            reference_display
            if field.field_type == "reference"
            and reference_display is not None
            else _value_text(field, value)
        )


def _form_structure(form: QtEditForm) -> tuple[Any, ...]:
    return (
        form.entity,
        tuple(
            (
                group.label,
                tuple(tuple(field.name for field in row) for row in group.rows),
            )
            for group in form.groups
        ),
        tuple(
            _collection_structure(collection)
            for collection in form.collections
        ),
        tuple(action.name for action in form.actions),
    )


def _collection_structure(collection: QtEditCollection) -> tuple[Any, ...]:
    return (
        collection.name,
        collection.entity,
        tuple(column.name for column in collection.columns),
        tuple(
            (
                group.label,
                tuple(tuple(field.name for field in row) for row in group.rows),
            )
            for group in collection.groups
        ),
        collection.actions,
    )


def _parse_edit_date(value: str) -> date:
    for parser in (
        date.fromisoformat,
        lambda candidate: datetime.strptime(candidate, "%d.%m.%Y").date(),
        lambda candidate: datetime.strptime(candidate, "%d/%m/%Y").date(),
    ):
        try:
            return parser(value)
        except ValueError:
            continue
    raise ValueError("invalid date")
