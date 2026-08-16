"""First metadata-driven Textual browse adapter."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, TypeVar

from textual import events, work
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DataTable, Footer, Header, Input, Select, Static

from tide.labels import value_caption, value_label
from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
from tide.data import FilterCondition, QuerySpec, SortField
from tide.presentation import (
    browse_columns,
    application_navigation,
    field_label,
    browse_named_filters,
    browse_search_field,
    browse_sortable_fields,
    record_label,
)
from tide.runtime import DeleteRestricted, RequestContext, TideRuntimeError
from tide.reporting import ReportService
from tide.security import PROTECTED
from tide.tui.table import table_cell, table_label
from tide.services import (
    ActionService,
    AuditHistoryReader,
    AuditHistoryService,
    RecordsService,
)
from tide.tui.audit import AuditHistoryScreen
from tide.tui.confirm import DeleteConfirmationScreen
from tide.tui.form import ReopenRecordEdit, RecordEditScreen, select_form_view
from tide.tui.report import ReportParametersScreen, ReportPreviewScreen


_CACHE_MISS = object()

_WidgetT = TypeVar("_WidgetT", bound=Widget)


class BrowseDataTable(DataTable[Any]):
    """Data table that announces when scrolling approaches loaded content."""

    class NearEnd(Message):
        def __init__(self, table: BrowseDataTable) -> None:
            super().__init__()
            self.table = table

        @property
        def control(self) -> BrowseDataTable:
            return self.table

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self.is_mounted and self.max_scroll_y - new_value <= 2:
            self.post_message(self.NearEnd(self))


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

    #browse-query {
        height: 3;
        padding: 0 1;
    }

    #browse-view {
        width: 22;
        margin-right: 1;
    }

    #search-query {
        width: 1fr;
    }

    #named-filter {
        width: 26;
        margin-left: 1;
    }

    #sort-field {
        width: 22;
        margin-left: 1;
    }

    #sort-direction, #clear-query {
        min-width: 10;
        margin-left: 1;
    }

    #browse-toolbar Button {
        min-width: 9;
        margin: 0;
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

    Screen.compact-terminal #browse-view,
    Screen.compact-terminal #named-filter,
    Screen.compact-terminal #sort-field,
    Screen.compact-terminal #sort-direction {
        display: none;
    }

    Screen.compact-terminal #browse-toolbar {
        padding: 0;
        align-horizontal: left;
    }

    Screen.compact-terminal #browse-toolbar Button {
        min-width: 8;
        margin: 0;
    }

    Screen.compact-terminal #clear-query {
        min-width: 8;
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("f", "focus_filter", "Filter"),
        Binding("d", "toggle_sort_direction", "Direction"),
        Binding("c", "create_record", "Create"),
        Binding("e", "edit_record", "Edit"),
        Binding("delete", "delete_record", "Delete"),
        Binding("r", "reload", "Refresh"),
        Binding("v", "preview_report", "Preview"),
        Binding("s", "summary_report", "Summary"),
        Binding("h", "audit_history", "History"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        model: ApplicationModel,
        records: RecordsService,
        context: RequestContext,
        *,
        actions: ActionService | None = None,
        audit_history: AuditHistoryReader | None = None,
        view_name: str | None = None,
        page_size: int | None = None,
        source_label: str = "in-memory",
        report_service: ReportService | None = None,
        report_output_directory: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.records = records
        self.actions = actions or ActionService(model, records)
        execution_store = getattr(self.actions, "execution_store", None)
        self.audit_history = audit_history or (
            AuditHistoryService(model, execution_store, records.security)
            if execution_store is not None
            else None
        )
        self.report_service = report_service or ReportService(model, records)
        self.report_output_directory = Path(
            report_output_directory or Path.cwd() / "output" / "reports"
        )
        self.context = context
        accessible_views = tuple(
            view
            for view in model.views.values()
            if view.kind == "browse"
            and self.records.security.can_access_entity(
                model.entity(view.entity),
                "list",
                context,
            )
        )
        self.view = _select_browse_view(model, view_name)
        if self.view not in accessible_views:
            raise ValueError(f"TUI view {self.view.name!r} is not accessible")
        navigation = application_navigation(
            model,
            (view.name for view in accessible_views),
            include_views=(self.view.name,),
        )
        self.browse_views = tuple(
            model.views[item.view]
            for group in navigation
            for item in group.items
        )
        self._browse_view_labels = {
            item.view: item.label
            for group in navigation
            for item in group.items
        }
        self._page_size_override = page_size
        self._configure_browse_view(self.view)
        self.source_label = source_label
        self.title = model.name
        self.sub_title = self.entity.label
        self._next_cursor: str | None = None
        self._current_records: tuple[dict[str, Any], ...] = ()
        self._reference_cache: dict[tuple[str, Any], str] = {}
        self._reference_cache_lock = Lock()
        self._query_generation = 0
        self._query_started = False
        self._query_loading = False
        self._query_error: str | None = None
        self._search_text = ""
        self._filter_name: str | None = None
        self._sort_field: str | None = None
        self._sort_descending = False
        self._syncing_query_controls = False

    @property
    def current_records(self) -> tuple[dict[str, Any], ...]:
        return self._current_records

    def compose(self) -> ComposeResult:
        roles = ", ".join(sorted(self.context.principal.roles)) or "no role"
        yield Header(show_clock=False)
        yield Static(
            self._context_text(roles),
            id="browse-context",
        )
        with Horizontal(id="browse-query"):
            yield Select(
                tuple(
                    (self._browse_view_labels[view.name], view.name)
                    for view in self.browse_views
                ),
                value=self.view.name,
                allow_blank=False,
                id="browse-view",
            )
            yield Input(
                placeholder=(
                    f"Search {_field_label(self.entity.field(self.search_field))}…"
                    if self.search_field is not None
                    else "Search is not configured"
                ),
                id="search-query",
                disabled=self.search_field is None,
            )
            yield Select(
                tuple(
                    (filter_data.label, filter_name)
                    for filter_name, filter_data in self.named_filters.items()
                ),
                prompt="All records",
                allow_blank=True,
                id="named-filter",
                disabled=not bool(self.named_filters),
            )
            yield Select(
                tuple(
                    (_field_label(self.entity.field(name)), name)
                    for name in self.sort_fields
                ),
                prompt="Default order",
                allow_blank=True,
                id="sort-field",
            )
            yield Button("↑ Asc", id="sort-direction", disabled=True)
            yield Button("Clear", id="clear-query")
        with Horizontal(id="browse-toolbar"):
            yield Button(
                "New",
                id="create-record",
                disabled=not self._create_allowed,
                variant="success",
            )
            yield Button("Edit", id="edit-record", disabled=True)
            delete_button = Button(
                "Delete",
                id="delete-record",
                disabled=True,
                variant="error",
            )
            delete_button.display = self._delete_allowed
            yield delete_button
            yield Button("Preview", id="preview-report", disabled=True)
            yield Button("Summary", id="summary-report", disabled=True)
            history_button = Button("History", id="audit-history", disabled=True)
            history_button.display = self._audit_allowed
            yield history_button
            yield Button("Refresh", id="refresh-page")
            yield Button("Quit", id="quit-app")
        yield BrowseDataTable(id="records")
        yield Static("Loading…", id="browse-status")
        yield Footer()

    def on_mount(self) -> None:
        self._sync_terminal_layout(self.size.width)
        table = self.query_one("#records", DataTable)
        self._add_table_columns(table)
        table.cursor_type = "row"
        table.zebra_stripes = bool(
            self.view.data.get("settings", {}).get("zebra_stripes", True)
        )
        table.focus()
        self._restart_query()

    def on_resize(self, event: events.Resize) -> None:
        self._sync_terminal_layout(event.size.width)
        self.call_after_refresh(self._prefetch_if_near_end)

    def _sync_terminal_layout(self, width: int) -> None:
        self.screen.set_class(width < 100, "compact-terminal")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-page":
            self.action_reload()
        elif event.button.id == "edit-record":
            self.action_edit_record()
        elif event.button.id == "delete-record":
            self.action_delete_record()
        elif event.button.id == "preview-report":
            self.action_preview_report()
        elif event.button.id == "summary-report":
            self.action_summary_report()
        elif event.button.id == "audit-history":
            self.action_audit_history()
        elif event.button.id == "create-record":
            self.action_create_record()
        elif event.button.id == "sort-direction":
            self.action_toggle_sort_direction()
        elif event.button.id == "clear-query":
            self.action_clear_query()
        elif event.button.id == "quit-app":
            self.exit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-query" or self._syncing_query_controls:
            return
        value = event.value.strip()
        if value == self._search_text:
            return
        self._search_text = value
        self._restart_query()

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._syncing_query_controls:
            return
        value = None if event.value is Select.NULL else str(event.value)
        if event.select.id == "browse-view" and value != self.view.name:
            self._activate_browse_view(value)
        elif event.select.id == "named-filter" and value != self._filter_name:
            self._filter_name = value
            self._restart_query()
        elif event.select.id == "sort-field" and value != self._sort_field:
            self._sort_field = value
            self._sort_descending = False
            self._update_sort_controls()
            self._restart_query()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        field_name = str(event.column_key.value)
        if field_name not in self.sort_fields:
            return
        if field_name == self._sort_field:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_field = field_name
            self._sort_descending = False
        self._sync_query_controls()
        self._update_sort_controls()
        self._restart_query()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        record = self._record_for_row_key(str(event.row_key.value))
        if record is None:
            return
        self.open_record(record[_primary_key(self.entity)])

    def on_data_table_row_highlighted(
        self,
        event: DataTable.RowHighlighted,
    ) -> None:
        if event.data_table.id != "records":
            return
        if event.cursor_row >= len(self._current_records) - 3:
            self._request_next_batch()

    def on_browse_data_table_near_end(
        self,
        event: BrowseDataTable.NearEnd,
    ) -> None:
        if event.control.id == "records":
            event.stop()
            self._request_next_batch()

    def action_edit_record(self) -> None:
        table = self.query_one("#records", DataTable)
        if not self._current_records or table.cursor_row < 0:
            return
        primary_key = _primary_key(self.entity)
        self.open_record(self._current_records[table.cursor_row][primary_key])

    def action_create_record(self) -> None:
        if self.form_view is None:
            self._notify_missing_form()
            return
        try:
            session = self.records.create(self.entity.name, self.context)
        except TideRuntimeError as error:
            self.notify(str(error), severity="error")
            return
        self._open_form(session)

    def action_delete_record(self) -> None:
        record = self._selected_record()
        if record is None or not self._delete_allowed:
            return
        record_title = _display_record(self.entity, record)
        if self._confirm_delete:
            self.push_screen(
                DeleteConfirmationScreen(
                    _record_label(self.entity), record_title
                ),
                lambda confirmed: self._delete_record(record) if confirmed else None,
            )
            return
        self._delete_record(record)

    def _delete_record(self, record: Mapping[str, Any]) -> None:
        identity = record[_primary_key(self.entity)]
        version_field = _version_field(self.entity)
        expected_version = (
            record.get(version_field) if version_field is not None else None
        )
        if expected_version is PROTECTED:
            expected_version = None
        try:
            self.records.delete(
                self.entity.name,
                identity,
                self.context,
                expected_version=expected_version,
            )
        except DeleteRestricted as error:
            self.notify(
                self._delete_restricted_message(record, error),
                severity="warning",
            )
            return
        except TideRuntimeError as error:
            self.notify(f"Delete failed: {error}", severity="error")
            return
        self.notify(
            f"{_display_record(self.entity, record)} deleted.",
            severity="information",
        )
        self._restart_query()

    def _delete_restricted_message(
        self,
        record: Mapping[str, Any],
        error: DeleteRestricted,
    ) -> str:
        reason = "it is referenced by another record"
        if error.relationship and "." in error.relationship:
            source_name, field_name = error.relationship.rsplit(".", 1)
            source = self.model.entities.get(source_name)
            if source is not None and field_name in source.fields:
                reason = (
                    f"it is used by {source.label} "
                    f"({_field_label(source.field(field_name))})"
                )
        return f"Cannot delete {_display_record(self.entity, record)!r}: {reason}."

    def action_preview_report(self) -> None:
        report_name = self._active_report("record")
        table = self.query_one("#records", DataTable)
        if report_name is None or not self._current_records or table.cursor_row < 0:
            return
        identity = self._current_records[table.cursor_row][_primary_key(self.entity)]
        try:
            document = self.report_service.build_for_record(
                report_name,
                identity,
                self.context,
            )
        except (TideRuntimeError, ValueError) as error:
            self.notify(f"Report preview failed: {error}", severity="error")
            return
        self.push_screen(
            ReportPreviewScreen(document, self.report_output_directory)
        )

    def action_summary_report(self) -> None:
        report_name = self._active_report("summary")
        if report_name is None:
            return
        report = self.model.reports.get(report_name, {})
        definitions = report.get("parameters", {})
        if definitions:
            def build(parameters: dict[str, Any] | None) -> None:
                if parameters is not None:
                    self._open_summary_report(report_name, parameters)

            self.push_screen(
                ReportParametersScreen(
                    str(report.get("title") or report_name),
                    definitions,
                ),
                build,
            )
            return
        self._open_summary_report(report_name, {})

    def _open_summary_report(
        self,
        report_name: str,
        parameters: dict[str, Any],
    ) -> None:
        """Build and show one summary, telling the user what refused and why.

        The inputs arrive as strings and the report service does the typing,
        so a wrong date lands here as the same `ValidationFailed` every other
        surface gets, worded by the one place that owns the rule.
        """

        try:
            document = self.report_service.build(
                report_name,
                parameters,
                self.context,
            )
        except (TideRuntimeError, ValueError) as error:
            self.notify(f"Summary report failed: {error}", severity="error")
            return
        self.push_screen(
            ReportPreviewScreen(document, self.report_output_directory)
        )

    def action_audit_history(self) -> None:
        record = self._selected_record()
        if record is None or not self._audit_allowed or self.audit_history is None:
            return
        identity = record[_primary_key(self.entity)]
        try:
            events = self.audit_history.for_record(
                self.entity.name,
                identity,
                self.context,
            )
        except (TideRuntimeError, ValueError) as error:
            self.notify(f"Audit history failed: {error}", severity="error")
            return
        self.push_screen(
            AuditHistoryScreen(
                self.model.name,
                _display_record(self.entity, record),
                events,
            )
        )

    def open_record(self, identity: Any) -> None:
        if self.form_view is None:
            self._notify_missing_form()
            return
        try:
            session = self.records.begin_edit(
                self.entity.name,
                identity,
                self.context,
            )
        except TideRuntimeError as error:
            self.notify(str(error), severity="error")
            return
        self._open_form(session)

    def _open_form(self, session: Any) -> None:
        self.push_screen(
            RecordEditScreen(
                self.model,
                self.records,
                self.actions,
                self.context,
                self.form_view,
                session,
            ),
            self._record_form_closed,
        )

    def _notify_missing_form(self) -> None:
        self.notify(
            f"No form view is defined for {self.entity.name}.",
            severity="warning",
        )

    def _record_form_closed(self, result: bool | ReopenRecordEdit | None) -> None:
        if isinstance(result, ReopenRecordEdit):
            self.action_reload()
            self.notify(result.message, severity="warning")
            self.call_later(self._open_form, result.session)
            return
        if result:
            self.action_reload()

    def action_reload(self) -> None:
        self._restart_query()

    def action_focus_search(self) -> None:
        search = self.query_one("#search-query", Input)
        if not search.disabled:
            search.focus()

    def action_focus_filter(self) -> None:
        filter_select = self.query_one("#named-filter", Select)
        if not filter_select.disabled:
            filter_select.focus()

    def action_toggle_sort_direction(self) -> None:
        if self._sort_field is None:
            return
        self._sort_descending = not self._sort_descending
        self._update_sort_controls()
        self._restart_query()

    def action_clear_query(self) -> None:
        self._search_text = ""
        self._filter_name = None
        self._sort_field = None
        self._sort_descending = False
        self._sync_query_controls()
        self._update_sort_controls()
        self._restart_query()

    def _restart_query(self) -> None:
        self._query_generation += 1
        self._next_cursor = None
        self._current_records = ()
        with self._reference_cache_lock:
            self._reference_cache.clear()
        self._query_started = False
        self._query_loading = False
        self._query_error = None
        self.query_one("#records", DataTable).clear()
        self._update_record_controls()
        self._request_next_batch()

    def _configure_browse_view(self, view: ResolvedView) -> None:
        self.view = view
        self.entity = self.model.entity(view.entity)
        self.form_view = select_form_view(self.model, self.entity.name)
        self.search_field = browse_search_field(view, self.entity)
        self.named_filters = browse_named_filters(view)
        self.columns = _browse_columns(view, self.entity)
        self.sort_fields = browse_sortable_fields(self.columns, self.entity)
        self._audit_allowed = bool(
            self.audit_history is not None
            and self.audit_history.can_view(self.entity.name, self.context)
        )
        self._create_allowed = bool(
            self.form_view is not None
            and self.records.security.can_access_entity(
                self.entity,
                "create",
                self.context,
            )
        )
        self._edit_allowed = bool(
            self.form_view is not None
            and self.records.security.can_access_entity(
                self.entity,
                "update",
                self.context,
            )
        )
        settings = view.data.get("settings", {})
        browse_actions = tuple(str(action) for action in settings.get("actions", ()))
        self._delete_allowed = bool(
            "delete" in browse_actions
            and self.records.security.can_access_entity(
                self.entity,
                "delete",
                self.context,
            )
        )
        self._confirm_delete = bool(settings.get("confirm_delete", True))
        configured_page_size = int(view.data.get("settings", {}).get("page_size", 25))
        self.page_size = (
            self._page_size_override
            if self._page_size_override is not None
            else configured_page_size
        )
        if self.page_size < 1 or self.page_size > 500:
            raise ValueError("TUI browse batch size must be between 1 and 500")

    def _activate_browse_view(self, view_name: str | None) -> None:
        view = next(
            (candidate for candidate in self.browse_views if candidate.name == view_name),
            None,
        )
        if view is None:
            self.notify(f"Workspace {view_name!r} is not accessible.", severity="error")
            return
        self._configure_browse_view(view)
        self.sub_title = self.entity.label
        self._search_text = ""
        self._filter_name = None
        self._sort_field = None
        self._sort_descending = False

        self._syncing_query_controls = True
        try:
            with self.prevent(Input.Changed, Select.Changed):
                roles = ", ".join(sorted(self.context.principal.roles)) or "no role"
                self.query_one("#browse-context", Static).update(
                    self._context_text(roles)
                )
                search = self.query_one("#search-query", Input)
                search.value = ""
                search.placeholder = (
                    f"Search {_field_label(self.entity.field(self.search_field))}…"
                    if self.search_field is not None
                    else "Search is not configured"
                )
                search.disabled = self.search_field is None
                filters = self.query_one("#named-filter", Select)
                filters.set_options(
                    tuple(
                        (data.label, name)
                        for name, data in self.named_filters.items()
                    )
                )
                filters.value = Select.NULL
                filters.disabled = not bool(self.named_filters)
                sorts = self.query_one("#sort-field", Select)
                sorts.set_options(
                    tuple(
                        (_field_label(self.entity.field(name)), name)
                        for name in self.sort_fields
                    )
                )
                sorts.value = Select.NULL
            self.query_one("#create-record", Button).disabled = not self._create_allowed
            self.query_one("#edit-record", Button).disabled = True
            delete_button = self.query_one("#delete-record", Button)
            delete_button.display = self._delete_allowed
            delete_button.disabled = True
            history_button = self.query_one("#audit-history", Button)
            history_button.display = self._audit_allowed
            history_button.disabled = True
            table = self.query_one("#records", DataTable)
            table.clear(columns=True)
            self._add_table_columns(table)
        finally:
            self._syncing_query_controls = False
        self._update_sort_controls()
        self._restart_query()

    def _add_table_columns(self, table: DataTable[Any]) -> None:
        for field_name in self.columns:
            field = self.entity.field(field_name)
            configuration = self.view.data.get("fields", {}).get(field_name, {})
            configured_width = configuration.get("width")
            table.add_column(
                table_label(field, _field_label(field), self.model.formats),
                key=field_name,
                width=(
                    int(configured_width)
                    if isinstance(configured_width, int) and configured_width > 0
                    else None
                ),
            )

    def _context_text(self, roles: str) -> str:
        return f"{self.view.name}  ·  {self.context.principal.identifier}  ·  {roles}"

    def _request_next_batch(self) -> None:
        if (
            self._query_loading
            or self._query_error is not None
            or (self._query_started and self._next_cursor is None)
        ):
            return
        generation = self._query_generation
        cursor = self._next_cursor
        query = QuerySpec(
            filters=self._query_filters(),
            sort=(
                (
                    SortField(
                        self._sort_field,
                        descending=self._sort_descending,
                    ),
                )
                if self._sort_field is not None
                else ()
            ),
            limit=self.page_size,
            cursor=cursor,
        )
        self._query_loading = True
        self._update_browse_status()
        self._load_batch(
            generation,
            cursor,
            self.entity,
            self.columns,
            query,
        )

    @work(
        group="browse-query",
        exclusive=True,
        exit_on_error=False,
    )
    async def _load_batch(
        self,
        generation: int,
        cursor: str | None,
        entity: NormalizedEntity,
        columns: tuple[str, ...],
        query: QuerySpec,
    ) -> None:
        try:
            page, rendered_rows = await asyncio.to_thread(
                self._query_and_format_batch,
                entity,
                columns,
                query,
            )
        except Exception as error:
            if generation == self._query_generation:
                self._apply_load_error(error)
            return
        if generation != self._query_generation:
            return
        if page.next_cursor is not None and page.next_cursor == cursor:
            self._apply_load_error(
                ValueError("server repeated the current continuation cursor")
            )
            return
        if not page.records and page.next_cursor is not None:
            self._apply_load_error(
                ValueError("server returned an empty continuation batch")
            )
            return
        table = self._browse_widget("#records", DataTable)
        if table is None:
            # The browse screen went away while this batch was in flight; that
            # is not a load failure and must not leave an error on the status.
            return
        try:
            for row_key, cells in rendered_rows:
                table.add_row(*cells, key=row_key)
        except Exception as error:
            self._apply_load_error(error)
            return
        self._current_records += page.records
        self._query_started = True
        self._next_cursor = page.next_cursor
        self._query_loading = False
        self._query_error = None
        self._update_record_controls()
        self._update_browse_status()
        table.refresh(layout=True)
        self.call_after_refresh(self._prefetch_if_near_end)

    def _query_and_format_batch(
        self,
        entity: NormalizedEntity,
        columns: tuple[str, ...],
        query: QuerySpec,
    ) -> tuple[Any, tuple[tuple[str, tuple[Any, ...]], ...]]:
        page = self.records.query_page(
            entity.name,
            query,
            self.context,
        )
        # The page already knows how it names what it points at. Taking those
        # first means the reference columns below cost nothing to draw; a page
        # that resolved none leaves each cell to fetch as it always did.
        with self._reference_cache_lock:
            self._reference_cache.update(page.references.entries)
        primary_key = _primary_key(entity)
        rendered_rows = tuple(
            (
                str(record[primary_key]),
                tuple(
                    table_cell(
                        entity.field(field_name),
                        self._format_value(entity.field(field_name), record),
                        self.model.formats,
                    )
                    for field_name in columns
                ),
            )
            for record in page.records
        )
        return page, rendered_rows

    def _apply_load_error(self, error: Exception) -> None:
        self._query_loading = False
        self._query_error = str(error)
        self._update_record_controls()
        self._update_browse_status()

    def _browse_widget(
        self,
        selector: str,
        widget_type: type[_WidgetT],
    ) -> _WidgetT | None:
        """Return a browse widget, or ``None`` while the screen has none.

        Browse batches are delivered from a worker thread, so an update can run
        before compose mounts these widgets and after teardown removes them.
        ``App.is_mounted`` takes a widget argument rather than answering that
        question, so ask the DOM and accept absence as an ordinary outcome.
        """

        try:
            return self.query_one(selector, widget_type)
        except (NoMatches, ScreenStackError):
            return None

    def _update_record_controls(self) -> None:
        edit = self._browse_widget("#edit-record", Button)
        delete = self._browse_widget("#delete-record", Button)
        audit = self._browse_widget("#audit-history", Button)
        if edit is None or delete is None or audit is None:
            return
        has_records = bool(self._current_records)
        edit.disabled = not (has_records and self._edit_allowed)
        delete.disabled = not (has_records and self._delete_allowed)
        audit.disabled = not (has_records and self._audit_allowed)
        self._update_report_control(has_records)

    def _update_browse_status(self) -> None:
        status = self._browse_widget("#browse-status", Static)
        if status is None:
            return
        count = len(self._current_records)
        noun = "record" if count == 1 else "records"
        if self._query_loading:
            state = "Loading more records…" if count else "Loading records…"
        elif self._query_error is not None:
            state = f"Loading failed: {self._query_error}  ·  R reload"
        elif self._next_cursor is not None:
            state = "Scroll for more"
        else:
            state = "All available records loaded"
        status.update(
            f"{count} {noun} loaded  ·  {state}  ·  "
            f"{self.source_label}{self._query_summary()}  ·  "
            "C create  E edit  V preview  R refresh"
            + (
                "  S summary"
                if self._active_report("summary") is not None
                else ""
            )
            + ("  Del delete" if self._delete_allowed else "")
            + ("  H history" if self._audit_allowed else "")
        )

    def _prefetch_if_near_end(self) -> None:
        table = self._browse_widget("#records", DataTable)
        if table is None:
            return
        if table.max_scroll_y - table.scroll_y <= 2:
            self._request_next_batch()

    def _active_report(self, kind: str) -> str | None:
        return next(
            (
                name
                for name, report in self.model.reports.items()
                if report["entity"] == self.entity.name
                and report.get("kind", "record") == kind
                and self.report_service.can_generate(name, self.context)
            ),
            None,
        )

    def _update_report_control(self, has_records: bool) -> None:
        buttons = list(self.query("#preview-report"))
        if not buttons:
            return
        report_name = self._active_report("record")
        buttons[0].display = report_name is not None
        buttons[0].disabled = report_name is None or not has_records
        summary_buttons = list(self.query("#summary-report"))
        if summary_buttons:
            summary_name = self._active_report("summary")
            summary_buttons[0].display = summary_name is not None
            summary_buttons[0].disabled = summary_name is None

    def _query_filters(self) -> tuple[FilterCondition, ...]:
        filters: list[FilterCondition] = []
        if self._search_text and self.search_field is not None:
            filters.append(
                FilterCondition(self.search_field, "contains", self._search_text)
            )
        if self._filter_name is not None:
            filters.extend(self.named_filters[self._filter_name].conditions)
        return tuple(filters)

    def _query_summary(self) -> str:
        parts: list[str] = []
        if self._search_text:
            parts.append(f"search {self._search_text!r}")
        if self._filter_name is not None:
            parts.append(self.named_filters[self._filter_name].label)
        if self._sort_field is not None:
            direction = "descending" if self._sort_descending else "ascending"
            parts.append(
                f"{_field_label(self.entity.field(self._sort_field))} {direction}"
            )
        return f"  ·  {'  ·  '.join(parts)}" if parts else ""

    def _sync_query_controls(self) -> None:
        self._syncing_query_controls = True
        try:
            with self.prevent(Input.Changed, Select.Changed):
                self.query_one("#search-query", Input).value = self._search_text
                self.query_one("#named-filter", Select).value = (
                    self._filter_name
                    if self._filter_name is not None
                    else Select.NULL
                )
                self.query_one("#sort-field", Select).value = (
                    self._sort_field
                    if self._sort_field is not None
                    else Select.NULL
                )
        finally:
            self._syncing_query_controls = False

    def _update_sort_controls(self) -> None:
        button = self.query_one("#sort-direction", Button)
        button.disabled = self._sort_field is None
        button.label = "↓ Desc" if self._sort_descending else "↑ Asc"
        table = self.query_one("#records", DataTable)
        for index, field_name in enumerate(self.columns):
            indicator = ""
            if field_name == self._sort_field:
                indicator = " ↓" if self._sort_descending else " ↑"
            field = self.entity.field(field_name)
            table.ordered_columns[index].label = table_label(
                field,
                f"{_field_label(field)}{indicator}",
                self.model.formats,
            )
            table.refresh_column(index)
        table.refresh(layout=True)

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
        caption = value_caption(field.metadata, value)
        if caption is not None:
            return caption
        if field.metadata["type"] == "choice":
            return value_label(value)
        return str(value)

    def _reference_display(self, target_entity: str, identity: Any) -> str:
        cache_key = target_entity, identity
        with self._reference_cache_lock:
            cached = self._reference_cache.get(cache_key, _CACHE_MISS)
        if cached is not _CACHE_MISS:
            return cached
        try:
            related = self.records.get(target_entity, identity, self.context)
            result = _display_record(self.model.entity(target_entity), related)
        except TideRuntimeError:
            result = "Protected"
        with self._reference_cache_lock:
            return self._reference_cache.setdefault(cache_key, result)

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

    def _selected_record(self) -> dict[str, Any] | None:
        table = self.query_one("#records", DataTable)
        if not self._current_records or table.cursor_row < 0:
            return None
        if table.cursor_row >= len(self._current_records):
            return None
        return self._current_records[table.cursor_row]

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
    browse_views = tuple(
        candidate for candidate in model.views.values() if candidate.kind == "browse"
    )
    view = next(
        (
            candidate
            for candidate in browse_views
            if candidate.data.get("settings", {}).get("default") is True
        ),
        browse_views[0] if browse_views else None,
    )
    if view is None:
        raise ValueError("application does not define a browse view")
    return view


_browse_columns = browse_columns


def _primary_key(entity: NormalizedEntity) -> str:
    return entity.primary_key.name


def _version_field(entity: NormalizedEntity) -> str | None:
    field = entity.version_field
    return None if field is None else field.name


_field_label = field_label
_record_label = record_label


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


