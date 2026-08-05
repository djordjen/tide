"""Read-only record detail."""

from __future__ import annotations


from PySide6.QtGui import (
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .columns import (
    configure_interactive_header,
    fit_interactive_columns,
    qt_alignment,
)
from .contracts import (
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
                    fit_interactive_columns(table, section.columns)

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
            editor.setAlignment(qt_alignment(field.alignment))
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
        configure_interactive_header(table)
        for row_index, row in enumerate(section.rows):
            for column_index, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    qt_alignment(section.columns[column_index].alignment)
                )
                table.setItem(row_index, column_index, item)
        table.setMinimumHeight(190)
        self.collection_tables[section.name] = table
        layout.addWidget(table)
        return group
