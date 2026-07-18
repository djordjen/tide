"""Metadata-driven transactional record editing for the Textual adapter."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping
from uuid import uuid4

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import Screen
from textual.validation import Regex
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from tide.compiler.expressions import evaluate_expression
from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
from tide.data import QuerySpec
from tide.runtime import RequestContext, TideRuntimeError, ValidationFailed
from tide.security import PROTECTED
from tide.tui.table import table_cell, table_label
from tide.services import ActionService, RecordsService
from tide.sessions import RecordConflict, RecordSession, SessionState, compare_record_conflict
from tide.tui.conflict import (
    ConflictChoice,
    ConflictReviewResult,
    ConflictReviewScreen,
)
from tide.tui.lookup import LookupField, LookupScreen


class DateInput(Input):
    """Compact local-date input with one-day keyboard stepping."""

    BINDINGS = [
        Binding("plus", "next_day", show=False, priority=True),
        Binding("minus", "previous_day", show=False, priority=True),
    ]

    def action_next_day(self) -> None:
        self._step(1)

    def action_previous_day(self) -> None:
        self._step(-1)

    def _step(self, days: int) -> None:
        current = _parse_date(self.value)
        if current is None:
            self.app.notify(
                "Enter a valid date before using + or -.",
                severity="warning",
            )
            return
        self.value = _format_date(current + timedelta(days=days))
        self.cursor_position = len(self.value)


class NumericMaskedInput(Input):
    """Numeric input that admits partial typing states and limits decimal places."""

    def __init__(
        self,
        *,
        value: str,
        mask: str,
        precision: int | None,
        scale: int | None,
        id: str,
        classes: str,
    ) -> None:
        match = re.fullmatch(r"0(?:([.,])(0+))?", mask)
        if match is None:  # Compiler validation makes this defensive only.
            raise ValueError(f"invalid numeric edit mask {mask!r}")
        self.decimal_separator = match.group(1)
        self.decimal_places = len(match.group(2) or "")
        integer_digits = (
            max(1, precision - int(scale or 0)) if precision is not None else None
        )
        integer_part = rf"\d{{0,{integer_digits}}}" if integer_digits else r"\d*"
        fractional_part = (
            rf"(?:{re.escape(self.decimal_separator)}\d{{0,{self.decimal_places}}})?"
            if self.decimal_separator is not None
            else ""
        )
        super().__init__(
            value=value,
            restrict=rf"-?{integer_part}{fractional_part}",
            max_length=(
                1
                + integer_digits
                + (1 + self.decimal_places if self.decimal_separator else 0)
                if integer_digits is not None
                else 0
            ),
            id=id,
            classes=classes,
        )

    def on_blur(self) -> None:
        raw = self.value.strip()
        if raw in {"", "-", ".", ",", "-.", "-,"}:
            return
        normalized = raw.replace(self.decimal_separator or ".", ".")
        try:
            value = Decimal(normalized)
        except InvalidOperation:
            return
        formatted = f"{value:.{self.decimal_places}f}"
        if self.decimal_separator == ",":
            formatted = formatted.replace(".", ",")
        self.value = formatted
        self.cursor_position = len(formatted)


class FormSelect(Select[Any]):
    """Select that reserves Enter for form traversal while collapsed."""

    BINDINGS = [
        Binding("enter", "focus_next_control", show=False),
        Binding("down,space,up", "show_overlay", "Show menu", show=False),
    ]

    def action_focus_next_control(self) -> None:
        self.screen.focus_next()


Editor = Input | Select[Any] | LookupField


@dataclass(frozen=True, slots=True)
class ReopenRecordEdit:
    """Ask the owning application to rebuild a form from a fresh session."""

    session: RecordSession
    message: str


class RecordEditScreen(Screen[Any]):
    """Edit one record and its first metadata-defined inline collection."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    RecordEditScreen {
        layout: vertical;
        background: $surface;
    }

    #form-context {
        height: 2;
        padding: 0 2;
        color: $text-muted;
        content-align: left middle;
    }

    #form-body {
        height: 1fr;
        padding: 0 2;
        overflow-y: auto;
    }

    #form-tabs, #form-tabs TabPane {
        height: 1fr;
    }

    #form-tabs TabPane {
        padding: 0;
    }

    .section-title {
        height: 2;
        margin-top: 1;
        color: $accent;
        text-style: bold;
        content-align: left middle;
    }

    #record-fields, #line-fields {
        height: auto;
    }

    .field-column {
        grid-size: 2;
        grid-columns: 16 1fr;
        grid-gutter: 0 1;
        width: 1fr;
        height: auto;
    }

    .field-label {
        height: 1;
        content-align: right middle;
        color: $text;
    }

    .readonly-label {
        color: $text-muted;
        text-style: italic;
    }

    .editable-value {
        height: 1;
        border: none;
        border-left: thick $primary;
        padding: 0 1;
        background: $surface-lighten-2;
        color: $text;
    }

    .editable-value:focus {
        background: $primary;
        color: $text;
    }

    Select.editable-value > SelectCurrent {
        border: none;
        background: $surface-lighten-2;
    }

    Select.editable-value:focus > SelectCurrent {
        border: none;
        background: $primary;
    }

    LookupField.editable-value {
        content-align: left middle;
    }

    .readonly-value {
        height: 1;
        padding: 0 1;
        border: none;
        border-left: thick $surface-lighten-2;
        background: $surface-lighten-1;
        color: $text-muted;
        text-style: italic;
        content-align: left middle;
    }

    #collection-records {
        height: 1fr;
        min-height: 5;
        border: round $primary;
    }

    #line-fields {
        margin-top: 1;
    }

    #form-actions {
        height: 3;
        padding: 0 2;
    }

    #line-actions, #record-actions {
        width: auto;
        height: 3;
    }

    #action-spacer {
        width: 1fr;
        height: 3;
    }

    #line-actions Button, #record-actions Button {
        min-width: 12;
    }

    #line-actions Button {
        margin-right: 1;
    }

    #record-actions Button {
        margin-left: 1;
    }

    #form-message {
        min-height: 1;
        height: auto;
        max-height: 3;
        padding: 0 2;
        color: $warning;
    }

    RecordEditScreen.compact-terminal #form-body,
    RecordEditScreen.compact-terminal #form-actions {
        padding-left: 1;
        padding-right: 1;
    }

    RecordEditScreen.compact-terminal .field-column {
        grid-columns: 12 1fr;
    }

    RecordEditScreen.compact-terminal #line-actions Button,
    RecordEditScreen.compact-terminal #record-actions Button {
        min-width: 9;
        margin-left: 0;
        margin-right: 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+p", "post", "Post"),
        Binding("ctrl+n", "add_line", "Add line"),
        Binding("ctrl+l", "apply_line", "Apply line"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        model: ApplicationModel,
        records: RecordsService,
        actions: ActionService,
        context: RequestContext,
        view: ResolvedView,
        session: RecordSession,
        *,
        select_after_save: bool = False,
    ) -> None:
        super().__init__()
        self.model = model
        self.records = records
        self.actions = actions
        self.context = context
        self.view = view
        self.session = session
        self.select_after_save = select_after_save
        self.entity = model.entity(session.entity)
        self.scalar_fields = _form_fields(view, self.entity)
        self.collection_name, self.inline_view = _collection_view(model, view)
        self.layout_tabs = _layout_tabs(view, self.entity)
        self.record_action_order = _record_action_order(view, self.entity)
        self.collection_action_order = _collection_action_order(
            view, self.collection_name
        )
        self.collection_entity = (
            model.entity(self.entity.field(self.collection_name).target_entity)
            if self.collection_name is not None
            else None
        )
        self.line_fields = (
            tuple(str(name) for name in self.inline_view.data.get("columns", ()))
            if self.inline_view is not None
            else ()
        )
        self.line_editor_columns = (
            _inline_editor_columns(
                self.inline_view,
                self.collection_entity,
                self.line_fields,
            )
            if self.inline_view is not None and self.collection_entity is not None
            else ((), ())
        )
        self.line_editor_fields = tuple(
            name for column in self.line_editor_columns for name in column
        )
        source_lines = (
            session.values.get(self.collection_name, [])
            if self.collection_name is not None
            else []
        )
        self._collection_protected = source_lines is PROTECTED
        self.lines = (
            deepcopy(list(source_lines))
            if isinstance(source_lines, (list, tuple))
            else []
        )
        self._selected_line: int | None = None
        self._reference_options: dict[str, tuple[tuple[str, Any], ...]] = {}
        self._reference_records: dict[str, dict[Any, dict[str, Any]]] = {}
        self._editors: dict[str, Editor] = {}
        self._line_editors: dict[str, Editor] = {}
        self._pending_conflict: RecordConflict | None = None
        self._pending_conflict_draft: dict[str, Any] | None = None
        self._load_reference_options()
        self.title = model.name
        entity_label = self.entity.label.removesuffix("s") or self.entity.label
        self.sub_title = (
            f"New {entity_label}" if session.is_new else f"Edit {entity_label}"
        )

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],
    ) -> bool | None:
        if action == "post" and "post" not in self.entity.actions:
            return False
        if action == "post" and "post" not in self.record_action_order:
            return False
        if action == "save" and "save" not in self.record_action_order:
            return False
        if action == "cancel" and "cancel" not in self.record_action_order:
            return False
        if action in {"add_line", "apply_line"} and self.collection_name is None:
            return False
        collection_action = {
            "add_line": "add",
            "apply_line": "apply",
        }.get(action)
        if (
            collection_action is not None
            and collection_action not in self.collection_action_order
        ):
            return False
        return super().check_action(action, parameters)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            f"{self.view.name}  ·  "
            f"{_record_title(self.entity, self.session.values) or 'New record'}",
            id="form-context",
        )
        with Vertical(id="form-body"):
            if self.layout_tabs:
                with TabbedContent(id="form-tabs"):
                    for tab_index, (label, sections) in enumerate(self.layout_tabs):
                        with TabPane(label, id=f"form-tab-{tab_index}"):
                            for section_index, section in sections:
                                if "collection" in section:
                                    yield from self._compose_collection_section()
                                else:
                                    fields = _section_fields(
                                        section,
                                        self.view,
                                        self.entity,
                                    )
                                    if fields:
                                        yield from self._compose_record_fields(
                                            fields,
                                            title=str(
                                                section.get("group")
                                                or self.entity.label
                                            ),
                                            widget_id=f"record-fields-{section_index}",
                                        )
            else:
                yield from self._compose_record_fields(
                    self.scalar_fields,
                    title=self.entity.label,
                    widget_id="record-fields",
                )
                yield from self._compose_collection_section()

        yield Static("", id="form-message")
        with Horizontal(id="form-actions"):
            if self.collection_name is not None:
                with Horizontal(id="line-actions"):
                    for action_name in self.collection_action_order:
                        yield _collection_action_button(action_name)
            yield Static("", id="action-spacer")
            with Horizontal(id="record-actions"):
                for action_name in self.record_action_order:
                    yield self._record_action_button(action_name)
        yield Footer()

    def _compose_record_fields(
        self,
        field_names: tuple[str, ...],
        *,
        title: str,
        widget_id: str,
    ) -> ComposeResult:
        if not field_names:
            return
        yield Static(title, classes="section-title")
        with Horizontal(id=widget_id):
            for column_fields in _field_columns(field_names):
                with Grid(classes="field-column"):
                    for field_name in column_fields:
                        field = self.entity.field(field_name)
                        editable = self._field_is_editable(
                            self.entity,
                            field,
                            self.session.original,
                        )
                        label_classes = (
                            "field-label" if editable else "field-label readonly-label"
                        )
                        yield Label(_field_label(field), classes=label_classes)
                        yield self._field_widget(
                            field,
                            self.session.values.get(field_name),
                            editable=editable,
                        )

    def _compose_collection_section(self) -> ComposeResult:
        if self.collection_name is None or self.collection_entity is None:
            return
        yield Static(
            _field_label(self.entity.field(self.collection_name)),
            classes="section-title",
        )
        yield DataTable(id="collection-records")
        with Horizontal(id="line-fields"):
            for column_fields in self.line_editor_columns:
                with Grid(classes="field-column"):
                    for field_name in column_fields:
                        field = self.collection_entity.field(field_name)
                        label_classes = (
                            "field-label"
                            if self._collection_is_editable()
                            else "field-label readonly-label"
                        )
                        yield Label(_field_label(field), classes=label_classes)
                        yield self._line_widget(field)

    def _record_action_button(self, action_name: str) -> Button:
        if action_name == "cancel":
            return Button("Cancel", id="cancel-form")
        if action_name == "save":
            return Button(
                "Save & Select" if self.select_after_save else "Save",
                id="save-form",
                variant="primary",
            )
        action = self.entity.actions[action_name]
        return Button(
            str(action.get("label") or action_name.replace("_", " ").title()),
            id=_record_action_button_id(action_name),
            variant="success" if action_name == "post" else "default",
        )

    def on_mount(self) -> None:
        self._sync_terminal_layout(self.app.size.width)
        if self.collection_name is not None:
            table = self.query_one("#collection-records", DataTable)
            for field_name in self.line_fields:
                field = self.collection_entity.field(field_name)
                table.add_column(
                    table_label(field, _field_label(field)),
                    key=field_name,
                )
            table.cursor_type = "row"
            table.zebra_stripes = True
            self._refresh_lines(select=0 if self.lines else None)
        self._update_actions()
        first_editor = next(
            (editor for editor in self._editors.values() if not editor.disabled),
            None,
        )
        if first_editor is not None:
            first_editor.focus()

    def on_resize(self, event: events.Resize) -> None:
        self._sync_terminal_layout(event.size.width)

    def _sync_terminal_layout(self, width: int) -> None:
        self.set_class(width < 100, "compact-terminal")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "cancel-form": self.action_cancel,
            "save-form": self.action_save,
            "add-line": self.action_add_line,
            "apply-line": self.action_apply_line,
            "remove-line": self.action_remove_line,
        }
        button_id = event.button.id or ""
        handler = handlers.get(button_id)
        if handler is not None:
            handler()
            return
        action_name = next(
            (
                name
                for name in self.entity.actions
                if _record_action_button_id(name) == button_id
            ),
            None,
        )
        if action_name is not None:
            self._execute_record_action(action_name)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.has_class("editable-value"):
            event.stop()
            self.focus_next()

    def on_lookup_field_open_requested(
        self,
        event: LookupField.OpenRequested,
    ) -> None:
        event.stop()
        self._open_lookup(event.lookup)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "collection-records":
            return
        key = str(event.row_key.value)
        if not key.startswith("line-"):
            return
        self._select_line(int(key.removeprefix("line-")))

    def action_save(self) -> None:
        buttons = list(self.query("#save-form"))
        if not buttons or buttons[0].disabled:
            return
        if not self._collect_form():
            return
        try:
            stored = self.records.commit(self.session, self.context)
        except ValidationFailed as error:
            self._show_validation(error)
            return
        except TideRuntimeError as error:
            if error.code == "stale_version" and not self.session.is_new:
                self._open_conflict_review()
                return
            self._show_error(error)
            return
        self.notify("Record saved.", severity="information")
        self.dismiss(stored if self.select_after_save else True)

    def action_post(self) -> None:
        self._execute_record_action("post")

    def _execute_record_action(self, action_name: str) -> None:
        buttons = list(self.query(f"#{_record_action_button_id(action_name)}"))
        if not buttons or buttons[0].disabled:
            return
        saved_before_post = False
        if not self._collect_form():
            return
        try:
            if self.session.changed_fields:
                self.records.commit(self.session, self.context)
                saved_before_post = True
            result = self.actions.execute(
                self.entity.name,
                action_name,
                self.session.identity,
                {},
                self.context,
                idempotency_key=f"tui:{uuid4()}",
                expected_version=self.session.expected_version,
            )
        except ValidationFailed as error:
            self._show_validation(error)
            self._recover_after_failed_post(saved_before_post)
            return
        except (TideRuntimeError, RuntimeError, ValueError) as error:
            self._show_error(error, prefix="Post failed")
            self._recover_after_failed_post(saved_before_post)
            return
        self.session.values = deepcopy(result)
        label = self.entity.actions[action_name].get("label") or action_name
        self.notify(f"{label} completed.", severity="information")
        self.dismiss(True)

    def action_cancel(self) -> None:
        if self.session.state is SessionState.ACTIVE:
            self.records.rollback(self.session)
        self.dismiss(False)

    def action_add_line(self) -> None:
        if self.collection_entity is None or not self._collection_is_editable():
            return
        try:
            defaults = self.records.create(
                self.collection_entity.name,
                self.context,
            ).values
        except TideRuntimeError as error:
            self._show_error(error)
            return
        line_number = (
            max(
                (int(line.get("line_number") or 0) for line in self.lines),
                default=0,
            )
            + 1
        )
        defaults["line_number"] = line_number
        inverse = self.entity.field(self.collection_name).metadata.get("inverse")
        if inverse:
            defaults[inverse] = self.session.identity
        self.lines.append(defaults)
        self._refresh_lines()
        self._select_line(len(self.lines) - 1)

    def action_apply_line(self) -> None:
        if self._selected_line is None or not self._collection_is_editable():
            return
        line = deepcopy(self.lines[self._selected_line])
        for field_name, editor in self._line_editors.items():
            field = self.collection_entity.field(field_name)
            line[field_name] = _editor_value(field, editor)
        _preview_computed_fields(self.collection_entity, line)
        self.lines[self._selected_line] = line
        self._refresh_lines(select=self._selected_line)
        self._set_message("Line applied locally; Save commits the invoice.")
        self._update_actions()

    def action_remove_line(self) -> None:
        if self._selected_line is None or not self._collection_is_editable():
            return
        del self.lines[self._selected_line]
        next_index = min(self._selected_line, len(self.lines) - 1)
        self._selected_line = None
        self._refresh_lines(select=next_index if next_index >= 0 else None)
        self._clear_line_editors()
        self._update_actions()

    def _field_widget(
        self,
        field: NormalizedField,
        value: Any,
        *,
        editable: bool,
    ) -> Editor | Static:
        if not editable:
            return Static(
                self._format_value(field, value),
                id=f"value-{field.name}",
                classes="readonly-value",
            )
        widget = self._editable_widget(field, value, prefix="field")
        self._editors[field.name] = widget
        return widget

    def _line_widget(self, field: NormalizedField) -> Editor:
        widget = self._editable_widget(field, None, prefix="line")
        widget.disabled = not self._collection_is_editable()
        self._line_editors[field.name] = widget
        return widget

    def _editable_widget(
        self,
        field: NormalizedField,
        value: Any,
        *,
        prefix: str,
    ) -> Editor:
        widget_id = f"{prefix}-{field.name}"
        field_type = field.metadata["type"]
        if field_type == "reference":
            if self._reference_editor(field.name, prefix) == "lookup":
                return LookupField(
                    value=value,
                    display=self._reference_display(field, value),
                    id=widget_id,
                    classes="editable-value",
                )
            return FormSelect(
                self._reference_options.get(field.name, ()),
                value=value if value is not None else Select.NULL,
                allow_blank=True,
                id=widget_id,
                classes="editable-value",
                compact=True,
            )
        if field_type == "choice":
            options = tuple(
                (str(choice).replace("_", " ").title(), choice)
                for choice in field.metadata.get("choices", ())
            )
            return FormSelect(
                options,
                value=value if value is not None else Select.NULL,
                allow_blank=True,
                id=widget_id,
                classes="editable-value",
                compact=True,
            )
        if field_type == "boolean":
            return FormSelect(
                (("Yes", True), ("No", False)),
                value=value if value is not None else Select.NULL,
                allow_blank=not field.metadata.get("required", False),
                id=widget_id,
                classes="editable-value",
                compact=True,
            )
        edit_mask = field.metadata.get("edit_mask")
        if isinstance(edit_mask, str):
            editor = NumericMaskedInput(
                value=_input_text(field, value),
                mask=edit_mask,
                precision=field.metadata.get("precision"),
                scale=field.metadata.get("scale"),
                id=widget_id,
                classes="editable-value",
            )
        else:
            validators = (
                Regex(
                    str(edit_mask["regex"]),
                    failure_description=f"{_field_label(field)} has an invalid format",
                )
                if isinstance(edit_mask, Mapping)
                else None
            )
            input_type = DateInput if field_type == "date" else Input
            editor = input_type(
                value=_input_text(field, value),
                validators=validators,
                validate_on=("blur", "submitted"),
                valid_empty=not field.metadata.get("required", False),
                max_length=int(field.metadata.get("length") or 0),
                id=widget_id,
                classes="editable-value",
            )
        if field_type == "date":
            editor.tooltip = "DD.MM.YYYY; use + or - to change one day"
        return editor

    def _collect_form(self) -> bool:
        if self._selected_line is not None and self._collection_is_editable():
            self.action_apply_line()
        for field_name, editor in self._editors.items():
            field = self.entity.field(field_name)
            self.session.set(field_name, _editor_value(field, editor))
        if self.collection_name is not None and self._collection_is_editable():
            self.session.set(self.collection_name, deepcopy(self.lines))
        return True

    def _select_line(self, index: int) -> None:
        if index < 0 or index >= len(self.lines):
            return
        self._selected_line = index
        line = self.lines[index]
        for field_name, editor in self._line_editors.items():
            value = line.get(field_name)
            self._set_editor_value(
                self.collection_entity.field(field_name),
                editor,
                value,
            )
        self._update_actions()

    def _refresh_lines(self, *, select: int | None = None) -> None:
        table = self.query_one("#collection-records", DataTable)
        table.clear()
        for index, line in enumerate(self.lines):
            table.add_row(
                *(
                    table_cell(
                        self.collection_entity.field(field_name),
                        self._format_value(
                            self.collection_entity.field(field_name),
                            line.get(field_name),
                        ),
                    )
                    for field_name in self.line_fields
                ),
                key=f"line-{index}",
            )
        if select is not None and self.lines:
            table.move_cursor(row=select, column=0)
            self._select_line(select)

    def _clear_line_editors(self) -> None:
        for field_name, editor in self._line_editors.items():
            if isinstance(editor, Select):
                editor.value = Select.NULL
            elif isinstance(editor, LookupField):
                editor.set_selection(None, "")
            else:
                editor.value = ""

    def _load_reference_options(self) -> None:
        fields = [(self.entity.field(name), "field") for name in self.scalar_fields]
        if self.collection_entity is not None:
            fields.extend(
                (self.collection_entity.field(name), "line")
                for name in self.line_editor_fields
            )
        for field, prefix in fields:
            if field.metadata["type"] != "reference" or not field.target_entity:
                continue
            if self._reference_editor(field.name, prefix) == "lookup":
                continue
            try:
                page = self.records.query_page(
                    field.target_entity,
                    QuerySpec(limit=500),
                    self.context,
                )
            except TideRuntimeError:
                continue
            target = self.model.entity(field.target_entity)
            key = _primary_key(target)
            options = tuple(
                (_record_title(target, record), record[key]) for record in page.records
            )
            self._reference_options[field.name] = options
            self._reference_records[field.name] = {
                record[key]: record for record in page.records
            }

    def _open_lookup(self, editor: LookupField) -> None:
        prefix, field_name = _editor_identity(editor.id)
        entity = self.entity if prefix == "field" else self.collection_entity
        if entity is None:
            return
        if prefix == "line" and self._selected_line is None:
            self._set_message("Add or select a line before choosing a lookup value.")
            return
        field = entity.field(field_name)
        lookup_name = self._reference_lookup_view(field, prefix)
        if lookup_name is None or lookup_name not in self.model.views:
            self._set_message(
                f"No lookup view is configured for {_field_label(field)}."
            )
            return

        self.app.push_screen(
            LookupScreen(
                self.model,
                self.records,
                self.actions,
                self.context,
                self.model.views[lookup_name],
                create_view=self._reference_create_view(field.name, prefix),
            ),
            lambda result: self._lookup_selected(editor, entity, field, prefix, result),
        )

    def _lookup_selected(
        self,
        editor: LookupField,
        entity: NormalizedEntity,
        field: NormalizedField,
        prefix: str,
        selected: dict[str, Any] | None,
    ) -> None:
        if selected is None or field.target_entity is None:
            return
        target = self.model.entity(field.target_entity)
        identity = selected[_primary_key(target)]
        editors = self._editors if prefix == "field" else self._line_editors
        if prefix == "field":
            draft = deepcopy(self.session.values)
        else:
            if self._selected_line is None:
                return
            draft = deepcopy(self.lines[self._selected_line])
        for name, draft_editor in editors.items():
            draft[name] = _editor_value(entity.field(name), draft_editor)
        try:
            updated = self.records.apply_reference_selection(
                entity.name,
                field.name,
                draft,
                identity,
                self.context,
            )
        except TideRuntimeError as error:
            self._show_error(error, prefix="Lookup selection failed")
            return

        assigned = {field.name, *field.metadata.get("on_select", {}).get("assign", {})}
        for name in assigned:
            destination_editor = editors.get(name)
            if destination_editor is None:
                continue
            display = (
                _record_title(target, selected)
                if name == field.name and isinstance(destination_editor, LookupField)
                else None
            )
            self._set_editor_value(
                entity.field(name),
                destination_editor,
                updated.get(name),
                display=display,
            )
        _preview_computed_fields(entity, updated)
        self._set_message(
            f"{_record_title(target, selected)} selected; initial values applied."
        )

    def _set_editor_value(
        self,
        field: NormalizedField,
        editor: Editor,
        value: Any,
        *,
        display: str | None = None,
    ) -> None:
        if isinstance(editor, Select):
            editor.value = value if value is not None else Select.NULL
        elif isinstance(editor, LookupField):
            editor.set_selection(
                value,
                display
                if display is not None
                else self._reference_display(field, value),
            )
        else:
            editor.value = _input_text(field, value)

    def _reference_editor(self, field_name: str, prefix: str) -> str:
        configuration = self._reference_configuration(field_name, prefix)
        return str(configuration.get("editor", "select"))

    def _reference_lookup_view(
        self,
        field: NormalizedField,
        prefix: str,
    ) -> str | None:
        configuration = self._reference_configuration(field.name, prefix)
        value = configuration.get("lookup_view", field.metadata.get("lookup_view"))
        return str(value) if value else None

    def _reference_create_view(
        self,
        field_name: str,
        prefix: str,
    ) -> ResolvedView | None:
        configuration = self._reference_configuration(field_name, prefix)
        if configuration.get("allow_create") is not True:
            return None
        create_name = configuration.get("create_view")
        return self.model.views.get(str(create_name)) if create_name else None

    def _reference_configuration(
        self,
        field_name: str,
        prefix: str,
    ) -> Mapping[str, Any]:
        view = self.view if prefix == "field" else self.inline_view
        if view is None:
            return {}
        configuration = view.data.get("fields", {}).get(field_name, {})
        return configuration if isinstance(configuration, Mapping) else {}

    def _reference_display(self, field: NormalizedField, value: Any) -> str:
        if value is None or value is PROTECTED or not field.target_entity:
            return ""
        related = self._reference_records.get(field.name, {}).get(value)
        if related is None:
            try:
                related = self.records.get(field.target_entity, value, self.context)
            except TideRuntimeError:
                return "Protected"
            self._reference_records.setdefault(field.name, {})[value] = related
        return _record_title(self.model.entity(field.target_entity), related)

    def _field_is_editable(
        self,
        entity: NormalizedEntity,
        field: NormalizedField,
        original: Mapping[str, Any],
    ) -> bool:
        metadata = field.metadata
        if metadata.get("readonly") or metadata.get("write", "normal") != "normal":
            return False
        if not self.records.security.can_write_field(
            entity.name,
            field.name,
            self.context,
        ):
            return False
        immutable_when = metadata.get("immutable_when")
        return not (
            immutable_when and bool(evaluate_expression(str(immutable_when), original))
        )

    def _collection_is_editable(self) -> bool:
        if self.collection_name is None or self._collection_protected:
            return False
        return self._field_is_editable(
            self.entity,
            self.entity.field(self.collection_name),
            self.session.original,
        )

    def _format_value(self, field: NormalizedField, value: Any) -> str:
        if value is PROTECTED:
            return "Protected"
        if value is None:
            return ""
        if field.metadata["type"] == "reference" and field.target_entity:
            related = self._reference_records.get(field.name, {}).get(value)
            if related is not None:
                return _record_title(self.model.entity(field.target_entity), related)
            try:
                related = self.records.get(field.target_entity, value, self.context)
            except TideRuntimeError:
                return "Protected"
            return _record_title(self.model.entity(field.target_entity), related)
        if isinstance(value, datetime):
            return value.astimezone().strftime("%d.%m.%Y %H:%M")
        if isinstance(value, date):
            return value.strftime("%d.%m.%Y")
        if isinstance(value, Decimal) and field.metadata.get("format") == "money":
            return f"{value:,.2f}"
        if field.metadata["type"] == "choice":
            return str(value).replace("_", " ").title()
        return str(value)

    def _update_actions(self) -> None:
        editable = bool(self._editors) or self._collection_is_editable()
        save_buttons = list(self.query("#save-form"))
        if save_buttons:
            save_buttons[0].disabled = not editable
        for action_name, action in self.entity.actions.items():
            action_buttons = list(
                self.query(f"#{_record_action_button_id(action_name)}")
            )
            if not action_buttons:
                continue
            can_execute = self.records.security.can_execute_action(
                action,
                self.context,
            )
            condition = action.get("enabled_when")
            if condition:
                values = deepcopy(self.session.values)
                if self.collection_name is not None:
                    values[self.collection_name] = deepcopy(self.lines)
                can_execute = can_execute and bool(
                    evaluate_expression(condition, values)
                )
            action_buttons[0].disabled = not can_execute
            visible_when = action.get("visible_when")
            if visible_when:
                values = deepcopy(self.session.values)
                if self.collection_name is not None:
                    values[self.collection_name] = deepcopy(self.lines)
                action_buttons[0].display = bool(
                    evaluate_expression(visible_when, values)
                )
        if self.collection_name is not None:
            collection_editable = self._collection_is_editable()
            for action_name in self.collection_action_order:
                button = self.query_one(
                    f"#{_collection_action_button_id(action_name)}", Button
                )
                button.disabled = not collection_editable or (
                    action_name in {"apply", "remove"} and self._selected_line is None
                )

    def _show_validation(self, error: ValidationFailed) -> None:
        messages = "; ".join(issue.message for issue in error.issues)
        self._set_message(f"Validation failed: {messages}")

    def _show_error(self, error: Exception, *, prefix: str = "Unable to save") -> None:
        self._set_message(f"{prefix}: {error}")

    def _open_conflict_review(self) -> None:
        try:
            current = self.records.get(
                self.entity.name,
                self.session.identity,
                self.context,
            )
        except TideRuntimeError as error:
            self._show_error(error, prefix="Unable to inspect the current record")
            return
        draft = deepcopy(self.session.values)
        conflict = compare_record_conflict(
            self.session.original,
            current,
            draft,
            fields=(
                field_name
                for field_name in (
                    *self.scalar_fields,
                    *((self.collection_name,) if self.collection_name else ()),
                )
                if self._field_is_editable(
                    self.entity,
                    self.entity.field(field_name),
                    self.session.original,
                )
            ),
        )
        self._pending_conflict = conflict
        self._pending_conflict_draft = draft
        self.app.push_screen(
            ConflictReviewScreen(
                conflict,
                field_label=lambda name: _field_label(self.entity.field(name)),
                format_value=self._format_conflict_value,
            ),
            self._conflict_review_closed,
        )

    def _conflict_review_closed(self, result: ConflictReviewResult | None) -> None:
        draft = self._pending_conflict_draft
        self._pending_conflict = None
        self._pending_conflict_draft = None
        if result is None or result.choice is ConflictChoice.KEEP_EDITING:
            self._set_message(
                "Your draft remains open and unsaved. Reload or rebase before saving."
            )
            return
        try:
            fresh = self.records.begin_edit(
                self.entity.name,
                self.session.identity,
                self.context,
            )
        except TideRuntimeError as error:
            self._show_error(error, prefix="Unable to reload the current record")
            return
        if (
            result.choice is ConflictChoice.REBASE
            and result.resolution is not None
            and draft is not None
        ):
            retained: list[str] = []
            dropped: list[str] = []
            for field_name in result.resolution.draft_fields:
                field = self.entity.field(field_name)
                if self._field_is_editable(self.entity, field, fresh.original):
                    fresh.set(field_name, deepcopy(draft.get(field_name)))
                    retained.append(field_name)
                else:
                    dropped.append(field_name)
            if retained:
                message = (
                    "Current data reloaded; non-conflicting draft fields were "
                    "retained. Review and save again."
                )
            else:
                message = "Current data reloaded; no draft fields could be retained."
            if dropped:
                message += (
                    " Workflow rules now lock: "
                    + ", ".join(_field_label(self.entity.field(name)) for name in dropped)
                    + "."
                )
        else:
            message = "Current data reloaded; the stale draft was discarded."
        self.dismiss(ReopenRecordEdit(fresh, message))

    def _format_conflict_value(self, field_name: str, value: object) -> str:
        if isinstance(value, (list, tuple)):
            suffix = "item" if len(value) == 1 else "items"
            return f"{len(value)} {suffix}"
        if isinstance(value, Mapping):
            return "Record"
        return self._format_value(self.entity.field(field_name), value)

    def _set_message(self, message: str) -> None:
        self.query_one("#form-message", Static).update(message)

    def _recover_after_failed_post(self, saved: bool) -> None:
        if not saved:
            return
        try:
            self.session = self.records.begin_edit(
                self.entity.name,
                self.session.identity,
                self.context,
            )
        except TideRuntimeError:
            return


def _form_fields(view: ResolvedView, entity: NormalizedEntity) -> tuple[str, ...]:
    result: list[str] = []
    for section in view.data.get("layout", ()):
        for row in section.get("rows", ()):
            for field_name in row:
                name = str(field_name)
                if (
                    name in entity.fields
                    and not _field_is_hidden(view, name)
                    and name not in result
                ):
                    result.append(name)
    return tuple(result)


def _section_fields(
    section: Mapping[str, Any],
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    result: list[str] = []
    for row in section.get("rows", ()):
        for field_name in row:
            name = str(field_name)
            if (
                name in entity.fields
                and not _field_is_hidden(view, name)
                and name not in result
            ):
                result.append(name)
    return tuple(result)


def _field_is_hidden(view: ResolvedView, name: str) -> bool:
    fields = view.data.get("fields", {})
    configuration = fields.get(name) if isinstance(fields, Mapping) else None
    return bool(
        isinstance(configuration, Mapping)
        and configuration.get("hidden", False)
    )


def _layout_tabs(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[tuple[str, tuple[tuple[int, Mapping[str, Any]], ...]], ...]:
    sections = tuple(
        (index, section)
        for index, section in enumerate(view.data.get("layout", ()))
        if isinstance(section, Mapping)
        and (
            (
                section.get("collection")
                and not _field_is_hidden(view, str(section["collection"]))
            )
            or _section_fields(section, view, entity)
        )
    )
    if not any(section.get("tab") for _index, section in sections):
        return ()
    grouped: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for index, section in sections:
        label = str(section.get("tab") or "General")
        grouped.setdefault(label, []).append((index, section))
    return tuple((label, tuple(items)) for label, items in grouped.items())


def _record_action_order(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    configured = tuple(str(name) for name in view.data.get("actions", ()))
    if configured:
        return configured
    return ("cancel", "save", *entity.actions)


def _collection_action_order(
    view: ResolvedView,
    collection_name: str | None,
) -> tuple[str, ...]:
    if collection_name is None:
        return ()
    for section in view.data.get("layout", ()):
        if not isinstance(section, Mapping):
            continue
        if str(section.get("collection")) != collection_name:
            continue
        configured = tuple(str(name) for name in section.get("actions", ()))
        return configured or ("add", "apply", "remove")
    return ("add", "apply", "remove")


def _record_action_button_id(action_name: str) -> str:
    if action_name == "post":
        return "post-record"
    return "record-action-" + action_name.replace("_", "-")


def _collection_action_button_id(action_name: str) -> str:
    return {
        "add": "add-line",
        "apply": "apply-line",
        "remove": "remove-line",
    }[action_name]


def _collection_action_button(action_name: str) -> Button:
    label, variant = {
        "add": ("Add line", "default"),
        "apply": ("Apply line", "primary"),
        "remove": ("Remove line", "warning"),
    }[action_name]
    return Button(
        label,
        id=_collection_action_button_id(action_name),
        variant=variant,
    )


def _inline_editor_columns(
    view: ResolvedView,
    entity: NormalizedEntity,
    table_fields: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    candidates = tuple(
        name
        for name in table_fields
        if name in entity.fields
        and not entity.field(name).metadata.get("computed")
        and not entity.field(name).metadata.get("readonly")
        and not view.data.get("fields", {}).get(name, {}).get("hidden", False)
    )
    rows: list[tuple[str, ...]] = []
    for section in view.data.get("layout", ()):
        for raw_row in section.get("rows", ()):
            row = tuple(str(name) for name in raw_row if str(name) in candidates)
            if row:
                rows.append(row)
    if not rows:
        return _field_columns(candidates)
    return (
        tuple(row[0] for row in rows),
        tuple(row[1] for row in rows if len(row) > 1),
    )


def _field_columns(
    field_names: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Keep the visual rows while composing focusable fields column-first."""

    return field_names[::2], field_names[1::2]


def _editor_identity(widget_id: str | None) -> tuple[str, str]:
    value = widget_id or ""
    for prefix in ("field", "line"):
        marker = f"{prefix}-"
        if value.startswith(marker):
            return prefix, value.removeprefix(marker)
    raise ValueError(f"unknown form editor id {value!r}")


def _collection_view(
    model: ApplicationModel,
    view: ResolvedView,
) -> tuple[str | None, ResolvedView | None]:
    for section in view.data.get("layout", ()):
        collection = section.get("collection")
        inline_name = section.get("view")
        if (
            collection
            and inline_name
            and not _field_is_hidden(view, str(collection))
        ):
            inline = model.views.get(str(inline_name))
            if inline is not None and inline.kind == "inline_edit":
                return str(collection), inline
    return None, None


def select_form_view(
    model: ApplicationModel,
    entity_name: str,
) -> ResolvedView | None:
    return next(
        (
            view
            for view in model.views.values()
            if view.kind == "form" and view.entity == entity_name
        ),
        None,
    )


def _editor_value(field: NormalizedField, editor: Editor) -> Any:
    if isinstance(editor, Select):
        return None if editor.value is Select.NULL else editor.value
    if isinstance(editor, LookupField):
        return editor.value
    raw = editor.value.strip()
    if raw == "" and not field.metadata.get("required"):
        return None
    field_type = field.metadata["type"]
    try:
        if field_type == "date":
            parsed = _parse_date(raw)
            return parsed if parsed is not None else raw
        if field_type == "datetime":
            return datetime.fromisoformat(raw)
        if field_type == "integer":
            return int(raw)
        if field_type == "decimal":
            return Decimal(raw.replace(",", "."))
    except ValueError:
        return raw
    except InvalidOperation:
        return raw
    return raw


def _preview_computed_fields(
    entity: NormalizedEntity,
    values: dict[str, Any],
) -> None:
    remaining = {
        name
        for name, field in entity.fields.items()
        if field.metadata.get("computed", {}).get("materialization") == "stored"
    }
    while remaining:
        progressed = False
        for field_name in tuple(remaining):
            field = entity.field(field_name)
            local_dependencies = {
                dependency.split(".", 1)[0] for dependency in field.dependencies
            }
            if local_dependencies & remaining:
                continue
            try:
                values[field_name] = evaluate_expression(
                    field.metadata["computed"]["expression"],
                    values,
                )
            except (ArithmeticError, TypeError, ValueError):
                values[field_name] = None
            remaining.remove(field_name)
            progressed = True
        if not progressed:
            break


def _input_text(field: NormalizedField, value: Any) -> str:
    if value is None or value is PROTECTED:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return (
            _format_date(value)
            if field.metadata.get("format") == "local_date"
            else value.isoformat()
        )
    return str(value)


def _parse_date(value: str) -> date | None:
    candidate = value.strip()
    for parser in (
        date.fromisoformat,
        lambda source: datetime.strptime(source, "%d.%m.%Y").date(),
        lambda source: datetime.strptime(source, "%d/%m/%Y").date(),
    ):
        try:
            return parser(candidate)
        except ValueError:
            continue
    return None


def _format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _primary_key(entity: NormalizedEntity) -> str:
    return next(
        name
        for name, field in entity.fields.items()
        if field.metadata.get("primary_key")
    )


def _field_label(field: NormalizedField) -> str:
    return str(field.metadata.get("label") or field.name.replace("_", " ").title())


def _record_title(entity: NormalizedEntity, record: Mapping[str, Any]) -> str:
    display = entity.display
    if not display:
        return str(record.get(_primary_key(entity), ""))
    if "{" not in display:
        return str(record.get(display, ""))
    try:
        return display.format_map(
            {
                name: "Protected" if value is PROTECTED else value
                for name, value in record.items()
            }
        )
    except (KeyError, ValueError):
        return str(record.get(_primary_key(entity), ""))
