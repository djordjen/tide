"""First property-editing Textual shell for TIDE Studio."""

from __future__ import annotations

import re

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    TextArea,
    Tree,
)
from textual.widgets.tree import TreeNode

from tide.development.designer import DesignerDocumentReference
from tide.development.studio import (
    StudioDocumentDetails,
    StudioError,
    StudioProperty,
    StudioService,
    StudioSessionState,
)


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
    }

    .panel-title {
        height: 2;
        padding: 0 1;
        color: $text-muted;
        content-align: left middle;
    }

    #property-table {
        height: 11;
        border: round $primary;
    }

    #property-editor, #studio-toolbar {
        height: 3;
        padding: 0 1;
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
        Binding("ctrl+s", "apply_source_edit", "Apply YAML"),
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
        self._preview_mode = "source"
        self._search_matches: list[tuple[tuple[int, int], tuple[int, int]]] = []
        self._search_match_index = -1
        self._source_editing = False

    def compose(self) -> ComposeResult:
        yield Header()
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
                with Horizontal(id="studio-toolbar"):
                    yield Button("Undo", id="undo-edit", disabled=True)
                    yield Button("Redo", id="redo-edit", disabled=True)
                    yield Button("YAML", id="show-source")
                    yield Button("Changes", id="show-changes", disabled=True)
                    yield Button("Diagnostics", id="show-diagnostics", disabled=True)
                    yield Button("Edit YAML", id="edit-source")
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
        self._populate_tree()
        tree = self.query_one("#studio-tree", Tree)
        if self._first_document_node is not None:
            tree.select_node(self._first_document_node)
            self._show_document(self._first_document_node.data)
        tree.focus()

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

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "property-table":
            return
        self._select_property(str(event.row_key.value))

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
        if self._first_document_node is not None:
            tree = self.query_one("#studio-tree", Tree)
            tree.select_node(self._first_document_node)
            self._show_document(self._first_document_node.data)
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

    def action_cancel_source_edit(self) -> None:
        if not self._source_editing:
            return
        self._finish_source_edit()
        self._update_preview()
        self._update_status()
        self.notify("Expert YAML edit cancelled")

    def action_quit(self) -> None:
        if self._source_editing:
            self.notify(
                "Apply or cancel the expert YAML edit before closing Studio",
                severity="warning",
            )
            return
        self.exit()

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
        self._update_controls()
        self._update_preview()
        self._update_status()

    def _select_property(self, row_key: str) -> None:
        selected = self._property_rows.get(row_key)
        if selected is None:
            return
        self.selected_property = selected
        self._sync_property_editor()

    def _sync_property_editor(self) -> None:
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
        )
        for button_id in normal_buttons:
            self.query_one(f"#{button_id}", Button).display = not self._source_editing
        self.query_one("#apply-source", Button).display = self._source_editing
        self.query_one("#cancel-source", Button).display = self._source_editing
        if self._source_editing:
            return
        self.query_one("#undo-edit", Button).disabled = not self.state.can_undo
        self.query_one("#redo-edit", Button).disabled = not self.state.can_redo
        self.query_one("#show-changes", Button).disabled = not self.state.dirty
        self.query_one("#show-diagnostics", Button).disabled = not bool(
            self.state.diagnostics
        )
        self.query_one("#edit-source", Button).disabled = self._preview_mode != "source"

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
                "· review Changes or Undo · no database connection"
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


def _text_location(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    row = before.count("\n")
    column = len(before.rsplit("\n", 1)[-1])
    return row, column
