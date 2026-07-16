"""A4 PDF rendering for report documents, with ReportLab loaded on demand."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from .document import ReportDocument


class PdfDependencyMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "PDF export requires the 'report' extra: pip install tide-framework[report]"
        )


def render_pdf(document: ReportDocument) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:  # pragma: no cover - exercised without the extra.
        raise PdfDependencyMissing from error

    font, bold_font = _register_fonts(pdfmetrics, TTFont)
    buffer = BytesIO()
    page_width, _page_height = A4
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=17 * mm,
        bottomMargin=19 * mm,
        title=document.title,
        author=document.application,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TideTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=25,
        leading=29,
        textColor=colors.HexColor("#1D4ED8"),
        alignment=TA_LEFT,
        spaceAfter=3 * mm,
    )
    subtitle_style = ParagraphStyle(
        "TideSubtitle",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=11,
        textColor=colors.HexColor("#334155"),
        spaceAfter=6 * mm,
    )
    normal = ParagraphStyle(
        "TideNormal",
        parent=styles["Normal"],
        fontName=font,
        fontSize=8.5,
        leading=11,
    )
    label = ParagraphStyle(
        "TideLabel",
        parent=normal,
        fontName=bold_font,
        textColor=colors.HexColor("#64748B"),
    )
    table_header = ParagraphStyle(
        "TideTableHeader",
        parent=label,
        textColor=colors.white,
    )
    right = ParagraphStyle("TideRight", parent=normal, alignment=TA_RIGHT)
    center = ParagraphStyle("TideCenter", parent=normal, alignment=TA_CENTER)
    paragraph_styles = {"left": normal, "right": right, "center": center}

    story: list[Any] = [
        Paragraph(_xml(document.title), title_style),
        Paragraph(_xml(document.application), subtitle_style),
    ]
    story.extend(Paragraph(_xml(text), normal) for text in document.header_text)
    if document.header_text:
        story.append(Spacer(1, 3 * mm))
    if document.record_values:
        facts: list[list[Any]] = []
        values = list(document.record_values)
        for index in range(0, len(values), 2):
            row: list[Any] = []
            for value in values[index : index + 2]:
                row.extend(
                    [
                        Paragraph(_xml(value.label), label),
                        Paragraph(_xml(value.text), paragraph_styles[value.alignment]),
                    ]
                )
            while len(row) < 4:
                row.extend(["", ""])
            facts.append(row)
        facts_table = Table(
            facts,
            colWidths=[27 * mm, 53 * mm, 24 * mm, 58 * mm],
            hAlign="LEFT",
        )
        facts_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend([facts_table, Spacer(1, 5 * mm)])

    table_data: list[list[Any]] = [
        [
            Paragraph(_xml(column.label), table_header)
            for column in document.detail.columns
        ]
    ]
    table_data.extend(
        [
            Paragraph(_xml(cell.text), paragraph_styles[cell.alignment])
            for cell in row
        ]
        for row in document.detail.rows
    )
    widths = _column_widths(document, page_width - doc.leftMargin - doc.rightMargin)
    detail_table = Table(table_data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    detail_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, 0), 0.25, colors.HexColor("#1E3A5F")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([detail_table, Spacer(1, 6 * mm)])

    if document.footer_values:
        footer_data = [
            [
                Paragraph(_xml(value.label), label),
                Paragraph(_xml(value.text), paragraph_styles[value.alignment]),
            ]
            for value in document.footer_values
        ]
        totals = Table(footer_data, colWidths=[42 * mm, 35 * mm], hAlign="RIGHT")
        totals.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 1.2, colors.HexColor("#2563EB")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(totals)

    canvas_type = _numbered_canvas(
        Canvas,
        document.page_footer_template,
        font,
        page_width,
        10 * mm,
    )
    doc.build(story, canvasmaker=canvas_type)
    return buffer.getvalue()


def write_pdf(document: ReportDocument, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(render_pdf(document))
    return destination


def _register_fonts(pdfmetrics: Any, ttfont: Any) -> tuple[str, str]:
    candidates = (
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
    )
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            try:
                pdfmetrics.registerFont(ttfont("TideSans", str(regular)))
                pdfmetrics.registerFont(ttfont("TideSans-Bold", str(bold)))
                return "TideSans", "TideSans-Bold"
            except Exception:  # pragma: no cover - font registry is environment-specific.
                continue
    return "Helvetica", "Helvetica-Bold"


def _column_widths(document: ReportDocument, available: float) -> list[float]:
    weights: list[float] = []
    for column in document.detail.columns:
        name = column.name
        if name in {"line_number", "quantity"}:
            weights.append(0.65)
        elif name in {"unit_price", "total"}:
            weights.append(0.85)
        elif name == "description":
            weights.append(2.1)
        elif name == "product":
            weights.append(1.45)
        else:
            weights.append(1.0)
    total = sum(weights) or 1
    return [available * weight / total for weight in weights]


def _numbered_canvas(
    canvas_base: Any,
    template: str,
    font: str,
    page_width: float,
    footer_y: float,
) -> Any:
    class NumberedCanvas(canvas_base):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._saved_page_states: list[dict[str, Any]] = []

        def showPage(self) -> None:  # noqa: N802 - ReportLab API name.
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            page_count = len(self._saved_page_states)
            for page_number, state in enumerate(self._saved_page_states, start=1):
                self.__dict__.update(state)
                self.setStrokeColorRGB(0.8, 0.84, 0.89)
                self.line(16 * 2.83465, footer_y + 5, page_width - 16 * 2.83465, footer_y + 5)
                self.setFillColorRGB(0.4, 0.46, 0.55)
                self.setFont(font, 7.5)
                text = template.format(
                    page_number=page_number,
                    page_count=page_count,
                )
                self.drawCentredString(page_width / 2, footer_y, text)
                canvas_base.showPage(self)
            canvas_base.save(self)

    return NumberedCanvas


def _xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
