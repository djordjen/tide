"""One vocabulary for naming and aligning a field, shared by every layer."""

from __future__ import annotations

import pytest

from tide.compiler.normalized import NormalizedField, immutable_mapping
from tide.labels import humanize


def _field(name: str, **metadata: object) -> NormalizedField:
    return NormalizedField(name=name, metadata=immutable_mapping(dict(metadata)))


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


def test_every_layer_humanizes_a_name_the_same_way() -> None:
    """Names that are not model fields still need one transform.

    Field labels go through `field_label` now, so most of these call sites are
    gone. What is left names actions, report aggregates, and designer view-field
    keys, and those must not drift apart again.
    """

    from tide import presentation
    from tide.development import studio
    from tide.mcp import runtime
    from tide.reporting import service

    forks = {
        "presentation": presentation._humanize,
        "mcp": runtime._humanize,
        "reporting": service._humanize,
        "studio": studio.humanize,
    }

    assert set(forks.values()) == {humanize}, forks


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


def test_every_layer_labels_a_field_through_one_implementation() -> None:
    """`_humanize` was shared; the wrapper that reaches for `label` was not.

    Seven copies decided independently whether an explicit label wins -- five
    named helpers plus inline copies in the REST input models and the MCP field
    schema -- and the Textual form's copy never called `_humanize` at all.
    """

    from tide import presentation
    from tide.qt import presenter
    from tide.reporting import service
    from tide.tui import app, form, lookup

    modules = (presenter, service, app, form, lookup)
    forks = {module.__name__: module._field_label for module in modules}

    assert set(forks.values()) == {presentation.field_label}, forks


def test_the_form_labels_a_camel_case_field_like_the_grid_does() -> None:
    """The same record showed two different labels depending on the screen.

    `tui/form.py` humanized with `str.title()` over the raw name, so a
    `customerName` field read as "Customername" in the form and "Customer Name"
    in the browse grid behind it.
    """

    from tide.tui import app, form

    field = _field("customerName", type="string")

    assert form._field_label(field) == app._field_label(field) == "Customer Name"


def test_every_layer_aligns_a_field_through_one_implementation() -> None:
    from tide import presentation
    from tide.qt import presenter
    from tide.reporting import service

    forks = {
        "qt": presenter._field_alignment,
        "reporting": service._alignment,
    }

    assert set(forks.values()) == {presentation.field_alignment}, forks


def test_a_report_honours_an_explicitly_left_aligned_number() -> None:
    """Reporting could not tell "explicitly left" from "nothing configured".

    Its fork defaulted an absent `align` to `"left"` and then treated `"left"`
    as absent, so a format that deliberately left-aligned a number was silently
    overridden by the numeric default.
    """

    from tide.reporting import service

    formats = {"plain": {"align": "left"}}
    field = _field("total", type="decimal", format="plain")

    assert service._alignment(field, formats, None) == "left"


def test_the_studio_humanizes_a_view_field_like_every_other_layer() -> None:
    from tide.development import studio

    assert studio._view_field_label("customerName", None) == "Customer Name"


def test_the_terminal_aligns_a_column_on_its_format_not_only_its_type() -> None:
    """The terminal justified by field type and never read the format.

    Qt and the Web honour an explicit `align`, so a format that centred a
    number centred it everywhere except the terminal, and one that
    left-aligned it was ignored there entirely.
    """

    from tide.tui.table import table_cell, table_label

    formats = {"plain": {"align": "center"}}
    field = _field("total", type="decimal", format="plain")

    assert table_label(field, "Total", formats).justify == "center"
    assert table_cell(field, "10.50", formats).justify == "center"


def test_the_terminal_still_leaves_ordinary_text_alone() -> None:
    """A left-aligned cell stays a plain string rather than becoming a Text."""

    from tide.tui.table import table_cell

    assert table_cell(_field("name", type="string"), "ACME", {}) == "ACME"


def test_the_terminal_inline_editor_hides_what_the_inline_view_hides() -> None:
    """`line_fields` read the raw `columns` list and filtered nothing.

    Every other renderer resolves inline collection columns through
    `browse_columns`, which drops hidden fields and refuses unknown ones, so a
    column hidden in the inline view stayed visible in the terminal alone.
    """

    from dataclasses import replace as replace_dataclass
    from pathlib import Path

    from tide import compile_project
    from tide.compiler.normalized import immutable_mapping
    from tide.data import InMemoryRepository
    from tide.runtime import Channel, Principal, RequestContext
    from tide.services import ActionService, RecordsService
    from tide.sessions import RecordSession
    from tide.tui.form import RecordEditScreen

    model = compile_project(
        Path(__file__).parents[1] / "applications" / "invoicing"
    )
    inline = model.views["sales.InvoiceLine.inline_edit"]
    model = replace_dataclass(
        model,
        views=immutable_mapping(
            {
                **dict(model.views),
                inline.name: replace_dataclass(
                    inline,
                    data=immutable_mapping(
                        {
                            **dict(inline.data),
                            "fields": {
                                **dict(inline.data["fields"]),
                                "description": {"hidden": True},
                            },
                        }
                    ),
                ),
            }
        ),
    )
    records = RecordsService(model, InMemoryRepository())
    screen = RecordEditScreen(
        model,
        records,
        ActionService(model, records),
        RequestContext(
            principal=Principal("labels:tui", roles=frozenset({"sales_clerk"})),
            channel=Channel.TUI,
        ),
        model.views["sales.Invoice.edit"],
        RecordSession(
            entity="sales.Invoice",
            identity=None,
            original={},
            values={},
            expected_version=None,
            is_new=True,
        ),
    )

    assert "description" not in screen.line_fields
    assert "total" in screen.line_fields
