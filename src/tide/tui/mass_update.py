"""One field for the marked rows: the terminal's mass-update dialog.

Scalar fields only, deliberately: the browser's dialog offers the
reference picker, while a lookup-in-a-modal is more terminal than this
slice wants -- and file fields claim a staged upload exactly once, so
assigning one to many rows is nonsense on every surface. Choice and
boolean fields get a select; everything else is typed text parsed the way
the record form parses it, with the service refusing what stays a string.
"""

from __future__ import annotations

from typing import Any, Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from tide.compiler.normalized import NormalizedEntity, NormalizedField, field_is_writable
from tide.presentation import field_label
from tide.tui.form import parse_editor_text

_TEXT_HINTS = {
    "date": "for example 2026-08-16",
    "datetime": "for example 2026-08-16T09:00:00",
    "integer": "a whole number",
    "decimal": "a number",
    "string": "text",
    "uuid": "a UUID",
}


def mass_assignable_fields(entity: NormalizedEntity) -> tuple[NormalizedField, ...]:
    """The fields this dialog offers: assignable scalars, in declaration order."""

    return tuple(
        field
        for field in entity.fields.values()
        if field.metadata["type"] not in {"collection", "reference", "file"}
        and field_is_writable(field, "update")
    )


class MassUpdateScreen(ModalScreen[tuple[str, Any] | None]):
    """Pick one field and one value for every marked row.

    Dismisses with ``(field_name, typed_value)`` -- ``None`` as the value
    only through the explicit Clear button -- or ``None`` for cancel.
    """

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    MassUpdateScreen {
        align: center middle;
        background: $background 65%;
    }

    #mass-update-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #mass-update-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    .mass-update-label {
        height: 1;
        color: $text-muted;
    }

    #mass-update-dialog Input,
    #mass-update-dialog Select {
        margin-bottom: 1;
    }

    #mass-update-actions {
        height: 3;
        align-horizontal: right;
    }

    #mass-update-actions Button {
        min-width: 10;
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, entity: NormalizedEntity, count: int) -> None:
        super().__init__()
        self.entity = entity
        self.count = count
        self.fields = mass_assignable_fields(entity)

    def compose(self) -> ComposeResult:
        noun = "record" if self.count == 1 else "records"
        with Vertical(id="mass-update-dialog"):
            yield Static(
                f"Mass update — {self.count} {noun}", id="mass-update-title"
            )
            yield Static("Field", classes="mass-update-label")
            yield Select(
                tuple((field_label(field), field.name) for field in self.fields),
                prompt="Choose a field",
                allow_blank=True,
                id="mass-field",
            )
            yield Static("New value", classes="mass-update-label")
            # The union of both editors, toggled by the chosen field's type:
            # remounting one per selection races Textual's scheduled
            # removals, a display flag has no lifecycle.
            yield Input(id="mass-value", disabled=True)
            choices = Select[Any](
                (),
                prompt="Choose a value",
                allow_blank=True,
                id="mass-choice",
            )
            choices.display = False
            yield choices
            with Horizontal(id="mass-update-actions"):
                yield Button("Cancel", id="mass-cancel")
                yield Button("Clear field", id="mass-clear", variant="warning")
                yield Button("Apply", id="mass-apply", variant="primary")

    def _chosen_field(self) -> NormalizedField | None:
        value = self.query_one("#mass-field", Select).value
        if value is Select.NULL:
            return None
        return self.entity.field(str(value))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "mass-field":
            return
        field = self._chosen_field()
        text_input = self.query_one("#mass-value", Input)
        choices = self.query_one("#mass-choice", Select)
        if field is None:
            text_input.disabled = True
            return
        field_type = str(field.metadata["type"])
        if field_type == "boolean":
            choices.set_options((("true", True), ("false", False)))
        elif field_type == "choice":
            choices.set_options(
                tuple(
                    (str(choice), choice)
                    for choice in field.metadata.get("choices", ())
                )
            )
        uses_choices = field_type in {"boolean", "choice"}
        choices.display = uses_choices
        text_input.display = not uses_choices
        text_input.disabled = uses_choices
        if not uses_choices:
            text_input.placeholder = _TEXT_HINTS.get(field_type, "text")
            text_input.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mass-cancel":
            self.action_cancel()
        elif event.button.id == "mass-clear":
            self._clear_field()
        elif event.button.id == "mass-apply":
            self._apply()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._apply()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _apply(self) -> None:
        field = self._chosen_field()
        if field is None:
            self.notify("Choose a field first.", severity="warning")
            return
        if str(field.metadata["type"]) in {"boolean", "choice"}:
            chosen = self.query_one("#mass-choice", Select).value
            if chosen is Select.NULL:
                self.notify(
                    "Choose a value, or Clear field to blank it.",
                    severity="warning",
                )
                return
            self.dismiss((field.name, chosen))
            return
        raw = self.query_one("#mass-value", Input).value
        if not raw.strip():
            # An untouched input must not quietly blank twenty rows; the
            # explicit button is the deliberate act.
            self.notify(
                "Type a value, or Clear field to blank it.",
                severity="warning",
            )
            return
        self.dismiss((field.name, parse_editor_text(field, raw)))

    def _clear_field(self) -> None:
        field = self._chosen_field()
        if field is None:
            self.notify("Choose a field first.", severity="warning")
            return
        self.dismiss((field.name, None))


class MassUpdateReportScreen(ModalScreen[None]):
    """What a mass update could not do, row by row, under the counts."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    MassUpdateReportScreen {
        align: center middle;
        background: $background 65%;
    }

    #mass-report-dialog {
        width: 72;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #mass-report-title {
        height: 2;
        color: $warning;
        text-style: bold;
    }

    .mass-update-refusal {
        height: auto;
        margin-bottom: 1;
    }

    #mass-report-actions {
        height: 3;
        align-horizontal: right;
    }

    #mass-report-actions Button {
        min-width: 10;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(
        self,
        updated: int,
        total: int,
        refusals: Sequence[tuple[str, str]],
    ) -> None:
        super().__init__()
        self.updated = updated
        self.total = total
        self.refusals = tuple(refusals)

    def compose(self) -> ComposeResult:
        with Vertical(id="mass-report-dialog"):
            yield Static(
                f"Updated {self.updated} of {self.total} records",
                id="mass-report-title",
            )
            for label, message in self.refusals:
                yield Static(
                    f"{label} — {message}",
                    classes="mass-update-refusal",
                    markup=False,
                )
            with Horizontal(id="mass-report-actions"):
                yield Button("Close", id="close-mass-report", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-mass-report":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)
