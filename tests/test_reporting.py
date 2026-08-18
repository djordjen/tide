from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.reporting import (
    ReportCell,
    ReportColumn,
    ReportDocument,
    ReportGroup,
    ReportService,
    ReportTable,
    ReportValue,
    render_csv,
    render_html,
    render_pdf,
    write_csv,
    write_html,
    write_pdf,
)
from tide.runtime import AuthorizationError, Channel, Principal, RequestContext, ValidationFailed
from tide.services import RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.fixture
def reporting() -> tuple[ReportService, RequestContext]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
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


def test_a_downloaded_report_is_named_from_metadata_not_from_invoicing() -> None:
    """The filename came from the sample application, in framework code.

    `f"invoice-{record.get('number', primary_key)}"` names every application's
    reports after an invoice, and reaches for a field only invoicing has. Both
    are invisible from invoicing itself, where the report is titled "Invoice"
    and the entity displays itself by `number`.
    """

    model = compile_project(ROOT / "tests" / "fixtures" / "valid" / "inspection")
    repository = InMemoryRepository()
    context = RequestContext(
        Principal("report:user", roles=frozenset({"inspector"})),
        channel=Channel.TUI,
    )
    records = RecordsService(model, repository)
    stored = records.commit(
        records.create(
            "inspect.Inspection",
            context,
            {"reference": "INS-001", "steps": [{"title": "Check seals"}]},
        ),
        context,
    )

    document = ReportService(model, records).build_for_record(
        "inspect.sheet",
        stored["id"],
        context,
    )

    assert document.suggested_filename == "inspection-sheet-INS-001"


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


def test_summary_report_groups_secured_rows_and_exports_safe_csv(
    reporting,
    tmp_path: Path,
) -> None:
    """The sales summary is a grouped listing: invoices inside their group.

    The group heading and subtotal live in `groups`; the detail table carries
    only the declared columns. CSV re-flattens by writing the group values on
    every row, because a spreadsheet has no headings to put them in and a row
    that does not say whose it is cannot be pivoted.
    """

    service, context = reporting

    document = service.build(
        "sales.summary",
        {},
        context,
        generated_at=datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
    )

    assert document.title == "Posted Sales Summary"
    assert document.suggested_filename == "posted-sales-summary-2026-07-20"
    assert [column.name for column in document.detail.columns] == [
        "number",
        "invoice_date",
        "total",
    ]
    assert [
        [cell.text for cell in row] for row in document.detail.rows
    ] == [
        ["INV-2026-0001", "01.07.2026", "850.00"],
        ["INV-2026-0004", "07.07.2026", "1,360.00"],
        ["INV-2026-0007", "13.07.2026", "2,400.00"],
    ]
    assert len(document.groups) == 1
    group = document.groups[0]
    assert [(value.label, value.text) for value in group.values] == [
        ("Customer", "ADRIA - Adria Consulting"),
        ("Currency", "EUR"),
    ]
    assert (group.row_start, group.row_count) == (0, 3)
    assert [(value.label, value.text) for value in group.footer_values] == [
        ("Invoices", "3"),
        ("Sales total", "4,610.00"),
    ]
    assert [(value.label, value.text) for value in document.footer_values] == [
        ("Invoices", "3"),
        ("Sales total", "4,610.00"),
        ("Source records", "3"),
    ]
    csv_text = render_csv(document)
    assert csv_text == (
        "Customer,Currency,Number,Invoice Date,Total\r\n"
        "ADRIA - Adria Consulting,EUR,INV-2026-0001,01.07.2026,850.00\r\n"
        'ADRIA - Adria Consulting,EUR,INV-2026-0004,07.07.2026,"1,360.00"\r\n'
        'ADRIA - Adria Consulting,EUR,INV-2026-0007,13.07.2026,"2,400.00"\r\n'
    )
    csv_path = write_csv(document, tmp_path / "summary.csv")
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert csv_path.read_bytes()[3:].decode("utf-8") == csv_text

    unsafe = replace(
        document,
        detail=ReportTable(
            document.detail.columns,
            ((ReportCell("=HYPERLINK(\"bad\")"), *document.detail.rows[0][1:]),)
            + document.detail.rows[1:],
        ),
    )
    assert render_csv(unsafe).splitlines()[1].split(",")[2].startswith("\"'=HYPERLINK")


def test_group_sections_read_in_order_in_plain_text() -> None:
    """A grouped document is sections: heading, that group's rows, its subtotal.

    Hand-built, because this is the document model's own promise: `groups` is
    presentation structure over the one flat detail table, so the rows exist
    exactly once and a group names its slice of them.
    """

    columns = (
        ReportColumn("number", "Number"),
        ReportColumn("total", "Total", "right"),
    )
    rows = (
        (ReportCell("INV-1"), ReportCell("100.00", "right")),
        (ReportCell("INV-2"), ReportCell("50.00", "right")),
        (ReportCell("INV-3"), ReportCell("70.00", "right")),
    )
    document = ReportDocument(
        report="sales.listing",
        title="Sales Listing",
        application="TIDE Invoicing",
        generated_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        header_text=(),
        record_values=(),
        detail=ReportTable(columns, rows),
        footer_values=(ReportValue("Sales total", "220.00", "right"),),
        page_footer_template="Page {page_number}",
        suggested_filename="sales-listing",
        groups=(
            ReportGroup(
                (ReportValue("Customer", "Adria"),),
                0,
                2,
                (ReportValue("Sales total", "150.00", "right"),),
            ),
            ReportGroup(
                (ReportValue("Customer", "Borealis"),),
                2,
                1,
                (ReportValue("Sales total", "70.00", "right"),),
            ),
        ),
    )

    assert document.plain_text() == (
        "Sales Listing\n"
        "TIDE Invoicing\n"
        "Number | Total\n"
        "Customer: Adria\n"
        "INV-1 | 100.00\n"
        "INV-2 | 50.00\n"
        "Sales total: 150.00\n"
        "Customer: Borealis\n"
        "INV-3 | 70.00\n"
        "Sales total: 70.00\n"
        "Sales total: 220.00"
    )


def test_an_ungrouped_document_reads_exactly_as_before() -> None:
    """The control: groups default to none and change nothing when absent."""

    document = ReportDocument(
        report="sales.listing",
        title="Sales Listing",
        application="TIDE Invoicing",
        generated_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        header_text=(),
        record_values=(),
        detail=ReportTable(
            (ReportColumn("number", "Number"),),
            ((ReportCell("INV-1"),),),
        ),
        footer_values=(),
        page_footer_template="Page {page_number}",
        suggested_filename="sales-listing",
    )

    assert document.plain_text() == "Sales Listing\nTIDE Invoicing\nNumber\nINV-1"
    assert document.groups == ()


def test_an_unsupplied_optional_parameter_drops_its_clause(reporting) -> None:
    """One report answers 'everything', 'since a date', or 'a period'.

    The sales summary declares optional parameters, and a criteria clause
    comparing against one that was not supplied is dropped rather than sent to
    the database as a comparison with nothing. `{}` therefore keeps meaning
    what it meant before the parameters existed -- which is also what keeps
    every surface that builds this report without asking questions working.
    """

    service, context = reporting
    repository = service.records.repository
    template = deepcopy(repository.all("sales.Invoice")[0])
    template["id"] = 99
    template["number"] = "INV-2026-0099"
    template["invoice_date"] = date(2026, 8, 15)
    repository.seed("sales.Invoice", [template])

    everything = service.build("sales.summary", {}, context)
    august = service.build("sales.summary", {"from_date": "2026-08-01"}, context)

    assert (everything.footer_values[0].label, everything.footer_values[0].text) == (
        "Invoices",
        "4",
    ), "the dropped clause must not filter anything"
    assert (august.footer_values[0].label, august.footer_values[0].text) == (
        "Invoices",
        "1",
    ), "the supplied one must"


def test_a_second_group_slices_the_listing_where_the_key_changes(
    reporting,
) -> None:
    """Groups are contiguous runs, subtotaled separately, totaled together.

    The service prepends the group fields to the declared sort, so the rows
    arrive already grouped and a group is exactly one run of equal keys --
    which is what lets each group name a slice of the one flat table.
    """

    service, context = reporting
    repository = service.records.repository
    template = deepcopy(repository.all("sales.Invoice")[0])
    template["id"] = 99
    template["number"] = "INV-2026-0099"
    template["customer"] = 2  # MORA - Mora Trade
    repository.seed("sales.Invoice", [template])

    document = service.build("sales.summary", {}, context)

    assert [
        (
            [(value.label, value.text) for value in group.values],
            group.row_start,
            group.row_count,
            [(value.label, value.text) for value in group.footer_values],
        )
        for group in document.groups
    ] == [
        (
            [("Customer", "ADRIA - Adria Consulting"), ("Currency", "EUR")],
            0,
            3,
            [("Invoices", "3"), ("Sales total", "4,610.00")],
        ),
        (
            [("Customer", "MORA - Mora Trade"), ("Currency", "EUR")],
            3,
            1,
            [("Invoices", "1"), ("Sales total", "850.00")],
        ),
    ]
    assert document.detail.rows[3][0].text == "INV-2026-0099"
    assert [(value.label, value.text) for value in document.footer_values] == [
        ("Invoices", "4"),
        ("Sales total", "5,460.00"),
        ("Source records", "4"),
    ]


def test_a_summary_without_columns_stays_one_row_per_group(tmp_path: Path) -> None:
    """The control: a summary that names no columns keeps its pivot shape."""

    project = tmp_path / "flat-summary"
    (project / "models").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Flat Summary, version: 0.1.0}",
                "model: {paths: [models]}",
                "reports: {paths: [reports]}",
                "security: {paths: [security]}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "security").mkdir()
    (project / "security" / "policies.yaml").write_text(
        "\n".join(
            [
                "permissions:",
                "  - demo.item.read",
                "roles:",
                "  viewer:",
                "    grants:",
                "      - demo.item.read",
            ]
        ),
        encoding="utf-8",
    )
    (project / "models" / "item.yaml").write_text(
        "\n".join(
            [
                "entity: demo.Item",
                "permissions:",
                "  list: demo.item.read",
                "  read: demo.item.read",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  name: {type: string}",
                "  amount: {type: decimal, precision: 12, scale: 2}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "reports" / "totals.yaml").write_text(
        "\n".join(
            [
                "report: demo.totals",
                "title: Item Totals",
                "entity: demo.Item",
                "kind: summary",
                "unrestricted: true",
                "group_by: [{field: name}]",
                "aggregates:",
                "  - {name: items, function: count}",
                "  - {name: amount_total, function: sum, field: amount}",
            ]
        ),
        encoding="utf-8",
    )
    model = compile_project(project)
    repository = InMemoryRepository()
    repository.seed(
        "demo.Item",
        [
            {"id": 1, "name": "left", "amount": Decimal("10.00")},
            {"id": 2, "name": "left", "amount": Decimal("5.00")},
            {"id": 3, "name": "right", "amount": Decimal("7.00")},
        ],
    )
    service = ReportService(model, RecordsService(model, repository))
    context = RequestContext(
        Principal("flat:user", roles=frozenset({"viewer"})),
        channel=Channel.TUI,
    )

    document = service.build("demo.totals", {}, context)

    assert document.groups == ()
    assert [
        [cell.text for cell in row] for row in document.detail.rows
    ] == [
        ["left", "2", "15.00"],
        ["right", "1", "7.00"],
    ]
    assert [(value.label, value.text) for value in document.footer_values] == [
        ("Items", "3"),
        ("Amount Total", "22.00"),
        ("Source records", "3"),
    ]


def test_group_fields_lead_the_sort_whatever_the_author_declared(
    tmp_path: Path,
) -> None:
    """A listing sorted by amount still arrives one contiguous run per group.

    The invoicing example cannot observe this -- its declared sort already
    leads with the group fields -- so this report deliberately sorts by
    something else. Without the prepend, `left 10 / right 7 / left 5` would
    fragment into three runs, two of them claiming to be the same group.
    """

    project = tmp_path / "sorted-listing"
    (project / "models").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "security").mkdir()
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Sorted Listing, version: 0.1.0}",
                "model: {paths: [models]}",
                "reports: {paths: [reports]}",
                "security: {paths: [security]}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "models" / "item.yaml").write_text(
        "\n".join(
            [
                "entity: demo.Item",
                "permissions:",
                "  list: demo.item.read",
                "  read: demo.item.read",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  name: {type: string}",
                "  amount: {type: decimal, precision: 12, scale: 2}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "security" / "policies.yaml").write_text(
        "\n".join(
            [
                "permissions:",
                "  - demo.item.read",
                "roles:",
                "  viewer:",
                "    grants:",
                "      - demo.item.read",
            ]
        ),
        encoding="utf-8",
    )
    (project / "reports" / "listing.yaml").write_text(
        "\n".join(
            [
                "report: demo.listing",
                "title: Items By Amount",
                "entity: demo.Item",
                "kind: summary",
                "unrestricted: true",
                "query: {sort: [-amount]}",
                "group_by: [{field: name}]",
                "columns: [amount]",
                "aggregates:",
                "  - {name: items, function: count}",
            ]
        ),
        encoding="utf-8",
    )
    model = compile_project(project)
    repository = InMemoryRepository()
    repository.seed(
        "demo.Item",
        [
            {"id": 1, "name": "left", "amount": Decimal("10.00")},
            {"id": 2, "name": "left", "amount": Decimal("5.00")},
            {"id": 3, "name": "right", "amount": Decimal("7.00")},
        ],
    )
    service = ReportService(model, RecordsService(model, repository))
    context = RequestContext(
        Principal("sorted:user", roles=frozenset({"viewer"})),
        channel=Channel.TUI,
    )

    document = service.build("demo.listing", {}, context)

    assert [
        (group.values[0].text, group.row_start, group.row_count)
        for group in document.groups
    ] == [("left", 0, 2), ("right", 2, 1)]
    assert [row[0].text for row in document.detail.rows] == [
        "10.00",
        "5.00",
        "7.00",
    ], "the declared sort still orders the rows inside each group"


def test_summary_report_refuses_incomplete_aggregates(reporting) -> None:
    service, context = reporting
    repository = service.records.repository
    template = repository.all("sales.Invoice")[0]
    extra = []
    for identity in range(9, 508):
        invoice = deepcopy(template)
        invoice["id"] = identity
        invoice["number"] = f"INV-2026-{identity:04d}"
        extra.append(invoice)
    repository.seed("sales.Invoice", extra)

    with pytest.raises(ValueError, match="exceeds its row limit of 500"):
        service.build("sales.summary", {}, context)


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


def test_grouped_documents_render_group_bands_in_html_and_pdf(
    reporting,
) -> None:
    """HTML and PDF show each group as a band: heading, rows, subtotal.

    The heading and subtotal are full-width rows inside the one detail table,
    so page breaks, zebra striping and column widths keep working unchanged.
    A record report is the control: no groups, no group markup.
    """

    service, context = reporting
    document = service.build("sales.summary", {}, context)

    html = render_html(document)
    assert 'class="group-heading"' in html
    assert "Customer: ADRIA - Adria Consulting" in html
    assert "Currency: EUR" in html
    assert 'class="group-footer"' in html
    assert "Sales total: 4,610.00" in html
    assert "INV-2026-0004" in html
    pdf = render_pdf(document)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 5_000

    record_html = render_html(service.build_for_record("sales.invoice", 1, context))
    assert 'class="group-heading"' not in record_html


def test_a_summary_report_names_its_groups_without_a_read_each(
    reporting,
    monkeypatch,
) -> None:
    """The one consumer of this transform that never had a cache.

    Every other renderer at least remembered a resolved name; the report
    formatter asked again for every cell, so a 500-row summary grouped by
    customer made 500 reads of the same handful of records.
    """

    service, context = reporting
    reads: list[tuple[str, object]] = []
    loaded = service.records.get

    def tracked_get(entity_name, identity, request_context):
        reads.append((entity_name, identity))
        return loaded(entity_name, identity, request_context)

    monkeypatch.setattr(service.records, "get", tracked_get)

    document = service.build("sales.summary", {}, context)

    assert isinstance(document.detail, ReportTable)
    assert document.groups[0].values[0].text == "ADRIA - Adria Consulting"
    assert [entity for entity, _ in reads if entity == "crm.Customer"] == []


def test_a_record_report_names_its_lines_without_a_read_each(
    reporting,
    monkeypatch,
) -> None:
    service, context = reporting
    reads: list[tuple[str, object]] = []
    loaded = service.records.get

    def tracked_get(entity_name, identity, request_context):
        reads.append((entity_name, identity))
        return loaded(entity_name, identity, request_context)

    monkeypatch.setattr(service.records, "get", tracked_get)

    service.build_for_record("sales.invoice", 1, context)

    # One read, for the invoice itself. Its customer and every line's product
    # come from the one resolution that read follows.
    assert reads == [("sales.Invoice", 1)]
