"""How one record names itself, answered once for every surface.

`tide.display.record_display` sits low in the stack precisely so the TUI,
the reports and the wire can all ask it -- and yet three copies of it grew
anyway, and drifted the way copies do: the form title rendered a null
display value as the text "None", and the report formatter, having no
bare-field path, rendered `display: number` as the literal word "number".
The identity tests are the cure the action-state drift already received:
every surface must hold *the* implementation, not a paraphrase of it.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

from tide.compiler.normalized import (
    NormalizedEntity,
    NormalizedField,
    immutable_mapping,
)
from tide.display import record_display
from tide.security import PROTECTED


def _entity(display: str | None) -> NormalizedEntity:
    return NormalizedEntity(
        name="demo.Invoice",
        label="Invoices",
        display=display,
        source_file=Path("demo.yaml"),
        metadata=immutable_mapping({}),
        fields={
            "id": NormalizedField(
                name="id",
                metadata=immutable_mapping(
                    {"type": "integer", "primary_key": True}
                ),
            ),
            "number": NormalizedField(
                name="number",
                metadata=immutable_mapping({"type": "string"}),
            ),
        },
        actions=immutable_mapping({}),
    )


def test_a_bare_display_names_the_record_by_that_field() -> None:
    assert record_display(_entity("number"), {"id": 7, "number": "INV-1"}) == (
        "INV-1"
    )


def test_the_report_formatter_names_a_bare_display_record_by_its_value() -> None:
    """`sales.Invoice` declares `display: number`, a bare field.

    The report formatter's copy only knew templates, and `format_map` over a
    string with no placeholders returns the string -- so a report reference
    cell falling back to it would have printed the literal word "number".
    Invoicing never fired it only because its report references happen to
    point at entities with templated displays.
    """

    report_display = import_module("tide.reporting.fields").display_record

    assert report_display(_entity("number"), {"id": 7, "number": "INV-1"}) == (
        "INV-1"
    )


def test_a_form_title_shows_nothing_for_a_null_display_value() -> None:
    """A null in the display column is a row TIDE did not write.

    The form's copy ran `str()` over it and titled the screen "None".
    """

    form_title = import_module("tide.tui.form")._record_title

    assert form_title(_entity("number"), {"id": 7, "number": None}) == ""


@pytest.mark.parametrize(
    "module_path, attribute",
    [
        ("tide.display", "record_display"),
        ("tide.tui.app", "_display_record"),
        ("tide.tui.form", "_record_title"),
        ("tide.reporting.fields", "display_record"),
    ],
)
def test_a_withheld_display_value_reads_protected_on_every_surface(
    module_path: str,
    attribute: str,
) -> None:
    """The sentinel never leaks, and the withholding is never invisible.

    The form's copy stringified the sentinel itself into the title, and the
    report formatter's copy blanked it -- hiding the one evidence that a
    record is there and its name withheld.
    """

    name = getattr(import_module(module_path), attribute)

    assert name(_entity("number"), {"id": 7, "number": PROTECTED}) == (
        "Protected"
    )


@pytest.mark.parametrize(
    "module_path, attribute",
    [
        ("tide.tui.app", "_display_record"),
        ("tide.tui.form", "_record_title"),
        ("tide.reporting.fields", "display_record"),
    ],
)
def test_every_surface_names_a_record_through_one_implementation(
    module_path: str,
    attribute: str,
) -> None:
    assert getattr(import_module(module_path), attribute) is record_display
