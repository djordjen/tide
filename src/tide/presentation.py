"""Renderer-neutral helpers for compiled browse-view query metadata."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from tide.compiler.normalized import NormalizedEntity, ResolvedView
from tide.data import FilterCondition


@dataclass(frozen=True, slots=True)
class BrowseNamedFilter:
    """One queryable named filter exposed by a compiled browse view."""

    name: str
    label: str
    conditions: tuple[FilterCondition, ...]


def browse_search_field(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> str | None:
    """Return the first configured field supported by incremental search."""

    configured = tuple(str(name) for name in view.data.get("search", ()))
    return next(
        (
            name
            for name in configured
            if name in entity.fields
            and entity.field(name).metadata["type"] in {"string", "choice"}
            and not entity.field(name).metadata.get("computed")
        ),
        None,
    )


def browse_sortable_fields(
    columns: tuple[str, ...],
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    """Return displayed fields that the structured query contract can sort."""

    return tuple(
        name
        for name in columns
        if entity.field(name).metadata["type"] not in {"collection", "reference"}
        and not (
            entity.field(name).metadata.get("computed")
            and entity.field(name).metadata["computed"].get("materialization")
            == "virtual"
        )
    )


def browse_named_filters(
    view: ResolvedView,
) -> dict[str, BrowseNamedFilter]:
    """Compile direct-comparison named filters for renderer query controls."""

    result: dict[str, BrowseNamedFilter] = {}
    for name, filter_data in view.data.get("filters", {}).items():
        criteria = filter_data.get("criteria")
        if not isinstance(criteria, str):
            continue
        try:
            conditions = _criteria_conditions(criteria)
        except ValueError:
            continue
        identifier = str(name)
        result[identifier] = BrowseNamedFilter(
            name=identifier,
            label=str(filter_data.get("label") or _humanize(identifier)),
            conditions=conditions,
        )
    return result


def _criteria_conditions(criteria: str) -> tuple[FilterCondition, ...]:
    try:
        expression = ast.parse(criteria, mode="eval").body
    except SyntaxError as error:
        raise ValueError("named filter has invalid syntax") from error
    clauses = (
        tuple(expression.values)
        if isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.And)
        else (expression,)
    )
    return tuple(_comparison_condition(clause) for clause in clauses)


def _comparison_condition(expression: ast.expr) -> FilterCondition:
    if (
        not isinstance(expression, ast.Compare)
        or len(expression.ops) != 1
        or len(expression.comparators) != 1
        or not isinstance(expression.left, ast.Name)
    ):
        raise ValueError("named filters must use direct field comparisons")
    operators: dict[type[ast.cmpop], str] = {
        ast.Eq: "eq",
        ast.NotEq: "ne",
        ast.Lt: "lt",
        ast.LtE: "lte",
        ast.Gt: "gt",
        ast.GtE: "gte",
    }
    operator = operators.get(type(expression.ops[0]))
    if operator is None:
        raise ValueError("named filter comparison is not queryable")
    try:
        value = ast.literal_eval(expression.comparators[0])
    except (ValueError, TypeError) as error:
        raise ValueError("named filter value must be a literal") from error
    return FilterCondition(expression.left.id, operator, value)


def _humanize(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()
