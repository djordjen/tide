"""Widgets for one field, and the value round trip between them and the compiled model."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from PySide6.QtCore import (
    QObject,
    QRegularExpression,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QKeySequence,
    QRegularExpressionValidator,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from tide.security import PROTECTED

from .presenter import (
    QtEditCollection,
    QtEditField,
    QtEditForm,
)


class TideQtReferenceEditor(QWidget):
    """Compact reference editor that opens a secured multi-column lookup."""

    lookupRequested = Signal()
    selectionChanged = Signal()

    def __init__(self, field: QtEditField, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.field = field
        self._identity = field.value
        self.setObjectName(f"edit-field-{field.name}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.display = QLineEdit(field.reference_display)
        self.display.setObjectName(f"reference-display-{field.name}")
        self.display.setReadOnly(True)
        self.select_button = QPushButton("Select…")
        self.select_button.setObjectName(f"lookup-{field.name}")
        self.select_button.clicked.connect(self.lookupRequested)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName(f"clear-reference-{field.name}")
        self.clear_button.setVisible(not field.required)
        self.clear_button.clicked.connect(self.clear)
        layout.addWidget(self.display, 1)
        layout.addWidget(self.select_button)
        layout.addWidget(self.clear_button)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self.select_button)
        self._lookup_shortcut = QShortcut(QKeySequence("F4"), self)
        self._lookup_shortcut.activated.connect(self.lookupRequested)

    @property
    def identity(self) -> Any:
        return self._identity

    def set_selection(self, identity: Any, display: str) -> None:
        self._identity = identity
        self.display.setText(display)
        self.selectionChanged.emit()

    def clear(self) -> None:
        self.set_selection(None, "")


def edit_text(field: QtEditField) -> str:
    if field.field_type == "reference":
        return field.reference_display
    return _value_text(field, field.value)


def _value_text(field: QtEditField, value: Any) -> str:
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


def field_validator(
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


def normalize_numeric_editor(
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


def editor_value(
    field: QtEditField,
    editor: QWidget,
    *,
    enforce_required: bool = True,
) -> Any:
    if isinstance(editor, TideQtReferenceEditor):
        value = editor.identity
        if value is None and field.required and enforce_required:
            raise ValueError(f"{field.label} is required")
        return value
    if isinstance(editor, QCheckBox):
        return editor.isChecked()
    if isinstance(editor, QComboBox):
        value = editor.currentData()
        if value is None and field.required and enforce_required:
            raise ValueError(f"{field.label} is required")
        return value
    if not isinstance(editor, QLineEdit):
        raise ValueError(f"{field.label} uses an unsupported editor")
    raw = editor.text().strip()
    if not raw:
        if field.required and enforce_required:
            raise ValueError(f"{field.label} is required")
        return None
    if not editor.hasAcceptableInput():
        raise ValueError(f"{field.label} has an invalid format")
    try:
        if field.field_type == "integer":
            value = int(raw)
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


def set_editor_value(
    field: QtEditField,
    editor: QWidget,
    value: Any,
    *,
    reference_display: str | None = None,
) -> None:
    if isinstance(editor, TideQtReferenceEditor):
        editor.set_selection(value, reference_display or "")
        return
    if isinstance(editor, QCheckBox):
        editor.setChecked(bool(value))
        return
    if isinstance(editor, QComboBox):
        index = editor.findData(value)
        editor.setCurrentIndex(max(index, 0))
        return
    if isinstance(editor, QLineEdit):
        editor.setText(
            reference_display
            if field.field_type == "reference"
            and reference_display is not None
            else _value_text(field, value)
        )


def form_structure(form: QtEditForm) -> tuple[Any, ...]:
    return (
        form.entity,
        tuple(
            (
                group.label,
                tuple(tuple(field.name for field in row) for row in group.rows),
            )
            for group in form.groups
        ),
        tuple(
            collection_structure(collection)
            for collection in form.collections
        ),
        tuple(action.name for action in form.actions),
    )


def collection_structure(collection: QtEditCollection) -> tuple[Any, ...]:
    return (
        collection.name,
        collection.entity,
        tuple(column.name for column in collection.columns),
        tuple(
            (
                group.label,
                tuple(tuple(field.name for field in row) for row in group.rows),
            )
            for group in collection.groups
        ),
        collection.actions,
    )


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
