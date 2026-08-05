"""Arranging a view's fields, tracks and actions."""

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

from tide.labels import humanize
from tide.development.studio import (
    StudioViewSection,
    StudioViewStructure,
)


@dataclass(frozen=True)
class StudioLayoutEdit:
    """One requested in-memory section, tab, collection, or action-bar edit."""

    operation: Literal[
        "tab",
        "move",
        "remove_collection",
        "add_collection",
        "actions",
    ]
    section_key: str | None = None
    label: str | None = None
    offset: Literal[-1, 1] | None = None
    collection: str | None = None
    inline_view: str | None = None
    bar_key: str | None = None
    actions: tuple[str, ...] = ()


class StudioLayoutScreen(ModalScreen[StudioLayoutEdit | None]):
    """Edit portable tabs, layout sections, collections, and action bars."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    StudioLayoutScreen {
        align: center middle;
        background: $background 70%;
    }

    #studio-layout-dialog {
        width: 94;
        height: 39;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #studio-layout-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    .studio-layout-help {
        height: 2;
        color: $text-muted;
    }

    .studio-layout-row {
        height: 3;
    }

    .studio-layout-row Select, .studio-layout-row Input {
        width: 1fr;
    }

    .studio-layout-row Button {
        min-width: 12;
        margin-left: 1;
    }

    #studio-layout-close-row {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #studio-layout-close-row Button {
        min-width: 14;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close", show=False)]

    def __init__(self, structure: StudioViewStructure) -> None:
        super().__init__()
        self.structure = structure
        self._sections = {section.key: section for section in structure.sections}
        self._active_collection: str | None = None
        self._active_action_bar: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="studio-layout-dialog"):
            yield Static(
                f"Layout, tabs, collections & actions — {self.structure.view}",
                id="studio-layout-title",
            )
            yield Static(
                "Sections define shared TUI/GUI/Web presentation order. A repeated "
                "tab label places sections on the same tab.",
                classes="studio-layout-help",
                markup=False,
            )
            with Horizontal(classes="studio-layout-row"):
                yield Select[str](
                    tuple(
                        (
                            f"{section.position + 1}. {section.label} · {section.kind}",
                            section.key,
                        )
                        for section in self.structure.sections
                    ),
                    prompt="Select layout section",
                    allow_blank=True,
                    id="studio-layout-section",
                )
            with Horizontal(classes="studio-layout-row"):
                yield Input(
                    placeholder="Tab label; blank means General when other tabs exist",
                    id="studio-layout-tab",
                )
                yield Button("Apply tab", id="studio-layout-apply-tab", disabled=True)
                yield Button("Clear tab", id="studio-layout-clear-tab", disabled=True)
            with Horizontal(classes="studio-layout-row"):
                yield Button("Move up", id="studio-layout-up", disabled=True)
                yield Button("Move down", id="studio-layout-down", disabled=True)
                yield Button(
                    "Remove collection",
                    id="studio-layout-remove-collection",
                    disabled=True,
                    variant="warning",
                )
            yield Static(
                "Add an unused collection with a compatible inline editor",
                classes="studio-layout-help",
            )
            with Horizontal(classes="studio-layout-row"):
                yield Select[str](
                    tuple(
                        (
                            f"{collection.label} · {collection.target_entity}",
                            collection.name,
                        )
                        for collection in self.structure.available_collections
                    ),
                    prompt="Collection field",
                    allow_blank=True,
                    id="studio-layout-collection",
                )
                yield Select[str](
                    (),
                    prompt="Inline editor view",
                    allow_blank=True,
                    id="studio-layout-inline-view",
                )
                yield Button(
                    "Add collection",
                    id="studio-layout-add-collection",
                    disabled=True,
                    variant="primary",
                )
            yield Static(
                "Order or include actions on the record bar or a collection bar",
                classes="studio-layout-help",
            )
            with Horizontal(classes="studio-layout-row"):
                yield Select[str](
                    (
                        (("Record actions", "record"),)
                        if self.structure.actions_editable
                        else ()
                    )
                    + tuple(
                        (f"{section.label} actions", section.key)
                        for section in self.structure.sections
                        if section.kind == "collection" and section.editable
                    ),
                    prompt="Action bar",
                    allow_blank=True,
                    id="studio-layout-action-bar",
                )
            with Horizontal(classes="studio-layout-row"):
                yield Select[str](
                    (),
                    prompt="Current action",
                    allow_blank=True,
                    id="studio-layout-current-action",
                )
                yield Button("Move up", id="studio-layout-action-up", disabled=True)
                yield Button("Move down", id="studio-layout-action-down", disabled=True)
                yield Button("Remove", id="studio-layout-action-remove", disabled=True)
            with Horizontal(classes="studio-layout-row"):
                yield Select[str](
                    (),
                    prompt="Available action",
                    allow_blank=True,
                    id="studio-layout-available-action",
                )
                yield Button(
                    "Add action",
                    id="studio-layout-action-add",
                    disabled=True,
                    variant="primary",
                )
            with Horizontal(id="studio-layout-close-row"):
                yield Button("Close", id="studio-layout-close")

    def on_mount(self) -> None:
        section_selector = self.query_one("#studio-layout-section", Select)
        if self.structure.sections:
            section_selector.value = self.structure.sections[0].key
        collection_selector = self.query_one("#studio-layout-collection", Select)
        if self.structure.available_collections:
            collection_selector.value = self.structure.available_collections[0].name
        bar_selector = self.query_one("#studio-layout-action-bar", Select)
        if self.structure.actions_editable:
            bar_selector.value = "record"
        else:
            first_collection = next(
                (
                    section.key
                    for section in self.structure.sections
                    if section.kind == "collection" and section.editable
                ),
                None,
            )
            if first_collection:
                bar_selector.value = first_collection
        self._sync_controls()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "studio-layout-section":
            section = self._selected_section()
            self.query_one("#studio-layout-tab", Input).value = (
                section.tab if section and section.tab else ""
            )
        self._sync_controls()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "studio-layout-tab":
            self._sync_controls()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        section = self._selected_section()
        if button_id == "studio-layout-close":
            self.action_cancel()
        elif button_id == "studio-layout-apply-tab" and section is not None:
            self.dismiss(
                StudioLayoutEdit(
                    operation="tab",
                    section_key=section.key,
                    label=self.query_one("#studio-layout-tab", Input).value.strip(),
                )
            )
        elif button_id == "studio-layout-clear-tab" and section is not None:
            self.dismiss(StudioLayoutEdit(operation="tab", section_key=section.key))
        elif button_id in {"studio-layout-up", "studio-layout-down"} and section:
            self.dismiss(
                StudioLayoutEdit(
                    operation="move",
                    section_key=section.key,
                    offset=-1 if button_id.endswith("up") else 1,
                )
            )
        elif button_id == "studio-layout-remove-collection" and section:
            self.dismiss(
                StudioLayoutEdit(
                    operation="remove_collection",
                    section_key=section.key,
                )
            )
        elif button_id == "studio-layout-add-collection":
            collection = self.query_one("#studio-layout-collection", Select).value
            inline_view = self.query_one("#studio-layout-inline-view", Select).value
            if collection is not Select.NULL and inline_view is not Select.NULL:
                self.dismiss(
                    StudioLayoutEdit(
                        operation="add_collection",
                        collection=str(collection),
                        inline_view=str(inline_view),
                    )
                )
        elif button_id in {
            "studio-layout-action-up",
            "studio-layout-action-down",
            "studio-layout-action-remove",
            "studio-layout-action-add",
        }:
            self._dismiss_action_edit(button_id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_section(self) -> StudioViewSection | None:
        value = self.query_one("#studio-layout-section", Select).value
        return self._sections.get(str(value)) if value is not Select.NULL else None

    def _action_bar(self) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
        value = self.query_one("#studio-layout-action-bar", Select).value
        if value is Select.NULL:
            return None, (), ()
        key = str(value)
        if key == "record":
            return (
                key,
                self.structure.record_actions,
                self.structure.available_record_actions,
            )
        section = self._sections.get(key)
        if section is None:
            return None, (), ()
        return key, section.actions, section.available_actions

    def _sync_controls(self) -> None:
        section = self._selected_section()
        tab = self.query_one("#studio-layout-tab", Input).value.strip()
        valid_tab = bool(tab and len(tab) <= 80 and "\n" not in tab and "\r" not in tab)
        self.query_one("#studio-layout-apply-tab", Button).disabled = not bool(
            section and section.editable and valid_tab and tab != section.tab
        )
        self.query_one("#studio-layout-clear-tab", Button).disabled = not bool(
            section and section.editable and section.tab
        )
        self.query_one("#studio-layout-up", Button).disabled = not bool(
            section and section.can_move_up
        )
        self.query_one("#studio-layout-down", Button).disabled = not bool(
            section and section.can_move_down
        )
        self.query_one("#studio-layout-remove-collection", Button).disabled = not bool(
            section and section.can_remove
        )

        collection_value = self.query_one("#studio-layout-collection", Select).value
        collection = next(
            (
                item
                for item in self.structure.available_collections
                if collection_value is not Select.NULL
                and item.name == str(collection_value)
            ),
            None,
        )
        inline_selector = self.query_one("#studio-layout-inline-view", Select)
        collection_key = collection.name if collection is not None else None
        if collection_key != self._active_collection:
            self._active_collection = collection_key
            previous_inline = inline_selector.value
            inline_selector.set_options(
                tuple(
                    (name, name)
                    for name in (collection.inline_views if collection else ())
                )
            )
            inline_selector.value = (
                previous_inline
                if collection
                and previous_inline is not Select.NULL
                and str(previous_inline) in collection.inline_views
                else (
                    collection.inline_views[0]
                    if collection and collection.inline_views
                    else Select.NULL
                )
            )
        self.query_one("#studio-layout-add-collection", Button).disabled = not bool(
            collection and inline_selector.value is not Select.NULL
        )

        bar_key, actions, allowed = self._action_bar()
        current_selector = self.query_one("#studio-layout-current-action", Select)
        available = tuple(name for name in allowed if name not in actions)
        available_selector = self.query_one("#studio-layout-available-action", Select)
        if bar_key != self._active_action_bar:
            self._active_action_bar = bar_key
            current_selector.set_options(
                tuple((_action_label(name), name) for name in actions)
            )
            current_selector.value = actions[0] if actions else Select.NULL
            available_selector.set_options(
                tuple((_action_label(name), name) for name in available)
            )
            available_selector.value = available[0] if available else Select.NULL
        current = (
            str(current_selector.value)
            if current_selector.value is not Select.NULL
            else None
        )
        current_index = actions.index(current) if current in actions else -1
        self.query_one("#studio-layout-action-up", Button).disabled = current_index <= 0
        self.query_one("#studio-layout-action-down", Button).disabled = not (
            0 <= current_index < len(actions) - 1
        )
        self.query_one("#studio-layout-action-remove", Button).disabled = (
            current is None
        )
        self.query_one("#studio-layout-action-add", Button).disabled = (
            available_selector.value is Select.NULL
        )

    def _dismiss_action_edit(self, button_id: str) -> None:
        bar_key, current_actions, _allowed = self._action_bar()
        if bar_key is None:
            return
        actions = list(current_actions)
        if button_id == "studio-layout-action-add":
            value = self.query_one("#studio-layout-available-action", Select).value
            if value is Select.NULL:
                return
            actions.append(str(value))
        else:
            value = self.query_one("#studio-layout-current-action", Select).value
            if value is Select.NULL:
                return
            index = actions.index(str(value))
            if button_id == "studio-layout-action-remove":
                actions.pop(index)
            else:
                destination = index + (-1 if button_id.endswith("up") else 1)
                if destination < 0 or destination >= len(actions):
                    return
                actions[index], actions[destination] = (
                    actions[destination],
                    actions[index],
                )
        self.dismiss(
            StudioLayoutEdit(
                operation="actions",
                bar_key=bar_key,
                actions=tuple(actions),
            )
        )


def _action_label(name: str) -> str:
    """Label a bare action name, with no metadata entry to consult."""

    return humanize(name)
