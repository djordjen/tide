"""Secured report preview and its controlled exports."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QThreadPool,
    QUrl,
)
from PySide6.QtGui import (
    QDesktopServices,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tide.reporting import (
    ReportDocument,
    write_csv,
    write_html,
    write_pdf,
)

from .columns import (
    configure_interactive_header,
    fit_interactive_columns,
    qt_alignment,
)
from .contracts import (
    QtBrowseColumn,
)
from .workers import CallWorker


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
        self._workers: set[CallWorker] = set()
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
                editor.setAlignment(qt_alignment(value.alignment))
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
        configure_interactive_header(self.detail)
        for row_index, cells in enumerate(document.detail.rows):
            for column_index, cell in enumerate(cells):
                item = QTableWidgetItem(cell.text)
                item.setTextAlignment(qt_alignment(cell.alignment))
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
                result.setAlignment(qt_alignment(value.alignment))
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
        fit_interactive_columns(
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
        worker = CallWorker(lambda: writer(self.document, path))
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
        worker: CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_exporting(False, f"{label} exported to {path}")

    def _export_failed(
        self,
        label: str,
        error: Exception,
        worker: CallWorker,
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


def open_local_report(path: Path) -> bool:
    """Ask the operating system to open one generated temporary report."""

    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
