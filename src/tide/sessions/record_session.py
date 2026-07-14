"""An editing boundary independent from any persistence implementation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tide.runtime.errors import InvalidSessionError


class SessionState(StrEnum):
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


@dataclass(slots=True)
class RecordSession:
    entity: str
    identity: Any
    original: dict[str, Any]
    values: dict[str, Any]
    expected_version: int | None
    is_new: bool = False
    state: SessionState = SessionState.ACTIVE

    def ensure_active(self) -> None:
        if self.state is not SessionState.ACTIVE:
            raise InvalidSessionError(f"record session is {self.state}")

    def set(self, field: str, value: Any) -> None:
        self.ensure_active()
        self.values[field] = value

    @property
    def changed_fields(self) -> frozenset[str]:
        names = set(self.original) | set(self.values)
        return frozenset(
            name for name in names if self.original.get(name) != self.values.get(name)
        )

    def rollback(self) -> None:
        self.ensure_active()
        self.values = deepcopy(self.original)
        self.state = SessionState.ROLLED_BACK

    def mark_committed(self, values: dict[str, Any]) -> None:
        self.values = deepcopy(values)
        self.original = deepcopy(values)
        self.state = SessionState.COMMITTED
