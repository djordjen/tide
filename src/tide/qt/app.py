"""PySide6 Qt Widgets adapter for the initial remote browse/detail prototype."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Callable, Mapping
from urllib.parse import quote

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
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QCloseEvent,
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
from tide.runtime import TideRuntimeError
from tide.security import PROTECTED

from .presenter import (
    BrowseApiClient,
    QtBrowseBatch,
    QtBrowseColumn,
    QtBrowseController,
    QtBrowseQuery,
    QtDetailCollection,
    QtDetailGroup,
    QtDetailRecord,
    QtEditField,
    QtEditForm,
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


class TideQtEditDialog(QDialog):
    """Metadata-driven create/update dialog for one flat entity."""

    recordSaved = Signal(object)

    def __init__(
        self,
        controller: QtBrowseController,
        form: QtEditForm,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.form = form
        self.editors: dict[str, QWidget] = {}
        self._fields = {field.name: field for field in form.fields}
        self._workers: set[_CallWorker] = set()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._saving = False
        self.setWindowTitle(f"{controller.model.name} — {form.title}")
        self.resize(760, 360)

        layout = QVBoxLayout(self)
        heading = QLabel(form.title)
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)
        focus_order: list[QWidget] = []
        for group in form.groups:
            container = QGroupBox(group.label)
            grid = QGridLayout(container)
            positioned: dict[tuple[int, int], QWidget] = {}
            for row_index, row in enumerate(group.rows):
                for column_index, field in enumerate(row):
                    offset = column_index * 2
                    label = QLabel(
                        f"{field.label} *" if field.required else field.label
                    )
                    editor = self._field_editor(field)
                    self.editors[field.name] = editor
                    positioned[row_index, column_index] = editor
                    if not field.editable:
                        label.setStyleSheet("color: palette(mid); font-style: italic;")
                    grid.addWidget(label, row_index, offset)
                    grid.addWidget(editor, row_index, offset + 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 1)
            layout.addWidget(container)
            column_count = max((len(row) for row in group.rows), default=0)
            for column_index in range(column_count):
                for row_index in range(len(group.rows)):
                    editor = positioned.get((row_index, column_index))
                    if (
                        editor is not None
                        and editor.isEnabled()
                        and editor.focusPolicy() != Qt.FocusPolicy.NoFocus
                    ):
                        focus_order.append(editor)

        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

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
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.buttons)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self._save)

        if focus_order:
            focus_order[0].setFocus()

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
        return self._thread_pool.waitForDone(milliseconds)

    def _field_editor(self, field: QtEditField) -> QWidget:
        if field.field_type == "boolean" and field.editable:
            editor = QCheckBox()
            editor.setChecked(bool(field.value))
            editor.setObjectName(f"edit-field-{field.name}")
            editor.installEventFilter(self)
            return editor
        if field.field_type == "choice" and field.editable:
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
            return editor

        editor = QLineEdit(_edit_text(field))
        editor.setObjectName(f"edit-field-{field.name}")
        editor.setReadOnly(not field.editable)
        if not field.editable:
            editor.setStyleSheet(
                "background: palette(alternate-base); color: palette(mid);"
            )
            editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            return editor
        if field.max_length is not None:
            editor.setMaxLength(field.max_length)
        validator = _field_validator(field, editor)
        if validator is not None:
            editor.setValidator(validator)
        if field.field_type == "date":
            editor.setPlaceholderText("DD.MM.YYYY")
        if field.numeric_mask is not None:
            editor.editingFinished.connect(
                lambda current=editor, item=field: _normalize_numeric_editor(
                    current,
                    item,
                )
            )
        editor.installEventFilter(self)
        return editor

    def _save(self) -> None:
        if self._saving:
            return
        try:
            values = self._editor_values()
        except ValueError as error:
            self.message.setText(str(error))
            return
        self._set_saving(True)
        worker = _CallWorker(
            lambda: self.controller.save_form(self.form, values)
        )
        self._workers.add(worker)
        worker.signals.completed.connect(self._save_completed)
        worker.signals.failed.connect(self._save_failed)
        self._thread_pool.start(worker)

    def _editor_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name, editor in self.editors.items():
            field = self._fields[field_name]
            if not field.editable:
                continue
            values[field_name] = _editor_value(field, editor)
        return values

    @Slot(object, object)
    def _save_completed(
        self,
        stored: Mapping[str, Any],
        worker: _CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        self.recordSaved.emit(dict(stored))
        super().accept()

    @Slot(object, object)
    def _save_failed(self, error: Exception, worker: _CallWorker) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        self.message.setText(f"Save failed: {error}")

    def _set_saving(self, saving: bool) -> None:
        self._saving = saving
        for field_name, editor in self.editors.items():
            if self._fields[field_name].editable:
                editor.setEnabled(not saving)
        self.save_button.setEnabled(not saving)
        self.cancel_button.setEnabled(not saving)
        self.save_button.setText("Saving…" if saving else "Save")
        if saving:
            self.message.setText("Saving through the TIDE API…")


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
    """Read-only Qt browse that delegates all data access to TideApiClient."""

    def __init__(
        self,
        controller: QtBrowseController,
        *,
        source_label: str,
        layout_settings: QSettings | None = None,
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
        self._operation_pool = QThreadPool(self)
        self._operation_pool.setMaxThreadCount(1)
        self._form_loading = False
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
        self.edit = QPushButton("Edit")
        self.edit.setObjectName("edit-record")
        self.view = QPushButton("View")
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
        actions.addWidget(self.edit)
        actions.addWidget(self.view)
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
        self.edit.clicked.connect(self._open_selected_form)
        self.view.clicked.connect(self._open_selected_detail)
        self.best_fit.clicked.connect(self._best_fit_all_columns)
        self.reset_layout.clicked.connect(self._reset_column_layout)
        self.table.selectionModel().selectionChanged.connect(
            self._update_detail_action
        )
        self.table.activated.connect(lambda index: self._open_detail(index.row()))
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
        self.new.setEnabled(
            self.controller.create_available and not self._form_loading
        )
        self.edit.setEnabled(
            self.controller.update_available
            and selected
            and not self._form_loading
        )
        self.view.setEnabled(
            self.controller.detail_available
            and selected
            and not self._form_loading
        )

    def _open_new_form(self) -> None:
        if self.controller.create_available:
            self._start_form_load(self.controller.new_form)

    def _open_selected_form(self) -> None:
        index = self.table.currentIndex()
        if not index.isValid() or not self.controller.update_available:
            return
        try:
            identity = self.table_model.identity_at(index.row())
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
        dialog = TideQtEditDialog(self.controller, form, parent=self)
        self._edit_dialogs.add(dialog)
        dialog.recordSaved.connect(self._record_saved)
        dialog.finished.connect(
            lambda _result, current=dialog: self._edit_dialogs.discard(current)
        )
        dialog.show()

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
        self._prefetch_if_near_end()

    def _load_failed(self, message: str) -> None:
        self._update_status()
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
    window = TideQtWindow(controller, source_label=source_label)
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
    value = field.value
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


def _editor_value(field: QtEditField, editor: QWidget) -> Any:
    if isinstance(editor, QCheckBox):
        return editor.isChecked()
    if isinstance(editor, QComboBox):
        value = editor.currentData()
        if value is None and field.required:
            raise ValueError(f"{field.label} is required")
        return value
    if not isinstance(editor, QLineEdit):
        raise ValueError(f"{field.label} uses an unsupported editor")
    raw = editor.text().strip()
    if not raw:
        if field.required:
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
