"""Per-user browse arrangements: chosen columns, their order, their labels.

The YAML `columns:` on a view is the declaration every principal starts from.
What this module holds is one person's overlay on it — the XAF split between
the application model and user differences, kept deliberately: state layered
over the rule, never a second spelling of the rule.

The rows contract is deliberately ignorant of the model. Validation — does
the view exist, is the field real, may this principal read it — belongs to
:class:`ViewStateService`, which owns it once for every transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tide.compiler.normalized import ApplicationModel
from tide.runtime.context import RequestContext

MAX_LABEL_CHARACTERS = 80


@dataclass(frozen=True, slots=True)
class ViewStateColumn:
    """One chosen column: the field's name, and a label if renamed."""

    name: str
    label: str | None = None


class ViewStateRows(Protocol):
    """Where a person's arrangements live: durable, or process-local."""

    shared: bool

    def get(
        self, principal: str, view: str
    ) -> tuple[ViewStateColumn, ...] | None: ...

    def put(
        self,
        principal: str,
        view: str,
        columns: tuple[ViewStateColumn, ...],
    ) -> None: ...

    def delete(self, principal: str, view: str) -> None: ...


class InMemoryViewStateRows:
    """Process-local arrangements, for demo mode and legacy databases.

    The same degradation browser sessions take: a database TIDE does not own
    is a database TIDE may not create a table in, so the choice lives as long
    as the process does.
    """

    shared = False

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], tuple[ViewStateColumn, ...]] = {}

    def get(
        self, principal: str, view: str
    ) -> tuple[ViewStateColumn, ...] | None:
        return self._rows.get((principal, view))

    def put(
        self,
        principal: str,
        view: str,
        columns: tuple[ViewStateColumn, ...],
    ) -> None:
        self._rows[(principal, view)] = tuple(columns)

    def delete(self, principal: str, view: str) -> None:
        self._rows.pop((principal, view), None)


class ViewStateError(Exception):
    """A refused arrangement, carrying every reason at once."""

    def __init__(self, issues: tuple[str, ...]) -> None:
        super().__init__("; ".join(issues))
        self.issues = issues


class UnknownViewStateView(Exception):
    """The named view is not a browse view of this application."""


class _SecurityReader(Protocol):
    def can_read_field(
        self, entity: str, field: str, context: RequestContext
    ) -> bool: ...


class ViewStateService:
    """Validate and keep per-user browse arrangements.

    One service entry for every transport, the same posture as attachments:
    REST is a doorway, not a second place the rules live.
    """

    def __init__(
        self,
        model: ApplicationModel,
        security: _SecurityReader,
        rows: ViewStateRows,
    ) -> None:
        self.model = model
        self.security = security
        self.rows = rows

    def _browse_entity(self, view_name: str) -> str:
        view = self.model.views.get(view_name)
        if view is None or view.kind != "browse":
            raise UnknownViewStateView(view_name)
        return str(view.entity)

    def get(
        self, context: RequestContext, view_name: str
    ) -> tuple[ViewStateColumn, ...]:
        self._browse_entity(view_name)
        stored = self.rows.get(context.principal.identifier, view_name)
        return stored or ()

    def put(
        self,
        context: RequestContext,
        view_name: str,
        columns: tuple[ViewStateColumn, ...],
    ) -> None:
        entity_name = self._browse_entity(view_name)
        entity = self.model.entity(entity_name)
        issues: list[str] = []
        if not columns:
            issues.append("an arrangement must keep at least one column")
        seen: set[str] = set()
        for column in columns:
            if column.name in seen:
                issues.append(f"column {column.name!r} is repeated")
                continue
            seen.add(column.name)
            field = entity.fields.get(column.name)
            if field is None:
                issues.append(f"unknown field {column.name!r}")
                continue
            if str(field.metadata["type"]) == "collection":
                issues.append(
                    f"field {column.name!r} is a collection, not a column"
                )
                continue
            if not self.security.can_read_field(
                entity_name, column.name, context
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
        if issues:
            raise ViewStateError(tuple(issues))
        normalized = tuple(
            ViewStateColumn(
                name=column.name,
                label=column.label.strip() if column.label is not None else None,
            )
            for column in columns
        )
        self.rows.put(context.principal.identifier, view_name, normalized)

    def delete(self, context: RequestContext, view_name: str) -> None:
        self._browse_entity(view_name)
        self.rows.delete(context.principal.identifier, view_name)
