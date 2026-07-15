"""Persistence contract consumed by application services."""

from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable


@runtime_checkable
class Repository(Protocol):
    def seed(
        self,
        entity: str,
        records: Iterable[dict[str, Any]],
        *,
        primary_key: str = "id",
    ) -> None: ...

    def all(self, entity: str) -> list[dict[str, Any]]: ...

    def get(self, entity: str, identity: Any) -> dict[str, Any]: ...

    def exists(self, entity: str, identity: Any) -> bool: ...

    def peek_next_identity(self, entity: str) -> int: ...

    def write(
        self,
        entity: str,
        values: dict[str, Any],
        *,
        primary_key: str,
        version_field: str | None,
        expected_version: int | None,
        is_new: bool,
    ) -> dict[str, Any]: ...
