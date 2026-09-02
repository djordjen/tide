"""Opaque continuation cursor storage for secured query services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import secrets
from threading import RLock
import time
from typing import Any, Protocol

from tide.data.repository import FilterCondition, SortField, SummaryRequest
from tide.runtime.errors import InvalidQueryCursor
from tide.services.references import NO_REFERENCE_DISPLAYS, ReferenceDisplays

CURSOR_VERSION = 1


@dataclass(frozen=True, slots=True)
class CursorShape:
    model: tuple[str, str, str]
    entity: str
    filters: tuple[FilterCondition, ...]
    sort: tuple[SortField, ...]
    limit: int
    principal: tuple[str, tuple[str, ...]]
    # Last and defaulted so cursors issued before criteria existed still
    # deserialize -- they then mismatch a criteria-carrying query instead of
    # silently opening an unfiltered page.
    criteria: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CursorState:
    version: int
    shape: CursorShape
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class QueryPage:
    records: tuple[dict[str, Any], ...]
    next_cursor: str | None
    references: ReferenceDisplays = NO_REFERENCE_DISPLAYS
    """How the records on this page name what they point at.

    Resolved with the page, because a grid draws its reference columns
    at the moment it draws the rows and would otherwise buy each name
    separately.
    """
    summaries: tuple[tuple[SummaryRequest, Any], ...] = ()
    """Each requested aggregate beside its value, in request order.

    Computed over the whole filtered set the page walks, never the visible
    slice -- and only when the query asked, so a page that did not carries
    nothing rather than an empty answer.
    """


class CursorStore(Protocol):
    def issue(self, state: CursorState) -> str: ...

    def resolve(self, token: str) -> CursorState: ...


@dataclass(frozen=True, slots=True)
class _StoredCursor:
    state: CursorState
    expires_at: float


class InMemoryCursorStore:
    """Bounded process-local cursor state with opaque random bearer tokens."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 900,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("cursor TTL must be positive")
        if max_entries < 1:
            raise ValueError("cursor store capacity must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._entries: dict[str, _StoredCursor] = {}
        self._lock = RLock()

    def issue(self, state: CursorState) -> str:
        if state.version != CURSOR_VERSION:
            raise ValueError(f"unsupported cursor version {state.version}")
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            while len(self._entries) >= self.max_entries:
                self._entries.pop(next(iter(self._entries)))
            token = self._new_token()
            self._entries[token] = _StoredCursor(
                state=state,
                expires_at=now + self.ttl_seconds,
            )
            return token

    def resolve(self, token: str) -> CursorState:
        if not isinstance(token, str) or not token:
            raise InvalidQueryCursor
        with self._lock:
            self._purge_expired(self._clock())
            stored = self._entries.get(token)
            if stored is None:
                raise InvalidQueryCursor
            return stored.state

    def _new_token(self) -> str:
        for _attempt in range(10):
            token = self._token_factory()
            if token and token not in self._entries:
                return token
        raise RuntimeError("could not allocate a unique query cursor")

    def _purge_expired(self, now: float) -> None:
        expired = [
            token
            for token, stored in self._entries.items()
            if stored.expires_at <= now
        ]
        for token in expired:
            self._entries.pop(token, None)
