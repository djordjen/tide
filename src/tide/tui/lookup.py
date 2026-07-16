"""Reusable secured multi-column lookup widgets for the Textual adapter."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Static

from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
from tide.runtime import RequestContext, TideRuntimeError
from tide.security import PROTECTED
from tide.services import ActionService, RecordsService
from tide.tui.table import table_cell, table_label


class LookupField(Static, can_focus=True):
    """Compact reference value that opens a lookup window on request."""

    BINDINGS = [
        Binding("enter", "focus_next_control", show=False),
        Binding("down,space,f4", "open_lookup", "Open lookup", show=False),
    ]

    class OpenRequested(Message):
        def __init__(self, lookup: LookupField) -> None:
            super().__init__()
            self.lookup = lookup

        @property
        def control(self) -> LookupField:
            return self.lookup

    def __init__(
        self,
        *,
        value: Any,
        display: str,
        id: str,
        classes: str,
    ) -> None:
        self.value = value
        self.display_value = display
        super().__init__(self._content(), id=id, classes=classes)

    def set_selection(self, value: Any, display: str) -> None:
        self.value = value
        self.display_value = display
        self.update(self._content())

    def action_focus_next_control(self) -> None:
        self.screen.focus_next()

    def action_open_lookup(self) -> None:
        if not self.disabled:
            self.post_message(self.OpenRequested(self))

    def on_click(self, event: Click) -> None:
        event.stop()
        self.action_open_lookup()

    def _content(self) -> str:
        return f"{self.display_value or 'Select…'}  ▾"


class LookupScreen(ModalScreen[dict[str, Any] | None]):
    """Search and choose one secured record through a resolved lookup view."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    LookupScreen {
        align: center middle;
        background: $background 65%;
    }

    #lookup-dialog {
        width: 90%;
        height: 82%;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }

    #lookup-title {
        height: 2;
        color: $accent;
        text-style: bold;
        content-align: left middle;
    }

    #lookup-search-label {
        width: 10;
        height: 3;
        content-align: right middle;
        padding-right: 1;
    }

    #lookup-search-row {
        height: 3;
    }

    #lookup-search {
        width: 1fr;
        height: 3;
    }

    #lookup-results {
        height: 1fr;
        border: round $primary;
    }

    #lookup-status {
        height: 2;
        color: $text-muted;
        content-align: left middle;
    }

    #lookup-actions {
        height: 3;
        align-horizontal: right;
    }

    #lookup-actions Button {
        min-width: 12;
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "create_record", "New"),
        Binding("escape", "cancel_lookup", "Cancel"),
    ]

    def __init__(
        self,
        model: ApplicationModel,
        records: RecordsService,
        actions: ActionService,
        context: RequestContext,
        view: ResolvedView,
        *,
        create_view: ResolvedView | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.records = records
        self.actions = actions
        self.context = context
        self.view = view
        self.entity = model.entity(view.entity)
        self.create_view = create_view
        self.can_create = bool(
            create_view is not None
            and self.records.security.can_access_entity(
                self.entity,
                "create",
                context,
            )
        )
        self.columns = _lookup_columns(view, self.entity)
        self.search_fields = _lookup_search_fields(view, self.entity)
        self.page_size = max(
            1,
            min(500, int(view.data.get("settings", {}).get("page_size", 20))),
        )
        self._records: tuple[dict[str, Any], ...] = ()

    def compose(self) -> ComposeResult:
        with Vertical(id="lookup-dialog"):
            yield Static(f"Select {_entity_label(self.entity)}", id="lookup-title")
            with Horizontal(id="lookup-search-row"):
                yield Label("Search", id="lookup-search-label")
                yield Input(
                    placeholder="Search "
                    + ", ".join(_field_label(self.entity.field(name)) for name in self.search_fields)
                    + "…",
                    id="lookup-search",
                )
            yield DataTable(id="lookup-results")
            yield Static("", id="lookup-status")
            with Horizontal(id="lookup-actions"):
                yield Button(
                    "New",
                    id="create-lookup-record",
                    disabled=not self.can_create,
                    variant="success",
                )
                yield Button("Cancel", id="cancel-lookup")
                yield Button("Select", id="select-lookup", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#lookup-results", DataTable)
        for field_name in self.columns:
            field = self.entity.field(field_name)
            table.add_column(table_label(field, _field_label(field)), key=field_name)
        table.cursor_type = "row"
        table.zebra_stripes = True
        self._reload()
        self.query_one("#lookup-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "lookup-search":
            self._reload()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "lookup-search" and self._records:
            event.stop()
            self.dismiss(dict(self._records[0]))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "lookup-results":
            return
        index = _row_index(event.row_key.value)
        if index is not None and index < len(self._records):
            self.dismiss(dict(self._records[index]))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-lookup":
            self.action_cancel_lookup()
        elif event.button.id == "select-lookup":
            self.action_select_lookup()
        elif event.button.id == "create-lookup-record":
            self.action_create_record()

    def action_cancel_lookup(self) -> None:
        self.dismiss(None)

    def action_select_lookup(self) -> None:
        if not self._records:
            return
        row = self.query_one("#lookup-results", DataTable).cursor_row
        self.dismiss(dict(self._records[max(0, min(row, len(self._records) - 1))]))

    def action_create_record(self) -> None:
        if not self.can_create or self.create_view is None:
            return
        try:
            session = self.records.create(self.entity.name, self.context)
        except TideRuntimeError as error:
            self.notify(str(error), severity="error")
            return
        from tide.tui.form import RecordEditScreen

        self.app.push_screen(
            RecordEditScreen(
                self.model,
                self.records,
                self.actions,
                self.context,
                self.create_view,
                session,
                select_after_save=True,
            ),
            self._record_created,
        )

    def _record_created(self, result: Any) -> None:
        if isinstance(result, Mapping):
            self.dismiss(dict(result))

    def _reload(self) -> None:
        search_text = self.query_one("#lookup-search", Input).value
        table = self.query_one("#lookup-results", DataTable)
        status = self.query_one("#lookup-status", Static)
        try:
            self._records = self.records.lookup_records(
                self.entity.name,
                self.search_fields,
                search_text,
                self.context,
                limit=self.page_size,
            )
        except (TideRuntimeError, ValueError) as error:
            self._records = ()
            status.update(f"Lookup failed: {error}")
        else:
            noun = "match" if len(self._records) == 1 else "matches"
            suffix = f" for {search_text!r}" if search_text else ""
            action_hint = "  ·  Ctrl+N creates" if self.can_create else ""
            status.update(
                f"{len(self._records)} {noun}{suffix}  ·  Enter selects{action_hint}"
            )
        table.clear()
        for index, record in enumerate(self._records):
            table.add_row(
                *(
                    table_cell(
                        self.entity.field(field_name),
                        _format_value(
                            self.entity.field(field_name), record.get(field_name)
                        ),
                    )
                    for field_name in self.columns
                ),
                key=f"lookup-{index}",
            )
        self.query_one("#select-lookup", Button).disabled = not self._records
        if self._records:
            table.move_cursor(row=0, column=0)


def _lookup_columns(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    configured = tuple(str(name) for name in view.data.get("columns", ()))
    return configured or tuple(
        name
        for name, field in entity.fields.items()
        if field.metadata["type"] not in {"collection"}
    )


def _lookup_search_fields(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    configured = tuple(str(name) for name in view.data.get("search", ()))
    candidates = configured or tuple(entity.metadata.get("search_fields", ()))
    result = tuple(
        name
        for name in candidates
        if name in entity.fields
        and entity.field(name).metadata["type"] in {"string", "choice"}
        and not entity.field(name).metadata.get("computed")
    )
    if not result:
        raise ValueError(f"lookup view {view.name!r} has no searchable string fields")
    return result


def _format_value(field: NormalizedField, value: Any) -> str:
    if value is None:
        return ""
    if value is PROTECTED:
        return "Protected"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, Decimal) and field.metadata.get("format") == "money":
        return f"{value:,.2f}"
    if field.metadata["type"] == "choice":
        return str(value).replace("_", " ").title()
    return str(value)


def _row_index(value: Any) -> int | None:
    text = str(value)
    if not text.startswith("lookup-"):
        return None
    try:
        return int(text.removeprefix("lookup-"))
    except ValueError:
        return None


def _field_label(field: NormalizedField) -> str:
    return str(field.metadata.get("label") or _humanize(field.name))


def _entity_label(entity: NormalizedEntity) -> str:
    return entity.label.removesuffix("s") or entity.label


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip().title()
