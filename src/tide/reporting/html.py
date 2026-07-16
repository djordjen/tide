"""Standalone, print-oriented HTML report renderer."""

from __future__ import annotations

from html import escape
from pathlib import Path

from .document import ReportDocument


def render_html(document: ReportDocument) -> str:
    header_text = "".join(f"<p>{escape(text)}</p>" for text in document.header_text)
    record_values = "".join(
        "<div class=\"fact\"><dt>{}</dt><dd>{}</dd></div>".format(
            escape(value.label), escape(value.text)
        )
        for value in document.record_values
    )
    headings = "".join(
        f'<th class="{column.alignment}">{escape(column.label)}</th>'
        for column in document.detail.columns
    )
    rows = "".join(
        "<tr>{}</tr>".format(
            "".join(
                f'<td class="{cell.alignment}">{escape(cell.text)}</td>' for cell in row
            )
        )
        for row in document.detail.rows
    )
    footer_values = "".join(
        "<div class=\"total\"><dt>{}</dt><dd class=\"{}\">{}</dd></div>".format(
            escape(value.label), value.alignment, escape(value.text)
        )
        for value in document.footer_values
    )
    page_footer = escape(
        document.page_footer_template.format(page_number=1, page_count=1)
    )
    generated = document.generated_at.astimezone().strftime("%d.%m.%Y %H:%M")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(document.title)}</title>
  <style>
    @page {{ size: A4; margin: 18mm 16mm 18mm; }}
    :root {{ color-scheme: light; font-family: Arial, Helvetica, sans-serif; color: #172033; }}
    body {{ max-width: 900px; margin: 0 auto; font-size: 12px; line-height: 1.45; }}
    header {{ display: flex; justify-content: space-between; gap: 24px; border-bottom: 3px solid #2563eb; padding-bottom: 14px; }}
    h1 {{ margin: 0; color: #1d4ed8; font-size: 30px; letter-spacing: .02em; }}
    .application {{ margin-top: 5px; font-size: 14px; font-weight: 700; }}
    .generated {{ color: #64748b; text-align: right; }}
    .header-text {{ color: #475569; }}
    dl {{ margin: 22px 0; }}
    .facts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 7px 30px; }}
    .fact, .total {{ display: grid; grid-template-columns: 125px 1fr; gap: 10px; }}
    dt {{ color: #64748b; font-weight: 700; }} dd {{ margin: 0; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 18px; }}
    th {{ background: #1e3a5f; color: white; padding: 8px 7px; text-align: left; }}
    td {{ border-bottom: 1px solid #dbe3ee; padding: 8px 7px; vertical-align: top; }}
    tbody tr:nth-child(even) {{ background: #f8fafc; }}
    .right {{ text-align: right; }} .center {{ text-align: center; }}
    .totals {{ margin: 20px 0 0 auto; width: 310px; border-top: 2px solid #2563eb; padding-top: 10px; }}
    .total {{ grid-template-columns: 1fr 1fr; padding: 3px 0; }}
    .total:last-child {{ font-weight: 700; font-size: 14px; }}
    footer {{ margin-top: 40px; border-top: 1px solid #cbd5e1; padding-top: 8px; color: #64748b; text-align: center; }}
    @media print {{ body {{ max-width: none; }} .generated {{ color: #475569; }} }}
  </style>
</head>
<body>
  <header>
    <div><h1>{escape(document.title)}</h1><div class="application">{escape(document.application)}</div></div>
    <div class="generated">Generated<br>{escape(generated)}</div>
  </header>
  <div class="header-text">{header_text}</div>
  <dl class="facts">{record_values}</dl>
  <table>
    <thead><tr>{headings}</tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <dl class="totals">{footer_values}</dl>
  <footer>{page_footer}</footer>
</body>
</html>
"""


def write_html(document: ReportDocument, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_html(document), encoding="utf-8")
    return destination
