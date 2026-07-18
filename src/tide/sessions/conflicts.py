"""Persistence-independent comparison of stale record drafts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping


class ConflictDisposition(StrEnum):
    """How one field changed relative to the originally observed record."""

    YOUR_CHANGE = "your_change"
    CURRENT_CHANGE = "current_change"
    SAME_CHANGE = "same_change"
    CONFLICT = "conflict"


class ConflictValueChoice(StrEnum):
    """The value a user selected for one genuinely conflicting field."""

    CURRENT = "current"
    DRAFT = "draft"


@dataclass(frozen=True, slots=True)
class RecordConflictField:
    """One changed field in a three-way record comparison."""

    name: str
    original: Any
    current: Any
    draft: Any
    disposition: ConflictDisposition

    @property
    def can_rebase(self) -> bool:
        """Return whether the draft value can be applied without overwriting."""

        return self.disposition is ConflictDisposition.YOUR_CHANGE


@dataclass(frozen=True, slots=True)
class RecordConflict:
    """The meaningful differences between original, current, and draft values."""

    fields: tuple[RecordConflictField, ...]

    @property
    def conflicting_fields(self) -> tuple[str, ...]:
        return tuple(
            field.name
            for field in self.fields
            if field.disposition is ConflictDisposition.CONFLICT
        )

    @property
    def rebase_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if field.can_rebase)


@dataclass(frozen=True, slots=True)
class RecordConflictResolution:
    """A validated plan for rebasing a stale draft onto current values."""

    draft_fields: tuple[str, ...]
    current_fields: tuple[str, ...]
    unresolved_fields: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.unresolved_fields


def compare_record_conflict(
    original: Mapping[str, Any],
    current: Mapping[str, Any],
    draft: Mapping[str, Any],
    *,
    fields: Iterable[str] | None = None,
) -> RecordConflict:
    """Classify a stale draft without choosing a conflict resolution policy."""

    names = tuple(fields) if fields is not None else tuple(
        dict.fromkeys((*original, *current, *draft))
    )
    result: list[RecordConflictField] = []
    for name in names:
        base_value = original.get(name)
        current_value = current.get(name)
        draft_value = draft.get(name)
        yours_changed = draft_value != base_value
        current_changed = current_value != base_value
        if not yours_changed and not current_changed:
            continue
        if yours_changed and current_changed:
            disposition = (
                ConflictDisposition.SAME_CHANGE
                if draft_value == current_value
                else ConflictDisposition.CONFLICT
            )
        elif yours_changed:
            disposition = ConflictDisposition.YOUR_CHANGE
        else:
            disposition = ConflictDisposition.CURRENT_CHANGE
        result.append(
            RecordConflictField(
                name=name,
                original=deepcopy(base_value),
                current=deepcopy(current_value),
                draft=deepcopy(draft_value),
                disposition=disposition,
            )
        )
    return RecordConflict(tuple(result))


def resolve_record_conflict(
    conflict: RecordConflict,
    choices: Mapping[str, ConflictValueChoice],
) -> RecordConflictResolution:
    """Build a plan while rejecting choices for non-conflicting fields."""

    conflicting = set(conflict.conflicting_fields)
    unknown = set(choices) - conflicting
    if unknown:
        raise ValueError(
            "conflict choices contain non-conflicting field(s): "
            + ", ".join(sorted(unknown))
        )
    draft_fields: list[str] = []
    current_fields: list[str] = []
    unresolved_fields: list[str] = []
    for field in conflict.fields:
        if field.disposition is ConflictDisposition.YOUR_CHANGE:
            draft_fields.append(field.name)
        elif field.disposition in {
            ConflictDisposition.CURRENT_CHANGE,
            ConflictDisposition.SAME_CHANGE,
        }:
            current_fields.append(field.name)
        else:
            choice = choices.get(field.name)
            if choice is ConflictValueChoice.DRAFT:
                draft_fields.append(field.name)
            elif choice is ConflictValueChoice.CURRENT:
                current_fields.append(field.name)
            else:
                unresolved_fields.append(field.name)
    return RecordConflictResolution(
        draft_fields=tuple(draft_fields),
        current_fields=tuple(current_fields),
        unresolved_fields=tuple(unresolved_fields),
    )
