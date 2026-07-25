"""PySide6 Qt Widgets adapter for the initial remote browse/detail prototype."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QRunnable,
    QSignalBlocker,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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

from .presenter import (
    BrowseApiClient,
    QtBrowseBatch,
    QtBrowseColumn,
    QtBrowseController,
    QtBrowseQuery,
    QtDetailCollection,
    QtDetailGroup,
    QtDetailRecord,
)


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
    ) -> None:
        super().__init__()
        self.controller = controller
        self.source_label = source_label
        self._column_widths_initialized = False
        self._detail_dialogs: set[TideQtDetailDialog] = set()
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
        header.setSortIndicatorShown(False)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.status = QLabel()
        self.view = QPushButton("View")
        self.refresh = QPushButton("Refresh")
        close = QPushButton("Close")
        actions.addWidget(self.status, 1)
        actions.addWidget(self.view)
        actions.addWidget(self.refresh)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.setCentralWidget(root)

        self.refresh.clicked.connect(self.table_model.reload)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._apply_query_controls)
        self.search.textChanged.connect(self._queue_search)
        self.named_filter.currentIndexChanged.connect(self._apply_query_controls)
        self.clear_query.clicked.connect(self._clear_query)
        header.sectionClicked.connect(self._sort_by_section)
        self.view.clicked.connect(self._open_selected_detail)
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
        self._update_detail_action()
        self.table_model.reload()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._column_widths_initialized:
            self._initialize_column_widths()

    def _initialize_column_widths(self) -> None:
        """Fit once, then leave every section under direct user control."""

        if self.table_model.rowCount() == 0:
            return
        _fit_interactive_columns(self.table, self.controller.columns)
        self._column_widths_initialized = True

    def _update_detail_action(self, *_args: Any) -> None:
        self.view.setEnabled(
            self.controller.detail_available and self.table.currentIndex().isValid()
        )

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
        self.status.setText(
            f"{count} {noun} loaded  ·  {state}  ·  "
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
    window.table_model.wait_for_done()
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
    table.resizeColumnsToContents()
    column_count = _table_column_count(table)
    for index in range(column_count):
        fitted = table.columnWidth(index)
        table.setColumnWidth(index, min(max(fitted, 72), 360))

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


def _table_column_count(table: QTableView | QTableWidget) -> int:
    model = table.model()
    return 0 if model is None else model.columnCount()
