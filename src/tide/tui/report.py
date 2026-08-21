"""Textual preview and export surface for report documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Input, Static

from tide.tui.header import TideHeader
from tide.labels import humanize
from tide.reporting import (
    PdfDependencyMissing,
    ReportDocument,
    write_csv,
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
        Binding("c", "export_csv", "Export CSV"),
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
        yield TideHeader(show_clock=False)
        yield Static(
            f"{self.document.report}  ·  {self.document.suggested_filename}",
            id="report-context",
        )
        with VerticalScroll(id="report-scroll"):
            yield Static(_preview_renderable(self.document), id="report-preview")
        with Horizontal(id="report-actions"):
            yield Button("Close", id="close-report")
            yield Button("Export CSV", id="export-csv")
            yield Button("Export HTML", id="export-html")
            yield Button("Export PDF", id="export-pdf", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "close-report": self.action_close,
            "export-csv": self.action_export_csv,
            "export-html": self.action_export_html,
            "export-pdf": self.action_export_pdf,
        }
        handler = handlers.get(event.button.id or "")
        if handler is not None:
            handler()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_export_csv(self) -> None:
        path = self.output_directory / f"{self.document.suggested_filename}.csv"
        try:
            write_csv(self.document, path)
        except OSError as error:
            self.notify(f"CSV export failed: {error}", severity="error")
            return
        self.notify(f"CSV exported to {path}", severity="information")

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


class ReportParametersScreen(ModalScreen[dict[str, Any] | None]):
    """Ask for a report's parameter values before building it.

    The inputs collect strings and nothing else: typing, ranges and the
    required check all belong to `ReportService`, which validates the same
    way for every surface. A blank input is simply not sent, which for an
    optional parameter means its criteria clause is dropped -- so building
    with every field empty is the same report `{}` always built.
    """

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    ReportParametersScreen {
        align: center middle;
        background: $background 65%;
    }

    #report-parameters-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #report-parameters-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    .report-parameter-label {
        height: 1;
        color: $text-muted;
    }

    #report-parameters-dialog Input {
        margin-bottom: 1;
    }

    #report-parameters-actions {
        height: 3;
        align-horizontal: right;
    }

    #report-parameters-actions Button {
        min-width: 14;
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(
        self,
        title: str,
        parameters: Mapping[str, Mapping[str, Any]],
    ) -> None:
        super().__init__()
        self.report_title = title
        self.parameters = parameters

    def compose(self) -> ComposeResult:
        with Vertical(id="report-parameters-dialog"):
            yield Static(f"{self.report_title} parameters", id="report-parameters-title")
            for name, definition in self.parameters.items():
                requirement = (
                    "required"
                    if definition.get("required") and definition.get("default") is None
                    else "optional"
                )
                yield Static(
                    f"{humanize(name)} ({definition.get('type', 'string')}, "
                    f"{requirement})",
                    classes="report-parameter-label",
                    markup=False,
                )
                yield Input(
                    placeholder=_parameter_placeholder(definition),
                    id=f"report-parameter-{name.replace('_', '-')}",
                )
            with Horizontal(id="report-parameters-actions"):
                yield Button("Cancel", id="cancel-report-parameters")
                yield Button(
                    "Build report",
                    id="build-report",
                    variant="primary",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "build-report":
            self.dismiss(self._collect())
        elif event.button.id == "cancel-report-parameters":
            self.action_cancel()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.dismiss(self._collect())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _collect(self) -> dict[str, Any]:
        supplied: dict[str, Any] = {}
        for name in self.parameters:
            value = self.query_one(
                f"#report-parameter-{name.replace('_', '-')}", Input
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
    if document.groups:
        # Rich has no colspan, so a band is a section: a rule, the heading
        # carried in the first cell, the group's rows, and its subtotal
        # right-aligned in the last cell -- all inside the one table so the
        # columns keep their shared widths.
        for group in document.groups:
            heading_text = "  ·  ".join(
                f"{value.label}: {value.text}" for value in group.values
            )
            heading_row: list[Text] = [
                Text(heading_text, style="bold #1e3a5f", overflow="fold")
            ] + [Text("")] * (len(document.detail.columns) - 1)
            detail.add_section()
            detail.add_row(*heading_row)
            for row in document.detail.rows[
                group.row_start : group.row_start + group.row_count
            ]:
                detail.add_row(
                    *(Text(cell.text, justify=cell.alignment) for cell in row)
                )
            subtotal_text = "  ·  ".join(
                f"{value.label}: {value.text}" for value in group.footer_values
            )
            # The first column is the widest at 80 columns, so the subtotal
            # folds there legibly instead of wrapping in the narrow last one.
            subtotal_row = [
                Text(subtotal_text, style="bold", overflow="fold")
            ] + [Text("")] * (len(document.detail.columns) - 1)
            detail.add_row(*subtotal_row)
            detail.add_section()
    else:
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
