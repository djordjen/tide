from __future__ import annotations

from decimal import Decimal

import pytest

from tide.compiler.expressions import evaluate_expression, validate_expression
from tide.model.source import EntitySource

ENTITY = EntitySource.model_validate(
    {
        "entity": "demo.Thing",
        "fields": {
            "id": {"type": "integer", "primary_key": True},
            "name": {"type": "string"},
            "unit_price": {"type": "decimal"},
            "posted_at": {"type": "datetime"},
        },
    }
)


def validate(expression: str):
    return validate_expression(expression, entity=ENTITY, entities={"demo.Thing": ENTITY})


def test_fractional_literals_evaluate_as_decimal() -> None:
    result = evaluate_expression("0.1 + 0.2", {})

    assert isinstance(result, Decimal)
    assert result == Decimal("0.3")


def test_decimal_field_survives_fractional_literal_arithmetic() -> None:
    result = evaluate_expression("unit_price * 1.21", {"unit_price": Decimal("100.00")})

    assert isinstance(result, Decimal)
    assert result == Decimal("121")


def test_division_of_exact_numbers_yields_decimal() -> None:
    result = evaluate_expression("10 / 4", {})

    assert isinstance(result, Decimal)
    assert result == Decimal("2.5")


@pytest.mark.parametrize("expression", ["'x' in name", "posted_at is null"])
def test_membership_and_identity_comparisons_are_rejected(expression: str) -> None:
    result = validate(expression)

    assert "TIDE308" in {issue.code for issue in result.issues}


def test_evaluator_rejects_unsupported_comparison_operators() -> None:
    with pytest.raises(ValueError):
        evaluate_expression("'x' in name", {"name": "example"})
