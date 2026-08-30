"""One coercion rule for declared parameters, shared by reports and actions.

The module owns what a declared scalar accepts -- typed values and their
string forms -- and how a refusal is spelled: every issue at once, under
the caller's rule name, with the parameter name as the field. Reports and
actions differ only in that name, and the human noun in each message is
derived from it, which is what keeps the report wording exactly as it was.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tide.runtime.errors import ValidationFailed
from tide.runtime.parameters import coerce_parameters

DEFINITIONS = {
    "reason": {"type": "string", "required": True},
    "occurred_on": {"type": "date"},
    "amount": {"type": "decimal", "default": "10.5"},
}


def test_string_forms_and_typed_values_land_as_the_same_parameters() -> None:
    coerced = coerce_parameters(
        DEFINITIONS,
        {"reason": "damaged", "occurred_on": "2026-08-30", "amount": "3.25"},
        rule="action_parameter",
    )
    assert coerced == {
        "reason": "damaged",
        "occurred_on": date(2026, 8, 30),
        "amount": Decimal("3.25"),
    }


def test_every_refusal_arrives_at_once_under_the_callers_rule() -> None:
    with pytest.raises(ValidationFailed) as refusal:
        coerce_parameters(
            DEFINITIONS,
            {"occurred_on": "not a date", "extra": 1},
            rule="action_parameter",
        )
    issues = refusal.value.issues
    assert {issue.rule for issue in issues} == {"action_parameter"}
    assert {issue.fields for issue in issues} == {
        ("extra",),
        ("reason",),
        ("occurred_on",),
    }
    messages = "\n".join(issue.message for issue in issues)
    assert "unknown action parameter 'extra'" in messages
    assert "action parameter 'reason' is required" in messages
    assert "action parameter 'occurred_on' must be date" in messages


def test_a_declared_default_fills_an_absent_value_and_optional_stays_none() -> None:
    coerced = coerce_parameters(DEFINITIONS, {"reason": "damaged"}, rule="x_y")
    assert coerced["amount"] == Decimal("10.5")
    assert coerced["occurred_on"] is None


def test_a_datetime_parameter_keeps_its_timezone() -> None:
    supplied = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    coerced = coerce_parameters(
        {"when": {"type": "datetime"}},
        {"when": supplied},
        rule="action_parameter",
    )
    assert coerced == {"when": supplied}
