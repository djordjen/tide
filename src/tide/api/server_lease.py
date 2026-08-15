"""Holding a server lease for as long as this process is serving.

The store answers "may I?"; this keeps saying "still here" afterwards, and
lets go on the way out. Split from the store because the decision is testable
without a thread and the thread is the part that can fail for unrelated
reasons -- so almost every test here drives :meth:`beat` directly, and exactly
one asserts that the thread calls it.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import secrets
import threading
import time

from tide.data.sqlalchemy_leases import LeaseResult, SQLAlchemyServerLeaseStore
from tide.runtime.errors import ServerLeaseError

LOGGER = logging.getLogger("tide.server")

DEFAULT_LEASE_TTL_SECONDS = 120.0
"""Long enough to survive a slow moment, short enough to clear after a crash.

The trade is entirely about the operator restarting a server that died: they
wait this long, and nothing they can do makes it shorter. Two minutes is the
compromise, and it is why the renewal interval below is a third of it -- two
missed heartbeats still hold the lease.
"""


def new_lease_id() -> str:
    return secrets.token_hex(16)


class ServerLeaseHolder:
    """This process's claim to be the one serving an application.

    Losing the lease mid-flight is logged and retried rather than fatal. A
    process that was paused long enough for its lease to expire is a process
    that is probably still serving requests, and killing it to resolve an
    ambiguity would turn a degraded deployment into an outage. The error in
    the log is the thing an operator can act on; carrying on is the thing that
    does not make it worse.
    """

    def __init__(
        self,
        store: SQLAlchemyServerLeaseStore,
        *,
        lease_id: str,
        application: str,
        scope: str = "browser-sessions",
        ttl: float = DEFAULT_LEASE_TTL_SECONDS,
        interval: float | None = None,
        clock: Callable[[], float] = time.time,
        on_beat: Callable[[bool], None] | None = None,
    ) -> None:
        if ttl <= 0:
            raise ValueError("lease TTL must be positive")
        self.store = store
        self.lease_id = lease_id
        self.application = application
        self.scope = scope
        self.ttl = ttl
        self.interval = ttl / 3 if interval is None else interval
        if self.interval <= 0:
            raise ValueError("lease renewal interval must be positive")
        self._clock = clock
        self._on_beat = on_beat
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def acquire(self) -> LeaseResult:
        return self.store.acquire(
            self.lease_id,
            application=self.application,
            scope=self.scope,
            now=self._clock(),
            ttl=self.ttl,
        )

    def beat(self) -> bool:
        """Renew once. ``False`` means this process no longer holds the lease."""

        now = self._clock()
        if self.store.renew(self.lease_id, now=now):
            return True
        # Lost it. Try to take it back -- the other holder may itself have
        # gone -- and say so either way, because two servers in a mode that
        # cannot share sessions is exactly what this exists to prevent.
        retaken = self.store.acquire(
            self.lease_id,
            application=self.application,
            scope=self.scope,
            now=now,
            ttl=self.ttl,
        )
        if retaken.granted:
            LOGGER.error(
                "server lease was lost and retaken; another process may have "
                "been serving %s at the same time",
                self.application,
            )
            return True
        LOGGER.error(
            "server lease for %s is held by another process (%s); browser "
            "sessions issued here will not be recognised there",
            self.application,
            retaken.holder,
        )
        return False

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="tide-server-lease",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=max(self.interval * 2, 1.0))
        try:
            self.store.release(self.lease_id)
        except ServerLeaseError:
            # Shutting down is not the moment to fail over a lease row: it
            # expires on its own, and raising here would replace a clean exit
            # with a traceback.
            LOGGER.warning("could not release the server lease on shutdown")

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                held = self.beat()
            except ServerLeaseError:
                LOGGER.warning("could not renew the server lease")
                continue
            if self._on_beat is not None:
                self._on_beat(held)
