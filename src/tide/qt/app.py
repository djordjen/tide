"""PySide6 Qt Widgets adapter for the initial remote browse prototype."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tide.api.contracts import TideSessionInfo
from tide.compiler.normalized import ApplicationModel
from tide.runtime import TideRuntimeError

from .presenter import BrowseApiClient, QtBrowseController, QtBrowsePage


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
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.status = QLabel()
        self.previous = QPushButton("Previous")
        self.refresh = QPushButton("Refresh")
        self.next = QPushButton("Next")
        close = QPushButton("Close")
        actions.addWidget(self.status, 1)
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
        close.clicked.connect(self.close)
        self._load(self.controller.refresh)

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
