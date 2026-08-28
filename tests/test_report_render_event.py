"""Every report build says who read which report.

Browse export became real through its `records.export` event; a report build
was invisible on every channel until this. The emit site is the one service
entry both kinds share, so REST preview, REST export, TUI preview, and MCP
all write it without knowing they do.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.reporting import ReportService
from tide.runtime import AuthorizationError, Channel, Principal, RequestContext
from tide.services import RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.fixture
def reporting() -> tuple[ReportService, RequestContext]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    service = ReportService(model, RecordsService(model, repository))
    context = RequestContext(
        Principal("report:user", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )
    return service, context


def _render_events(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if getattr(record, "tide_event", None) == "reports.render"
    ]


def test_a_successful_build_says_who_read_which_report(
    reporting: tuple[ReportService, RequestContext],
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, context = reporting
    with caplog.at_level(logging.INFO, logger="tide.runtime"):
        typed = service.build_export("sales.summary", {}, context)
    events = _render_events(caplog)
    assert len(events) == 1
    fields = events[0].tide_fields
    assert fields["subject"] == "sales.summary"
    assert fields["operation"] == "summary"
    assert fields["principal"] == "report:user"
    assert fields["channel"] == context.channel.value
    assert fields["rows"] == len(typed.document.detail.rows)
    assert fields["correlation_id"] == context.correlation_id


def test_a_record_report_build_logs_exactly_once(
    reporting: tuple[ReportService, RequestContext],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`build_export_for_record` delegates to `build_export`: one site, one event."""

    service, context = reporting
    with caplog.at_level(logging.INFO, logger="tide.runtime"):
        service.build_export_for_record("sales.invoice", 1, context)
    events = _render_events(caplog)
    assert len(events) == 1
    assert events[0].tide_fields["operation"] == "record"
    assert events[0].tide_fields["subject"] == "sales.invoice"


def test_a_refused_build_says_nothing(
    reporting: tuple[ReportService, RequestContext],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event records a read that happened, never one that was refused."""

    service, _ = reporting
    outsider = RequestContext(
        Principal("report:nobody", roles=frozenset()),
        channel=Channel.TUI,
    )
    with caplog.at_level(logging.INFO, logger="tide.runtime"):
        with pytest.raises(AuthorizationError):
            service.build_export("sales.summary", {}, outsider)
    assert _render_events(caplog) == []
