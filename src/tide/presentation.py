"""Renderer-neutral helpers for compiled browse-view query metadata."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from tide.compiler.normalized import NormalizedEntity, ResolvedView
from tide.data import FilterCondition


@dataclass(frozen=True, slots=True)
class BrowseNamedFilter:
    """One queryable named filter exposed by a compiled browse view."""

    name: str
    label: str
    conditions: tuple[FilterCondition, ...]


@dataclass(frozen=True, slots=True)
class FormLayoutSection:
    """One portable form section resolved for every presentation adapter."""

    index: int
    kind: Literal["group", "collection"]
    label: str
    rows: tuple[tuple[str, ...], ...] = ()
    collection: str | None = None
    inline_view: str | None = None
    actions: tuple[str, ...] = ()
    tab: str | None = None
    align: str | None = None
    configuration: Mapping[str, Any] | None = None

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(name for row in self.rows for name in row)


def form_layout_sections(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[FormLayoutSection, ...]:
    """Resolve the semantic form layout shared by TUI, Qt, and future Web UI."""

    sections: list[FormLayoutSection] = []
    for index, raw in enumerate(view.data.get("layout", ())):
        if not isinstance(raw, Mapping):
            continue
        tab = str(raw["tab"]) if raw.get("tab") else None
        collection = raw.get("collection")
        if collection:
            name = str(collection)
            if (
                name in entity.fields
                and entity.field(name).metadata["type"] == "collection"
                and not view_field_hidden(view, name)
            ):
                sections.append(
                    FormLayoutSection(
                        index=index,
                        kind="collection",
                        label=_field_label(entity.field(name)),
                        collection=name,
                        inline_view=(
                            str(raw["view"]) if raw.get("view") else None
                        ),
                        actions=tuple(str(item) for item in raw.get("actions", ())),
                        tab=tab,
                        configuration=raw,
                    )
                )
            continue

        rows = tuple(
            tuple(
                name
                for item in row
                if (name := str(item)) in entity.fields
                and entity.field(name).metadata["type"] != "collection"
                and not view_field_hidden(view, name)
            )
            for row in raw.get("rows", ())
        )
        visible_rows = tuple(row for row in rows if row)
        if visible_rows:
            sections.append(
                FormLayoutSection(
                    index=index,
                    kind="group",
                    label=str(raw.get("group") or entity.label),
                    rows=visible_rows,
                    tab=tab,
                    align=str(raw["align"]) if raw.get("align") else None,
                    configuration=raw,
                )
            )
    settings = view.data.get("settings", {})
    if (
        isinstance(settings, Mapping)
        and settings.get("compact_groups") is True
    ):
        return _compact_form_groups(tuple(sections))
    return tuple(sections)


def form_layout_tabs(
    sections: tuple[FormLayoutSection, ...],
) -> tuple[tuple[str, tuple[FormLayoutSection, ...]], ...]:
    """Group portable sections into ordered tabs when any tab is declared."""

    if not any(section.tab for section in sections):
        return ()
    grouped: dict[str, list[FormLayoutSection]] = {}
    for section in sections:
        grouped.setdefault(section.tab or "General", []).append(section)
    return tuple((label, tuple(items)) for label, items in grouped.items())


def view_field_hidden(view: ResolvedView, name: str) -> bool:
    """Return the shared compiled visibility flag for one view field."""

    fields = view.data.get("fields", {})
    configuration = fields.get(name) if isinstance(fields, Mapping) else None
    return bool(
        isinstance(configuration, Mapping)
        and configuration.get("hidden", False)
    )


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


def _field_label(field: Any) -> str:
    return str(
        field.metadata.get("label")
        or field.name.replace("_", " ").title()
    )


def _compact_form_groups(
    sections: tuple[FormLayoutSection, ...],
) -> tuple[FormLayoutSection, ...]:
    """Merge all scalar groups into one portable two-column header."""

    groups = tuple(section for section in sections if section.kind == "group")
    if not groups:
        return sections
    first = groups[0]
    fields = tuple(name for group in groups for name in group.fields)
    compact = FormLayoutSection(
        index=first.index,
        kind="group",
        label=first.label,
        rows=tuple(
            tuple(fields[index : index + 2])
            for index in range(0, len(fields), 2)
        ),
        tab=first.tab,
        configuration=first.configuration,
    )
    result: list[FormLayoutSection] = []
    emitted = False
    for section in sections:
        if section.kind == "collection":
            result.append(section)
            continue
        if not emitted:
            result.append(compact)
            emitted = True
    return tuple(result)
