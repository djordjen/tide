"""Typed validation and safe evaluation for TIDE expressions."""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from tide.model.source import EntitySource

ALLOWED_FUNCTIONS = frozenset(
    {"round", "coalesce", "min", "max", "today", "length", "sum", "count", "average", "any", "all"}
)
ALLOWED_COMPARISONS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
PARAMETER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
NUMERIC_TYPES = frozenset({"integer", "decimal"})


@dataclass(frozen=True, slots=True)
class ExpressionIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ExpressionResult:
    dependencies: tuple[str, ...]
    issues: tuple[ExpressionIssue, ...]
    value_type: str = "unknown"


def validate_expression(
    expression: str,
    *,
    entity: EntitySource,
    entities: Mapping[str, EntitySource],
    parameters: Mapping[str, str] | frozenset[str] = frozenset(),
    globals_: Mapping[str, str] | frozenset[str] = frozenset(),
    expected_type: str | None = None,
) -> ExpressionResult:
    tree, syntax_issue = _parse(expression)
    if syntax_issue:
        return ExpressionResult((), (syntax_issue,))
    assert tree is not None
    parameter_types = (
        dict(parameters) if isinstance(parameters, Mapping) else {name: "unknown" for name in parameters}
    )
    global_types = (
        dict(globals_) if isinstance(globals_, Mapping) else {name: "unknown" for name in globals_}
    )
    validator = _ExpressionValidator(
        entity=entity,
        entities=entities,
        parameters=parameter_types,
        globals_=global_types,
    )
    value_type = validator.visit(tree) or "unknown"
    if expected_type and not _compatible(value_type, expected_type):
        validator.issues.append(
            ExpressionIssue(
                "TIDE307",
                f"expression has type {value_type!r}; expected {_normalized_type(expected_type)!r}",
            )
        )
    return ExpressionResult(
        dependencies=tuple(sorted(validator.dependencies)),
        issues=tuple(validator.issues),
        value_type=value_type,
    )


def evaluate_expression(
    expression: str,
    values: Mapping[str, Any],
    *,
    parameters: Mapping[str, Any] | None = None,
    globals_: Mapping[str, Any] | None = None,
) -> Any:
    """Evaluate the validated expression subset without Python ``eval``."""

    tree, issue = _parse(expression)
    if issue:
        raise ValueError(issue.message)
    assert tree is not None
    return _Evaluator(
        values=values,
        parameters=parameters or {},
        globals_=globals_ or {},
    ).visit(tree)


def _parse(expression: str) -> tuple[ast.Expression | None, ExpressionIssue | None]:
    rewritten = PARAMETER_PATTERN.sub(r"__tide_parameter_\1", expression)
    try:
        return ast.parse(rewritten, mode="eval"), None
    except SyntaxError as error:
        return None, ExpressionIssue("TIDE300", f"invalid expression syntax: {error.msg}")


class _ExpressionValidator(ast.NodeVisitor):
    def __init__(
        self,
        *,
        entity: EntitySource,
        entities: Mapping[str, EntitySource],
        parameters: Mapping[str, str],
        globals_: Mapping[str, str],
    ) -> None:
        self.entity = entity
        self.entities = entities
        self.parameters = parameters
        self.globals = globals_
        self.dependencies: set[str] = set()
        self.issues: list[ExpressionIssue] = []

    def generic_visit(self, node: ast.AST) -> str:
        self.issues.append(
            ExpressionIssue("TIDE301", f"{type(node).__name__} is not allowed in TIDE expressions")
        )
        return "unknown"

    def visit_Expression(self, node: ast.Expression) -> str:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> str:
        if node.value is None:
            return "null"
        if isinstance(node.value, bool):
            return "boolean"
        if isinstance(node.value, int):
            return "integer"
        if isinstance(node.value, float):
            return "decimal"
        if isinstance(node.value, str):
            return "string"
        self.issues.append(ExpressionIssue("TIDE301", "unsupported literal value"))
        return "unknown"

    def visit_Name(self, node: ast.Name) -> str:
        name = node.id
        parameter_prefix = "__tide_parameter_"
        if name.startswith(parameter_prefix):
            parameter = name[len(parameter_prefix) :]
            if parameter not in self.parameters:
                self.issues.append(ExpressionIssue("TIDE304", f"unknown parameter ${parameter}"))
                return "unknown"
            return _normalized_type(self.parameters[parameter])
        if name == "true" or name == "false":
            return "boolean"
        if name == "null":
            return "null"
        if name in self.globals:
            return _normalized_type(self.globals[name])
        field = self.entity.fields.get(name)
        if field is None:
            self.issues.append(ExpressionIssue("TIDE303", f"unknown field {name!r}"))
            return "unknown"
        self.dependencies.add(name)
        return _field_type(field.type, field.target)

    def visit_Attribute(self, node: ast.Attribute) -> str:
        parts = _attribute_parts(node)
        if not parts:
            self.issues.append(ExpressionIssue("TIDE301", "invalid attribute path"))
            return "unknown"
        current = self.entity
        through_collection = False
        final_type = "unknown"
        for index, part in enumerate(parts):
            field = current.fields.get(part)
            if field is None:
                self.issues.append(
                    ExpressionIssue("TIDE303", f"unknown field path {'.'.join(parts[: index + 1])!r}")
                )
                return "unknown"
            final_type = _field_type(field.type, field.target)
            if index < len(parts) - 1:
                if field.type not in {"reference", "collection"} or not field.target:
                    self.issues.append(
                        ExpressionIssue("TIDE305", f"field {part!r} is not a relationship and cannot be traversed")
                    )
                    return "unknown"
                through_collection = through_collection or field.type == "collection"
                target = self.entities.get(field.target)
                if target is None:
                    return "unknown"
                current = target
        self.dependencies.add(".".join(parts))
        return f"collection[{final_type}]" if through_collection else final_type

    def visit_BinOp(self, node: ast.BinOp) -> str:
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add) and (left == "string" or right == "string"):
            return "string"
        if left in NUMERIC_TYPES and right in NUMERIC_TYPES:
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Mod)):
                return "decimal" if "decimal" in {left, right} else "integer"
            if isinstance(node.op, ast.Div):
                return "decimal"
        self.issues.append(ExpressionIssue("TIDE306", f"operator {type(node.op).__name__} does not accept {left} and {right}"))
        return "unknown"

    def visit_BoolOp(self, node: ast.BoolOp) -> str:
        types = [self.visit(value) for value in node.values]
        if any(value not in {"boolean", "unknown"} for value in types):
            self.issues.append(ExpressionIssue("TIDE306", "and/or operands must be boolean"))
        return "boolean"

    def visit_UnaryOp(self, node: ast.UnaryOp) -> str:
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            if operand not in {"boolean", "unknown"}:
                self.issues.append(ExpressionIssue("TIDE306", "not operand must be boolean"))
            return "boolean"
        if isinstance(node.op, (ast.USub, ast.UAdd)) and operand in NUMERIC_TYPES:
            return operand
        self.issues.append(ExpressionIssue("TIDE306", f"invalid unary operand {operand}"))
        return "unknown"

    def visit_Compare(self, node: ast.Compare) -> str:
        for operator_node in node.ops:
            if not isinstance(operator_node, ALLOWED_COMPARISONS):
                self.issues.append(
                    ExpressionIssue(
                        "TIDE308",
                        f"comparison operator {_operator_name(operator_node)!r} is not supported in TIDE expressions",
                    )
                )
        types = [self.visit(node.left), *(self.visit(value) for value in node.comparators)]
        for left, right in zip(types, types[1:]):
            if not _comparable(left, right):
                self.issues.append(ExpressionIssue("TIDE306", f"cannot compare {left} with {right}"))
        return "boolean"

    def visit_Call(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
            self.issues.append(ExpressionIssue("TIDE302", "only allow-listed TIDE functions may be called"))
            for argument in node.args:
                self.visit(argument)
            return "unknown"
        if node.keywords:
            self.issues.append(ExpressionIssue("TIDE301", "keyword arguments are not supported"))
        name = node.func.id
        argument_types = [self.visit(argument) for argument in node.args]
        if name == "today":
            return "date"
        if name in {"count", "length"}:
            return "integer"
        if name in {"any", "all"}:
            return "boolean"
        if name == "average":
            return "decimal"
        if name == "sum":
            item_type = _collection_item(argument_types[0]) if argument_types else "unknown"
            if item_type not in NUMERIC_TYPES | {"unknown"}:
                self.issues.append(ExpressionIssue("TIDE306", "sum requires numeric values"))
            return item_type
        if name == "round":
            value_type = argument_types[0] if argument_types else "unknown"
            if value_type not in NUMERIC_TYPES | {"unknown"}:
                self.issues.append(ExpressionIssue("TIDE306", "round requires a numeric value"))
            return value_type
        if name in {"min", "max"}:
            values = [_collection_item(value) for value in argument_types]
            return _unify(values)
        if name == "coalesce":
            return _unify([value for value in argument_types if value != "null"])
        return "unknown"


class _Evaluator(ast.NodeVisitor):
    def __init__(self, *, values: Mapping[str, Any], parameters: Mapping[str, Any], globals_: Mapping[str, Any]) -> None:
        self.values = values
        self.parameters = parameters
        self.globals = globals_

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"{type(node).__name__} is not allowed in TIDE expressions")

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, float):
            return Decimal(str(node.value))
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id.startswith("__tide_parameter_"):
            return self.parameters[node.id.removeprefix("__tide_parameter_")]
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id == "null":
            return None
        if node.id in self.globals:
            return self.globals[node.id]
        return self.values.get(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        value = self.visit(node.value)
        if isinstance(value, (list, tuple)):
            return [_lookup(item, node.attr) for item in value]
        return _lookup(value, node.attr)

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        left, right = self.visit(node.left), self.visit(node.right)
        if isinstance(node.op, ast.Add) and (isinstance(left, str) or isinstance(right, str)):
            return f"{left}{right}"
        if (
            isinstance(node.op, ast.Div)
            and isinstance(left, (int, Decimal))
            and isinstance(right, (int, Decimal))
        ):
            return Decimal(left) / Decimal(right)
        operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod}
        operation = operations.get(type(node.op))
        if operation is None:
            raise ValueError("unsupported binary operator")
        return operation(left, right)

    def visit_BoolOp(self, node: ast.BoolOp) -> bool:
        if isinstance(node.op, ast.And):
            return all(bool(self.visit(value)) for value in node.values)
        return any(bool(self.visit(value)) for value in node.values)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        value = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        raise ValueError("unsupported unary operator")

    def visit_Compare(self, node: ast.Compare) -> bool:
        operations = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
        left = self.visit(node.left)
        for operator_node, comparator in zip(node.ops, node.comparators):
            operation = operations.get(type(operator_node))
            if operation is None:
                raise ValueError(
                    f"comparison operator {_operator_name(operator_node)!r} is not supported"
                )
            right = self.visit(comparator)
            if not operation(left, right):
                return False
            left = right
        return True

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
            raise ValueError("function is not allowed")
        arguments = [self.visit(argument) for argument in node.args]
        name = node.func.id
        if name == "today":
            return self.globals.get("today", date.today())
        if name == "coalesce":
            return next((value for value in arguments if value is not None), None)
        if name == "length":
            return len(arguments[0])
        if name == "count":
            return len(arguments[0])
        if name == "sum":
            return sum(arguments[0])
        if name == "average":
            return sum(arguments[0]) / len(arguments[0]) if arguments[0] else None
        if name == "any":
            return any(arguments[0])
        if name == "all":
            return all(arguments[0])
        if name == "round":
            return round(*arguments)
        if name in {"min", "max"}:
            function = min if name == "min" else max
            return function(arguments[0]) if len(arguments) == 1 and isinstance(arguments[0], (list, tuple)) else function(*arguments)
        raise ValueError(f"unsupported function {name}")


def _lookup(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    raise ValueError(f"cannot traverse {type(value).__name__}")


def _operator_name(node: ast.cmpop) -> str:
    names = {ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not"}
    return names.get(type(node), type(node).__name__)


def _attribute_parts(node: ast.Attribute) -> tuple[str, ...]:
    parts: list[str] = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return ()
    parts.append(value.id)
    return tuple(reversed(parts))


def _field_type(field_type: str, target: str | None) -> str:
    normalized = _normalized_type(field_type)
    if field_type == "collection":
        return f"collection[{target or 'unknown'}]"
    if field_type == "reference":
        return f"reference[{target or 'unknown'}]"
    return normalized


def _normalized_type(value: str) -> str:
    return {"choice": "string", "datetime": "datetime", "boolean": "boolean"}.get(value, value)


def _collection_item(value: str) -> str:
    return value[11:-1] if value.startswith("collection[") and value.endswith("]") else value


def _unify(values: list[str]) -> str:
    known = {value for value in values if value not in {"unknown", "null"}}
    if not known:
        return "unknown"
    if known <= NUMERIC_TYPES:
        return "decimal" if "decimal" in known else "integer"
    return next(iter(known)) if len(known) == 1 else "unknown"


def _compatible(actual: str, expected: str) -> bool:
    expected = _normalized_type(expected)
    actual = _collection_item(actual)
    if actual in {"unknown", "null"}:
        return True
    if expected == actual:
        return True
    return expected == "decimal" and actual == "integer"


def _comparable(left: str, right: str) -> bool:
    if "unknown" in {left, right} or "null" in {left, right}:
        return True
    if left == right:
        return True
    return left in NUMERIC_TYPES and right in NUMERIC_TYPES
