"""Textual preview and export surface for report documents."""

from __future__ import annotations

from pathlib import Path

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from tide.reporting import (
    PdfDependencyMissing,
    ReportDocument,
    write_html,
    write_pdf,
)


class ReportPreviewScreen(Screen[None]):
    """Preview a secured document and export stable HTML/PDF files."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    ReportPreviewScreen {
        layout: vertical;
        background: $surface;
    }

    #report-context {
        height: 2;
        padding: 0 2;
        color: $text-muted;
        content-align: left middle;
    }

    #report-scroll {
        height: 1fr;
        padding: 1 2;
    }

    #report-preview {
        width: 1fr;
    }

    #report-actions {
        height: 3;
        padding: 0 2;
        align-horizontal: right;
    }

    #report-actions Button {
        min-width: 14;
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("h", "export_html", "Export HTML"),
        Binding("p", "export_pdf", "Export PDF"),
        Binding("escape", "close", "Close"),
    ]

    def __init__(self, document: ReportDocument, output_directory: Path) -> None:
        super().__init__()
        self.document = document
        self.output_directory = output_directory
        self.title = document.application
        self.sub_title = f"Preview {document.title}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            f"{self.document.report}  ·  {self.document.suggested_filename}",
            id="report-context",
        )
        with VerticalScroll(id="report-scroll"):
            yield Static(_preview_renderable(self.document), id="report-preview")
        with Horizontal(id="report-actions"):
            yield Button("Close", id="close-report")
            yield Button("Export HTML", id="export-html")
            yield Button("Export PDF", id="export-pdf", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "close-report": self.action_close,
            "export-html": self.action_export_html,
            "export-pdf": self.action_export_pdf,
        }
        handler = handlers.get(event.button.id or "")
        if handler is not None:
            handler()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_export_html(self) -> None:
        path = self.output_directory / f"{self.document.suggested_filename}.html"
        try:
            write_html(self.document, path)
        except OSError as error:
            self.notify(f"HTML export failed: {error}", severity="error")
            return
        self.notify(f"HTML exported to {path}", severity="information")

    def action_export_pdf(self) -> None:
        path = self.output_directory / f"{self.document.suggested_filename}.pdf"
        try:
            write_pdf(self.document, path)
        except (OSError, PdfDependencyMissing) as error:
            self.notify(f"PDF export failed: {error}", severity="error")
            return
        self.notify(f"PDF exported to {path}", severity="information")


def _preview_renderable(document: ReportDocument) -> Group:
    heading = Text(document.title, style="bold bright_blue", justify="left")
    application = Text(document.application, style="bold")
    facts = Table.grid(expand=True, padding=(0, 2))
    facts.add_column(style="dim", ratio=1)
    facts.add_column(ratio=2)
    facts.add_column(style="dim", ratio=1)
    facts.add_column(ratio=2)
    values = list(document.record_values)
    for index in range(0, len(values), 2):
        pair = values[index : index + 2]
        cells: list[str | Text] = []
        for value in pair:
            cells.extend((value.label, Text(value.text, justify=value.alignment)))
        while len(cells) < 4:
            cells.extend(("", ""))
        facts.add_row(*cells)

    detail = Table(expand=True, show_lines=False, header_style="bold white on #1e3a5f")
    for column in document.detail.columns:
        detail.add_column(column.label, justify=column.alignment)
    for row in document.detail.rows:
        detail.add_row(*(Text(cell.text, justify=cell.alignment) for cell in row))

    totals = Table.grid(padding=(0, 2))
    totals.add_column(style="bold", justify="right")
    totals.add_column(justify="right")
    for value in document.footer_values:
        totals.add_row(value.label, value.text)

    contents: list[object] = [heading, application]
    contents.extend(Text(text, style="dim") for text in document.header_text)
    contents.extend((Text(""), facts, Text(""), detail, Text(""), totals))
    return Group(Panel(Group(*contents), border_style="blue", padding=(1, 2)))
