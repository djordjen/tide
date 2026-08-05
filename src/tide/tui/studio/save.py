"""Reviewing and naming a Studio save before it writes anything."""

from __future__ import annotations


from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Static,
    TextArea,
)

from tide.development.studio import (
    StudioSaveReview,
)


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
