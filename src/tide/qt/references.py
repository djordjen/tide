"""Remembering how a referenced record names itself."""

from __future__ import annotations

from threading import Lock
from typing import Any

from tide.compiler.normalized import ApplicationModel
from tide.presentation import record_display
from tide.runtime import TideRuntimeError

from .contracts import BrowseApiClient

_MISSING = object()


class ReferenceDisplayCache:
    """Resolve a reference to its display text, once per record.

    A browse grid asks for the same customer once per row that names it, and
    the Qt renderer resolves references on worker threads, several at a time.
    So this is a locked cache rather than a dict: two threads asking for the
    same unseen record must make one request between them and agree on the
    answer, which is what `setdefault` under the second lock settles.

    It is the controller's only mutable state. Holding it here leaves the
    controller's own attributes fixed once it is built.
    """

    def __init__(self, client: BrowseApiClient, model: ApplicationModel) -> None:
        self._client = client
        self._model = model
        self._entries: dict[tuple[str, Any], str] = {}
        self._lock = Lock()

    def display(self, entity_name: str, identity: Any) -> str:
        """Return how a record names itself, fetching it the first time.

        A record the caller may not read reads as "Protected" rather than
        raising: the reference is being drawn beside data they can see, and
        the server has already decided what they are allowed to know.
        """

        key = entity_name, identity
        with self._lock:
            cached = self._entries.get(key, _MISSING)
        if cached is not _MISSING:
            return str(cached)
        try:
            record = self._client.get_record(entity_name, identity).values
            result = record_display(self._model.entity(entity_name), record)
        except TideRuntimeError:
            result = "Protected"
        with self._lock:
            return self._entries.setdefault(key, result)

    def remember(self, entity_name: str, identity: Any, display: str) -> None:
        """Record a display the caller already fetched, to save asking again."""

        with self._lock:
            self._entries[(entity_name, identity)] = display

    def clear(self) -> None:
        """Forget everything, because the records behind it may have changed."""

        with self._lock:
            self._entries.clear()
