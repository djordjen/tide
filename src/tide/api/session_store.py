"""Where browser sessions live, so more than one worker can serve them.

An authenticator used to be the storage: a dict on the instance, which made a
session a fact about one process. Two workers behind one address then disagree
about who is signed in, and a restart forgets everyone. This is the same
separation the query cursors already have -- a contract, a process-local
implementation that is still the default, and a shared one beside it.

The store deliberately does not understand a session. It keeps three things it
has to keep: the identifier it is found by, the subject whose sessions can be
ended together, and when it stops counting. Everything else is `state`, which
the authenticator writes and reads back and the store only carries.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, Protocol

SESSION_STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """One browser session, in the only shape every store shares."""

    subject: str
    """Whose session it is, so revoking an account can find all of them.

    Whatever the authenticator can name an account by and will look up again:
    the password store puts its normalized username here, the development one
    its principal identifier. The store only ever compares it.
    """

    expires_at: float
    """Epoch seconds, matching the `time.time` clock the authenticators use."""

    state: Mapping[str, Any]

    def merged(self, changes: Mapping[str, Any]) -> SessionRecord:
        return replace(self, state={**self.state, **changes})


class BrowserSessionStore(Protocol):
    """The eight operations an authenticator actually performs on storage.

    Each one exists because a call site does; there is no operation here that
    nothing calls. `now` is passed in rather than read, so a store has no clock
    of its own to disagree with the authenticator's, and it is epoch seconds
    for the same reason `expires_at` is.
    """

    def create(self, session_id: str, record: SessionRecord, *, now: float) -> None:
        """Store a new session, evicting the least recently used if full."""

    def read(self, session_id: str, *, now: float) -> SessionRecord | None:
        """Return a live session and mark it used, or ``None``."""

    def replace(
        self,
        session_id: str,
        expected: SessionRecord,
        record: SessionRecord,
        *,
        now: float,
    ) -> bool:
        """Swap a session for a revalidated one if nobody else changed it.

        Returns whether the swap happened. A caller that loses re-reads: the
        winner's record is as good as its own, and mistaking a concurrent
        refresh for a revocation would sign a user out for someone else's
        success.
        """

    def discard(self, session_id: str, expected: SessionRecord) -> bool:
        """Delete a session only if it is still the one that was read.

        A verdict reached while revalidating was reached against the record in
        hand. If another worker has replaced it since, that verdict is about a
        session that no longer exists, and acting on it would end one that was
        just legitimately refreshed.
        """

    def delete(self, session_id: str) -> None: ...

    def delete_subject(self, subject: str) -> int:
        """End every session one subject holds, returning how many."""

    def clear(self) -> int: ...

    def update_subject(self, subject: str, changes: Mapping[str, Any]) -> int:
        """Merge `changes` into the state of every session one subject holds."""


class LoginThrottleStore(Protocol):
    """How many times lately a subject has failed to prove who they are.

    Kept beside the sessions rather than in the authenticator for the same
    reason: a budget of five attempts that is really five *per worker* is not
    the number it claims to be, and nothing about adding a worker says so.
    """

    def count_failures(self, subject: str, *, now: float, window: float) -> int: ...

    def record_failure(
        self,
        subject: str,
        *,
        now: float,
        window: float,
        limit: int,
    ) -> None:
        """Record one failure, stopping at `limit` so a bucket cannot grow."""

    def clear_failures(self, subject: str) -> None: ...


class SessionAndThrottleStore(BrowserSessionStore, LoginThrottleStore, Protocol):
    """What an authenticator that verifies a credential needs: both."""


class InMemorySessionStore:
    """Bounded process-local sessions: what an authenticator used to be.

    This is still the default, and for a single-worker deployment it is the
    right answer -- nothing is written down, so nothing has to be protected at
    rest. It is not a shared store, and `tide serve` refuses more than one
    worker while it is the one in use.
    """

    shared = False

    def __init__(
        self,
        *,
        max_entries: int = 4096,
        max_failure_subjects: int = 4096,
    ) -> None:
        for label, value in (
            ("capacity", max_entries),
            ("failure-subject capacity", max_failure_subjects),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"session store {label} must be an integer")
            if value < 1:
                raise ValueError(f"session store {label} must be positive")
        self.max_entries = max_entries
        self.max_failure_subjects = max_failure_subjects
        self._entries: OrderedDict[str, SessionRecord] = OrderedDict()
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = RLock()

    def create(self, session_id: str, record: SessionRecord, *, now: float) -> None:
        with self._lock:
            self._purge_expired(now)
            self._entries[session_id] = record
            self._entries.move_to_end(session_id)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def read(self, session_id: str, *, now: float) -> SessionRecord | None:
        with self._lock:
            self._purge_expired(now)
            record = self._entries.get(session_id)
            if record is None:
                return None
            self._entries.move_to_end(session_id)
            return record

    def replace(
        self,
        session_id: str,
        expected: SessionRecord,
        record: SessionRecord,
        *,
        now: float,
    ) -> bool:
        with self._lock:
            current = self._entries.get(session_id)
            if current is None or current != expected:
                return False
            self._entries[session_id] = record
            self._entries.move_to_end(session_id)
            return True

    def discard(self, session_id: str, expected: SessionRecord) -> bool:
        with self._lock:
            if self._entries.get(session_id) != expected:
                return False
            self._entries.pop(session_id, None)
            return True

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._entries.pop(session_id, None)

    def delete_subject(self, subject: str) -> int:
        with self._lock:
            doomed = [
                key
                for key, record in self._entries.items()
                if record.subject == subject
            ]
            for key in doomed:
                self._entries.pop(key, None)
        return len(doomed)

    def clear(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
        return count

    def update_subject(self, subject: str, changes: Mapping[str, Any]) -> int:
        with self._lock:
            changed = [
                key
                for key, record in self._entries.items()
                if record.subject == subject
            ]
            for key in changed:
                self._entries[key] = self._entries[key].merged(changes)
        return len(changed)

    def count_failures(self, subject: str, *, now: float, window: float) -> int:
        with self._lock:
            return len(self._bucket(subject, now=now, window=window))

    def record_failure(
        self,
        subject: str,
        *,
        now: float,
        window: float,
        limit: int,
    ) -> None:
        with self._lock:
            failures = self._bucket(subject, now=now, window=window)
            if len(failures) < limit:
                failures.append(now)

    def clear_failures(self, subject: str) -> None:
        with self._lock:
            self._failures.pop(subject, None)

    def _bucket(self, subject: str, *, now: float, window: float) -> deque[float]:
        failures = self._failures.setdefault(subject, deque())
        boundary = now - window
        while failures and failures[0] <= boundary:
            failures.popleft()
        self._failures.move_to_end(subject)
        while len(self._failures) > self.max_failure_subjects:
            self._failures.popitem(last=False)
        return failures

    def _purge_expired(self, now: float) -> None:
        for key in [
            key
            for key, record in self._entries.items()
            if record.expires_at <= now
        ]:
            self._entries.pop(key, None)
