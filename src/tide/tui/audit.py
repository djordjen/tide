"""Read-only Textual surface for safe action audit history."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from tide.services import ActionAuditEvent


class AuditHistoryScreen(Screen[None]):
    """Display newest-first action history without exposing stored payloads."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    AuditHistoryScreen {
        layout: vertical;
        background: $surface;
    }

    #audit-context {
        height: 3;
        padding: 0 2;
        color: $text-muted;
        content-align: left middle;
    }

    #audit-events {
        height: 1fr;
        margin: 0 1;
        border: round $primary;
    }

    #audit-status {
        height: 2;
        padding: 0 2;
        color: $text-muted;
        content-align: left middle;
    }

    #audit-actions {
        height: 3;
        padding: 0 2;
        align-horizontal: right;
    }

    #audit-actions Button {
        min-width: 14;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(
        self,
        application: str,
        record_title: str,
        events: tuple[ActionAuditEvent, ...],
    ) -> None:
        super().__init__()
        self.events = events
        self.title = application
        self.sub_title = "Audit history"
        self.record_title = record_title

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            f"{self.record_title}  ·  Action history",
            id="audit-context",
        )
        yield DataTable(id="audit-events")
        count = len(self.events)
        noun = "event" if count == 1 else "events"
        yield Static(
            f"{count} {noun}  ·  Newest first  ·  Payloads are never included",
            id="audit-status",
        )
        with Horizontal(id="audit-actions"):
            yield Button("Close", id="close-audit", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#audit-events", DataTable)
        table.add_columns(
            "Started",
            "Action",
            "Outcome",
            "Principal",
            "Channel",
            "Correlation",
        )
        table.cursor_type = "row"
        table.zebra_stripes = True
        for event in self.events:
            table.add_row(
                _format_timestamp(event.started_at),
                event.action,
                str(event.outcome),
                event.principal,
                event.channel,
                event.correlation_id,
                key=event.event_id,
            )
        table.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-audit":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


def _format_timestamp(value: datetime) -> str:
    localized = value.astimezone() if value.tzinfo is not None else value
    return localized.strftime("%d.%m.%Y %H:%M:%S")
