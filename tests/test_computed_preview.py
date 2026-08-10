"""Renderers preview stored computed fields through one implementation."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tide import compile_project
from tide.compiler.normalized import immutable_mapping
from tide.presentation import preview_computed_fields

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.fixture(scope="module")
def line_entity():
    return compile_project(INVOICING).entity("sales.InvoiceLine")


def test_preview_computes_a_stored_field_from_its_dependencies(line_entity) -> None:
    values = {"quantity": Decimal("2.5"), "unit_price": Decimal("4.20")}

    preview_computed_fields(line_entity, values)

    assert values["total"] == Decimal("10.50")


def test_preview_yields_no_value_while_the_draft_is_incomplete(line_entity) -> None:
    """A half-typed row is ordinary, not an error.

    The service raises on a value it cannot evaluate, because by then the
    commit is authoritative. A preview runs on every keystroke and has to show
    a blank total instead of tearing down the form.
    """

    values = {"quantity": None, "unit_price": Decimal("4.20")}

    preview_computed_fields(line_entity, values)

    assert values["total"] is None


def test_preview_refuses_a_dependency_cycle(line_entity) -> None:
    """The compiler rejects cycles, so reaching one means something is wrong.

    Both renderer copies used to stop quietly here, which would have shown a
    partially computed row as if it were complete.
    """

    looping = replace(
        line_entity.field("total"),
        dependencies=("total",),
    )
    cyclic = replace(
        line_entity,
        fields=immutable_mapping({**dict(line_entity.fields), "total": looping}),
    )

    with pytest.raises(RuntimeError, match="cycle"):
        preview_computed_fields(cyclic, {"quantity": Decimal("1"), "unit_price": Decimal("1")})


def test_the_renderer_previews_through_the_shared_helper() -> None:
    """Two byte-identical copies existed; neither may come back.

    One of the two left with the Qt renderer. The guard stays on the survivor
    because what it forbids is a renderer growing its own copy, and the Web
    shell computes nothing locally -- the server owns the recomputation.
    """

    from tide.tui import form

    assert form._preview_computed_fields is preview_computed_fields
