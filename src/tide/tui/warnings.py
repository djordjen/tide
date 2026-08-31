"""The confirmation a warning-severity refusal asks for.

Errors are fixed and warnings are weighed: when a commit is refused only
by warning-severity rules, this modal lists what the rules said and lets
the person proceed or step back. Confirming dismisses with True and the
caller retries the same door with the warnings acknowledged; anything
else dismisses with False and the form stays as it was.
"""

from __future__ import annotations

from typing import Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class WarningsScreen(ModalScreen[bool]):
    """List warning messages over a Save anyway / Cancel choice."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    WarningsScreen {
        align: center middle;
        background: $background 65%;
    }

    #warnings-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #warnings-title {
        height: 2;
        color: $warning;
        text-style: bold;
    }

    .warning-message {
        height: auto;
        margin-bottom: 1;
    }

    #warnings-actions {
        height: 3;
        align-horizontal: right;
    }

    #warnings-actions Button {
        min-width: 14;
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(
        self,
        messages: Sequence[str],
        *,
        confirm_label: str = "Save anyway",
    ) -> None:
        super().__init__()
        self.messages = tuple(messages)
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="warnings-dialog"):
            yield Static("Warnings", id="warnings-title")
            for message in self.messages:
                yield Static(message, classes="warning-message", markup=False)
            with Horizontal(id="warnings-actions"):
                yield Button("Cancel", id="cancel-warnings")
                yield Button(
                    self.confirm_label,
                    id="confirm-warnings",
                    variant="warning",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-warnings":
            self.dismiss(True)
        elif event.button.id == "cancel-warnings":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(False)
