"""Secured text search across every entity one identity may read."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from tide.compiler.normalized import ApplicationModel, NormalizedEntity
from tide.display import record_display
from tide.labels import humanize
from tide.runtime import RequestContext
from tide.runtime.errors import AuthorizationError
from tide.services.records import RecordsService

#: The most hits one entity may contribute. A search is a doorway, not a
#: browse: whoever needs more than this has already named the entity and
#: belongs in its view, where paging and filters live.
MAX_GROUP_LIMIT = 25


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One record a search found: its identity, and how it names itself."""

    identity: Any
    display: str


@dataclass(frozen=True, slots=True)
class SearchGroup:
    """Every hit one entity contributed, bounded and saying so."""

    entity: str
    label: str
    hits: tuple[SearchHit, ...]
    truncated: bool


class GlobalSearchService:
    """One text, every entity: a fan-out over the secured lookup path.

    Deliberately owns no security of its own. Each entity is asked through
    :meth:`RecordsService.lookup_records`, which applies the entity
    permission, the row policies and field security -- so what a search can
    see is exactly what that identity's own browsing can see. An entity
    that refuses is skipped, never an error the whole search wears, because
    "you may not read this" is an ordinary answer inside a sweep.

    Only fields the application marked ``searchable`` are swept, and only
    where this identity may read them: sweeping an unreadable field would
    let its values be guessed one probe at a time.
    """

    def __init__(self, model: ApplicationModel, records: RecordsService) -> None:
        if records.model is not model:
            raise ValueError("global search and records must share a model")
        self.model = model
        self.records = records

    def search(
        self,
        text: str,
        context: RequestContext,
        *,
        limit: int = 5,
        entity_names: Iterable[str] | None = None,
    ) -> tuple[SearchGroup, ...]:
        candidate = text.strip() if isinstance(text, str) else ""
        if not candidate:
            raise ValueError("search text must not be empty")
        if limit < 1 or limit > MAX_GROUP_LIMIT:
            raise ValueError(
                f"search limit must be between 1 and {MAX_GROUP_LIMIT}"
            )
        allowed = None if entity_names is None else set(entity_names)
        groups: list[SearchGroup] = []
        for entity_name, entity in self.model.entities.items():
            if allowed is not None and entity_name not in allowed:
                continue
            fields = self._searchable_fields(entity_name, entity, context)
            if not fields:
                continue
            try:
                found = self.records.lookup_records(
                    entity_name,
                    fields,
                    candidate,
                    context,
                    limit=limit + 1,
                )
            except AuthorizationError:
                continue
            if not found:
                continue
            primary_key = entity.primary_key.name
            groups.append(
                SearchGroup(
                    entity=entity_name,
                    label=self._label(entity_name, entity),
                    hits=tuple(
                        SearchHit(
                            identity=record[primary_key],
                            display=record_display(entity, record),
                        )
                        for record in found[:limit]
                    ),
                    truncated=len(found) > limit,
                )
            )
        return tuple(groups)

    def _searchable_fields(
        self,
        entity_name: str,
        entity: NormalizedEntity,
        context: RequestContext,
    ) -> tuple[str, ...]:
        return tuple(
            name
            for name in entity.metadata.get("search_fields", ())
            if name in entity.fields
            and entity.field(name).metadata["type"] in {"string", "choice"}
            and not entity.field(name).metadata.get("computed")
            and self.records.security.can_read_field(entity_name, name, context)
        )

    def _label(self, entity_name: str, entity: NormalizedEntity) -> str:
        label = entity.metadata.get("label")
        if isinstance(label, str) and label:
            return label
        return humanize(entity_name.rsplit(".", 1)[-1])
