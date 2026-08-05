"""Three-way review of a record that changed under an open draft."""

from __future__ import annotations


from PySide6.QtCore import (
    Slot,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tide.sessions import ConflictDisposition, ConflictValueChoice

from .presenter import (
    QtBrowseController,
    QtEditConflict,
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
