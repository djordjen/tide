"""First property-editing Textual shell for TIDE Studio."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
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
    StudioSaveReview,
    StudioService,
    StudioSessionState,
    StudioViewField,
    StudioViewGroup,
    StudioViewSection,
    StudioViewPreview,
    StudioViewStructure,
)


@dataclass(frozen=True)
class StudioGroupEdit:
    """One requested in-memory group operation from the modal editor."""

    operation: Literal["create", "rename", "move", "remove"]
    group_key: str | None = None
    label: str | None = None
    offset: Literal[-1, 1] | None = None


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


class StudioSaveScreen(ModalScreen[str | None]):
    """Review and explicitly approve one exact Studio candidate save."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    StudioSaveScreen {
        align: center middle;
        background: $background 70%;
    }

    #studio-save-dialog {
        width: 94%;
        height: 90%;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #studio-save-title {
        height: 2;
        color: $warning;
        text-style: bold;
    }

    #studio-save-summary {
        height: auto;
        max-height: 10;
        color: $text-muted;
    }

    #studio-save-diff {
        height: 1fr;
        margin: 1 0;
        border: round $primary;
    }

    #studio-save-challenge {
        height: auto;
        color: $text;
    }

    #studio-save-approval {
        height: 3;
    }

    #studio-save-actions {
        height: 3;
        align-horizontal: right;
    }

    #studio-save-actions Button {
        min-width: 16;
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "approve", "Save", show=False),
    ]

    def __init__(self, review: StudioSaveReview) -> None:
        super().__init__()
        self.review = review

    def compose(self) -> ComposeResult:
        preparation = self.review.preparation
        ready = preparation.ready and preparation.approval_prompt is not None
        with Vertical(id="studio-save-dialog"):
            yield Static("Review exact Designer save", id="studio-save-title")
            yield Static(
                _save_review_summary(self.review),
                id="studio-save-summary",
                markup=False,
            )
            yield TextArea(
                preparation.diff or "# No candidate diff is available.\n",
                read_only=True,
                show_line_numbers=True,
                soft_wrap=False,
                id="studio-save-diff",
            )
            yield Static(
                (
                    f"Type exactly: {preparation.approval_prompt}"
                    if ready
                    else "Save approval is unavailable until every blocker is resolved."
                ),
                id="studio-save-challenge",
                markup=False,
            )
            yield Input(
                placeholder=(preparation.approval_prompt or "Designer save is blocked"),
                disabled=not ready,
                id="studio-save-approval",
            )
            with Horizontal(id="studio-save-actions"):
                yield Button("Cancel", id="cancel-save")
                yield Button(
                    "Save approved candidate",
                    id="confirm-save",
                    variant="warning",
                    disabled=True,
                )

    def on_mount(self) -> None:
        if self.review.preparation.ready:
            self.query_one("#studio-save-approval", Input).focus()
        else:
            self.query_one("#cancel-save", Button).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "studio-save-approval":
            return
        expected = self.review.preparation.approval_prompt
        self.query_one("#confirm-save", Button).disabled = event.value != expected

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "studio-save-approval":
            self.action_approve()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-save":
            self.action_approve()
        elif event.button.id == "cancel-save":
            self.action_cancel()

    def action_approve(self) -> None:
        approval = self.query_one("#studio-save-approval", Input)
        if approval.disabled:
            return
        expected = self.review.preparation.approval_prompt
        if expected is not None and approval.value == expected:
            self.dismiss(approval.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


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

    def _dismiss_action_edit(self, button_id: str | None) -> None:
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


class StudioPreviewScreen(ModalScreen[None]):
    """Inspect one compiled view as a selected role and terminal size."""

    ENABLE_COMMAND_PALETTE = False
    _SIZES: tuple[tuple[str, str, int, int], ...] = (
        ("Compact · 80 × 24", "80x24", 80, 24),
        ("Standard · 100 × 30", "100x30", 100, 30),
        ("Wide · 140 × 40", "140x40", 140, 40),
    )

    CSS = """
    StudioPreviewScreen {
        align: center middle;
        background: $background 70%;
    }

    #studio-preview-dialog {
        width: 96%;
        height: 94%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #studio-preview-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    #studio-preview-controls {
        height: 3;
    }

    #studio-preview-role, #studio-preview-size {
        width: 1fr;
        margin-right: 1;
    }

    #studio-preview-summary {
        height: 3;
        color: $text-muted;
    }

    #studio-preview-canvas {
        height: 1fr;
        border: round $primary;
    }

    #studio-preview-actions {
        height: 3;
        align-horizontal: right;
    }

    #studio-preview-actions Button {
        min-width: 14;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(
        self,
        service: StudioService,
        target: DesignerDocumentReference,
    ) -> None:
        super().__init__()
        self.service = service
        self.target = target
        probe = service.preview_view(target, role=None, width=100, height=30)
        self.role = probe.available_roles[0] if probe.available_roles else None
        self.preview = service.preview_view(
            target,
            role=self.role,
            width=100,
            height=30,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="studio-preview-dialog"):
            yield Static(
                f"Role & terminal preview — {self.preview.view}",
                id="studio-preview-title",
            )
            with Horizontal(id="studio-preview-controls"):
                yield Select[str](
                    (("(No role)", "__none__"),)
                    + tuple((role.replace("_", " ").title(), role) for role in self.preview.available_roles),
                    value=self.role or "__none__",
                    allow_blank=False,
                    id="studio-preview-role",
                )
                yield Select[str](
                    tuple((label, value) for label, value, _width, _height in self._SIZES),
                    value="100x30",
                    allow_blank=False,
                    id="studio-preview-size",
                )
            yield Static(
                _studio_preview_summary(self.preview),
                id="studio-preview-summary",
                markup=False,
            )
            yield TextArea(
                _studio_view_preview_text(self.preview),
                read_only=True,
                show_line_numbers=False,
                soft_wrap=False,
                id="studio-preview-canvas",
            )
            with Horizontal(id="studio-preview-actions"):
                yield Button("Close", id="studio-preview-close")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id not in {"studio-preview-role", "studio-preview-size"}:
            return
        role_value = self.query_one("#studio-preview-role", Select).value
        size_value = self.query_one("#studio-preview-size", Select).value
        if role_value is Select.NULL or size_value is Select.NULL:
            return
        role = None if str(role_value) == "__none__" else str(role_value)
        size = next(
            (
                (width, height)
                for _label, value, width, height in self._SIZES
                if value == str(size_value)
            ),
            None,
        )
        if size is None:
            return
        try:
            self.preview = self.service.preview_view(
                self.target,
                role=role,
                width=size[0],
                height=size[1],
            )
        except (StudioError, ValueError) as error:
            self.app.notify(str(error), severity="error")
            return
        self.role = role
        self.query_one("#studio-preview-summary", Static).update(
            _studio_preview_summary(self.preview)
        )
        self.query_one("#studio-preview-canvas", TextArea).load_text(
            _studio_view_preview_text(self.preview)
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "studio-preview-close":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


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
        view_fields = self.query_one("#view-field-table", DataTable)
        view_fields.add_column("Track", key="track", width=22)
        view_fields.add_column("#", key="position", width=4)
        view_fields.add_column("Field", key="field", width=18)
        view_fields.add_column("Label", key="label")
        view_fields.add_column("Type", key="type", width=12)
        view_fields.add_column("Origin", key="origin", width=18)
        view_fields.cursor_type = "row"
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
        selected_row = 0
        for index, field in enumerate(structure.fields):
            row_key = f"view-field-{index}"
            self._view_field_rows[row_key] = field
            table.add_row(
                (
                    f"{field.track_label} / {field.source_group}"
                    if field.source_group
                    else field.track_label
                ),
                str(field.position + 1),
                field.name,
                field.label,
                field.field_type,
                field.origin,
                key=row_key,
            )
            if field.key == self._selected_view_field_key:
                selected_row = index
        self.query_one("#view-structure", Horizontal).display = True
        self.query_one("#view-structure-preview", Static).update(
            _view_structure_preview(structure)
        )
        if structure.fields:
            table.move_cursor(row=selected_row)
            self._select_view_field(f"view-field-{selected_row}")
        self._sync_view_field_controls()

    def _select_view_field(self, row_key: str) -> None:
        selected = self._view_field_rows.get(row_key)
        if selected is None:
            return
        self.selected_view_field = selected
        self._selected_view_field_key = selected.key
        group_selector = self.query_one("#view-field-group-choice", Select)
        if selected.source_group_key is not None and any(
            group.key == selected.source_group_key
            for group in (self.view_structure.groups if self.view_structure else ())
        ):
            group_selector.value = selected.source_group_key
            self._selected_view_group_label = selected.source_group
        self._sync_view_field_controls()

    def _sync_view_field_controls(self) -> None:
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


def _save_review_summary(review: StudioSaveReview) -> str:
    preparation = review.preparation
    changed = ", ".join(preparation.changed_files) or "none"
    lines = [
        preparation.summary,
        f"Project: {preparation.project_path}",
        f"Changed YAML: {changed}",
        f"Receipt: {preparation.receipt_path or 'not available'}",
    ]
    lines.extend(
        f"{blocker.code}: {blocker.message}" for blocker in preparation.blockers
    )
    if review.recovery is not None:
        lines.append(review.recovery.summary)
        lines.extend(
            f"{blocker.code}: {blocker.message}" for blocker in review.recovery.blockers
        )
    if review.recovery_command is not None:
        lines.append(f"Recovery preview: {review.recovery_command}")
    return "\n".join(lines)


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


def _action_label(name: str) -> str:
    return name.replace("_", " ").title()


def _studio_preview_summary(preview: StudioViewPreview) -> str:
    role = preview.role or "(no role)"
    return (
        f"{role} · {preview.width} × {preview.height} · {preview.fit.upper()} · "
        f"minimum {preview.minimum_width} × {preview.minimum_height} · "
        f"{len(preview.effective_permissions)} effective permission(s)\n"
        "Static metadata/security preview only · no records, database, or application code"
    )


def _studio_view_preview_text(preview: StudioViewPreview) -> str:
    status_marker = {
        "editable": "E",
        "conditional": "?",
        "read_only": "R",
        "protected": "P",
        "hidden": "H",
    }
    body: list[str] = []
    access = "  ".join(
        f"{item.operation}:{'yes' if item.allowed else 'no'}"
        for item in preview.access
    )
    body.append(f"Access  {access}")
    if preview.sections:
        tabs: list[str] = []
        for section in preview.sections:
            label = section.tab or "General"
            if label not in tabs:
                tabs.append(label)
        if any(section.tab for section in preview.sections):
            body.append("Tabs    " + " | ".join(tabs))
        body.append(
            "Layout  "
            + " -> ".join(
                f"{section.label} [{section.kind}]" for section in preview.sections
            )
        )
    track_order: list[str] = []
    fields_by_track: dict[str, list[str]] = {}
    for field in preview.fields:
        if field.track_label not in fields_by_track:
            track_order.append(field.track_label)
            fields_by_track[field.track_label] = []
        fields_by_track[field.track_label].append(
            f"{status_marker[field.status]}:{field.label}"
        )
    for track in track_order:
        body.append(f"{track}  " + " | ".join(fields_by_track[track]))
    action_bars: dict[str, list[str]] = {}
    section_labels = {section.key: section.label for section in preview.sections}
    for action in preview.actions:
        label = "Record actions" if action.bar == "record" else (
            f"{section_labels.get(action.bar, action.bar)} actions"
        )
        action_bars.setdefault(label, []).append(
            f"{action.name}:{'on' if action.enabled else 'off'}"
            + ("?" if action.runtime_condition else "")
        )
    for label, actions in action_bars.items():
        body.append(f"{label}  " + " | ".join(actions))
    if preview.warnings:
        body.append("Warnings")
        body.extend(f"! {warning}" for warning in preview.warnings)
    else:
        body.append("No preview warnings.")

    width = preview.width
    height = preview.height
    inner_width = width - 2
    title = f" {preview.view} · {preview.role or 'no role'} · {preview.fit} "
    title = title[:inner_width]
    canvas = ["+" + title + "-" * (inner_width - len(title)) + "+"]
    available_body_rows = height - 2
    if len(body) > available_body_rows:
        body = body[: max(0, available_body_rows - 1)] + [
            "... preview content clipped at selected terminal height ..."
        ]
    for line in body:
        clipped = line[:inner_width]
        canvas.append("|" + clipped + " " * (inner_width - len(clipped)) + "|")
    while len(canvas) < height - 1:
        canvas.append("|" + " " * inner_width + "|")
    canvas.append("+" + "-" * inner_width + "+")

    details = [
        "",
        "Resolved preview details",
        "Legend: E editable, ? record-dependent, R read-only, P protected, H hidden",
        "",
        "Entity access:",
    ]
    details.extend(
        f"- {item.operation}: {'allowed' if item.allowed else 'denied'}"
        + (f" ({item.permission})" if item.permission else " (no permission declared)")
        for item in preview.access
    )
    details.append("")
    details.append("Field placements:")
    details.extend(
        f"- [{status_marker[field.status]}] {field.track_label} / {field.label}: "
        f"{field.reason}"
        for field in preview.fields
    )
    details.append("")
    details.append("Actions:")
    details.extend(
        f"- {action.label}: {'enabled' if action.enabled else 'disabled'}"
        f"{' (record-dependent)' if action.runtime_condition else ''} · {action.reason}"
        for action in preview.actions
    )
    if preview.warnings:
        details.append("")
        details.append("Warnings:")
        details.extend(f"- {warning}" for warning in preview.warnings)
    return "\n".join((*canvas, *details)) + "\n"


def _text_location(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    row = before.count("\n")
    column = len(before.rsplit("\n", 1)[-1])
    return row, column
