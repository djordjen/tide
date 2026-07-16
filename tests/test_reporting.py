from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.reporting import ReportService, render_html, render_pdf, write_html, write_pdf
from tide.runtime import AuthorizationError, Channel, Principal, RequestContext, ValidationFailed
from tide.services import RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.fixture
def reporting() -> tuple[ReportService, RequestContext]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    service = ReportService(model, records)
    context = RequestContext(
        Principal("report:user", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )
    return service, context


def test_report_service_builds_secured_formatted_invoice(reporting) -> None:
    service, context = reporting

    document = service.build_for_record(
        "sales.invoice",
        1,
        context,
        generated_at=datetime(2026, 7, 16, 10, 30, tzinfo=timezone.utc),
    )

    assert document.title == "Invoice"
    assert document.suggested_filename == "invoice-INV-2026-0001"
    assert [(value.label, value.text) for value in document.record_values] == [
        ("Invoice number", "INV-2026-0001"),
        ("Invoice date", "01.07.2026"),
        ("Customer", "ADRIA - Adria Consulting"),
        ("Status", "Posted"),
    ]
    assert [column.name for column in document.detail.columns] == [
        "line_number",
        "product",
        "description",
        "quantity",
        "unit_price",
        "total",
    ]
    assert [cell.text for cell in document.detail.rows[0]] == [
        "1",
        "CONS - Consulting hour",
        "Demo invoice line",
        "10",
        "85.00",
        "850.00",
    ]
    assert document.detail.rows[0][-1].alignment == "right"
    assert document.page_footer_template == "Page {page_number}"
    assert "Total: 850.00" in document.plain_text()


def test_report_permission_and_protected_detail_fail_closed(reporting) -> None:
    service, _context = reporting
    denied = RequestContext(
        Principal("summary", roles=frozenset({"summary_viewer"})),
        channel=Channel.TUI,
    )
    with pytest.raises(AuthorizationError, match="may not generate"):
        service.build_for_record("sales.invoice", 1, denied)

    no_detail = RequestContext(
        Principal(
            "custom",
            permissions=frozenset({"sales.invoice.read", "sales.invoice.report"}),
        ),
        channel=Channel.REST,
    )
    with pytest.raises(AuthorizationError, match=r"sales\.Invoice\.(lines|total)"):
        service.build_for_record("sales.invoice", 1, no_detail)


def test_report_parameters_are_typed_and_required(reporting) -> None:
    service, context = reporting

    with pytest.raises(ValidationFailed, match="invoice_id.*required"):
        service.build("sales.invoice", {}, context)
    with pytest.raises(ValidationFailed, match="invoice_id.*integer"):
        service.build("sales.invoice", {"invoice_id": "not-a-number"}, context)
    with pytest.raises(ValidationFailed, match="invoice_id.*integer"):
        service.build("sales.invoice", {"invoice_id": 1.5}, context)


def test_html_and_pdf_renderers_write_standalone_documents(
    reporting,
    tmp_path: Path,
) -> None:
    service, context = reporting
    document = service.build_for_record("sales.invoice", 1, context)

    html = render_html(document)
    pdf = render_pdf(document)

    assert "<!doctype html>" in html
    assert "INV-2026-0001" in html
    assert "CONS - Consulting hour" in html
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 5_000
    html_path = write_html(document, tmp_path / "invoice.html")
    pdf_path = write_pdf(document, tmp_path / "invoice.pdf")
    assert html_path.read_text(encoding="utf-8") == html
    assert pdf_path.read_bytes().startswith(b"%PDF-")
