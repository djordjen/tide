"""Taking the browse query you are looking at away as a file.

A reader who has filtered, sorted and totalled a grid needs the result
somewhere a spreadsheet can reach it. Export is bounded, says when it was
bounded, and is gated by a declared capability -- not because paging could not
reach the same rows, but because a deployment should be able to say "reads on
screen, does not take the file away", and because an export is worth finding
in a log a year later.
"""

from __future__ import annotations

from pathlib import Path

from tide import compile_project
from tide.model.source import FRAMEWORK_PERMISSIONS
from tide.runtime import Channel, Principal, RequestContext
from tide.security import SecurityEngine

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal(
            "user:clerk", roles=frozenset(roles or ("sales_clerk",))
        ),
        channel=Channel.REST,
    )


def test_export_is_a_declarable_framework_capability() -> None:
    assert "tide.records.export" in FRAMEWORK_PERMISSIONS

    model = compile_project(INVOICING)
    security = SecurityEngine(model)

    # The clerk who reads the grid may take it away.
    assert "tide.records.export" in security.effective_permissions(
        context().principal
    )
    # The administrator role grants administration and nothing else, so it is
    # the proof that the capability is granted rather than ambient.
    assert "tide.records.export" not in security.effective_permissions(
        context("administrator").principal
    )
