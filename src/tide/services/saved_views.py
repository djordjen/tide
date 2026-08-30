"""Saved views: the state of a browse grid, named and kept per person.

A saved view stores the *components* of the screen -- the named filter,
the funnel checks, the sort, and a snapshot of the columns as arranged --
because restoring must relight the controls a person sees. A flattened
filter list could constrain the rows, but the funnel it came from would
sit unlit, and a grid constrained by conditions its controls do not show
is lying.

The rows contract knows nothing about the model; the service owns the
rules once for every transport, the same posture as arrangements. What a
stored value *matches* is deliberately not validated here: a replayed
condition goes through the query service like any other, so a value that
went stale simply matches nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from tide.compiler.normalized import ApplicationModel
from tide.runtime.context import RequestContext
from tide.services.view_state import (
    MAX_LABEL_CHARACTERS,
    ViewStateColumn,
)

MAX_SAVED_VIEWS = 20
MAX_NAME_CHARACTERS = 60


@dataclass(frozen=True, slots=True)
class SavedView:
    """One named grid state. `columns=None` follows the standing arrangement.

    `value_filters` is the membership map the checklists relight;
    `conditions` carries the operator filters beside it -- (field,
    operator, value) triples for ranges and contains -- because a range
    must relight its bounds on restore, not merely constrain the rows.
    """

    name: str
    named_filter: str | None = None
    value_filters: dict[str, tuple[object, ...]] = field(default_factory=dict)
    conditions: tuple[tuple[str, str, object], ...] = ()
    sort: tuple[tuple[str, bool], ...] = ()
    columns: tuple[ViewStateColumn, ...] | None = None


class SavedViewRows(Protocol):
    """Where saved views live: durable, or process-local."""

    shared: bool

    def list(self, principal: str, view: str) -> tuple[SavedView, ...]: ...

    def list_mine(
        self, principal: str
    ) -> tuple[tuple[str, SavedView], ...]: ...

    def put(self, principal: str, view: str, entry: SavedView) -> None: ...

    def delete(self, principal: str, view: str, name: str) -> None: ...


class InMemorySavedViewRows:
    """Process-local saved views, for demo mode and legacy databases."""

    shared = False

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], SavedView] = {}

    def list(self, principal: str, view: str) -> tuple[SavedView, ...]:
        return tuple(
            sorted(
                (
                    entry
                    for (owner, owner_view, _), entry in self._rows.items()
                    if owner == principal and owner_view == view
                ),
                key=lambda entry: entry.name,
            )
        )

    def list_mine(self, principal: str) -> tuple[tuple[str, SavedView], ...]:
        return tuple(
            sorted(
                (
                    (owner_view, entry)
                    for (owner, owner_view, _), entry in self._rows.items()
                    if owner == principal
                ),
                key=lambda item: (item[0], item[1].name),
            )
        )

    def put(self, principal: str, view: str, entry: SavedView) -> None:
        self._rows[(principal, view, entry.name)] = entry

    def delete(self, principal: str, view: str, name: str) -> None:
        self._rows.pop((principal, view, name), None)


class SavedViewError(Exception):
    """A refused saved view, carrying every reason at once."""

    def __init__(self, issues: tuple[str, ...]) -> None:
        super().__init__("; ".join(issues))
        self.issues = issues


class UnknownSavedViewView(Exception):
    """The named view is not a browse view of this application."""


class _SecurityReader(Protocol):
    def can_read_field(
        self, entity: str, field: str, context: RequestContext
    ) -> bool: ...


class SavedViewService:
    """Validate and keep named grid states, once, for every door."""

    def __init__(
        self,
        model: ApplicationModel,
        security: _SecurityReader,
        rows: SavedViewRows,
    ) -> None:
        self.model = model
        self.security = security
        self.rows = rows

    def _browse_view(self, view_name: str):  # noqa: ANN202 - ResolvedView
        view = self.model.views.get(view_name)
        if view is None or view.kind != "browse":
            raise UnknownSavedViewView(view_name)
        return view

    def list(
        self, context: RequestContext, view_name: str
    ) -> tuple[SavedView, ...]:
        self._browse_view(view_name)
        return self.rows.list(context.principal.identifier, view_name)

    def list_mine(
        self, context: RequestContext
    ) -> tuple[tuple[str, SavedView], ...]:
        """Everything this principal keeps, across views, for the home
        surface. A saved view outlives the application changing under it,
        so a row whose view is no longer a browse is left dormant rather
        than offered as a broken tile."""

        return tuple(
            (view_name, entry)
            for view_name, entry in self.rows.list_mine(
                context.principal.identifier
            )
            if (view := self.model.views.get(view_name)) is not None
            and view.kind == "browse"
        )

    def put(
        self, context: RequestContext, view_name: str, entry: SavedView
    ) -> None:
        # Imported here, not at the top: the SQLAlchemy store imports
        # this module for the SavedView shape, `tide.data`'s package
        # init imports the store through the framework schema, and
        # `tide.presentation` imports `tide.data` -- a cycle that only
        # closes if this import runs while the package is half built.
        from tide.presentation import (
            browse_filterable_fields,
            browse_sortable_fields,
        )

        view = self._browse_view(view_name)
        entity = self.model.entity(str(view.entity))
        issues: list[str] = []

        name = entry.name.strip()
        if not name or len(name) > MAX_NAME_CHARACTERS:
            issues.append(
                f"name must be 1 to {MAX_NAME_CHARACTERS} characters"
            )

        declared_filters = view.data.get("filters", {})
        if (
            entry.named_filter is not None
            and entry.named_filter not in declared_filters
        ):
            issues.append(f"unknown named filter {entry.named_filter!r}")

        # One rule, declared once: the same field-type functions the
        # manifest's capability lists are built from decide what a funnel
        # or a sort may touch, asked one field at a time -- plus the
        # readability this principal actually has.
        for field_name in entry.value_filters:
            if field_name not in entity.fields or not (
                browse_filterable_fields((field_name,), entity)
                and self.security.can_read_field(
                    str(view.entity), field_name, context
                )
            ):
                issues.append(
                    f"field {field_name!r} cannot carry a value filter"
                )
        # Conditions answer to the same field rule as the membership map;
        # the operator itself is the wire model's closed set, and stored
        # values stay un-revalidated -- replay goes through the query
        # service, per the standing ruling.
        for field_name, _operator, _value in entry.conditions:
            if field_name not in entity.fields or not (
                browse_filterable_fields((field_name,), entity)
                and self.security.can_read_field(
                    str(view.entity), field_name, context
                )
            ):
                issues.append(
                    f"field {field_name!r} cannot carry a condition"
                )
        seen_sorts: set[str] = set()
        for field_name, _descending in entry.sort:
            if field_name in seen_sorts:
                issues.append(f"sort field {field_name!r} is repeated")
                continue
            seen_sorts.add(field_name)
            if field_name not in entity.fields or not (
                browse_sortable_fields((field_name,), entity)
                and self.security.can_read_field(
                    str(view.entity), field_name, context
                )
            ):
                issues.append(f"field {field_name!r} cannot be sorted")

        columns = entry.columns
        if columns is not None:
            if not columns:
                issues.append(
                    "a columns snapshot must keep at least one column"
                )
            seen: set[str] = set()
            for column in columns:
                if column.name in seen:
                    issues.append(f"column {column.name!r} is repeated")
                    continue
                seen.add(column.name)
                fielddef = entity.fields.get(column.name)
                if fielddef is None:
                    issues.append(f"unknown field {column.name!r}")
                    continue
                if str(fielddef.metadata["type"]) == "collection":
                    issues.append(
                        f"field {column.name!r} is a collection, not a column"
                    )
                    continue
                if not self.security.can_read_field(
                    str(view.entity), column.name, context
                ):
                    issues.append(f"field {column.name!r} cannot be read")
                    continue
                if column.label is not None:
                    label = column.label.strip()
                    if not label or len(label) > MAX_LABEL_CHARACTERS:
                        issues.append(
                            f"label for {column.name!r} must be 1 to "
                            f"{MAX_LABEL_CHARACTERS} characters"
                        )

        existing = {
            stored.name
            for stored in self.rows.list(
                context.principal.identifier, view_name
            )
        }
        if name not in existing and len(existing) >= MAX_SAVED_VIEWS:
            issues.append(
                f"at most {MAX_SAVED_VIEWS} saved views per browse; "
                "delete one first"
            )

        if issues:
            raise SavedViewError(tuple(issues))
        self.rows.put(
            context.principal.identifier,
            view_name,
            SavedView(
                name=name,
                named_filter=entry.named_filter,
                value_filters=dict(entry.value_filters),
                conditions=tuple(entry.conditions),
                sort=tuple(entry.sort),
                columns=(
                    tuple(
                        ViewStateColumn(
                            name=column.name,
                            label=(
                                column.label.strip()
                                if column.label is not None
                                else None
                            ),
                        )
                        for column in columns
                    )
                    if columns is not None
                    else None
                ),
            ),
        )

    def delete(
        self, context: RequestContext, view_name: str, name: str
    ) -> None:
        self._browse_view(view_name)
        self.rows.delete(context.principal.identifier, view_name, name)
