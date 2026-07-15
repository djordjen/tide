"""Metadata-driven transactional record editing for the Textual adapter."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import uuid4

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static

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
from tide.services import ActionService, RecordsService
from tide.sessions.record_session import RecordSession, SessionState


class RecordEditScreen(Screen[bool]):
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

    #form-scroll {
        height: 1fr;
        padding: 0 2;
    }

    .section-title {
        height: 2;
        margin-top: 1;
        color: $accent;
        text-style: bold;
        content-align: left middle;
    }

    #record-fields, #line-fields {
        grid-size: 4;
        grid-columns: 16 1fr 16 1fr;
        grid-gutter: 0 1;
        height: auto;
    }

    .field-label {
        height: 3;
        content-align: right middle;
        color: $text-muted;
    }

    .readonly-value {
        height: 3;
        padding: 0 1;
        border: tall $surface-lighten-2;
        content-align: left middle;
    }

    #collection-records {
        height: 12;
        border: round $primary;
    }

    #line-toolbar, #form-actions {
        height: 3;
        align-horizontal: right;
    }

    #line-toolbar Button, #form-actions Button {
        min-width: 12;
        margin-left: 1;
    }

    #form-message {
        min-height: 2;
        height: auto;
        padding: 0 1;
        color: $warning;
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
    ) -> None:
        super().__init__()
        self.model = model
        self.records = records
        self.actions = actions
        self.context = context
        self.view = view
        self.session = session
        self.entity = model.entity(session.entity)
        self.scalar_fields = _form_fields(view, self.entity)
        self.collection_name, self.inline_view = _collection_view(model, view)
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
        self._editors: dict[str, Input | Select[Any]] = {}
        self._line_editors: dict[str, Input | Select[Any]] = {}
        self._load_reference_options()
        self.title = model.name
        self.sub_title = (
            f"New {self.entity.label}"
            if session.is_new
            else f"Edit {self.entity.label}"
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            f"{self.view.name}  ·  "
            f"{_record_title(self.entity, self.session.values) or 'New record'}",
            id="form-context",
        )
        with VerticalScroll(id="form-scroll"):
            yield Static(self.entity.label, classes="section-title")
            with Grid(id="record-fields"):
                for field_name in self.scalar_fields:
                    field = self.entity.field(field_name)
                    yield Label(_field_label(field), classes="field-label")
                    yield self._field_widget(field, self.session.values.get(field_name))

            if self.collection_name is not None and self.collection_entity is not None:
                yield Static(
                    _field_label(self.entity.field(self.collection_name)),
                    classes="section-title",
                )
                yield DataTable(id="collection-records")
                with Horizontal(id="line-toolbar"):
                    yield Button("Add line", id="add-line")
                    yield Button("Apply line", id="apply-line", variant="primary")
                    yield Button("Remove line", id="remove-line", variant="warning")
                with Grid(id="line-fields"):
                    for field_name in self.line_fields:
                        field = self.collection_entity.field(field_name)
                        if field.metadata.get("computed") or field.metadata.get("readonly"):
                            continue
                        yield Label(_field_label(field), classes="field-label")
                        yield self._line_widget(field)

        yield Static("", id="form-message")
        with Horizontal(id="form-actions"):
            yield Button("Cancel", id="cancel-form")
            yield Button("Save", id="save-form", variant="primary")
            yield Button("Post", id="post-record", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        if self.collection_name is not None:
            table = self.query_one("#collection-records", DataTable)
            for field_name in self.line_fields:
                table.add_column(
                    _field_label(self.collection_entity.field(field_name)),
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "cancel-form": self.action_cancel,
            "save-form": self.action_save,
            "post-record": self.action_post,
            "add-line": self.action_add_line,
            "apply-line": self.action_apply_line,
            "remove-line": self.action_remove_line,
        }
        handler = handlers.get(event.button.id or "")
        if handler is not None:
            handler()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "collection-records":
            return
        key = str(event.row_key.value)
        if not key.startswith("line-"):
            return
        self._select_line(int(key.removeprefix("line-")))

    def action_save(self) -> None:
        if self.query_one("#save-form", Button).disabled:
            return
        if not self._collect_form():
            return
        try:
            self.records.commit(self.session, self.context)
        except ValidationFailed as error:
            self._show_validation(error)
            return
        except TideRuntimeError as error:
            self._show_error(error)
            return
        self.notify("Record saved.", severity="information")
        self.dismiss(True)

    def action_post(self) -> None:
        if self.query_one("#post-record", Button).disabled:
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
                "post",
                self.session.identity,
                {},
                self.context,
                idempotency_key=f"tui:{uuid4()}",
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
        self.notify("Invoice posted.", severity="information")
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
        line_number = max(
            (int(line.get("line_number") or 0) for line in self.lines),
            default=0,
        ) + 1
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
    ) -> Input | Select[Any] | Static:
        if not self._field_is_editable(self.entity, field, self.session.original):
            return Static(
                self._format_value(field, value),
                id=f"value-{field.name}",
                classes="readonly-value",
            )
        widget = self._editable_widget(field, value, prefix="field")
        self._editors[field.name] = widget
        return widget

    def _line_widget(self, field: NormalizedField) -> Input | Select[Any]:
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
    ) -> Input | Select[Any]:
        widget_id = f"{prefix}-{field.name}"
        field_type = field.metadata["type"]
        if field_type == "reference":
            return Select(
                self._reference_options.get(field.name, ()),
                value=value if value is not None else Select.NULL,
                allow_blank=True,
                id=widget_id,
            )
        if field_type == "choice":
            options = tuple(
                (str(choice).replace("_", " ").title(), choice)
                for choice in field.metadata.get("choices", ())
            )
            return Select(
                options,
                value=value if value is not None else Select.NULL,
                allow_blank=True,
                id=widget_id,
            )
        return Input(value=_input_text(value), id=widget_id)

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
            if isinstance(editor, Select):
                editor.value = value if value is not None else Select.NULL
            else:
                editor.value = _input_text(value)
        self._update_actions()

    def _refresh_lines(self, *, select: int | None = None) -> None:
        table = self.query_one("#collection-records", DataTable)
        table.clear()
        for index, line in enumerate(self.lines):
            table.add_row(
                *(
                    self._format_value(
                        self.collection_entity.field(field_name),
                        line.get(field_name),
                    )
                    for field_name in self.line_fields
                ),
                key=f"line-{index}",
            )
        if select is not None and self.lines:
            table.move_cursor(row=select, column=0)
            self._select_line(select)

    def _clear_line_editors(self) -> None:
        for editor in self._line_editors.values():
            if isinstance(editor, Select):
                editor.value = Select.NULL
            else:
                editor.value = ""

    def _load_reference_options(self) -> None:
        fields = [self.entity.field(name) for name in self.scalar_fields]
        if self.collection_entity is not None:
            fields.extend(
                self.collection_entity.field(name) for name in self.line_fields
            )
        for field in fields:
            if field.metadata["type"] != "reference" or not field.target_entity:
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
            immutable_when
            and bool(evaluate_expression(str(immutable_when), original))
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
        self.query_one("#save-form", Button).disabled = not editable
        post = self.entity.actions.get("post")
        can_post = False
        if post is not None:
            permission = post.get("permission")
            can_post = bool(
                post.get("unrestricted") is True
                or self.records.security.has_permission(self.context, permission)
            )
            condition = post.get("enabled_when")
            if condition:
                values = deepcopy(self.session.values)
                if self.collection_name is not None:
                    values[self.collection_name] = deepcopy(self.lines)
                can_post = can_post and bool(evaluate_expression(condition, values))
        self.query_one("#post-record", Button).disabled = not can_post
        collection_editable = self._collection_is_editable()
        self.query_one("#add-line", Button).disabled = not collection_editable
        self.query_one("#apply-line", Button).disabled = (
            not collection_editable or self._selected_line is None
        )
        self.query_one("#remove-line", Button).disabled = (
            not collection_editable or self._selected_line is None
        )

    def _show_validation(self, error: ValidationFailed) -> None:
        messages = "; ".join(issue.message for issue in error.issues)
        self._set_message(f"Validation failed: {messages}")

    def _show_error(self, error: Exception, *, prefix: str = "Unable to save") -> None:
        self._set_message(f"{prefix}: {error}")

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
                if name in entity.fields and name not in result:
                    result.append(name)
    return tuple(result)


def _collection_view(
    model: ApplicationModel,
    view: ResolvedView,
) -> tuple[str | None, ResolvedView | None]:
    for section in view.data.get("layout", ()):
        collection = section.get("collection")
        inline_name = section.get("view")
        if collection and inline_name:
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


def _editor_value(field: NormalizedField, editor: Input | Select[Any]) -> Any:
    if isinstance(editor, Select):
        return None if editor.value is Select.NULL else editor.value
    raw = editor.value.strip()
    if raw == "" and not field.metadata.get("required"):
        return None
    field_type = field.metadata["type"]
    try:
        if field_type == "date":
            return date.fromisoformat(raw)
        if field_type == "datetime":
            return datetime.fromisoformat(raw)
        if field_type == "integer":
            return int(raw)
        if field_type == "decimal":
            return Decimal(raw)
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


def _input_text(value: Any) -> str:
    if value is None or value is PROTECTED:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


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
