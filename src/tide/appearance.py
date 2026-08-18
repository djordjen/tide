"""What an entity's ``appearance`` rules make of one record.

Its own module, and deliberately a small one: the service enforces the locks a
rule declares, and `tide.services` cannot import `tide.presentation` — that
reaches `tide.data`, which reaches back into `tide.services`. Nothing here
imports anything but the expression engine, so both sides can have it.

`tide.presentation` re-exports these, so every renderer keeps asking the one
module it already asks about action state and field locks.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from types import MappingProxyType
from typing import Any, Iterable, Mapping, get_args

from tide.compiler.expressions import evaluate_expression
from tide.model.source import TideEmphasis

EMPHASIS_VALUES: tuple[str, ...] = get_args(TideEmphasis)
"""The closed set an ``appearance`` rule may ask for, from the schema itself.

Names rather than colours. The framework owns its palette in a light theme, a
dark one and a terminal, so an author who wrote ``#FFFF88`` would have authored
something that works in one of the three. The author says what the record
means; the renderer says what that looks like -- the same split as labels.
"""


def _no_fields() -> Mapping[str, str]:
    """An empty verdict that still answers ``.get`` and cannot be shared."""

    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class RecordAppearance:
    """What an entity's ``appearance`` rules say about one record.

    ``locks_record`` and ``locked`` are not a second answer to "may this be
    edited": they are folded into ``field_is_immutable``, which every renderer
    already asks, and enforced beside ``immutable_when`` in the service, so
    REST and MCP honour a rule the browser respects.
    """

    record: str | None = None
    fields: Mapping[str, str] = dataclass_field(default_factory=_no_fields)
    locks_record: bool = False
    locked: frozenset[str] = frozenset()
    hidden: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return (
            self.record is not None
            or bool(self.fields)
            or self.locks_record
            or bool(self.locked)
            or bool(self.hidden)
        )


def record_appearance(
    rules: Iterable[Mapping[str, Any]],
    values: Mapping[str, Any],
) -> RecordAppearance:
    """Resolve an entity's appearance rules against one record.

    The first matching rule owns a target, so precedence is the order the
    rules are written in rather than a priority number kept somewhere else.
    A rule naming ``fields`` speaks for those fields; one naming none speaks
    for the record as a whole, which is the grid row and the record heading.

    A condition that cannot be evaluated applies nothing -- the opposite of
    ``field_is_immutable``, and both fail safe for what they are. Withholding
    an edit is caution; painting a record a colour that means something it is
    not is a lie about the data.
    """

    record: str | None = None
    fields: dict[str, str] = {}
    locks_record = False
    locked: set[str] = set()
    hidden: set[str] = set()
    for rule in rules:
        condition = rule.get("when")
        if not condition:
            continue
        try:
            matched = bool(evaluate_expression(str(condition), values))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if not matched:
            continue
        emphasis = str(rule.get("emphasis") or "")
        targets = tuple(str(name) for name in rule.get("fields") or ())
        if not targets:
            if emphasis and record is None:
                record = emphasis
            # A record-level lock reaches every ordinarily writable field, the
            # way a transition's `locks_record` does; the two differ only in
            # what they ask about.
            locks_record = locks_record or rule.get("enabled") is False
            continue
        if emphasis:
            for name in targets:
                fields.setdefault(name, emphasis)
        if rule.get("enabled") is False:
            locked.update(targets)
        if rule.get("visible") is False:
            hidden.update(targets)
    return RecordAppearance(
        record=record,
        fields=fields,
        locks_record=locks_record,
        locked=frozenset(locked),
        hidden=frozenset(hidden),
    )
