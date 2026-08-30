"""One modal for declared parameters, shared by reports and actions.

The inputs collect strings and nothing else: typing, ranges and the
required check all belong to the owning service, which validates the same
way for every surface. A blank input is simply not sent -- for an optional
report parameter that drops its criteria clause, and for an action it
leaves the default to the service.
"""

from __future__ import annotations

from typing import Any, Mapping

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from tide.labels import humanize


class ParametersScreen(ModalScreen[dict[str, Any] | None]):
    """Ask for declared parameter values before a report builds or an
    action executes; dismisses with the collected strings, or None."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    ParametersScreen {
        align: center middle;
        background: $background 65%;
    }

    #parameters-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #parameters-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    .parameter-label {
        height: 1;
        color: $text-muted;
    }

    #parameters-dialog Input {
        margin-bottom: 1;
    }

    #parameters-actions {
        height: 3;
        align-horizontal: right;
    }

    #parameters-actions Button {
        min-width: 14;
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(
        self,
        title: str,
        parameters: Mapping[str, Mapping[str, Any]],
        *,
        confirm_label: str = "OK",
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.parameters = parameters
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="parameters-dialog"):
            yield Static(f"{self.dialog_title} parameters", id="parameters-title")
            for name, definition in self.parameters.items():
                requirement = (
                    "required"
                    if definition.get("required") and definition.get("default") is None
                    else "optional"
                )
                yield Static(
                    f"{humanize(name)} ({definition.get('type', 'string')}, "
                    f"{requirement})",
                    classes="parameter-label",
                    markup=False,
                )
                yield Input(
                    placeholder=_parameter_placeholder(definition),
                    id=f"parameter-{name.replace('_', '-')}",
                )
            with Horizontal(id="parameters-actions"):
                yield Button("Cancel", id="cancel-parameters")
                yield Button(
                    self.confirm_label,
                    id="confirm-parameters",
                    variant="primary",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-parameters":
            self.dismiss(self._collect())
        elif event.button.id == "cancel-parameters":
            self.action_cancel()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.dismiss(self._collect())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _collect(self) -> dict[str, Any]:
        supplied: dict[str, Any] = {}
        for name in self.parameters:
            value = self.query_one(
                f"#parameter-{name.replace('_', '-')}", Input
            ).value.strip()
            if value:
                supplied[name] = value
        return supplied


def _parameter_placeholder(definition: Mapping[str, Any]) -> str:
    hints = {
        "date": "for example 2026-08-16",
        "datetime": "for example 2026-08-16T09:00:00",
        "integer": "a whole number",
        "decimal": "a number",
        "boolean": "true or false",
        "string": "text",
    }
    hint = hints.get(str(definition.get("type", "string")), "text")
    default = definition.get("default")
    if default is not None:
        return f"{hint} (default: {default})"
    return f"{hint} (leave blank to skip)"
