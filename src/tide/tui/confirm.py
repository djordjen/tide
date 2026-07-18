"""Small reusable confirmation surfaces for destructive TUI operations."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class DeleteConfirmationScreen(ModalScreen[bool]):
    """Require an explicit second gesture before deleting one record."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    DeleteConfirmationScreen {
        align: center middle;
        background: $background 65%;
    }

    #delete-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #delete-title {
        height: 2;
        color: $warning;
        text-style: bold;
    }

    #delete-message {
        height: auto;
        margin-bottom: 1;
        color: $text;
    }

    #delete-actions {
        height: 3;
        align-horizontal: right;
    }

    #delete-actions Button {
        min-width: 14;
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, entity_label: str, record_title: str) -> None:
        super().__init__()
        singular = entity_label.removesuffix("s") or entity_label
        self.entity_label = singular
        self.record_title = record_title

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-dialog"):
            yield Static(f"Delete {self.entity_label}?", id="delete-title")
            yield Static(
                f"Delete {self.record_title!r}? This operation cannot be undone.",
                id="delete-message",
                markup=False,
            )
            with Horizontal(id="delete-actions"):
                yield Button("Keep record", id="cancel-delete")
                yield Button(
                    "Delete",
                    id="confirm-delete",
                    variant="error",
                )

    def on_mount(self) -> None:
        self.query_one("#cancel-delete", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-delete":
            self.dismiss(True)
        elif event.button.id == "cancel-delete":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(False)
