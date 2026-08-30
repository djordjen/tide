"""The view-state service owns the arrangement rules, once, for every door.

A stored arrangement is only ever a subset of what the principal could see
anyway -- the rows carry every readable field already -- so what validation
protects is coherence, not confidentiality: a real browse view, real fields,
no collections wearing column hats, and labels a header can actually show.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.runtime import Principal, RequestContext
from tide.services import RecordsService
from tide.services.view_state import (
    InMemoryViewStateRows,
    UnknownViewStateView,
    ViewStateColumn,
    ViewStateError,
    ViewStateService,
)

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _service() -> ViewStateService:
    model = compile_project(INVOICING)
    records = RecordsService(model, InMemoryRepository())
    return ViewStateService(model, records.security, InMemoryViewStateRows())


def _context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal("local:test", roles=frozenset(roles))
    )


def test_an_arrangement_is_kept_per_principal_and_read_back() -> None:
    service = _service()
    clerk = _context("sales_clerk")
    columns = (
        ViewStateColumn(name="number", label="No."),
        ViewStateColumn(name="total"),
        ViewStateColumn(name="version"),
    )

    assert service.get(clerk, "sales.Invoice.browse") == ()
    service.put(clerk, "sales.Invoice.browse", columns)
    assert service.get(clerk, "sales.Invoice.browse") == columns

    other = _context("sales_clerk")
    assert other.principal.identifier == clerk.principal.identifier
    stranger = RequestContext(
        principal=Principal("local:other", roles=frozenset({"sales_clerk"}))
    )
    assert service.get(stranger, "sales.Invoice.browse") == ()

    service.delete(clerk, "sales.Invoice.browse")
    assert service.get(clerk, "sales.Invoice.browse") == ()


def test_a_label_is_trimmed_before_it_is_kept() -> None:
    service = _service()
    clerk = _context("sales_clerk")
    service.put(
        clerk,
        "sales.Invoice.browse",
        (ViewStateColumn(name="number", label="  No.  "),),
    )
    assert service.get(clerk, "sales.Invoice.browse") == (
        ViewStateColumn(name="number", label="No."),
    )


def test_every_refusal_reason_is_named_at_once() -> None:
    service = _service()
    clerk = _context("sales_clerk")
    with pytest.raises(ViewStateError) as refused:
        service.put(
            clerk,
            "sales.Invoice.browse",
            (
                ViewStateColumn(name="number"),
                ViewStateColumn(name="number"),
                ViewStateColumn(name="no_such_field"),
                ViewStateColumn(name="lines"),
                ViewStateColumn(name="total", label="   "),
                ViewStateColumn(name="currency", label="x" * 81),
            ),
        )
    issues = "\n".join(refused.value.issues)
    assert "'number' is repeated" in issues
    assert "unknown field 'no_such_field'" in issues
    assert "'lines' is a collection" in issues
    assert "label for 'total'" in issues
    assert "label for 'currency'" in issues
    assert service.get(clerk, "sales.Invoice.browse") == ()


def test_an_empty_arrangement_is_refused() -> None:
    service = _service()
    with pytest.raises(ViewStateError) as refused:
        service.put(_context("sales_clerk"), "sales.Invoice.browse", ())
    assert "at least one column" in refused.value.issues[0]


def test_a_field_the_principal_cannot_read_is_refused() -> None:
    service = _service()
    clerk = _context("sales_clerk")
    with pytest.raises(ViewStateError) as refused:
        service.put(
            clerk,
            "sales.Invoice.browse",
            (ViewStateColumn(name="posted_by"),),
        )
    assert "'posted_by' cannot be read" in refused.value.issues[0]

    auditor = _context("sales_clerk", "auditor")
    service.put(auditor, "sales.Invoice.browse", (ViewStateColumn(name="posted_by"),))
    assert service.get(auditor, "sales.Invoice.browse") == (
        ViewStateColumn(name="posted_by"),
    )


@pytest.mark.parametrize(
    "view_name", ["no.such.view", "sales.Invoice.edit"]
)
def test_only_a_real_browse_view_carries_an_arrangement(view_name: str) -> None:
    service = _service()
    clerk = _context("sales_clerk")
    with pytest.raises(UnknownViewStateView):
        service.get(clerk, view_name)
    with pytest.raises(UnknownViewStateView):
        service.put(clerk, view_name, (ViewStateColumn(name="number"),))
    with pytest.raises(UnknownViewStateView):
        service.delete(clerk, view_name)
