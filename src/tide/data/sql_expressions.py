"""Translate validated TIDE record expressions into SQLAlchemy clauses."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import operator
import re
from typing import Any, Mapping

from sqlalchemy import Date, Numeric, and_, cast, exists, false, func, not_, or_, select, true
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.selectable import FromClause

from tide.compiler.normalized import ApplicationModel, NormalizedEntity
from tide.runtime.errors import TideRuntimeError

PARAMETER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class QueryTranslationError(TideRuntimeError):
    code = "query_not_translatable"


class _TideCurrentDate(FunctionElement[date]):
    type = Date()
    inherit_cache = True


@compiles(_TideCurrentDate)
def _compile_current_date_default(
    _element: _TideCurrentDate,
    _compiler: Any,
    **_kwargs: Any,
) -> str:
    return "CURRENT_DATE"


@compiles(_TideCurrentDate, "mssql")
def _compile_current_date_mssql(
    _element: _TideCurrentDate,
    _compiler: Any,
    **_kwargs: Any,
) -> str:
    return "CAST(GETDATE() AS DATE)"


def translate_expression(
    expression: str,
    *,
    model: ApplicationModel,
    entity: NormalizedEntity,
    columns: Mapping[str, ColumnElement[Any]],
    tables: Mapping[str, FromClause] | None = None,
    parameters: Mapping[str, Any] | None = None,
    relationship_criteria: Mapping[str, tuple[str, ...]] | None = None,
    _policy_stack: frozenset[str] | None = None,
    _alias_prefix: str = "tide_rel",
) -> ColumnElement[bool]:
    rewritten = PARAMETER_PATTERN.sub(r"__tide_parameter_\1", expression)
    try:
        tree = ast.parse(rewritten, mode="eval")
    except SyntaxError as error:
        raise QueryTranslationError(
            f"invalid expression syntax: {error.msg}"
        ) from error
    translator = _SQLExpressionTranslator(
        source=rewritten,
        model=model,
        entity=entity,
        columns=columns,
        tables=tables,
        parameters=parameters or {},
        relationship_criteria=relationship_criteria or {},
        policy_stack=_policy_stack or frozenset({entity.name}),
        alias_prefix=_alias_prefix,
    )
    result = translator.visit(tree)
    if not isinstance(result, ColumnElement):
        raise QueryTranslationError(
            f"row criteria {expression!r} did not produce a SQL predicate"
        )
    return result


@dataclass(frozen=True, slots=True)
class _CollectionSQL:
    value: ColumnElement[Any] | None
    from_clauses: tuple[FromClause, ...]
    predicates: tuple[ColumnElement[bool], ...]
    root: FromClause
    path: str


class _SQLExpressionTranslator(ast.NodeVisitor):
    def __init__(
        self,
        *,
        source: str,
        model: ApplicationModel,
        entity: NormalizedEntity,
        columns: Mapping[str, ColumnElement[Any]],
        tables: Mapping[str, FromClause] | None,
        parameters: Mapping[str, Any],
        relationship_criteria: Mapping[str, tuple[str, ...]],
        policy_stack: frozenset[str],
        alias_prefix: str,
    ) -> None:
        self.source = source
        self.model = model
        self.entity = entity
        self.columns = columns
        self.tables = tables
        self.parameters = parameters
        self.relationship_criteria = relationship_criteria
        self.policy_stack = policy_stack
        self.alias_prefix = alias_prefix
        self._relationship_index = 0

    def generic_visit(self, node: ast.AST) -> Any:
        raise QueryTranslationError(
            f"{type(node).__name__} cannot be translated to SQL"
        )

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, bool):
            return true() if node.value else false()
        if isinstance(node.value, float):
            literal = ast.get_source_segment(self.source, node)
            if literal is None:
                raise QueryTranslationError("decimal literal source is unavailable")
            try:
                return Decimal(literal.replace("_", ""))
            except InvalidOperation as error:
                raise QueryTranslationError(
                    f"invalid decimal literal {literal!r}"
                ) from error
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id.startswith("__tide_parameter_"):
            parameter = node.id.removeprefix("__tide_parameter_")
            if parameter not in self.parameters:
                raise QueryTranslationError(f"unknown SQL parameter ${parameter}")
            return self.parameters[parameter]
        if node.id == "true":
            return true()
        if node.id == "false":
            return false()
        if node.id == "null":
            return None
        if node.id not in self.entity.fields:
            raise QueryTranslationError(f"unknown field {node.id!r}")
        return self._resolve_path((node.id,))

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        parts = _attribute_parts(node)
        if not parts:
            raise QueryTranslationError("invalid relationship path")
        return self._resolve_path(parts)

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        left = self._scalar(self.visit(node.left), "binary operator")
        right = self._scalar(self.visit(node.right), "binary operator")
        operations = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Mod: operator.mod,
        }
        operation = operations.get(type(node.op))
        if operation is None:
            raise QueryTranslationError(
                f"operator {type(node.op).__name__} cannot be translated to SQL"
            )
        return operation(left, right)

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        values = [
            self._scalar(self.visit(value), "boolean operator")
            for value in node.values
        ]
        if isinstance(node.op, ast.And):
            return and_(*values)
        if isinstance(node.op, ast.Or):
            return or_(*values)
        raise QueryTranslationError(
            f"boolean operator {type(node.op).__name__} cannot be translated to SQL"
        )

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        value = self._scalar(self.visit(node.operand), "unary operator")
        if isinstance(node.op, ast.Not):
            return not_(value)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        raise QueryTranslationError(
            f"unary operator {type(node.op).__name__} cannot be translated to SQL"
        )

    def visit_Compare(self, node: ast.Compare) -> Any:
        comparisons = []
        left = self._scalar(self.visit(node.left), "comparison")
        for comparison, comparator in zip(node.ops, node.comparators):
            right = self._scalar(self.visit(comparator), "comparison")
            if isinstance(comparison, ast.Eq):
                if right is None and isinstance(left, ColumnElement):
                    result = left.is_(None)
                elif left is None and isinstance(right, ColumnElement):
                    result = right.is_(None)
                else:
                    result = left == right
            elif isinstance(comparison, ast.NotEq):
                if right is None and isinstance(left, ColumnElement):
                    result = left.is_not(None)
                elif left is None and isinstance(right, ColumnElement):
                    result = right.is_not(None)
                else:
                    result = left != right
            elif isinstance(comparison, ast.Lt):
                result = left < right
            elif isinstance(comparison, ast.LtE):
                result = left <= right
            elif isinstance(comparison, ast.Gt):
                result = left > right
            elif isinstance(comparison, ast.GtE):
                result = left >= right
            else:
                raise QueryTranslationError(
                    f"comparison {type(comparison).__name__} cannot be translated to SQL"
                )
            comparisons.append(result)
            left = right
        return and_(*comparisons)

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise QueryTranslationError("only named TIDE functions can be translated")
        if node.keywords:
            raise QueryTranslationError("keyword arguments cannot be translated to SQL")
        name = node.func.id
        arguments = [self.visit(argument) for argument in node.args]
        if name == "today" and not arguments:
            return _TideCurrentDate()
        if name == "coalesce" and arguments:
            return func.coalesce(
                *(self._scalar(argument, "coalesce") for argument in arguments)
            )
        if name == "length" and len(arguments) == 1:
            if isinstance(arguments[0], _CollectionSQL):
                return self._collection_count(arguments[0])
            return func.length(self._scalar(arguments[0], "length"))
        if name == "count" and len(arguments) == 1:
            return self._collection_count(
                self._collection(arguments[0], "count")
            )
        if name == "sum" and len(arguments) == 1:
            collection = self._valued_collection(arguments[0], "sum")
            aggregate = self._collection_select(
                collection, func.sum(collection.value)
            ).scalar_subquery()
            return func.coalesce(aggregate, 0)
        if name == "average" and len(arguments) == 1:
            collection = self._valued_collection(arguments[0], "average")
            total = self._collection_select(
                collection, func.sum(collection.value)
            ).scalar_subquery()
            count = self._collection_select(
                collection, func.count(collection.value)
            ).scalar_subquery()
            divisor = cast(func.nullif(count, 0), Numeric(38, 18))
            return total / divisor
        if name in {"any", "all"} and len(arguments) == 1:
            collection = self._valued_collection(arguments[0], name)
            truth_value = func.coalesce(collection.value, false())
            predicate = (
                truth_value == true() if name == "any" else truth_value != true()
            )
            matching = self._collection_select(
                collection, 1, extra_predicates=(predicate,)
            )
            return exists(matching) if name == "any" else not_(exists(matching))
        if name in {"min", "max"} and len(arguments) == 1:
            collection = self._valued_collection(arguments[0], name)
            function = func.min if name == "min" else func.max
            return self._collection_select(
                collection, function(collection.value)
            ).scalar_subquery()
        if name == "round" and len(arguments) in {1, 2}:
            return func.round(
                *(self._scalar(argument, "round") for argument in arguments)
            )
        raise QueryTranslationError(
            f"function {name!r} is not yet translatable to SQL"
        )

    def _resolve_path(self, parts: tuple[str, ...]) -> Any:
        current_entity = self.entity
        root = self._root_table()
        current_from = root
        from_clauses: list[FromClause] = []
        predicates: list[ColumnElement[bool]] = []
        through_collection = False

        for index, part in enumerate(parts):
            field = current_entity.fields.get(part)
            path = ".".join(parts[: index + 1])
            if field is None:
                raise QueryTranslationError(f"unknown field path {path!r}")
            field_type = str(field.metadata["type"])
            last = index == len(parts) - 1

            if field_type == "collection":
                if through_collection:
                    raise QueryTranslationError(
                        "multiple collection traversals cannot be translated to SQL"
                    )
                target_name = field.target_entity
                inverse_name = field.metadata.get("inverse")
                if not target_name or not inverse_name:
                    raise QueryTranslationError(
                        f"collection {current_entity.name}.{part} has no SQL inverse"
                    )
                target_entity = self.model.entity(target_name)
                target_from = self._relationship_table(target_name)
                parent_key = _primary_key(current_entity)
                parent_column = self._column(current_entity, current_from, parent_key)
                inverse_column = self._column(
                    target_entity, target_from, str(inverse_name)
                )
                predicates.append(inverse_column == parent_column)
                predicates.extend(
                    self._relationship_policy_predicates(
                        target_entity,
                        target_from,
                    )
                )
                from_clauses.append(target_from)
                current_entity = target_entity
                current_from = target_from
                through_collection = True
                if last:
                    return _CollectionSQL(
                        value=None,
                        from_clauses=tuple(from_clauses),
                        predicates=tuple(predicates),
                        root=root,
                        path=".".join(parts),
                    )
                continue

            column = self._column(current_entity, current_from, part)
            if field_type == "reference" and not last:
                target_name = field.target_entity
                if not target_name:
                    raise QueryTranslationError(
                        f"reference {current_entity.name}.{part} has no SQL target"
                    )
                target_entity = self.model.entity(target_name)
                target_from = self._relationship_table(target_name)
                target_key = _primary_key(target_entity)
                target_column = self._column(
                    target_entity, target_from, target_key
                )
                predicates.append(target_column == column)
                predicates.extend(
                    self._relationship_policy_predicates(
                        target_entity,
                        target_from,
                    )
                )
                from_clauses.append(target_from)
                current_entity = target_entity
                current_from = target_from
                continue

            if not last:
                raise QueryTranslationError(
                    f"field {current_entity.name}.{part} is not a relationship"
                )
            if through_collection:
                return _CollectionSQL(
                    value=column,
                    from_clauses=tuple(from_clauses),
                    predicates=tuple(predicates),
                    root=root,
                    path=".".join(parts),
                )
            if from_clauses:
                return (
                    select(column)
                    .select_from(*from_clauses)
                    .where(and_(*predicates))
                    .correlate(root)
                    .scalar_subquery()
                )
            return column

        raise QueryTranslationError("empty field path")

    def _root_table(self) -> FromClause:
        if self.tables is not None and self.entity.name in self.tables:
            return self.tables[self.entity.name]
        for column in self.columns.values():
            table = getattr(column, "table", None)
            if isinstance(table, FromClause):
                return table
        raise QueryTranslationError("SQL table metadata is required for this expression")

    def _relationship_table(self, entity_name: str) -> FromClause:
        if self.tables is None or entity_name not in self.tables:
            raise QueryTranslationError(
                "SQL table metadata is required for relationship translation"
            )
        self._relationship_index += 1
        return self.tables[entity_name].alias(
            f"{self.alias_prefix}_{self._relationship_index}"
        )

    def _relationship_policy_predicates(
        self,
        entity: NormalizedEntity,
        from_clause: FromClause,
    ) -> tuple[ColumnElement[bool], ...]:
        if entity.name in self.policy_stack:
            return ()
        criteria = self.relationship_criteria.get(entity.name, ())
        if not criteria:
            return ()
        tables = dict(self.tables or {})
        tables[entity.name] = from_clause
        stack = self.policy_stack | {entity.name}
        return tuple(
            translate_expression(
                expression,
                model=self.model,
                entity=entity,
                columns=from_clause.c,
                tables=tables,
                parameters=self.parameters,
                relationship_criteria=self.relationship_criteria,
                _policy_stack=stack,
                _alias_prefix=(
                    f"{self.alias_prefix}_{self._relationship_index}_policy"
                ),
            )
            for expression in criteria
        )

    def _column(
        self,
        entity: NormalizedEntity,
        from_clause: FromClause,
        field_name: str,
    ) -> ColumnElement[Any]:
        if entity.name == self.entity.name and from_clause is self._root_table():
            column = self.columns.get(field_name)
        else:
            column = from_clause.c.get(field_name)
        if column is None:
            raise QueryTranslationError(
                f"field {entity.name}.{field_name} is not stored and cannot be queried in SQL"
            )
        return column

    @staticmethod
    def _scalar(value: Any, context: str) -> Any:
        if isinstance(value, _CollectionSQL):
            raise QueryTranslationError(
                f"collection path {value.path!r} requires an aggregate in {context}"
            )
        return value

    @staticmethod
    def _collection(value: Any, function: str) -> _CollectionSQL:
        if not isinstance(value, _CollectionSQL):
            raise QueryTranslationError(
                f"function {function!r} requires a collection path"
            )
        return value

    def _valued_collection(self, value: Any, function: str) -> _CollectionSQL:
        collection = self._collection(value, function)
        if collection.value is None:
            raise QueryTranslationError(
                f"function {function!r} requires a collection field path"
            )
        return collection

    def _collection_select(
        self,
        collection: _CollectionSQL,
        value: Any,
        *,
        extra_predicates: tuple[ColumnElement[bool], ...] = (),
    ) -> Any:
        statement = select(value).select_from(*collection.from_clauses)
        predicates = (*collection.predicates, *extra_predicates)
        if predicates:
            statement = statement.where(and_(*predicates))
        return statement.correlate(collection.root)

    def _collection_count(self, collection: _CollectionSQL) -> ColumnElement[Any]:
        return self._collection_select(collection, func.count()).scalar_subquery()


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


def _primary_key(entity: NormalizedEntity) -> str:
    for name, field in entity.fields.items():
        if field.metadata.get("primary_key"):
            return name
    raise QueryTranslationError(f"entity {entity.name} has no primary key")
