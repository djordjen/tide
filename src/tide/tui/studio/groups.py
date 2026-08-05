"""Editing the groups a view arranges its fields into."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Select,
    Static,
)

from tide.development.studio import (
    StudioViewGroup,
    StudioViewStructure,
)


@dataclass(frozen=True)
class StudioGroupEdit:
    """One requested in-memory group operation from the modal editor."""

    operation: Literal["create", "rename", "move", "remove"]
    group_key: str | None = None
    label: str | None = None
    offset: Literal[-1, 1] | None = None


class StudioGroupsScreen(ModalScreen[StudioGroupEdit | None]):
    """Create and safely maintain local field groups for one resolved view."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    StudioGroupsScreen {
        align: center middle;
        background: $background 70%;
    }

    #studio-groups-dialog {
        width: 76;
        height: 24;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #studio-groups-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    #studio-groups-summary {
        height: 4;
        color: $text-muted;
    }

    #studio-group-select, #studio-group-name {
        height: 3;
    }

    #studio-group-order-actions, #studio-group-edit-actions {
        height: 3;
        align-horizontal: right;
    }

    #studio-group-order-actions Button, #studio-group-edit-actions Button {
        min-width: 12;
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close", show=False)]

    def __init__(self, structure: StudioViewStructure) -> None:
        super().__init__()
        self.structure = structure
        self._groups = {group.key: group for group in structure.groups}

    def compose(self) -> ComposeResult:
        with Vertical(id="studio-groups-dialog"):
            yield Static(
                f"Manage groups — {self.structure.view}",
                id="studio-groups-title",
            )
            yield Static(
                "Groups cannot cross collection sections. Remove is enabled only "
                "after every field leaves the group.",
                id="studio-groups-summary",
                markup=False,
            )
            yield Select[str](
                tuple(
                    (
                        f"{group.label} · {group.field_count} field(s)",
                        group.key,
                    )
                    for group in self.structure.groups
                ),
                prompt="Select a local group",
                allow_blank=True,
                id="studio-group-select",
            )
            yield Input(
                placeholder="New or replacement group label",
                id="studio-group-name",
            )
            with Horizontal(id="studio-group-order-actions"):
                yield Button("Move up", id="studio-group-up", disabled=True)
                yield Button("Move down", id="studio-group-down", disabled=True)
                yield Button(
                    "Remove empty",
                    id="studio-group-remove",
                    disabled=True,
                )
            with Horizontal(id="studio-group-edit-actions"):
                yield Button("Close", id="studio-group-close")
                yield Button("Rename", id="studio-group-rename", disabled=True)
                yield Button(
                    "Create group",
                    id="studio-group-create",
                    variant="primary",
                    disabled=True,
                )

    def on_mount(self) -> None:
        selector = self.query_one("#studio-group-select", Select)
        first = next(iter(self._groups), None)
        if first is not None:
            selector.value = first
            selector.focus()
        else:
            self.query_one("#studio-group-name", Input).focus()
        self._sync_controls()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "studio-group-select":
            return
        selected = self._selected_group()
        if selected is not None:
            self.query_one("#studio-group-name", Input).value = selected.label
        self._sync_controls()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "studio-group-name":
            self._sync_controls()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "studio-group-name":
            return
        rename = self.query_one("#studio-group-rename", Button)
        if not rename.disabled:
            self._dismiss_rename()
        elif not self.query_one("#studio-group-create", Button).disabled:
            self._dismiss_create()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "studio-group-create":
            self._dismiss_create()
        elif event.button.id == "studio-group-rename":
            self._dismiss_rename()
        elif event.button.id == "studio-group-up":
            self._dismiss_move(-1)
        elif event.button.id == "studio-group-down":
            self._dismiss_move(1)
        elif event.button.id == "studio-group-remove":
            selected = self._selected_group()
            if selected is not None and selected.can_remove:
                self.dismiss(
                    StudioGroupEdit(operation="remove", group_key=selected.key)
                )
        elif event.button.id == "studio-group-close":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_group(self) -> StudioViewGroup | None:
        value = self.query_one("#studio-group-select", Select).value
        return self._groups.get(str(value)) if value is not Select.NULL else None

    def _label(self) -> str:
        return self.query_one("#studio-group-name", Input).value.strip()

    def _label_available(self, label: str, *, except_key: str | None = None) -> bool:
        return bool(
            label
            and len(label) <= 80
            and "\n" not in label
            and "\r" not in label
            and all(
                group.key == except_key or group.label.casefold() != label.casefold()
                for group in self.structure.groups
            )
        )

    def _sync_controls(self) -> None:
        selected = self._selected_group()
        label = self._label()
        self.query_one("#studio-group-up", Button).disabled = not bool(
            selected and selected.can_move_up
        )
        self.query_one("#studio-group-down", Button).disabled = not bool(
            selected and selected.can_move_down
        )
        self.query_one("#studio-group-remove", Button).disabled = not bool(
            selected and selected.can_remove
        )
        self.query_one("#studio-group-create", Button).disabled = not (
            self.structure.can_create_group and self._label_available(label)
        )
        self.query_one("#studio-group-rename", Button).disabled = not bool(
            selected
            and selected.editable
            and label != selected.label
            and self._label_available(label, except_key=selected.key)
        )

    def _dismiss_create(self) -> None:
        label = self._label()
        if self.structure.can_create_group and self._label_available(label):
            self.dismiss(StudioGroupEdit(operation="create", label=label))

    def _dismiss_rename(self) -> None:
        selected = self._selected_group()
        label = self._label()
        if (
            selected is not None
            and selected.editable
            and label != selected.label
            and self._label_available(label, except_key=selected.key)
        ):
            self.dismiss(
                StudioGroupEdit(
                    operation="rename",
                    group_key=selected.key,
                    label=label,
                )
            )

    def _dismiss_move(self, offset: Literal[-1, 1]) -> None:
        selected = self._selected_group()
        if selected is None:
            return
        allowed = selected.can_move_up if offset < 0 else selected.can_move_down
        if allowed:
            self.dismiss(
                StudioGroupEdit(
                    operation="move",
                    group_key=selected.key,
                    offset=offset,
                )
            )
