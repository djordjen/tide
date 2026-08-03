"""One label vocabulary, shared by every layer that names a field."""

from __future__ import annotations

import pytest

from tide.labels import humanize


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("unit_price", "Unit Price"),
        ("line_number", "Line Number"),
        # camelCase has to split, or a field reads as one run-together word
        ("customerName", "Customer Name"),
        ("InvoiceLine", "Invoice Line"),
        # kebab-case appears in view and preset identifiers
        ("posted-at", "Posted At"),
        ("invoice_date-local", "Invoice Date Local"),
        ("total", "Total"),
    ],
)
def test_humanize_covers_every_naming_style_in_the_model(
    identifier: str,
    expected: str,
) -> None:
    """snake, kebab and camel all appear in real models and must agree."""

    assert humanize(identifier) == expected


def test_every_layer_labels_a_field_the_same_way() -> None:
    """The label a user sees must not depend on which screen showed it.

    Ten copies of this transform existed with three different behaviours, so
    `customerName` rendered as "Customer Name" in the Textual browse grid and
    "Customername" in its own lookup dialog.
    """

    from tide import presentation
    from tide.api import inputs
    from tide.mcp import runtime
    from tide.reporting import service
    from tide.tui import app, lookup

    modules = (presentation, inputs, runtime, service, app, lookup)
    labelled = {module.__name__: module._humanize("customerName") for module in modules}

    assert set(labelled.values()) == {"Customer Name"}, labelled


def test_every_renderer_resolves_browse_columns_the_same_way() -> None:
    """A browse view has one column order, whichever renderer draws it.

    Qt kept a private copy that had lost the unknown-column guard, so a view
    naming a field that does not exist rendered a broken grid there while the
    other renderers refused it.
    """

    from dataclasses import replace as replace_dataclass

    from tide import compile_project
    from tide.presentation import browse_columns
    from tide.qt import presenter
    from tide.tui import app

    from pathlib import Path

    model = compile_project(Path(__file__).parents[1] / "applications" / "invoicing")
    view = model.views["sales.Invoice.browse"]
    entity = model.entity("sales.Invoice")

    resolvers = (browse_columns, presenter._browse_columns, app._browse_columns)
    assert len({resolver(view, entity) for resolver in resolvers}) == 1

    broken = replace_dataclass(view, data={**dict(view.data), "columns": ["nonexistent"]})
    for resolver in resolvers:
        with pytest.raises(ValueError, match="unknown columns"):
            resolver(broken, entity)
