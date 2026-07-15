"""First metadata-driven Textual browse adapter."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any, Mapping

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static

from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
from tide.data import QuerySpec
from tide.runtime import RequestContext, TideRuntimeError
from tide.security import PROTECTED
from tide.services import RecordsService


class TideApp(App[None]):
    """Render one secured browse view through the headless records service."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    Header {
        background: $primary;
        color: $text;
    }

    #browse-context {
        height: 2;
        padding: 0 2;
        color: $text-muted;
        content-align: left middle;
    }

    #browse-toolbar {
        height: 3;
        padding: 0 1;
        align-horizontal: right;
    }

    #browse-toolbar Button {
        min-width: 12;
        margin: 0 0 0 1;
    }

    #records {
        height: 1fr;
        margin: 0 1;
        border: round $primary;
    }

    #browse-status {
        height: 2;
        padding: 0 2;
        color: $text-muted;
        content-align: left middle;
    }
    """

    BINDINGS = [
        Binding("p", "previous_page", "Previous"),
        Binding("n", "next_page", "Next"),
        Binding("r", "reload", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        model: ApplicationModel,
        records: RecordsService,
        context: RequestContext,
        *,
        view_name: str | None = None,
        page_size: int | None = None,
        source_label: str = "in-memory",
    ) -> None:
        super().__init__()
        self.model = model
        self.records = records
        self.context = context
        self.view = _select_browse_view(model, view_name)
        self.entity = model.entity(self.view.entity)
        self.columns = _browse_columns(self.view, self.entity)
        configured_page_size = int(
            self.view.data.get("settings", {}).get("page_size", 25)
        )
        self.page_size = page_size if page_size is not None else configured_page_size
        if self.page_size < 1 or self.page_size > 500:
            raise ValueError("TUI page size must be between 1 and 500")
        self.source_label = source_label
        self.title = model.name
        self.sub_title = self.entity.label
        self._page_cursors: list[str | None] = [None]
        self._page_index = 0
        self._next_cursor: str | None = None
        self._current_records: tuple[dict[str, Any], ...] = ()
        self._reference_cache: dict[tuple[str, Any], str] = {}

    @property
    def page_number(self) -> int:
        return self._page_index + 1

    @property
    def current_records(self) -> tuple[dict[str, Any], ...]:
        return self._current_records

    def compose(self) -> ComposeResult:
        roles = ", ".join(sorted(self.context.principal.roles)) or "no role"
        yield Header(show_clock=False)
        yield Static(
            f"{self.view.name}  ·  {self.context.principal.identifier}  ·  {roles}",
            id="browse-context",
        )
        with Horizontal(id="browse-toolbar"):
            yield Button("Previous", id="previous-page", disabled=True)
            yield Button("Next", id="next-page", disabled=True, variant="primary")
            yield Button("Refresh", id="refresh-page")
            yield Button("Quit", id="quit-app")
        yield DataTable(id="records")
        yield Static("Loading…", id="browse-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#records", DataTable)
        for field_name in self.columns:
            table.add_column(
                _field_label(self.entity.field(field_name)), key=field_name
            )
        table.cursor_type = "row"
        table.zebra_stripes = bool(
            self.view.data.get("settings", {}).get("zebra_stripes", True)
        )
        table.focus()
        self._load_page()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "previous-page":
            self.action_previous_page()
        elif event.button.id == "next-page":
            self.action_next_page()
        elif event.button.id == "refresh-page":
            self.action_reload()
        elif event.button.id == "quit-app":
            self.exit()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        record = self._record_for_row_key(str(event.row_key.value))
        if record is None:
            return
        display = _display_record(self.entity, record)
        self.notify(f"Selected {display}. The edit form is the next TIDE slice.")

    def action_previous_page(self) -> None:
        if self._page_index == 0:
            return
        self._page_index -= 1
        self._load_page()

    def action_next_page(self) -> None:
        if self._next_cursor is None:
            return
        self._page_cursors = self._page_cursors[: self._page_index + 1]
        self._page_cursors.append(self._next_cursor)
        self._page_index += 1
        self._load_page()

    def action_reload(self) -> None:
        self._page_cursors = [None]
        self._page_index = 0
        self._reference_cache.clear()
        self._load_page()

    def _load_page(self) -> None:
        table = self.query_one("#records", DataTable)
        status = self.query_one("#browse-status", Static)
        try:
            page = self.records.query_page(
                self.entity.name,
                QuerySpec(
                    limit=self.page_size,
                    cursor=self._page_cursors[self._page_index],
                ),
                self.context,
            )
            self._current_records = page.records
            self._next_cursor = page.next_cursor
            table.clear()
            primary_key = _primary_key(self.entity)
            for record in page.records:
                table.add_row(
                    *(
                        self._format_value(self.entity.field(field_name), record)
                        for field_name in self.columns
                    ),
                    key=str(record[primary_key]),
                )
            count = len(page.records)
            noun = "record" if count == 1 else "records"
            status.update(
                f"Page {self.page_number}  ·  {count} {noun}  ·  "
                f"{self.source_label}  ·  P/N page  R refresh  Q quit"
            )
            self._update_navigation()
        except (TideRuntimeError, ValueError) as error:
            self._current_records = ()
            self._next_cursor = None
            table.clear()
            status.update(f"Unable to load {self.entity.label}: {error}")
            self._update_navigation(force_disabled=True)

    def _format_value(
        self,
        field: NormalizedField,
        record: Mapping[str, Any],
    ) -> str:
        value = record.get(field.name)
        if value is PROTECTED:
            return "Protected"
        if value is None:
            return ""
        if field.metadata["type"] == "reference" and field.target_entity:
            return self._reference_display(field.target_entity, value)
        if isinstance(value, datetime):
            return value.astimezone().strftime("%d.%m.%Y %H:%M")
        if isinstance(value, date):
            return value.strftime("%d.%m.%Y")
        if isinstance(value, Decimal):
            if field.metadata.get("format") == "money":
                return f"{value:,.2f}"
            return str(value)
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if field.metadata["type"] == "choice":
            return str(value).replace("_", " ").title()
        return str(value)

    def _reference_display(self, target_entity: str, identity: Any) -> str:
        cache_key = target_entity, identity
        if cache_key in self._reference_cache:
            return self._reference_cache[cache_key]
        try:
            related = self.records.get(target_entity, identity, self.context)
            result = _display_record(self.model.entity(target_entity), related)
        except TideRuntimeError:
            result = "Protected"
        self._reference_cache[cache_key] = result
        return result

    def _record_for_row_key(self, key: str) -> dict[str, Any] | None:
        primary_key = _primary_key(self.entity)
        return next(
            (
                record
                for record in self._current_records
                if str(record.get(primary_key)) == key
            ),
            None,
        )

    def _update_navigation(self, *, force_disabled: bool = False) -> None:
        self.query_one("#previous-page", Button).disabled = (
            force_disabled or self._page_index == 0
        )
        self.query_one("#next-page", Button).disabled = (
            force_disabled or self._next_cursor is None
        )


def _select_browse_view(
    model: ApplicationModel,
    view_name: str | None,
) -> ResolvedView:
    if view_name is not None:
        view = model.views.get(view_name)
        if view is None:
            raise ValueError(f"unknown TUI view {view_name!r}")
        if view.kind != "browse":
            raise ValueError(f"TUI view {view_name!r} is not a browse view")
        return view
    view = next(
        (candidate for candidate in model.views.values() if candidate.kind == "browse"),
        None,
    )
    if view is None:
        raise ValueError("application does not define a browse view")
    return view


def _browse_columns(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    configured = tuple(str(name) for name in view.data.get("columns", ()))
    columns = configured or tuple(
        name
        for name, field in entity.fields.items()
        if field.metadata["type"] != "collection"
    )
    unknown = [name for name in columns if name not in entity.fields]
    if unknown:
        raise ValueError(f"browse view contains unknown columns: {', '.join(unknown)}")
    return columns


def _primary_key(entity: NormalizedEntity) -> str:
    return next(
        name
        for name, field in entity.fields.items()
        if field.metadata.get("primary_key")
    )


def _field_label(field: NormalizedField) -> str:
    return str(field.metadata.get("label") or _humanize(field.name))


def _display_record(entity: NormalizedEntity, record: Mapping[str, Any]) -> str:
    display = entity.display
    if not display:
        return str(record.get(_primary_key(entity), ""))
    if "{" not in display:
        return _safe_display_value(record.get(display))
    values = {name: _safe_display_value(value) for name, value in record.items()}
    try:
        return display.format_map(values)
    except (KeyError, ValueError):
        return str(record.get(_primary_key(entity), ""))


def _safe_display_value(value: Any) -> str:
    if value is PROTECTED:
        return "Protected"
    return "" if value is None else str(value)


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").title()
