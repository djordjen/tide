"""A field constraint the model declares must hold on every channel.

`validation: <id>` was a second way to say what `edit_mask` already says, and
it was never finished: nothing resolved the id, nothing enforced it, and the
compiler never checked that it named anything. The one field that used it was
therefore checked only by the web client, which recognised the literal id
`email` and applied a regular expression of its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.model.source import FieldSource
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.errors import ValidationFailed
from tide.services import RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _service() -> tuple[RecordsService, RequestContext]:
    model = compile_project(INVOICING)
    context = RequestContext(
        Principal("tests:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.REST,
        correlation_id="field-validation",
    )
    return RecordsService(model, InMemoryRepository()), context


def _customer(email: str) -> dict[str, object]:
    return {"code": "PROBE", "name": "Probe", "email": email}


def test_the_service_refuses_a_customer_email_that_is_not_one() -> None:
    """This used to commit. The web client was the only thing that refused."""

    records, context = _service()
    session = records.create("crm.Customer", context, _customer("not an email"))

    with pytest.raises(ValidationFailed) as failure:
        records.commit(session, context)

    assert any(
        issue.fields == ("email",) for issue in failure.value.issues
    ), failure.value.issues


def test_the_service_still_accepts_an_ordinary_address() -> None:
    records, context = _service()
    session = records.create(
        "crm.Customer", context, _customer("buyer@example.com")
    )

    assert records.commit(session, context) is not None


def test_an_optional_address_may_still_be_left_empty() -> None:
    """The field is not required, and a mask must not make it so."""

    records, context = _service()
    session = records.create("crm.Customer", context, {"code": "P2", "name": "P"})

    assert records.commit(session, context) is not None


def test_a_field_can_no_longer_declare_an_unenforced_validation_id() -> None:
    """`extra="forbid"` is what now reports the dangling reference.

    Leaving the key accepted would let a model keep declaring a constraint
    that no channel applies, which is exactly how the gap survived.
    """

    with pytest.raises(ValueError):
        FieldSource(type="string", validation="email")
