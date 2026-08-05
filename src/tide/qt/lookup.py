"""Choosing a related record, and creating one without losing the draft."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from PySide6.QtCore import (
    QThreadPool,
    QTimer,
    Slot,
)
from PySide6.QtGui import (
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tide.presentation import field_label

from .columns import (
    configure_interactive_header,
    fit_interactive_columns,
    qt_alignment,
)
from .presenter import (
    QtBrowseController,
    QtLookupRecord,
    QtLookupSpec,
)
from .workers import CallWorker

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from .form import TideQtEditDialog


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
        self._workers: set[CallWorker] = set()
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
                field_label(
                    controller.model.entity(spec.target_entity).field(name)
                )
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
        configure_interactive_header(self.table)
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
        worker = CallWorker(
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
        worker: CallWorker,
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
                    qt_alignment(self.spec.columns[column_index].alignment)
                )
                self.table.setItem(row_index, column_index, item)
        if records:
            self.table.selectRow(0)
            fit_interactive_columns(self.table, self.spec.columns)
        noun = "match" if len(records) == 1 else "matches"
        suffix = f" for {search_text!r}" if search_text else ""
        create_hint = "  ·  Ctrl+N creates" if self.spec.create_available else ""
        self.status.setText(
            f"{len(records)} {noun}{suffix}  ·  Enter selects{create_hint}"
        )
        self._selection_changed()

    @Slot(object, object)
    def _search_failed(self, error: Exception, worker: CallWorker) -> None:
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
        # Imported here rather than at the top because the recursion is real:
        # a lookup can create the record it is looking for, and that record's
        # editor can open a lookup of its own. `form` imports this module
        # normally; only this one direction has to wait until it is used.
        from .form import TideQtEditDialog

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
