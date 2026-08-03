"""The compiled model answers structural questions about itself."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tide import compile_project
from tide.compiler.normalized import immutable_mapping

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.fixture(scope="module")
def model():
    return compile_project(INVOICING)


def test_entity_names_its_own_primary_key(model) -> None:
    """Every module used to rescan the fields for this; the model knows it."""

    invoice = model.entity("sales.Invoice")

    assert invoice.primary_key.name == "id"
    assert invoice.primary_key is invoice.field("id")


def test_entity_names_its_own_concurrency_token(model) -> None:
    invoice = model.entity("sales.Invoice")

    assert invoice.version_field is not None
    assert invoice.version_field.name == "version"


def test_entity_without_a_concurrency_token_reports_none(model) -> None:
    """Optional by design: only versioned entities take part in preconditions."""

    assert model.entity("sales.InvoiceLine").version_field is None


def test_missing_primary_key_is_reported_clearly(model) -> None:
    """The compiler guarantees one, so reaching here means the model was built
    by hand; say so rather than raising StopIteration from a generator."""

    invoice = model.entity("sales.Invoice")
    without_key = replace(
        invoice,
        fields=immutable_mapping(
            {
                name: field
                for name, field in invoice.fields.items()
                if not field.metadata.get("primary_key")
            }
        ),
    )

    with pytest.raises(ValueError, match="primary key"):
        _ = without_key.primary_key
