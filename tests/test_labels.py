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
