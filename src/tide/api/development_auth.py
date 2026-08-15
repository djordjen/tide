"""A browser session for local development, with no credential to paste.

`--auth development` already exists and already refuses to listen anywhere but
loopback. What it did not have was a way into the Web renderer: the browser
signs in with a bearer token typed into a form, so opening the UI meant moving
a 32-character secret from a terminal into a browser by hand, every restart.
This grants the configured development principal a session on request instead.

**There is no credential here, and that is the whole design.** What stands
between this and an attacker is the network boundary rather than a secret, so
the boundary is enforced structurally in three places instead of documented in
one:

* `tide serve` refuses `--auth development` on a non-loopback interface, which
  predates this module;
* `build_fastapi_app` refuses to attach a development browser session to a
  production bearer adapter, so the mode cannot be carried into a deployment by
  a stray argument; and
* the server rejects any request whose `Host` header is not a loopback name,
  which is what actually stops DNS rebinding -- an attacker's domain resolving
  to 127.0.0.1 is same-origin to the browser, so neither the bind address nor
  the absent CORS headers help there, and the `Host` header is the one part of
  such a request that still carries the attacker's name.

Sessions go wherever the session store puts them, which by default is this
process and nowhere else. There is still no store of *identities* to
initialize, which is the point: `--auth local` is the mode that owns those and
it will not start until `tide auth create-user` has made one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import secrets
import time

from tide.api.browser_session import BrowserSessionAccess
from tide.api.session_store import (
    SESSION_STATE_VERSION,
    BrowserSessionStore,
    InMemorySessionStore,
    SessionRecord,
)
from tide.runtime import Principal

__all__ = [
    "DevelopmentBrowserAuth",
    "DevelopmentSessionResult",
    "LOOPBACK_HOST_NAMES",
    "is_loopback_host_header",
]

LOOPBACK_HOST_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
"""Host names a loopback request may carry.

Names rather than addresses: the check reads the `Host` header a client sent,
not the socket it arrived on. Every address in `127.0.0.0/8` is loopback, but
only these are reachable by default and an allow-list that has to parse CIDR is
a larger thing to get wrong than one that compares strings.
"""


def is_loopback_host_header(value: str | None) -> bool:
    """Whether a `Host` header names this machine and not somebody's domain."""

    if not isinstance(value, str) or not value:
        return False
    host = value.strip()
    if host.startswith("["):
        # IPv6 literals keep their brackets and may carry a port after them.
        closing = host.find("]")
        if closing == -1:
            return False
        host = host[: closing + 1]
    elif ":" in host:
        host = host.split(":", 1)[0]
    return host.casefold() in LOOPBACK_HOST_NAMES


@dataclass(frozen=True, slots=True)
class DevelopmentSessionResult:
    """What starting a development session hands back to the browser."""

    session_id: str
    csrf_token: str


class DevelopmentBrowserAuth:
    """Grant one configured principal a browser session, on request, unasked.

    The principal and its roles come from `--principal` and `--role`, so this
    is not an implicit superuser: a development session sees exactly what that
    role is granted, which is also what makes it useful for checking a screen
    the way a particular role will meet it.
    """

    authentication_type = "development-session"
    authentication_mode = "development"
    production = False

    def __init__(
        self,
        principal: Principal,
        *,
        secure_cookie: bool = False,
        session_lifetime_seconds: int = 8 * 60 * 60,
        max_sessions: int = 64,
        sessions: BrowserSessionStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        for label, value in (
            ("session lifetime", session_lifetime_seconds),
            ("maximum sessions", max_sessions),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"development session {label} must be positive")
        self.principal = principal
        self.secure_cookie = secure_cookie
        self.session_lifetime_seconds = session_lifetime_seconds
        self.max_sessions = max_sessions
        self.session_cookie_name = (
            "__Host-tide_session" if secure_cookie else "tide_session"
        )
        self._clock = clock
        self._sessions: BrowserSessionStore = (
            InMemorySessionStore(max_entries=max_sessions)
            if sessions is None
            else sessions
        )

    def begin_session(self) -> DevelopmentSessionResult:
        """Start a session. Nothing is verified, because nothing was asked for."""

        now = self._clock()
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        self._sessions.create(
            session_id,
            SessionRecord(
                subject=self.principal.identifier,
                expires_at=now + self.session_lifetime_seconds,
                state={
                    "version": SESSION_STATE_VERSION,
                    "csrf_token": csrf_token,
                },
            ),
            now=now,
        )
        return DevelopmentSessionResult(session_id, csrf_token)

    def authenticate(self, credential: str) -> Principal | None:
        access = self.authenticate_session(credential)
        return access.principal if access is not None else None

    def authenticate_session(
        self, session_id: str | None
    ) -> BrowserSessionAccess | None:
        if not isinstance(session_id, str) or not session_id:
            return None
        record = self._sessions.read(session_id, now=self._clock())
        if record is None:
            return None
        if record.state.get("version") != SESSION_STATE_VERSION:
            self._sessions.discard(session_id, record)
            return None
        csrf_token = record.state.get("csrf_token")
        if not isinstance(csrf_token, str) or not csrf_token:
            self._sessions.discard(session_id, record)
            return None
        return BrowserSessionAccess(self.principal, csrf_token)

    def end_session(self, session_id: str | None) -> None:
        if not isinstance(session_id, str) or not session_id:
            return
        self._sessions.delete(session_id)
