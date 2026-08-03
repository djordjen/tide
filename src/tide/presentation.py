"""Renderer-neutral helpers for compiled browse-view query metadata."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from tide.labels import humanize as _humanize
from tide.compiler.normalized import (
    ApplicationModel,
    NavigationGroup,
    NavigationItem,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
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


def application_navigation(
    model: ApplicationModel,
    accessible_views: Iterable[str] | None = None,
    *,
    include_views: Iterable[str] = (),
) -> tuple[NavigationGroup, ...]:
    """Resolve capability-filtered application navigation for every renderer.

    Security remains the caller's responsibility: adapters pass only browse
    views for which the current principal has list capability.
    """

    browse_names = tuple(
        view.name for view in model.views.values() if view.kind == "browse"
    )
    allowed = (
        set(browse_names)
        if accessible_views is None
        else set(accessible_views) & set(browse_names)
    )
    if model.navigation:
        groups = [
            NavigationGroup(
                label=group.label,
                items=tuple(item for item in group.items if item.view in allowed),
            )
            for group in model.navigation
        ]
        groups = [group for group in groups if group.items]
    else:
        groups = [
            NavigationGroup(
                label="Application",
                items=tuple(
                    NavigationItem(
                        view=name,
                        label=model.entity(model.views[name].entity).label,
                    )
                    for name in browse_names
                    if name in allowed
                ),
            )
        ]
        groups = [group for group in groups if group.items]

    included = {item.view for group in groups for item in group.items}
    extras = tuple(
        NavigationItem(
            view=name,
            label=model.entity(model.views[name].entity).label,
        )
        for name in include_views
        if name in allowed and name not in included
    )
    if extras:
        other_index = next(
            (
                index
                for index, group in enumerate(groups)
                if group.label.casefold() == "other"
            ),
            None,
        )
        if other_index is None:
            groups.append(NavigationGroup(label="Other", items=extras))
        else:
            group = groups[other_index]
            groups[other_index] = NavigationGroup(
                label=group.label,
                items=(*group.items, *extras),
            )
    return tuple(groups)


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
                        label=field_label(entity.field(name)),
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


def browse_columns(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    """Return the portable visible column order for a compiled browse view."""

    configured = tuple(str(name) for name in view.data.get("columns", ()))
    columns = configured or tuple(
        name
        for name, field in entity.fields.items()
        if field.metadata["type"] != "collection"
    )
    unknown = tuple(name for name in columns if name not in entity.fields)
    if unknown:
        raise ValueError(
            "browse view contains unknown columns: " + ", ".join(unknown)
        )
    return tuple(
        name for name in columns if not view_field_hidden(view, name)
    )


def field_label(field: NormalizedField) -> str:
    """Return the portable field label used by presentation adapters."""

    return str(field.metadata.get("label") or _humanize(field.name))


def field_alignment(
    field: NormalizedField,
    formats: Mapping[str, Mapping[str, Any]],
) -> Literal["left", "center", "right"]:
    """Return explicit format alignment or the shared type-based default."""

    configured = formats.get(str(field.metadata.get("format")), {}).get("align")
    if configured in {"left", "center", "right"}:
        return configured
    return "right" if field.metadata["type"] in {"integer", "decimal"} else "left"


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
