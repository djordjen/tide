"""How a loaded page names the records it points at."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ReferenceDisplays:
    """Display text for every reference a page of records resolved.

    Keyed by target entity and identity rather than by the field that
    pointed there: the same customer reached through two different fields
    is one record, and should cost one load and read the same either way.

    An absent key is the only negative answer, and it covers every reason
    there could be one -- no such row, a read policy that refuses it, a
    display the principal may not see. A renderer that gets nothing shows
    the stored value, which is what it did before any of this existed.
    """

    entries: Mapping[tuple[str, Any], str]

    def display(self, target_entity: str, identity: Any) -> str | None:
        """Return how ``identity`` names itself, or ``None`` if it may not."""

        if identity is None:
            return None
        return self.entries.get((target_entity, identity))

    def __bool__(self) -> bool:
        return bool(self.entries)


NO_REFERENCE_DISPLAYS = ReferenceDisplays({})
"""What a caller that resolved nothing hands on, so nobody tests for None."""
