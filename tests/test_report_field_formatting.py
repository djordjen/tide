"""One value, formatted the way every document shows it.

Reports and browse exports must not be able to disagree about what a decimal,
a date or a reference looks like, so both ask the same object rather than each
keeping a copy of the rules.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from tide import compile_project
from tide.data import InMemoryRepository
from tide.reporting.fields import FieldFormatter
from tide.reporting.service import ReportService
from tide.runtime import Channel, Principal, RequestContext
from tide.security import PROTECTED
from tide.services import RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _formatter() -> FieldFormatter:
    model = compile_project(INVOICING)
    return FieldFormatter(model, RecordsService(model, InMemoryRepository()))


def test_the_formatter_answers_for_scalars_the_way_reports_do() -> None:
    formatter = _formatter()

    assert formatter.scalar(None) == ""
    assert formatter.scalar(True) == "Yes"
    assert formatter.scalar(False) == "No"
    assert formatter.scalar(date(2026, 8, 22)) == "2026-08-22"
    # An unconfigured decimal keeps every digit it was given.
    assert formatter.scalar(Decimal("10.50")) == "10.50"


def test_a_protected_value_is_blank_rather_than_its_own_repr() -> None:
    """The sentinel must never reach a cell.

    Reports never meet it, because they format declared report columns. A
    browse view can name a field a field policy protects, and the fallback
    `str(value)` would have written `ProtectedValue` into the file.
    """

    formatter = _formatter()
    model = formatter.model
    field = model.entity("sales.Invoice").field("posted_by")

    assert formatter.scalar(PROTECTED) == ""
    assert formatter.field(field, PROTECTED, _context()) == ""


def test_the_report_service_formats_through_that_same_object() -> None:
    model = compile_project(INVOICING)
    service = ReportService(model, RecordsService(model, InMemoryRepository()))

    assert isinstance(service.formatter, FieldFormatter)
    field = model.entity("sales.Invoice").field("invoice_date")
    assert service._format_field(
        field, date(2026, 8, 22), _context()
    ) == service.formatter.field(field, date(2026, 8, 22), _context())


def _context() -> RequestContext:
    return RequestContext(
        principal=Principal("user:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.REST,
    )
