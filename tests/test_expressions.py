from __future__ import annotations

from decimal import Decimal, localcontext

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


@pytest.mark.parametrize(
    "literal",
    [
        "0.10000000000000001",
        "9999999999999999.99",
        "1.234567890123456789",
    ],
)
def test_fractional_literals_preserve_their_source_value(literal: str) -> None:
    result = evaluate_expression(literal, {})

    assert result == Decimal(literal)


def test_fractional_literal_source_survives_parameter_rewriting() -> None:
    result = evaluate_expression(
        "$offset + 9999999999999999.99",
        {},
        parameters={"offset": Decimal("0")},
    )

    assert result == Decimal("9999999999999999.99")


def test_decimal_field_survives_fractional_literal_arithmetic() -> None:
    result = evaluate_expression("unit_price * 1.21", {"unit_price": Decimal("100.00")})

    assert isinstance(result, Decimal)
    assert result == Decimal("121")


def test_division_of_exact_numbers_yields_decimal() -> None:
    result = evaluate_expression("10 / 4", {})

    assert isinstance(result, Decimal)
    assert result == Decimal("2.5")


def test_average_of_integers_yields_decimal() -> None:
    result = evaluate_expression("average(numbers)", {"numbers": [1, 2]})

    assert isinstance(result, Decimal)
    assert result == Decimal("1.5")


def test_average_rejects_non_numeric_runtime_values() -> None:
    with pytest.raises(ValueError):
        evaluate_expression("average(values)", {"values": [True, False]})


def test_expression_arithmetic_uses_the_framework_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 3
        result = evaluate_expression("10 / 7", {})

    assert result == Decimal("1.4285714285714285714285714285714285714")


@pytest.mark.parametrize("expression", ["'x' in name", "posted_at is null"])
def test_membership_and_identity_comparisons_are_rejected(expression: str) -> None:
    result = validate(expression)

    assert "TIDE308" in {issue.code for issue in result.issues}


def test_evaluator_rejects_unsupported_comparison_operators() -> None:
    with pytest.raises(ValueError):
        evaluate_expression("'x' in name", {"name": "example"})
