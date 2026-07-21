"""PySide6 Qt Widgets adapter for the initial remote browse/detail prototype."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QtBrowseColumn,
    QtBrowseController,
    QtBrowsePage,
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

        self.table = QTableWidget(0, len(controller.columns))
        self.table.setHorizontalHeaderLabels(
            [column.label for column in controller.columns]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        _configure_interactive_header(self.table)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.status = QLabel()
        self.view = QPushButton("View")
        self.previous = QPushButton("Previous")
        self.refresh = QPushButton("Refresh")
        self.next = QPushButton("Next")
        close = QPushButton("Close")
        actions.addWidget(self.status, 1)
        actions.addWidget(self.view)
        actions.addWidget(self.previous)
        actions.addWidget(self.refresh)
        actions.addWidget(self.next)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.setCentralWidget(root)

        self.previous.clicked.connect(
            lambda: self._load(self.controller.previous_page)
        )
        self.refresh.clicked.connect(lambda: self._load(self.controller.refresh))
        self.next.clicked.connect(lambda: self._load(self.controller.next_page))
        self.view.clicked.connect(self._open_selected_detail)
        self.table.itemSelectionChanged.connect(self._update_detail_action)
        self.table.itemActivated.connect(
            lambda item: self._open_detail(item.row())
        )
        close.clicked.connect(self.close)
        self._load(self.controller.refresh)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._column_widths_initialized:
            self._initialize_column_widths()

    def _initialize_column_widths(self) -> None:
        """Fit once, then leave every section under direct user control."""

        _fit_interactive_columns(self.table, self.controller.columns)
        self._column_widths_initialized = True

    def _update_detail_action(self) -> None:
        self.view.setEnabled(
            self.controller.detail_available and self.table.currentRow() >= 0
        )

    def _open_selected_detail(self) -> None:
        row_index = self.table.currentRow()
        if row_index >= 0:
            self._open_detail(row_index)

    def _open_detail(self, row_index: int) -> None:
        try:
            detail = self.controller.load_detail(row_index)
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

    def _load(self, operation: Callable[[], QtBrowsePage]) -> None:
        try:
            page = operation()
        except (TideRuntimeError, ValueError) as error:
            QMessageBox.critical(self, "TIDE Qt", f"Unable to load records: {error}")
            return
        self.table.setRowCount(len(page.rows))
        for row_index, row in enumerate(page.rows):
            for column_index, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    _qt_alignment(page.columns[column_index].alignment)
                )
                self.table.setItem(row_index, column_index, item)
        self.previous.setEnabled(page.previous_available)
        self.next.setEnabled(page.next_available)
        self.table.clearSelection()
        self._update_detail_action()
        noun = "record" if len(page.rows) == 1 else "records"
        self.status.setText(
            f"Page {page.page_number}  ·  {len(page.rows)} {noun}  ·  "
            f"{self.source_label}"
        )


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
    return int(application.exec())


def _qt_alignment(value: str) -> Any:
    horizontal = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }[value]
    return horizontal | Qt.AlignmentFlag.AlignVCenter


def _configure_interactive_header(table: QTableWidget) -> None:
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setMinimumSectionSize(56)
    header.setStretchLastSection(False)


def _fit_interactive_columns(
    table: QTableWidget,
    columns: tuple[QtBrowseColumn, ...],
) -> None:
    table.resizeColumnsToContents()
    for index in range(table.columnCount()):
        fitted = table.columnWidth(index)
        table.setColumnWidth(index, min(max(fitted, 72), 360))

    left_aligned = tuple(
        index for index, column in enumerate(columns) if column.alignment == "left"
    )
    if not left_aligned:
        return
    flexible = max(left_aligned, key=table.columnWidth)
    used = sum(table.columnWidth(index) for index in range(table.columnCount()))
    available = table.viewport().width()
    extra = max(available - used - 2, 0)
    flexible_limit = max(280, int(available * 0.55))
    table.setColumnWidth(
        flexible,
        min(table.columnWidth(flexible) + extra, flexible_limit),
    )
