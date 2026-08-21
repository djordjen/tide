"""The Studio shell: a document tree, a property editor, and the screens that change a view."""

from __future__ import annotations

import re
from typing import Literal, NamedTuple, TypeVar

from textual import events
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Select,
    Static,
    TextArea,
    Tree,
)
from textual.widget import Widget
from textual.widgets.tree import TreeNode

from tide.tui.header import TideHeader
from tide.development.designer import DesignerDocumentReference
from tide.development.studio import (
    StudioDocumentDetails,
    StudioError,
    StudioProperty,
    StudioSaveReview,
    StudioService,
    StudioSessionState,
    StudioViewField,
    StudioViewStructure,
)

from .groups import StudioGroupEdit, StudioGroupsScreen
from .layout import StudioLayoutEdit, StudioLayoutScreen
from .preview import StudioPreviewScreen
from .save import StudioSaveScreen


_WidgetT = TypeVar("_WidgetT", bound=Widget)


class ViewFieldColumn(NamedTuple):
    """One column of the view-structure table, with the width its data needs."""

    label: str
    key: str
    width: int


# Priority order, most useful first. The field name is what the table is for.
# The position says what Move up and Move down will do. Type and origin are
# the resolved information no single source file holds -- `view overlay`
# against an entity default is the reason to read this panel instead of the
# YAML -- and the label is derivable from the name, so it goes first.
#
# `Track` is deliberately absent. It repeated identically down each contiguous
# run, spent 22 columns doing so, and was truncated even at the widest
# certified terminal: `Line editor · right column / Line details` is 41
# characters. It is the heading beside the table now.
_VIEW_FIELD_COLUMNS = (
    ViewFieldColumn("Field", "field", 14),
    ViewFieldColumn("#", "position", 3),
    ViewFieldColumn("Type", "type", 10),
    ViewFieldColumn("Origin", "origin", 12),
    ViewFieldColumn("Label", "label", 14),
)

# `DataTable` pads every cell by one cell on each side.
_CELL_PADDING = 2

# Side by side, the view-structure panel wants the 34-column document tree, a
# 48-column side panel, and enough left over for the field table to be worth
# reading. Narrower than this the two stack instead. `browse` uses its own
# threshold in `tui/app.py`: the number belongs to a layout, not to a
# terminal, and these are different layouts.
_SIDE_BY_SIDE_MINIMUM_WIDTH = 125

_UNLAID_RETRY_LIMIT = 4
"""How many refreshes the column fit may wait for a width before giving up.

Four rather than one because the panel is shown, populated and measured across
separate refreshes, and rather than unbounded because a details pane that is
genuinely zero-wide would otherwise reschedule for as long as it is on screen.
"""


def view_field_columns(available: int) -> tuple[ViewFieldColumn, ...]:
    """Return the columns that fit `available` cells, most useful first.

    Always at least one. A `DataTable` with no columns renders no rows at all,
    so a terminal too narrow even for the field name is better served by a
    truncated name than by a panel that silently empties.
    """

    chosen = [_VIEW_FIELD_COLUMNS[0]]
    used = _VIEW_FIELD_COLUMNS[0].width + _CELL_PADDING
    for column in _VIEW_FIELD_COLUMNS[1:]:
        cost = column.width + _CELL_PADDING
        if used + cost > available:
            break
        used += cost
        chosen.append(column)
    return tuple(chosen)


class StudioApp(App[None]):
    """Edit an in-memory metadata candidate without opening a database."""

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

    #studio-context {
        height: 3;
        padding: 0 2;
        color: $text-muted;
        content-align: left middle;
    }

    #studio-workspace {
        height: 1fr;
        margin: 0 1;
    }

    #studio-navigation {
        width: 34;
        min-width: 24;
        margin-right: 1;
        border: round $primary;
    }

    #studio-tree {
        height: 1fr;
        padding: 0 1;
    }

    #studio-details {
        width: 1fr;
        overflow-y: auto;
    }

    .panel-title {
        height: 2;
        padding: 0 1;
        color: $text-muted;
        content-align: left middle;
    }

    #property-table {
        height: 9;
        border: round $primary;
    }

    #view-structure {
        display: none;
        height: 22;
        margin: 0 1;
    }

    #view-field-table {
        width: 1fr;
        border: round $accent;
    }

    #view-structure-side {
        width: 48;
        margin-left: 1;
        border: round $accent;
    }

    /* A 48-column side panel beside a `1fr` table leaves the table ten cells
       in a 63-wide details pane. Below the width where both fit, they stack
       and the table gets the full pane. */
    Screen.compact-terminal #view-structure {
        layout: vertical;
        height: auto;
    }

    Screen.compact-terminal #view-field-table {
        height: 10;
    }

    Screen.compact-terminal #view-structure-side {
        width: 1fr;
        height: auto;
        margin-left: 0;
        margin-top: 1;
    }

    Screen.compact-terminal #view-structure-preview {
        height: auto;
        min-height: 4;
    }

    #view-structure-title {
        height: 2;
        padding: 0 1;
        color: $accent;
        text-style: bold;
    }

    #view-structure-preview {
        height: 1fr;
        padding: 0 1;
        color: $text-muted;
    }

    #view-field-add-choice, #view-field-group-choice {
        height: 3;
        margin: 0 1;
    }

    #view-structure-move-actions, #view-structure-edit-actions,
    #view-structure-presentation-actions {
        height: 3;
        align-horizontal: right;
        padding: 0 1;
    }

    #view-structure-move-actions Button, #view-structure-edit-actions Button,
    #view-structure-presentation-actions Button {
        min-width: 9;
        margin-left: 1;
    }

    #property-editor, #studio-toolbar {
        height: 3;
        padding: 0 1;
    }

    /* Docked, so it keeps its place when the details pane scrolls. Selecting
       a view adds a structure panel that pushed this off the bottom of every
       certified terminal, and `Diagnostics` and `Edit YAML` are reachable
       nowhere else. */
    #studio-toolbar {
        dock: bottom;
    }

    #property-value {
        width: 1fr;
    }

    #property-choice {
        width: 1fr;
        display: none;
    }

    #property-editor Button, #studio-toolbar Button {
        min-width: 10;
        margin-left: 1;
    }

    #apply-source, #cancel-source {
        display: none;
    }

    #source-preview {
        height: 1fr;
        min-height: 10;
        border: round $primary;
    }

    #source-search {
        display: none;
        height: 3;
        padding: 0 1;
    }

    #source-search-query {
        width: 1fr;
    }

    #source-search Button {
        min-width: 8;
        margin-left: 1;
    }

    #source-search-status {
        width: 12;
        margin-left: 1;
        content-align: center middle;
        color: $text-muted;
    }

    #studio-status {
        height: 2;
        padding: 0 2;
        color: $text-muted;
        content-align: left middle;
    }
    """

    BINDINGS = [
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo"),
        Binding("ctrl+d", "show_changes", "Changes"),
        Binding("ctrl+f", "focus_source_search", "Find"),
        Binding("ctrl+s", "save_or_apply", "Save"),
        Binding("escape", "cancel_source_edit", "Cancel YAML", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, service: StudioService) -> None:
        super().__init__()
        self.service = service
        self.state = service.state
        self.workspace = service.workspace
        self.selected_target: DesignerDocumentReference | None = None
        self.document_details: StudioDocumentDetails | None = None
        self.selected_property: StudioProperty | None = None
        self.title = "TIDE Studio"
        self.sub_title = self.workspace.application
        self._first_document_node: TreeNode[DesignerDocumentReference] | None = None
        self._property_rows: dict[str, StudioProperty] = {}
        self.view_structure: StudioViewStructure | None = None
        self.selected_view_field: StudioViewField | None = None
        self._view_field_rows: dict[str, StudioViewField] = {}
        self._selected_view_field_key: str | None = None
        self._selected_view_group_label: str | None = None
        self._preview_mode = "source"
        self._search_matches: list[tuple[tuple[int, int], tuple[int, int]]] = []
        self._search_match_index = -1
        self._source_editing = False
        self._view_field_relayouts = 0

    def compose(self) -> ComposeResult:
        yield TideHeader()
        yield Static(self._context_text(self.state), id="studio-context")
        with Horizontal(id="studio-workspace"):
            with Vertical(id="studio-navigation"):
                yield Static("Application model", classes="panel-title")
                yield Tree[DesignerDocumentReference](
                    self.workspace.application,
                    id="studio-tree",
                )
            with Vertical(id="studio-details"):
                yield Static("Properties", id="property-title", classes="panel-title")
                yield DataTable(id="property-table")
                with Horizontal(id="property-editor"):
                    yield Input(
                        placeholder="Select an editable scalar property",
                        disabled=True,
                        id="property-value",
                    )
                    yield Select[str](
                        (),
                        prompt="Select a value",
                        allow_blank=True,
                        disabled=True,
                        id="property-choice",
                    )
                    yield Button(
                        "Apply in memory",
                        id="apply-property",
                        disabled=True,
                        variant="primary",
                    )
                with Horizontal(id="view-structure"):
                    yield DataTable(id="view-field-table")
                    with Vertical(id="view-structure-side"):
                        yield Static(
                            "Resolved TUI structure",
                            id="view-structure-title",
                        )
                        yield Static("", id="view-structure-preview", markup=False)
                        yield Select[str](
                            (),
                            prompt="Choose an entity field to add",
                            allow_blank=True,
                            disabled=True,
                            id="view-field-add-choice",
                        )
                        yield Select[str](
                            (),
                            prompt="Destination layout group",
                            allow_blank=True,
                            disabled=True,
                            id="view-field-group-choice",
                        )
                        with Horizontal(id="view-structure-move-actions"):
                            yield Button("Move up", id="move-view-field-up")
                            yield Button("Move down", id="move-view-field-down")
                            yield Button("← Swap", id="move-view-field-left")
                            yield Button("Swap →", id="move-view-field-right")
                        with Horizontal(id="view-structure-edit-actions"):
                            yield Button(
                                "Add field",
                                id="add-view-field",
                                variant="primary",
                            )
                            yield Button("Remove field", id="remove-view-field")
                        with Horizontal(id="view-structure-presentation-actions"):
                            yield Button("Groups…", id="manage-view-groups")
                            yield Button("Layout…", id="manage-view-layout")
                            yield Button("Preview…", id="preview-view")
                with Horizontal(id="studio-toolbar"):
                    yield Button("Undo", id="undo-edit", disabled=True)
                    yield Button("Redo", id="redo-edit", disabled=True)
                    yield Button("YAML", id="show-source")
                    yield Button("Changes", id="show-changes", disabled=True)
                    yield Button("Diagnostics", id="show-diagnostics", disabled=True)
                    yield Button("Edit YAML", id="edit-source")
                    yield Button(
                        "Save candidate",
                        id="save-candidate",
                        variant="success",
                        disabled=True,
                    )
                    yield Button(
                        "Apply YAML",
                        id="apply-source",
                        variant="primary",
                    )
                    yield Button("Cancel edit", id="cancel-source")
                yield Static("YAML source", id="source-title", classes="panel-title")
                with Horizontal(id="source-search"):
                    yield Input(
                        placeholder="Find in current YAML, diff, or diagnostics",
                        id="source-search-query",
                    )
                    yield Button("Previous", id="search-previous", disabled=True)
                    yield Button("Next", id="search-next", disabled=True)
                    yield Button("Close", id="search-close")
                    yield Static("No query", id="source-search-status")
                yield TextArea(
                    language="yaml",
                    read_only=True,
                    show_line_numbers=True,
                    soft_wrap=False,
                    id="source-preview",
                )
        yield Static("Clean in-memory candidate", id="studio-status")
        yield Footer()

    def on_mount(self) -> None:
        properties = self.query_one("#property-table", DataTable)
        properties.add_column("Property path", key="property", width=34)
        properties.add_column("Value", key="value")
        properties.add_column("Mode", key="mode", width=10)
        properties.cursor_type = "row"
        # The view-structure columns are chosen against the width the layout
        # actually gives the table, which is not known until it is displayed.
        self.query_one("#view-field-table", DataTable).cursor_type = "row"
        self._populate_tree()
        self._select_first_document()
        self.query_one("#studio-tree", Tree).focus()

    def on_resize(self, event: events.Resize) -> None:
        self.screen.set_class(
            event.size.width < _SIDE_BY_SIDE_MINIMUM_WIDTH, "compact-terminal"
        )
        self.call_after_refresh(self._sync_view_field_columns)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-property":
            self._apply_selected_property()
        elif event.button.id == "undo-edit":
            self.action_undo()
        elif event.button.id == "redo-edit":
            self.action_redo()
        elif event.button.id == "show-source":
            self.action_show_source()
        elif event.button.id == "show-changes":
            self.action_show_changes()
        elif event.button.id == "show-diagnostics":
            self.action_show_diagnostics()
        elif event.button.id == "edit-source":
            self.action_edit_source()
        elif event.button.id == "save-candidate":
            self.action_prepare_save()
        elif event.button.id == "move-view-field-up":
            self._move_selected_view_field(-1)
        elif event.button.id == "move-view-field-down":
            self._move_selected_view_field(1)
        elif event.button.id == "move-view-field-left":
            self._move_selected_view_field_across(-1)
        elif event.button.id == "move-view-field-right":
            self._move_selected_view_field_across(1)
        elif event.button.id == "add-view-field":
            self._add_view_field()
        elif event.button.id == "remove-view-field":
            self._remove_selected_view_field()
        elif event.button.id == "manage-view-groups":
            self._manage_view_groups()
        elif event.button.id == "manage-view-layout":
            self._manage_view_layout()
        elif event.button.id == "preview-view":
            self._preview_selected_view()
        elif event.button.id == "apply-source":
            self.action_apply_source_edit()
        elif event.button.id == "cancel-source":
            self.action_cancel_source_edit()
        elif event.button.id == "search-previous":
            self.action_previous_search_match()
        elif event.button.id == "search-next":
            self.action_next_search_match()
        elif event.button.id == "search-close":
            self.action_close_source_search()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "property-value":
            self._apply_selected_property()
        elif event.input.id == "source-search-query":
            self.action_next_search_match()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "source-search-query":
            self._refresh_search()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in {
            "view-field-add-choice",
            "view-field-group-choice",
        }:
            self._sync_view_field_controls()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "property-table":
            self._select_property(str(event.row_key.value))
        elif event.data_table.id == "view-field-table":
            self._select_view_field(str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "property-table":
            return
        self._select_property(str(event.row_key.value))
        if self.selected_property is not None and self.selected_property.editable:
            if self.selected_property.editor in {"choice", "boolean"}:
                self.query_one("#property-choice", Select).focus()
            else:
                self.query_one("#property-value", Input).focus()

    def on_tree_node_highlighted(
        self,
        event: Tree.NodeHighlighted[DesignerDocumentReference],
    ) -> None:
        if event.node.data is not None and not self._source_editing:
            self._show_document(event.node.data)

    def action_refresh(self) -> None:
        if self._source_editing:
            self.notify(
                "Apply or cancel the expert YAML edit before reloading",
                severity="warning",
            )
            return
        if self.state.dirty:
            self.notify(
                "Reload would discard in-memory changes; undo them or restart Studio",
                severity="warning",
            )
            return
        self.workspace = self.service.refresh()
        self.state = self.service.state
        self.sub_title = self.workspace.application
        self.query_one("#studio-context", Static).update(self._context_text(self.state))
        self._populate_tree()
        self._select_first_document()
        self.notify("Application sources reloaded")

    def action_undo(self) -> None:
        if self._source_editing or not self.state.can_undo:
            return
        self.state = self.service.undo()
        self._after_edit("Last in-memory edit undone")

    def action_redo(self) -> None:
        if self._source_editing or not self.state.can_redo:
            return
        self.state = self.service.redo()
        self._after_edit("Last in-memory edit restored")

    def action_show_source(self) -> None:
        if self._source_editing:
            return
        self._preview_mode = "source"
        self._update_preview()
        self._update_controls()

    def action_show_changes(self) -> None:
        if self._source_editing:
            return
        self._preview_mode = "changes"
        self._update_preview()
        self._update_controls()

    def action_show_diagnostics(self) -> None:
        if self._source_editing:
            return
        self._preview_mode = "diagnostics"
        self._update_preview()
        self._update_controls()

    def action_focus_source_search(self) -> None:
        search = self.query_one("#source-search", Horizontal)
        search.display = True
        self.query_one("#source-search-query", Input).focus()
        self._refresh_search()

    def action_close_source_search(self) -> None:
        self.query_one("#source-search-query", Input).value = ""
        self.query_one("#source-search", Horizontal).display = False
        preview = self.query_one("#source-preview", TextArea)
        preview.move_cursor(preview.cursor_location)
        preview.focus()

    def action_next_search_match(self) -> None:
        if not self._search_matches:
            return
        self._search_match_index = (self._search_match_index + 1) % len(
            self._search_matches
        )
        self._select_search_match()

    def action_previous_search_match(self) -> None:
        if not self._search_matches:
            return
        self._search_match_index = (self._search_match_index - 1) % len(
            self._search_matches
        )
        self._select_search_match()

    def action_edit_source(self) -> None:
        if self._source_editing or self.document_details is None:
            return
        if self._preview_mode != "source":
            self.action_show_source()
        if self.query_one("#source-search", Horizontal).display:
            self.action_close_source_search()
        self._source_editing = True
        preview = self.query_one("#source-preview", TextArea)
        preview.read_only = False
        self.query_one("#studio-tree", Tree).disabled = True
        self.query_one("#property-table", DataTable).disabled = True
        self._sync_property_editor()
        self._update_controls()
        self.query_one("#source-title", Static).update(
            f"Expert YAML editor — {self.document_details.file}"
        )
        self._update_status()
        preview.focus()

    def action_apply_source_edit(self) -> None:
        if not self._source_editing or self.selected_target is None:
            return
        source = self.query_one("#source-preview", TextArea).text
        try:
            self.state = self.service.replace_document_source(
                self.selected_target,
                source,
            )
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self._finish_source_edit()
        self._preview_mode = "changes"
        self._after_edit("Applied expert YAML edit in memory")

    def action_save_or_apply(self) -> None:
        if self._source_editing:
            self.action_apply_source_edit()
        else:
            self.action_prepare_save()

    def action_prepare_save(self) -> None:
        if self._source_editing:
            return
        if not self.state.dirty:
            self.notify("There are no in-memory changes to save")
            return
        if not self.state.valid:
            self.notify(
                "The in-memory candidate must compile before it can be saved",
                severity="error",
            )
            return
        try:
            review = self.service.prepare_save()
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self.push_screen(
            StudioSaveScreen(review),
            lambda approval: self._complete_save(review, approval),
        )

    def _complete_save(
        self,
        review: StudioSaveReview,
        approval: str | None,
    ) -> None:
        if approval is None:
            return
        try:
            result = self.service.save(review, approval)
        except (StudioError, ValueError) as error:
            self.state = self.service.state
            self.workspace = self.state.workspace
            if self.selected_target is not None:
                self._show_document(self.selected_target)
            else:
                self._update_controls()
                self._update_status()
            self.notify(str(error), severity="error")
            return
        self.state = result.state
        self.workspace = result.state.workspace
        self._preview_mode = "source"
        self._after_edit(
            f"Saved {len(result.changed_files)} YAML file(s); receipt: "
            f"{result.receipt_path}"
        )

    def action_cancel_source_edit(self) -> None:
        if not self._source_editing:
            return
        self._finish_source_edit()
        self._update_preview()
        self._update_status()
        self.notify("Expert YAML edit cancelled")

    async def action_quit(self) -> None:
        # Async because `App.action_quit` is. Textual awaits whatever an action
        # returns, so a synchronous override worked -- until something held the
        # base class to its own contract and awaited the coroutine it declares.
        if self._source_editing:
            self.notify(
                "Apply or cancel the expert YAML edit before closing Studio",
                severity="warning",
            )
            return
        self.exit()

    def _select_first_document(self) -> None:
        """Open the first document in the tree, if the tree has one.

        `TreeNode.data` is optional in Textual's contract even though every
        node this app builds carries a reference, so the check is here once
        rather than at each caller.
        """

        node = self._first_document_node
        if node is None or node.data is None:
            return
        self.query_one("#studio-tree", Tree).select_node(node)
        self._show_document(node.data)

    def _finish_source_edit(self) -> None:
        self._source_editing = False
        self.query_one("#source-preview", TextArea).read_only = True
        self.query_one("#studio-tree", Tree).disabled = False
        self.query_one("#property-table", DataTable).disabled = False
        self._sync_property_editor()
        self._update_controls()

    def _populate_tree(self) -> None:
        tree = self.query_one("#studio-tree", Tree)
        tree.root.remove_children()
        tree.root.set_label(self.workspace.application)
        tree.root.expand()
        self._first_document_node = None
        for group in self.workspace.groups:
            group_label = f"{group.label} ({len(group.documents)})"
            group_node = tree.root.add(group_label)
            for document in group.documents:
                node = group_node.add_leaf(
                    document.label,
                    data=document.target,
                )
                if self._first_document_node is None:
                    self._first_document_node = node
            if group.kind != "source":
                group_node.expand()

    def _show_document(
        self,
        target: DesignerDocumentReference,
        *,
        selected_path: tuple[str | int, ...] | None = None,
    ) -> None:
        try:
            details = self.service.document(target)
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self.selected_target = target
        self.document_details = details
        properties = self.query_one("#property-table", DataTable)
        properties.clear()
        self._property_rows.clear()
        selected_row = 0
        for index, item in enumerate(details.properties):
            row_key = f"property-{index}"
            self._property_rows[row_key] = item
            properties.add_row(
                item.name,
                item.value,
                "Editable" if item.editable else "Locked",
                key=row_key,
            )
            if selected_path is not None and item.path == selected_path:
                selected_row = index
        if details.properties:
            properties.move_cursor(row=selected_row)
            self._select_property(f"property-{selected_row}")
        else:
            self.selected_property = None
            self._sync_property_editor()
        self.query_one("#property-title", Static).update(
            f"Properties — {details.title}"
        )
        self._update_view_structure(target)
        self._update_controls()
        self._update_preview()
        self._update_status()

    def _select_property(self, row_key: str) -> None:
        selected = self._property_rows.get(row_key)
        if selected is None:
            return
        self.selected_property = selected
        self._sync_property_editor()

    def _update_view_structure(self, target: DesignerDocumentReference) -> None:
        table = self.query_one("#view-field-table", DataTable)
        add_selector = self.query_one("#view-field-add-choice", Select)
        group_selector = self.query_one("#view-field-group-choice", Select)
        table.clear()
        self._view_field_rows.clear()
        self.selected_view_field = None
        if target.kind != "view":
            self.view_structure = None
            add_selector.set_options(())
            group_selector.set_options(())
            self.query_one("#view-structure", Horizontal).display = False
            self._sync_view_field_controls()
            return
        try:
            structure = self.service.view_structure(target)
        except (StudioError, ValueError) as error:
            self.view_structure = None
            add_selector.set_options(())
            group_selector.set_options(())
            self.query_one("#view-structure", Horizontal).display = True
            self.query_one("#view-structure-preview", Static).update(str(error))
            self._sync_view_field_controls()
            return
        self.view_structure = structure
        previous_addition = add_selector.value
        add_selector.set_options(
            tuple(
                (f"{field.label} · {field.field_type}", field.name)
                for field in structure.available_fields
            )
        )
        available_names = {field.name for field in structure.available_fields}
        add_selector.value = (
            previous_addition
            if previous_addition is not Select.NULL
            and str(previous_addition) in available_names
            else Select.NULL
        )
        previous_group = group_selector.value
        local_groups = tuple(group for group in structure.groups if group.editable)
        group_selector.set_options(
            tuple(
                (f"{group.label} · {group.field_count} field(s)", group.key)
                for group in local_groups
            )
        )
        group_keys = {group.key for group in local_groups}
        preferred_group = next(
            (
                group.key
                for group in local_groups
                if group.label == self._selected_view_group_label
            ),
            None,
        )
        if preferred_group is None and previous_group is not Select.NULL:
            previous_key = str(previous_group)
            preferred_group = previous_key if previous_key in group_keys else None
        if preferred_group is None and local_groups:
            preferred_group = local_groups[0].key
        group_selector.value = preferred_group or Select.NULL
        group_selector.display = structure.kind in {"form", "inline_edit"}
        self.query_one("#view-structure", Horizontal).display = True
        self._populate_view_field_table(structure)
        self.query_one("#view-structure-preview", Static).update(
            _view_structure_preview(structure)
        )
        self._sync_view_field_controls()
        # The panel was hidden until a moment ago, so the width its table ends
        # up with is only known after the next layout. Fit the columns to it
        # then rather than to the zero it reports now.
        self.call_after_refresh(self._sync_view_field_columns)

    def _populate_view_field_table(self, structure: StudioViewStructure) -> None:
        table = self.query_one("#view-field-table", DataTable)
        table.clear(columns=True)
        self._view_field_rows.clear()
        self.query_one("#view-structure-title", Static).update("Resolved TUI structure")
        columns = view_field_columns(self._view_field_table_width())
        for column in columns:
            table.add_column(column.label, key=column.key, width=column.width)
        selected_row = 0
        for index, field in enumerate(structure.fields):
            row_key = f"view-field-{index}"
            self._view_field_rows[row_key] = field
            cells = {
                "field": field.name,
                "position": str(field.position + 1),
                "type": field.field_type,
                "origin": field.origin,
                "label": field.label,
            }
            table.add_row(*(cells[column.key] for column in columns), key=row_key)
            if field.key == self._selected_view_field_key:
                selected_row = index
        if structure.fields:
            table.move_cursor(row=selected_row)
            self._select_view_field(f"view-field-{selected_row}")

    def _view_field_table_width(self) -> int:
        table = self._panel_widget("#view-field-table", DataTable)
        return 0 if table is None else table.scrollable_content_region.width

    def _sync_view_field_columns(self) -> None:
        """Re-fit the view-structure columns to the width the layout gave them."""

        structure = self.view_structure
        table = self._panel_widget("#view-field-table", DataTable)
        if structure is None or table is None or not table.display:
            return
        available = self._view_field_table_width()
        if available <= 0:
            # Displayed is not the same as laid out. `call_after_refresh` can
            # run before the table has a region, and `view_field_columns(0)`
            # returns the one column that always survives -- which the
            # comparison below then finds already in place and accepts, so a
            # width read too early became the answer for the life of the
            # selection. Ask again after the next refresh instead. Bounded,
            # because a panel that is genuinely zero-wide must not spin.
            if self._view_field_relayouts < _UNLAID_RETRY_LIMIT:
                self._view_field_relayouts += 1
                self.call_after_refresh(self._sync_view_field_columns)
            return
        self._view_field_relayouts = 0
        wanted = tuple(column.key for column in view_field_columns(available))
        if wanted == tuple(str(key.value) for key in table.columns):
            return
        self._populate_view_field_table(structure)

    def _panel_widget(
        self,
        selector: str,
        widget_type: type[_WidgetT],
    ) -> _WidgetT | None:
        """Return a composed panel widget, or ``None`` while the screen has none.

        These panels are composed whole and only ever hidden, so a query that
        finds nothing is not a missing widget -- it is a query running against
        a DOM the tree is not in, either before compose mounts it or after
        teardown removes it. Messages already in the queue are delivered in
        both windows, so absence is an ordinary outcome here rather than a
        fault.

        Guarding the panel rather than each widget inside it is what keeps this
        from moving the crash: every control the synchronising methods touch
        arrives and leaves with its container, so one question answers for all
        of them.
        """

        try:
            return self.query_one(selector, widget_type)
        except (NoMatches, ScreenStackError):
            return None

    def _select_view_field(self, row_key: str) -> None:
        selected = self._view_field_rows.get(row_key)
        if selected is None:
            return
        self.selected_view_field = selected
        self._selected_view_field_key = selected.key
        title = self._panel_widget("#view-structure-title", Static)
        if title is not None:
            # Move up, Move down and the two swaps all act within one track,
            # so the selected field's track is what a person needs while
            # pressing them -- stated once here rather than repeated down a
            # column of identical cells.
            title.update(
                f"{selected.track_label} / {selected.source_group}"
                if selected.source_group
                else selected.track_label
            )
        group_selector = self._panel_widget("#view-field-group-choice", Select)
        if group_selector is not None and selected.source_group_key is not None and any(
            group.key == selected.source_group_key
            for group in (self.view_structure.groups if self.view_structure else ())
        ):
            group_selector.value = selected.source_group_key
            self._selected_view_group_label = selected.source_group
        self._sync_view_field_controls()

    def _sync_view_field_controls(self) -> None:
        if self._panel_widget("#view-structure-side", Vertical) is None:
            return
        selected = self.selected_view_field
        disabled = self._source_editing or selected is None
        self.query_one("#move-view-field-up", Button).disabled = disabled or not bool(
            selected and selected.can_move_up
        )
        self.query_one("#move-view-field-down", Button).disabled = disabled or not bool(
            selected and selected.can_move_down
        )
        self.query_one("#move-view-field-left", Button).disabled = disabled or not bool(
            selected and selected.can_move_left
        )
        self.query_one("#move-view-field-right", Button).disabled = (
            disabled or not bool(selected and selected.can_move_right)
        )
        self.query_one("#remove-view-field", Button).disabled = disabled or not bool(
            selected and selected.can_remove
        )
        add_selector = self.query_one("#view-field-add-choice", Select)
        group_selector = self.query_one("#view-field-group-choice", Select)
        can_add = bool(
            not self._source_editing
            and self.view_structure is not None
            and self.view_structure.available_fields
        )
        add_selector.disabled = not can_add
        layout_requires_group = bool(
            self.view_structure and self.view_structure.kind in {"form", "inline_edit"}
        )
        group_selector.disabled = bool(
            self._source_editing
            or self.view_structure is None
            or not self.view_structure.groups
        )
        self.query_one("#add-view-field", Button).disabled = bool(
            not can_add
            or add_selector.value is Select.NULL
            or (layout_requires_group and group_selector.value is Select.NULL)
        )
        self.query_one("#manage-view-groups", Button).disabled = not bool(
            not self._source_editing
            and self.view_structure is not None
            and self.view_structure.can_create_group
        )
        self.query_one("#manage-view-layout", Button).disabled = not bool(
            not self._source_editing
            and self.view_structure is not None
            and self.view_structure.kind == "form"
            and (
                self.view_structure.sections
                or self.view_structure.available_collections
                or self.view_structure.actions_editable
            )
        )
        self.query_one("#preview-view", Button).disabled = not bool(
            not self._source_editing
            and self.view_structure is not None
            and self.selected_target is not None
        )

    def _move_selected_view_field(self, offset: Literal[-1, 1]) -> None:
        target = self.selected_target
        selected = self.selected_view_field
        if self._source_editing or target is None or selected is None:
            return
        try:
            self.state = self.service.move_view_field(target, selected.key, offset)
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self._selected_view_field_key = selected.key
        self._preview_mode = "changes"
        direction = "up" if offset < 0 else "down"
        self._after_edit(
            f"Moved {selected.label} {direction} in {selected.track_label}"
        )

    def _move_selected_view_field_across(self, direction: Literal[-1, 1]) -> None:
        target = self.selected_target
        selected = self.selected_view_field
        if self._source_editing or target is None or selected is None:
            return
        try:
            self.state = self.service.move_view_field_across(
                target,
                selected.key,
                direction,
            )
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        destination = "layout-left" if direction < 0 else "layout-right"
        self._selected_view_field_key = f"{destination}:{selected.name}"
        self._preview_mode = "changes"
        self._after_edit(
            f"Swapped {selected.label} into the "
            f"{'left' if direction < 0 else 'right'} column"
        )

    def _add_view_field(self) -> None:
        target = self.selected_target
        structure = self.view_structure
        selector = self.query_one("#view-field-add-choice", Select)
        group_selector = self.query_one("#view-field-group-choice", Select)
        if (
            self._source_editing
            or target is None
            or structure is None
            or selector.value is Select.NULL
        ):
            return
        field_name = str(selector.value)
        destination_group_key = (
            str(group_selector.value)
            if group_selector.value is not Select.NULL
            else None
        )
        near_key = (
            self.selected_view_field.key
            if self.selected_view_field is not None
            else None
        )
        try:
            self.state = self.service.add_view_field(
                target,
                field_name,
                near_field_key=near_key,
                destination_group_key=destination_group_key,
            )
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        if structure.kind in {"browse", "lookup", "inline_edit"}:
            self._selected_view_field_key = f"columns:{field_name}"
        else:
            updated = self.service.view_structure(target)
            added = next(field for field in updated.fields if field.name == field_name)
            self._selected_view_field_key = added.key
        self._preview_mode = "changes"
        self._after_edit(f"Added {field_name} to {structure.view}")

    def _remove_selected_view_field(self) -> None:
        target = self.selected_target
        selected = self.selected_view_field
        structure = self.view_structure
        if (
            self._source_editing
            or target is None
            or selected is None
            or structure is None
        ):
            return
        try:
            self.state = self.service.remove_view_field(target, selected.key)
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self._selected_view_field_key = None
        self._preview_mode = "changes"
        self._after_edit(f"Removed {selected.label} from {structure.view}")

    def _manage_view_groups(self) -> None:
        structure = self.view_structure
        if self._source_editing or structure is None or not structure.can_create_group:
            return
        self.push_screen(StudioGroupsScreen(structure), self._apply_group_edit)

    def _manage_view_layout(self) -> None:
        structure = self.view_structure
        if self._source_editing or structure is None or structure.kind != "form":
            return
        self.push_screen(StudioLayoutScreen(structure), self._apply_layout_edit)

    def _preview_selected_view(self) -> None:
        target = self.selected_target
        if self._source_editing or target is None or target.kind != "view":
            return
        try:
            screen = StudioPreviewScreen(self.service, target)
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self.push_screen(screen)

    def _apply_layout_edit(self, edit: StudioLayoutEdit | None) -> None:
        if edit is None or self.selected_target is None or self.view_structure is None:
            return
        target = self.selected_target
        structure = self.view_structure
        selected = next(
            (
                section
                for section in structure.sections
                if section.key == edit.section_key
            ),
            None,
        )
        try:
            if edit.operation == "tab":
                assert edit.section_key is not None
                self.state = self.service.set_view_section_tab(
                    target,
                    edit.section_key,
                    edit.label,
                )
                message = (
                    f"Assigned {selected.label if selected else 'section'} to tab "
                    f"{edit.label}"
                    if edit.label
                    else f"Cleared tab for {selected.label if selected else 'section'}"
                )
            elif edit.operation == "move":
                assert edit.section_key is not None and edit.offset is not None
                self.state = self.service.move_view_section(
                    target,
                    edit.section_key,
                    edit.offset,
                )
                message = (
                    f"Moved {selected.label if selected else 'layout section'} "
                    f"{'up' if edit.offset < 0 else 'down'}"
                )
            elif edit.operation == "remove_collection":
                assert edit.section_key is not None
                self.state = self.service.remove_view_collection(
                    target,
                    edit.section_key,
                )
                message = f"Removed {selected.label if selected else 'collection'}"
            elif edit.operation == "add_collection":
                assert edit.collection is not None and edit.inline_view is not None
                self.state = self.service.add_view_collection(
                    target,
                    edit.collection,
                    edit.inline_view,
                )
                message = f"Added collection {edit.collection}"
            else:
                assert edit.bar_key is not None
                self.state = self.service.set_view_action_order(
                    target,
                    edit.bar_key,
                    edit.actions,
                )
                message = "Updated action-bar order"
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self._preview_mode = "changes"
        self._after_edit(message)

    def _apply_group_edit(self, edit: StudioGroupEdit | None) -> None:
        if edit is None or self.selected_target is None or self.view_structure is None:
            return
        target = self.selected_target
        structure = self.view_structure
        selected_group = next(
            (group for group in structure.groups if group.key == edit.group_key),
            None,
        )
        preferred_label: str | None = None
        try:
            if edit.operation == "create":
                assert edit.label is not None
                self.state = self.service.create_view_group(target, edit.label)
                message = f"Created view group {edit.label}"
                preferred_label = edit.label
            elif edit.operation == "rename":
                assert edit.group_key is not None and edit.label is not None
                self.state = self.service.rename_view_group(
                    target,
                    edit.group_key,
                    edit.label,
                )
                message = f"Renamed view group to {edit.label}"
                preferred_label = edit.label
            elif edit.operation == "move":
                assert edit.group_key is not None and edit.offset is not None
                self.state = self.service.move_view_group(
                    target,
                    edit.group_key,
                    edit.offset,
                )
                label = selected_group.label if selected_group else "view group"
                message = f"Moved {label} {'up' if edit.offset < 0 else 'down'}"
                preferred_label = selected_group.label if selected_group else None
            else:
                assert edit.group_key is not None
                self.state = self.service.remove_view_group(target, edit.group_key)
                label = selected_group.label if selected_group else "empty view group"
                message = f"Removed {label}"
                preferred_label = None
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self._selected_view_group_label = preferred_label
        self._preview_mode = "changes"
        self._after_edit(message)
        if preferred_label is not None and self.view_structure is not None:
            preferred = next(
                (
                    group
                    for group in self.view_structure.groups
                    if group.label == preferred_label
                ),
                None,
            )
            if preferred is not None:
                self.query_one("#view-field-group-choice", Select).value = preferred.key

    def _sync_property_editor(self) -> None:
        if self._panel_widget("#property-editor", Horizontal) is None:
            return
        editor = self.query_one("#property-value", Input)
        selector = self.query_one("#property-choice", Select)
        apply_button = self.query_one("#apply-property", Button)
        selected = self.selected_property
        editable = (
            selected is not None and selected.editable and not self._source_editing
        )
        apply_button.disabled = not editable
        choice_editor = bool(
            editable
            and selected is not None
            and selected.editor in {"choice", "boolean"}
        )
        editor.display = not choice_editor
        editor.disabled = not editable or choice_editor
        selector.display = choice_editor
        selector.disabled = not choice_editor
        if selected is None:
            editor.value = ""
            editor.placeholder = "Select an editable scalar property"
            selector.set_options(())
        else:
            editor.value = selected.value
            editor.placeholder = selected.name
            selector.set_options((choice, choice) for choice in selected.choices)
            selector.value = (
                selected.value if selected.value in selected.choices else Select.NULL
            )

    def _apply_selected_property(self) -> None:
        target = self.selected_target
        selected = self.selected_property
        if (
            self._source_editing
            or target is None
            or selected is None
            or not selected.editable
        ):
            return
        if selected.editor in {"choice", "boolean"}:
            value = self.query_one("#property-choice", Select).value
            if value is Select.NULL:
                return
            text = str(value)
        else:
            text = self.query_one("#property-value", Input).value
        try:
            self.state = self.service.set_property(target, selected.path, text)
        except (StudioError, ValueError) as error:
            self.notify(str(error), severity="error")
            return
        self._preview_mode = "changes"
        self._after_edit(
            f"Applied {selected.name} in memory",
            selected_path=selected.path,
        )

    def _after_edit(
        self,
        message: str,
        *,
        selected_path: tuple[str | int, ...] | None = None,
    ) -> None:
        self.workspace = self.state.workspace
        self.sub_title = self.workspace.application
        self.query_one("#studio-tree", Tree).root.set_label(self.workspace.application)
        self.query_one("#studio-context", Static).update(self._context_text(self.state))
        if self.selected_target is not None:
            if selected_path is None and self.selected_property is not None:
                selected_path = self.selected_property.path
            self._show_document(self.selected_target, selected_path=selected_path)
        self.notify(
            message,
            severity="information" if self.state.valid else "warning",
        )

    def _update_controls(self) -> None:
        normal_buttons = (
            "undo-edit",
            "redo-edit",
            "show-source",
            "show-changes",
            "show-diagnostics",
            "edit-source",
            "save-candidate",
        )
        for button_id in normal_buttons:
            self.query_one(f"#{button_id}", Button).display = not self._source_editing
        self.query_one("#apply-source", Button).display = self._source_editing
        self.query_one("#cancel-source", Button).display = self._source_editing
        self.query_one("#view-structure", Horizontal).display = (
            not self._source_editing
            and self.selected_target is not None
            and self.selected_target.kind == "view"
        )
        self._sync_view_field_controls()
        if self._source_editing:
            return
        self.query_one("#undo-edit", Button).disabled = not self.state.can_undo
        self.query_one("#redo-edit", Button).disabled = not self.state.can_redo
        self.query_one("#show-changes", Button).disabled = not self.state.dirty
        self.query_one("#show-diagnostics", Button).disabled = not bool(
            self.state.diagnostics
        )
        self.query_one("#edit-source", Button).disabled = self._preview_mode != "source"
        self.query_one("#save-candidate", Button).disabled = not (
            self.state.dirty and self.state.valid
        )

    def _update_preview(self) -> None:
        if self._source_editing:
            return
        details = self.document_details
        if details is None:
            return
        title = self.query_one("#source-title", Static)
        preview = self.query_one("#source-preview", TextArea)
        if self._preview_mode == "changes":
            title.update("Pending changes — exact unified diff")
            preview.language = None
            preview.load_text(self.state.diff or "# No pending changes.\n")
        elif self._preview_mode == "diagnostics":
            title.update("Compiler diagnostics")
            preview.language = None
            preview.load_text(self._diagnostic_text())
        else:
            title.update(f"YAML source — {details.file}")
            preview.language = "yaml"
            preview.load_text(details.source)
        self._refresh_search()

    def _refresh_search(self) -> None:
        query = self.query_one("#source-search-query", Input).value
        preview = self.query_one("#source-preview", TextArea)
        status = self.query_one("#source-search-status", Static)
        self._search_matches = []
        self._search_match_index = -1
        if query:
            for match in re.finditer(re.escape(query), preview.text, re.IGNORECASE):
                self._search_matches.append(
                    (
                        _text_location(preview.text, match.start()),
                        _text_location(preview.text, match.end()),
                    )
                )
        enabled = bool(self._search_matches)
        self.query_one("#search-previous", Button).disabled = not enabled
        self.query_one("#search-next", Button).disabled = not enabled
        if not query:
            status.update("No query")
            preview.move_cursor(preview.cursor_location)
        elif not enabled:
            status.update("No matches")
            preview.move_cursor(preview.cursor_location)
        else:
            self._search_match_index = 0
            self._select_search_match()

    def _select_search_match(self) -> None:
        if not self._search_matches:
            return
        start, end = self._search_matches[self._search_match_index]
        preview = self.query_one("#source-preview", TextArea)
        preview.move_cursor(start)
        preview.move_cursor(end, select=True, center=True)
        self.query_one("#source-search-status", Static).update(
            f"{self._search_match_index + 1} / {len(self._search_matches)}"
        )

    def _diagnostic_text(self) -> str:
        if not self.state.diagnostics:
            return "No compiler diagnostics.\n"
        lines: list[str] = []
        for diagnostic in self.state.diagnostics:
            code = str(diagnostic.get("code", "TIDE"))
            severity = str(diagnostic.get("severity", "error")).upper()
            message = str(diagnostic.get("message", "Compiler diagnostic"))
            location = str(diagnostic.get("file", ""))
            line = diagnostic.get("line")
            if line is not None:
                location += f":{line}"
            prefix = f"{location}: " if location else ""
            lines.append(f"{prefix}{severity} {code}: {message}")
        return "\n".join(lines) + "\n"

    def _update_status(self) -> None:
        if self._source_editing:
            status = (
                "Expert YAML buffer · Ctrl+S applies in memory · Esc cancels · "
                "no source writes"
            )
        elif not self.state.valid:
            first = self.state.diagnostics[0] if self.state.diagnostics else {}
            detail = f"{first.get('code', 'TIDE')}: {first.get('message', 'invalid')}"
            status = f"Invalid in-memory candidate · {detail} · Undo is available"
        elif self.state.dirty:
            count = len(self.state.changed_files)
            status = (
                f"Unsaved in-memory changes · {count} file{'s' if count != 1 else ''} "
                "· review Changes, Save candidate, or Undo · no database connection"
            )
        else:
            status = "Clean candidate · no source writes · no database connection"
        self.query_one("#studio-status", Static).update(status)

    @staticmethod
    def _context_text(state: StudioSessionState) -> str:
        workspace = state.workspace
        validity = "valid" if state.valid else "has diagnostics"
        change_state = "modified in memory" if state.dirty else "clean"
        return (
            f"{workspace.application} · {workspace.entity_count} entities · "
            f"{workspace.view_count} views · {workspace.report_count} reports · "
            f"{validity} · {change_state}"
        )


def _view_structure_preview(structure: StudioViewStructure) -> str:
    fields = {field.key: field for field in structure.fields}
    lines = [f"{structure.kind} · {structure.entity}"]
    for track in structure.tracks:
        names = " → ".join(fields[key].name for key in track.fields)
        lines.append(f"{track.label}: {names}")
    if structure.available_fields:
        names = ", ".join(field.name for field in structure.available_fields)
        lines.append(f"Available to add: {names}")
    if structure.groups:
        groups = " → ".join(
            f"{group.label} ({group.field_count})" for group in structure.groups
        )
        lines.append(f"Field groups: {groups}")
    if structure.sections:
        sections = " → ".join(
            f"{section.label}{f' [{section.tab}]' if section.tab else ''}"
            for section in structure.sections
        )
        lines.append(f"Sections: {sections}")
    if structure.record_actions:
        lines.append("Record actions: " + " → ".join(structure.record_actions))
    for section in structure.sections:
        if section.kind == "collection" and section.actions:
            lines.append(f"{section.label} actions: " + " → ".join(section.actions))
    if not structure.tracks:
        lines.append("No explicit field structure is resolved for this view.")
    elif not structure.editable:
        lines.append("Inherited/generated structure is preview-only.")
    return "\n".join(lines)


def _text_location(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    row = before.count("\n")
    column = len(before.rsplit("\n", 1)[-1])
    return row, column
