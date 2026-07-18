"""Interactive review of a stale record draft."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from tide.sessions import (
    ConflictDisposition,
    ConflictValueChoice,
    RecordConflict,
    RecordConflictField,
    RecordConflictResolution,
    resolve_record_conflict,
)


class ConflictChoice(StrEnum):
    """Safe actions available after reviewing a stale draft."""

    KEEP_EDITING = "keep_editing"
    RELOAD = "reload"
    REBASE = "rebase"


@dataclass(frozen=True, slots=True)
class ConflictReviewResult:
    """The user's dialog action and optional complete rebase plan."""

    choice: ConflictChoice
    resolution: RecordConflictResolution | None = None


class ConflictReviewScreen(ModalScreen[ConflictReviewResult]):
    """Show a three-way field comparison without auto-resolving conflicts."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    ConflictReviewScreen {
        align: center middle;
        background: $background 65%;
    }

    #conflict-dialog {
        width: 92%;
        max-width: 112;
        height: 82%;
        min-height: 18;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #conflict-title {
        height: 2;
        color: $warning;
        text-style: bold;
    }

    #conflict-summary, #conflict-guidance {
        height: auto;
        color: $text;
    }

    #conflict-guidance {
        margin-bottom: 1;
        color: $text-muted;
    }

    #conflict-fields {
        height: 1fr;
        min-height: 7;
        border: round $primary;
    }

    #conflict-field-actions {
        height: 3;
        align-horizontal: right;
    }

    #conflict-selection-status {
        width: 1fr;
        height: 3;
        color: $text-muted;
        content-align: left middle;
    }

    #conflict-field-actions Button {
        min-width: 14;
        margin-left: 1;
    }

    #conflict-actions {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }

    #conflict-actions Button {
        min-width: 17;
        margin-left: 1;
    }

    ConflictReviewScreen.compact-terminal #conflict-dialog {
        width: 98%;
        height: 94%;
        padding-left: 1;
        padding-right: 1;
    }

    ConflictReviewScreen.compact-terminal #conflict-actions Button {
        min-width: 12;
        margin-left: 0;
    }

    ConflictReviewScreen.compact-terminal #conflict-field-actions Button {
        min-width: 11;
        margin-left: 0;
    }
    """

    BINDINGS = [Binding("escape", "keep_editing", "Keep editing", show=False)]

    def __init__(
        self,
        conflict: RecordConflict,
        *,
        field_label: Callable[[str], str],
        format_value: Callable[[str, object], str],
    ) -> None:
        super().__init__()
        self.conflict = conflict
        self.field_label = field_label
        self.format_value = format_value
        self._choices: dict[str, ConflictValueChoice] = {}

    def compose(self) -> ComposeResult:
        conflicts = len(self.conflict.conflicting_fields)
        safe = len(self.conflict.rebase_fields)
        summary = (
            f"The record changed after you opened it. {conflicts} field(s) require "
            f"a decision; {safe} of your field change(s) can be safely rebased."
        )
        with Vertical(id="conflict-dialog"):
            yield Static("Record changed elsewhere", id="conflict-title")
            yield Static(summary, id="conflict-summary")
            yield Static(
                "Select each conflicting row and choose Use Current or Use Mine. "
                "The result is reopened for review and normal validation.",
                id="conflict-guidance",
            )
            yield DataTable(id="conflict-fields")
            with Horizontal(id="conflict-field-actions"):
                yield Static("Select a conflicting field.", id="conflict-selection-status")
                yield Button("Use Current", id="use-current-conflict", disabled=True)
                yield Button("Use Mine", id="use-draft-conflict", disabled=True)
            with Horizontal(id="conflict-actions"):
                yield Button("Continue Editing", id="keep-conflict-draft")
                yield Button("Reload Current", id="reload-conflict-record")
                yield Button(
                    "Apply Resolution",
                    id="apply-conflict-resolution",
                    variant="primary",
                    disabled=True,
                )

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < 100, "compact-terminal")
        table = self.query_one("#conflict-fields", DataTable)
        table.add_column("Field", key="field")
        table.add_column("Original", key="original")
        table.add_column("Current", key="current")
        table.add_column("Your draft", key="draft")
        table.add_column("Resolution", key="resolution")
        table.cursor_type = "row"
        table.zebra_stripes = True
        for index, field in enumerate(self.conflict.fields):
            table.add_row(
                self.field_label(field.name),
                self.format_value(field.name, field.original),
                self.format_value(field.name, field.current),
                self.format_value(field.name, field.draft),
                _resolution_label(field),
                key=f"conflict-{index}",
            )
        self._refresh_controls()
        self.query_one("#keep-conflict-draft", Button).focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "conflict-fields":
            self._refresh_controls()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "use-current-conflict":
            self._choose_selected(ConflictValueChoice.CURRENT)
        elif button_id == "use-draft-conflict":
            self._choose_selected(ConflictValueChoice.DRAFT)
        elif button_id == "keep-conflict-draft":
            self.dismiss(ConflictReviewResult(ConflictChoice.KEEP_EDITING))
        elif button_id == "reload-conflict-record":
            self.dismiss(ConflictReviewResult(ConflictChoice.RELOAD))
        elif button_id == "apply-conflict-resolution":
            resolution = resolve_record_conflict(self.conflict, self._choices)
            if resolution.complete:
                self.dismiss(
                    ConflictReviewResult(ConflictChoice.REBASE, resolution)
                )

    def action_keep_editing(self) -> None:
        self.dismiss(ConflictReviewResult(ConflictChoice.KEEP_EDITING))

    def _choose_selected(self, choice: ConflictValueChoice) -> None:
        selected = self._selected_field()
        if selected is None or selected.disposition is not ConflictDisposition.CONFLICT:
            return
        self._choices[selected.name] = choice
        row_index = self.conflict.fields.index(selected)
        self.query_one("#conflict-fields", DataTable).update_cell(
            f"conflict-{row_index}",
            "resolution",
            _resolution_label(selected, choice),
        )
        self._refresh_controls()

    def _selected_field(self) -> RecordConflictField | None:
        table = self.query_one("#conflict-fields", DataTable)
        if table.cursor_row < 0 or table.cursor_row >= len(self.conflict.fields):
            return None
        return self.conflict.fields[table.cursor_row]

    def _refresh_controls(self) -> None:
        resolution = resolve_record_conflict(self.conflict, self._choices)
        remaining = len(resolution.unresolved_fields)
        conflicts = len(self.conflict.conflicting_fields)
        safe = len(self.conflict.rebase_fields)
        self.query_one("#conflict-summary", Static).update(
            f"The record changed after you opened it. {conflicts} field(s) require "
            f"a decision; {remaining} remain. {safe} draft-only change(s) are safe."
        )
        self.query_one("#apply-conflict-resolution", Button).disabled = (
            not resolution.complete
        )
        selected = self._selected_field()
        selectable = bool(
            selected is not None
            and selected.disposition is ConflictDisposition.CONFLICT
        )
        self.query_one("#use-current-conflict", Button).disabled = not selectable
        self.query_one("#use-draft-conflict", Button).disabled = not selectable
        status = self.query_one("#conflict-selection-status", Static)
        if not selectable or selected is None:
            status.update("This row needs no manual choice.")
            return
        choice = self._choices.get(selected.name)
        status.update(
            f"{self.field_label(selected.name)}: "
            + (
                "choose Current or Mine"
                if choice is None
                else f"using {choice.value.title()}"
            )
        )


def _resolution_label(
    field: RecordConflictField,
    choice: ConflictValueChoice | None = None,
) -> str:
    if field.disposition is ConflictDisposition.CONFLICT:
        return "Choose value" if choice is None else f"Use {choice.value.title()}"
    return {
        ConflictDisposition.YOUR_CHANGE: "Keep your change",
        ConflictDisposition.CURRENT_CHANGE: "Use current",
        ConflictDisposition.SAME_CHANGE: "Already same",
    }[field.disposition]
