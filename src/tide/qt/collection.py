"""An inline collection edited as a local draft inside its parent form."""

from __future__ import annotations

from copy import deepcopy
from functools import partial
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
from .editors import (
    build_field_editor,
    collection_structure,
    configure_field_editor,
    configure_field_label,
    editor_value,
    set_editor_value,
)
from .presenter import (
    QtEditCollection,
    QtEditField,
    QtLookupSelection,
)

if TYPE_CHECKING:  # pragma: no cover - the parent is only an annotation
    from .form import TideQtEditDialog


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
        configure_interactive_header(self.table)
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
                    editor = build_field_editor(
                        field,
                        event_filter=dialog,
                        lookup_handler=partial(self._open_lookup, field),
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
                    placed = positioned.get((row_index, column_index))
                    if placed is not None:
                        focus_order.append(placed)
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

        if collection_structure(collection) != collection_structure(
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
            configure_field_editor(field, editor)
            configure_field_label(
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
            draft[name] = editor_value(
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
                configure_field_editor(self._fields[name], editor)
            self._update_actions()

    def apply_lookup_selection(self, selection: QtLookupSelection) -> None:
        for name, value in selection.values.items():
            editor = self.editors.get(name)
            field = self._fields.get(name)
            if editor is None or field is None or not field.editable:
                continue
            set_editor_value(
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
            set_editor_value(
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
                    qt_alignment(
                        self.collection.columns[column_index].alignment
                    )
                )
                self.table.setItem(row_index, column_index, item)
        if self.rows and fit_columns:
            fit_interactive_columns(self.table, self.collection.columns)
        if select is not None and 0 <= select < len(self.rows):
            self.table.selectRow(select)
            self._select_row(select)
        else:
            self._selected_row = None
            self._clear_editors()
        self._update_actions()

    def _clear_editors(self) -> None:
        for name, editor in self.editors.items():
            set_editor_value(
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
