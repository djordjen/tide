"""The browse grid's model, filled a page at a time from the controller."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QObject,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)

from .columns import qt_alignment
from .contracts import (
    QtBrowseBatch,
    QtBrowseQuery,
)
from .presenter import (
    QtBrowseController,
)
from .workers import BatchWorker


# The overrides below spell `QModelIndex | QPersistentModelIndex` out each
# time rather than sharing an alias. Qt passes an index in either form and the
# stubs type every overridable method that way -- but where PySide6 is not
# installed, which is the Linux CI job, both names are `Any`, and an alias
# assigned from `Any | Any` stops being usable as a type at all.
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
        self._workers: set[BatchWorker] = set()
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

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
    ) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return qt_alignment(self.columns[index.column()].alignment)
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

    def canFetchMore(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
        /,
    ) -> bool:
        return (
            not parent.isValid()
            and not self._loading
            and self._load_error is None
            and self.has_more
        )

    def fetchMore(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
        /,
    ) -> None:
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
        worker = BatchWorker(
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
        worker: BatchWorker,
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
        worker: BatchWorker,
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
