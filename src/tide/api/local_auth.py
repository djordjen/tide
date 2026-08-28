"""Framework-owned username/password identities and opaque local sessions."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Iterable
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import pbkdf2_hmac, sha256
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
from threading import BoundedSemaphore
import time
from typing import Callable, Iterator, NoReturn

from tide.api.browser_session import BrowserSessionAccess
from tide.api.session_store import (
    SESSION_STATE_VERSION,
    InMemorySessionStore,
    SessionAndThrottleStore,
    SessionRecord,
)
from tide.runtime import Principal


DEFAULT_PASSWORD_ITERATIONS = 600_000
MAX_PASSWORD_BYTES = 1024
MIN_PASSWORD_CHARACTERS = 12
_MAX_PASSWORD_ITERATIONS = 2_000_000
_SCHEMA_VERSION = "1"
_USERNAME = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?")
_WINDOWS_SID = re.compile(r"\bS-\d+(?:-\d+)+\b", re.IGNORECASE)


class LocalAuthenticationError(ValueError):
    """A local identity operation could not be completed safely."""


class LocalAuthenticationBusy(LocalAuthenticationError):
    """Too many sign-ins are already being verified.

    A subclass so anything catching the generic error keeps refusing, but a
    distinct type so the route can answer "try again" rather than telling a
    legitimate user their password is wrong.
    """


@dataclass(frozen=True, slots=True)
class LocalUser:
    username: str
    display_name: str
    password_hash: str
    enabled: bool
    roles: frozenset[str]


@dataclass(frozen=True, slots=True)
class LocalUserSummary:
    """One account as administration sees it.

    Deliberately not :class:`LocalUser` without a field: this carries no
    password hash at all, so a listing cannot leak one by being projected
    carelessly somewhere downstream. The timestamps are the two questions an
    administrator actually asks of an account they did not create.
    """

    username: str
    display_name: str
    enabled: bool
    roles: frozenset[str]
    created_at: str
    password_changed_at: str


@dataclass(frozen=True, slots=True)
class LocalLoginResult:
    session_id: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class _LocalSession:
    principal: Principal
    csrf_token: str
    expires_at: float
    username: str
    credential_stamp: str
    """Digest of the password hash this session was issued against.

    Not the hash, and never compared with anything a caller sends: it exists so
    that changing a password is recognisable as a different credential, which
    is what ends the sessions opened under the old one.

    The stored `password_changed_at` would read more naturally and is not used,
    because the store keeps it to the second -- creating a user and resetting
    their password within the same second are indistinguishable in it, which is
    ordinary in a test and plausible in an emergency. The digest cannot miss.
    Upgrading the work factor also moves the hash, so `login` re-stamps the
    sessions it did that to rather than signing anyone out for it.
    """

    revalidate_at: float


def _session_record(session: _LocalSession) -> SessionRecord:
    """Flatten a session into the shape any store can hold.

    The principal is split into its identifier and roles rather than stored as
    an object, because a shared store has to write it down and read it back in
    another process.
    """

    return SessionRecord(
        subject=session.username,
        expires_at=session.expires_at,
        state={
            "version": SESSION_STATE_VERSION,
            "principal": session.principal.identifier,
            "roles": sorted(session.principal.roles),
            "csrf_token": session.csrf_token,
            "credential_stamp": session.credential_stamp,
            "revalidate_at": session.revalidate_at,
        },
    )


def _local_session(record: SessionRecord) -> _LocalSession | None:
    """Rebuild a session from storage, or ``None`` if the record cannot be read.

    A shared store holds rows this process did not write, so an unreadable one
    is an ordinary possibility rather than a broken invariant: a state version
    from an older build, a value somebody edited, a column that changed type on
    the way through a driver. It ends that session rather than the request, and
    it never guesses a missing field -- a session with an unreadable role list
    is not a session with no roles.
    """

    state = record.state
    if state.get("version") != SESSION_STATE_VERSION:
        return None
    identifier = state.get("principal")
    roles = state.get("roles")
    csrf_token = state.get("csrf_token")
    credential_stamp = state.get("credential_stamp")
    revalidate_at = state.get("revalidate_at")
    if (
        not isinstance(identifier, str)
        or not identifier
        or not isinstance(csrf_token, str)
        or not isinstance(credential_stamp, str)
        or isinstance(revalidate_at, bool)
        or not isinstance(revalidate_at, (int, float))
        or not isinstance(roles, (list, tuple))
        or not all(isinstance(role, str) for role in roles)
    ):
        return None
    return _LocalSession(
        principal=Principal(identifier, roles=frozenset(roles)),
        csrf_token=csrf_token,
        expires_at=record.expires_at,
        username=record.subject,
        credential_stamp=credential_stamp,
        revalidate_at=float(revalidate_at),
    )


class LocalUserStore:
    """Persistent identities in a TIDE-owned SQLite file.

    The file is intentionally independent of the application's managed or
    externally owned business database. Schema creation is explicit through
    :meth:`initialize`; normal server startup only validates an existing store.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        application: str,
        password_iterations: int = DEFAULT_PASSWORD_ITERATIONS,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.application = application.strip()
        self.password_iterations = password_iterations
        if not self.application:
            raise ValueError("local identity application name must not be empty")
        if (
            isinstance(password_iterations, bool)
            or not isinstance(password_iterations, int)
            or not 1 <= password_iterations <= _MAX_PASSWORD_ITERATIONS
        ):
            raise ValueError("local password iteration count is invalid")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tide_local_auth_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tide_local_users (
                    username TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    password_changed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tide_local_user_roles (
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY (username, role),
                    FOREIGN KEY (username)
                        REFERENCES tide_local_users(username)
                        ON DELETE CASCADE
                )
                """
            )
            self._bind_metadata(connection, "schema_version", _SCHEMA_VERSION)
            self._bind_metadata(connection, "application", self.application)
            connection.execute("PRAGMA optimize")
        _restrict_to_owner(self.path)

    def validate(self) -> None:
        if not self.path.is_file():
            raise LocalAuthenticationError(
                f"local identity store is not initialized: {self.path}"
            )
        try:
            with self._connect() as connection:
                metadata = dict(
                    connection.execute(
                        "SELECT key, value FROM tide_local_auth_metadata"
                    ).fetchall()
                )
        except sqlite3.Error as error:
            raise LocalAuthenticationError(
                "local identity store is invalid or unavailable"
            ) from error
        if metadata.get("schema_version") != _SCHEMA_VERSION:
            raise LocalAuthenticationError(
                "local identity store has an unsupported schema version"
            )
        if metadata.get("application") != self.application:
            raise LocalAuthenticationError(
                "local identity store belongs to a different application"
            )

    def create_user(
        self,
        username: str,
        password: str,
        *,
        roles: Iterable[str],
        display_name: str | None = None,
    ) -> LocalUser:
        self.validate()
        normalized_username = normalize_username(username)
        normalized_roles = frozenset(_normalize_role(role) for role in roles)
        if not normalized_roles:
            raise ValueError("a local user must have at least one role")
        normalized_display_name = (display_name or username).strip()
        if not normalized_display_name or len(normalized_display_name) > 128:
            raise ValueError("local user display name must contain 1-128 characters")
        password_hash = hash_password(password, iterations=self.password_iterations)
        timestamp = _utc_timestamp()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO tide_local_users (
                        username, display_name, password_hash, enabled,
                        created_at, password_changed_at
                    ) VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (
                        normalized_username,
                        normalized_display_name,
                        password_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO tide_local_user_roles (username, role)
                    VALUES (?, ?)
                    """,
                    (
                        (normalized_username, role)
                        for role in sorted(normalized_roles)
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise LocalAuthenticationError(
                f"local user {normalized_username!r} already exists"
            ) from error
        return LocalUser(
            normalized_username,
            normalized_display_name,
            password_hash,
            True,
            normalized_roles,
        )

    def set_password(self, username: str, password: str) -> None:
        self.validate()
        normalized_username = normalize_username(username)
        password_hash = hash_password(password, iterations=self.password_iterations)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tide_local_users
                SET password_hash = ?, password_changed_at = ?
                WHERE username = ?
                """,
                (password_hash, _utc_timestamp(), normalized_username),
            )
        if cursor.rowcount != 1:
            raise LocalAuthenticationError(
                f"local user {normalized_username!r} does not exist"
            )

    def update_user(
        self,
        username: str,
        *,
        roles: Iterable[str] | None = None,
        enabled: bool | None = None,
        guard: Callable[[tuple[LocalUserSummary, ...]], None] | None = None,
    ) -> frozenset[str] | None:
        """Apply this account's roles and enabled flag as one write.

        A caller changing both must never be left half-applied: refused --
        by the guard, or by the account not existing -- neither lands.
        ``guard`` runs inside the write's own immediate transaction, over
        the accounts as they are at write time; raising refuses the write.
        This is what lets a caller hold an invariant like "an enabled
        administrator remains" against concurrent writers instead of
        between a check and an act.

        Returns the roles the account now holds when ``roles`` was given.
        """

        if roles is None and enabled is None:
            raise ValueError("a local user update must change roles or enabled")
        self.validate()
        normalized_username = normalize_username(username)
        normalized_roles: frozenset[str] | None = None
        if roles is not None:
            normalized_roles = frozenset(_normalize_role(role) for role in roles)
            if not normalized_roles:
                raise ValueError("a local user must keep at least one role")
        with self._connect(immediate=guard is not None) as connection:
            if guard is not None:
                guard(self._user_summaries(connection))
            exists = connection.execute(
                "SELECT 1 FROM tide_local_users WHERE username = ?",
                (normalized_username,),
            ).fetchone()
            if exists is None:
                raise LocalAuthenticationError(
                    f"local user {normalized_username!r} does not exist"
                )
            if normalized_roles is not None:
                connection.execute(
                    "DELETE FROM tide_local_user_roles WHERE username = ?",
                    (normalized_username,),
                )
                connection.executemany(
                    """
                    INSERT INTO tide_local_user_roles (username, role)
                    VALUES (?, ?)
                    """,
                    (
                        (normalized_username, role)
                        for role in sorted(normalized_roles)
                    ),
                )
            if enabled is not None:
                connection.execute(
                    """
                    UPDATE tide_local_users
                    SET enabled = ?
                    WHERE username = ?
                    """,
                    (1 if enabled else 0, normalized_username),
                )
        return normalized_roles

    def set_enabled(
        self,
        username: str,
        enabled: bool,
        *,
        guard: Callable[[tuple[LocalUserSummary, ...]], None] | None = None,
    ) -> None:
        """Allow or refuse this account's sign-ins.

        A disabled account keeps its password and roles; nothing is deleted, so
        the decision is reversible. `login` refuses it outright, and a session
        already open ends at its next revalidation. ``guard`` behaves as it
        does for ``update_user``, which carries the write.
        """

        self.update_user(username, enabled=enabled, guard=guard)

    def set_roles(
        self,
        username: str,
        roles: Iterable[str],
        *,
        guard: Callable[[tuple[LocalUserSummary, ...]], None] | None = None,
    ) -> frozenset[str]:
        """Replace this account's roles, returning what it now holds.

        Replaces rather than merges, so withdrawing a role is expressible --
        an add-only interface can only ever grant. The caller is responsible
        for the roles naming something the application compiled; the store
        does not know the model. ``guard`` behaves as it does for
        ``update_user``, which carries the write.
        """

        replaced = self.update_user(username, roles=roles, guard=guard)
        assert replaced is not None  # roles were given, so they were replaced
        return replaced

    def list_users(self, *, limit: int | None = None) -> tuple[LocalUserSummary, ...]:
        """Every account, by username, without any password material.

        ``limit`` exists so a caller answering a request can ask for one more
        than it will show and say out loud that it truncated, the way bounded
        distinct values do. The store itself has no opinion about how many
        accounts is too many.
        """

        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
        ):
            raise ValueError("local user listing limit must be a positive integer")
        self.validate()
        with self._connect() as connection:
            return self._user_summaries(connection, limit=limit)

    def _user_summaries(
        self,
        connection: sqlite3.Connection,
        *,
        limit: int | None = None,
    ) -> tuple[LocalUserSummary, ...]:
        """Read the accounts through the given connection.

        The connection is the point: a write guard reads through its own
        immediate transaction, so what it sees is what the write meets.
        """

        rows = connection.execute(
            """
            SELECT username, display_name, enabled,
                   created_at, password_changed_at
            FROM tide_local_users
            ORDER BY username
            """
            + ("LIMIT ?" if limit is not None else ""),
            (limit,) if limit is not None else (),
        ).fetchall()
        usernames = {str(row["username"]) for row in rows}
        roles: dict[str, set[str]] = {username: set() for username in usernames}
        for username, role in connection.execute(
            """
            SELECT username, role
            FROM tide_local_user_roles
            ORDER BY username, role
            """
        ).fetchall():
            # A role row for an account the page did not reach is not this
            # listing's business; `limit` is the only way that happens.
            if str(username) in roles:
                roles[str(username)].add(str(role))
        return tuple(
            LocalUserSummary(
                username=str(row["username"]),
                display_name=str(row["display_name"]),
                enabled=bool(row["enabled"]),
                roles=frozenset(roles[str(row["username"])]),
                created_at=str(row["created_at"]),
                password_changed_at=str(row["password_changed_at"]),
            )
            for row in rows
        )

    def upgrade_password_hash(
        self,
        username: str,
        expected_password_hash: str,
        password_hash: str,
    ) -> bool:
        """Replace a stored hash with a stronger one for the same password.

        Deliberately leaves `password_changed_at` alone: the password did not
        change, only the cost of checking it, and moving that timestamp would
        sign the user out of every other session for an upgrade they did not
        ask for. The expected hash makes this compare-and-swap: an
        administrator's concurrent password reset must win rather than being
        overwritten by a sign-in that verified the previous password.
        """

        self.validate()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tide_local_users
                SET password_hash = ?
                WHERE username = ? AND password_hash = ?
                """,
                (
                    password_hash,
                    normalize_username(username),
                    expected_password_hash,
                ),
            )
        return cursor.rowcount == 1

    def get_user(self, username: str) -> LocalUser | None:
        self.validate()
        try:
            normalized_username = normalize_username(username)
        except ValueError:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT username, display_name, password_hash, enabled
                FROM tide_local_users
                WHERE username = ?
                """,
                (normalized_username,),
            ).fetchone()
            if row is None:
                return None
            roles = frozenset(
                role
                for (role,) in connection.execute(
                    """
                    SELECT role
                    FROM tide_local_user_roles
                    WHERE username = ?
                    ORDER BY role
                    """,
                    (normalized_username,),
                ).fetchall()
            )
        return LocalUser(
            username=str(row["username"]),
            display_name=str(row["display_name"]),
            password_hash=str(row["password_hash"]),
            enabled=bool(row["enabled"]),
            roles=roles,
        )

    @contextmanager
    def _connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        if immediate:
            # The write lock is taken before anything is read, so a guard
            # deciding inside this transaction decides over the state the
            # write will actually meet -- across processes, not only
            # threads, which is how `tide serve` deploys.
            connection.isolation_level = None
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if immediate:
            connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _bind_metadata(
        self,
        connection: sqlite3.Connection,
        key: str,
        value: str,
    ) -> None:
        existing = connection.execute(
            "SELECT value FROM tide_local_auth_metadata WHERE key = ?",
            (key,),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO tide_local_auth_metadata (key, value) VALUES (?, ?)",
                (key, value),
            )
            return
        if existing["value"] != value:
            raise LocalAuthenticationError(
                f"local identity store {key.replace('_', ' ')} does not match"
            )


class LocalPasswordAuth:
    """Authenticate local users and keep their sessions wherever the store is.

    The store also holds the failed-login counters, because they are the
    same coordination problem wearing a different hat: a limit of five
    attempts counted per process is five attempts *per process*, and adding
    a worker does not announce that it has doubled the budget.
    """

    authentication_type = "local-password"
    authentication_mode = "password"
    production = True

    def __init__(
        self,
        store: LocalUserStore,
        *,
        allowed_roles: Iterable[str],
        secure_cookie: bool,
        session_lifetime_seconds: int = 8 * 60 * 60,
        revalidate_interval_seconds: int = 30,
        max_sessions: int = 4096,
        max_failures: int = 5,
        max_failure_subjects: int = 4096,
        failure_window_seconds: int = 60,
        max_concurrent_verifications: int = 8,
        sessions: SessionAndThrottleStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        for label, value in (
            ("session lifetime", session_lifetime_seconds),
            ("revalidation interval", revalidate_interval_seconds),
            ("maximum sessions", max_sessions),
            ("maximum failures", max_failures),
            ("maximum failure subjects", max_failure_subjects),
            ("failure window", failure_window_seconds),
            ("concurrent verifications", max_concurrent_verifications),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"local authentication {label} must be positive")
        store.validate()
        self.store = store
        self.allowed_roles = frozenset(allowed_roles)
        self.secure_cookie = secure_cookie
        self.session_lifetime_seconds = session_lifetime_seconds
        self.revalidate_interval_seconds = revalidate_interval_seconds
        self.max_sessions = max_sessions
        self.max_failures = max_failures
        self.max_failure_subjects = max_failure_subjects
        self.failure_window_seconds = failure_window_seconds
        self.max_concurrent_verifications = max_concurrent_verifications
        self._verifications = BoundedSemaphore(max_concurrent_verifications)
        self.session_cookie_name = (
            "__Host-tide_session" if secure_cookie else "tide_session"
        )
        self._clock = clock
        # An injected store owns its own capacity, because two workers sharing
        # one must not each believe a different number. `max_sessions` sizes
        # the default instead, which is the only store this can still name.
        self._sessions: SessionAndThrottleStore = (
            InMemorySessionStore(
                max_entries=max_sessions,
                max_failure_subjects=max_failure_subjects,
            )
            if sessions is None
            else sessions
        )
        self._dummy_hash = hash_password(
            secrets.token_urlsafe(24),
            iterations=store.password_iterations,
        )

    @contextmanager
    def verification_slot(self) -> Iterator[None]:
        """Hold one of the bounded password-verification slots.

        Per-username throttling cannot see an attacker who never repeats a
        name, so the cost of verifying needs a bound of its own. These handlers
        run in the server's shared threadpool: without a cap, a few dozen
        concurrent attempts starve every other request the application serves.
        """

        if not self._verifications.acquire(blocking=False):
            raise LocalAuthenticationBusy("too many sign-in attempts in progress")
        try:
            yield
        finally:
            self._verifications.release()

    def login(self, *, username: str, password: str) -> LocalLoginResult:
        now = self._clock()
        try:
            normalized_username = normalize_username(username)
            username_valid = True
        except ValueError:
            normalized_username = "invalid"
            username_valid = False
        throttled = (
            self._sessions.count_failures(
                normalized_username,
                now=now,
                window=self.failure_window_seconds,
            )
            >= self.max_failures
        )

        if throttled:
            # The refusal is already decided. Hashing anyway would let an
            # attacker convert one cheap request into a third of a second of
            # server CPU, which is the amplification throttling exists to stop.
            # It reveals that this username is currently throttled -- something
            # whoever caused the throttling already knows.
            raise LocalAuthenticationError("username or password is incorrect")

        with self.verification_slot():
            user = self.store.get_user(normalized_username)
            # An un-throttled miss still pays, so a present username cannot be
            # told from an absent one by how long the answer takes.
            password_hash = (
                user.password_hash if user is not None else self._dummy_hash
            )
            password_matches = verify_password(password, password_hash)
        roles = (
            user.roles.intersection(self.allowed_roles)
            if user is not None
            else frozenset()
        )
        if (
            not username_valid
            or user is None
            or not user.enabled
            or not password_matches
            or not roles
        ):
            self._refuse_login(normalized_username, now=now)

        # The one moment the plaintext is in hand and already known good, so
        # it is the only chance to re-hash at the current cost without asking
        # anyone to change their password. The stored format carries its own
        # iteration count, so an old hash keeps verifying until it is replaced.
        stored_hash = user.password_hash
        if _hash_iterations(stored_hash) < self.store.password_iterations:
            candidate_hash = hash_password(
                password, iterations=self.store.password_iterations
            )
            upgraded = self.store.upgrade_password_hash(
                user.username,
                stored_hash,
                candidate_hash,
            )
            current = self.store.get_user(user.username)
            current_roles = (
                current.roles.intersection(self.allowed_roles)
                if current is not None
                else frozenset()
            )
            current_password_matches = bool(
                current is not None
                and (
                    (upgraded and current.password_hash == candidate_hash)
                    or verify_password(password, current.password_hash)
                )
            )
            if (
                current is None
                or not current.enabled
                or not current_password_matches
                or not current_roles
            ):
                self._refuse_login(normalized_username, now=now)
            user = current
            roles = current_roles
            stored_hash = current.password_hash
            if upgraded and stored_hash == candidate_hash:
                self._restamp_sessions(
                    user.username,
                    _credential_stamp(stored_hash),
                )

        principal = Principal(
            f"local:{user.username}",
            roles=frozenset(roles),
        )
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        self._sessions.clear_failures(normalized_username)
        self._sessions.create(
            session_id,
            _session_record(
                _LocalSession(
                    principal=principal,
                    csrf_token=csrf_token,
                    expires_at=now + self.session_lifetime_seconds,
                    username=user.username,
                    credential_stamp=_credential_stamp(stored_hash),
                    revalidate_at=now + self.revalidate_interval_seconds,
                )
            ),
            now=now,
        )
        return LocalLoginResult(session_id, csrf_token)

    def authenticate(self, credential: str) -> Principal | None:
        access = self.authenticate_session(credential)
        return access.principal if access is not None else None

    def authenticate_session(self, session_id: str | None) -> BrowserSessionAccess | None:
        if not isinstance(session_id, str) or not session_id:
            return None
        now = self._clock()
        while True:
            record = self._sessions.read(session_id, now=now)
            if record is None:
                return None
            session = _local_session(record)
            if session is None:
                self._sessions.discard(session_id, record)
                return None
            if now < session.revalidate_at:
                return BrowserSessionAccess(
                    session.principal,
                    session.csrf_token,
                )

            # Re-reading opens a connection to the store, so it is bounded by
            # an interval rather than done per request. Two requests may reach
            # the boundary together, and with a shared store they may be in
            # different processes. If the other one refreshes the record first,
            # loop over its result instead of mistaking replacement for
            # revocation; an actual revocation removes the record and the next
            # pass returns None.
            refreshed = self._revalidated(session, now=now)
            if refreshed is None:
                if self._sessions.discard(session_id, record):
                    return None
                continue
            if self._sessions.replace(
                session_id,
                record,
                _session_record(refreshed),
                now=now,
            ):
                return BrowserSessionAccess(
                    refreshed.principal,
                    refreshed.csrf_token,
                )

    def _revalidated(
        self, session: _LocalSession, *, now: float
    ) -> _LocalSession | None:
        """Return this session carried forward, or ``None`` if it may not be.

        The session keeps its identifier and CSRF token; what it picks up is
        the account's current roles, so withdrawing one takes effect without
        signing the user out of work they are in the middle of. Losing the
        account, being disabled, having the password changed, or being left
        with no allowed role at all are the four ways it ends.
        """

        user = self.store.get_user(session.username)
        if user is None or not user.enabled:
            return None
        if _credential_stamp(user.password_hash) != session.credential_stamp:
            return None
        roles = user.roles.intersection(self.allowed_roles)
        if not roles:
            return None
        principal = session.principal
        if principal.roles != roles:
            principal = Principal(principal.identifier, roles=frozenset(roles))
        return _LocalSession(
            principal=principal,
            csrf_token=session.csrf_token,
            expires_at=session.expires_at,
            username=session.username,
            credential_stamp=session.credential_stamp,
            revalidate_at=now + self.revalidate_interval_seconds,
        )

    def _restamp_sessions(self, username: str, stamp: str) -> None:
        """Carry this user's live sessions onto a re-hashed credential.

        The hash moved because the work factor did, not because the password
        did, so the sessions issued against the old one stay valid.
        """

        self._sessions.update_subject(username, {"credential_stamp": stamp})

    def revoke_user(self, username: str) -> int:
        """End every session held by one account, now rather than on interval.

        Returns how many were ended, so an operator gets an answer rather than
        silence. A changed password already ends them at the next check; this
        is for when waiting is not acceptable.
        """

        try:
            normalized = normalize_username(username)
        except ValueError:
            return 0
        return self._sessions.delete_subject(normalized)

    def revoke_all(self) -> int:
        """End every local-password session the store holds."""

        return self._sessions.clear()

    def end_session(self, session_id: str | None) -> None:
        if not isinstance(session_id, str) or not session_id:
            return
        self._sessions.delete(session_id)

    def _refuse_login(self, username: str, *, now: float) -> NoReturn:
        """Record one generic credential refusal and disclose nothing else."""

        self._sessions.record_failure(
            username,
            now=now,
            window=self.failure_window_seconds,
            limit=self.max_failures,
        )
        raise LocalAuthenticationError("username or password is incorrect")


def normalize_username(username: str) -> str:
    if not isinstance(username, str):
        raise ValueError("local username must be text")
    normalized = username.strip().casefold()
    if not _USERNAME.fullmatch(normalized):
        raise ValueError(
            "local username must contain 1-64 lowercase letters, numbers, dots, "
            "underscores, or hyphens"
        )
    return normalized


def hash_password(
    password: str,
    *,
    iterations: int = DEFAULT_PASSWORD_ITERATIONS,
) -> str:
    encoded = _password_bytes(password)
    if (
        isinstance(iterations, bool)
        or not isinstance(iterations, int)
        or not 1 <= iterations <= _MAX_PASSWORD_ITERATIONS
    ):
        raise ValueError("local password iteration count is invalid")
    salt = secrets.token_bytes(16)
    digest = pbkdf2_hmac("sha256", encoded, salt, iterations, dklen=32)
    return "$".join(
        (
            "pbkdf2-sha256",
            str(iterations),
            _encode(salt),
            _encode(digest),
        )
    )


def validate_password(password: str) -> None:
    """Validate the bounded local password policy without retaining a value."""

    _password_bytes(password)


def _credential_stamp(password_hash: str) -> str:
    """Digest a stored hash, so a change to it is recognisable without keeping it."""

    return sha256(password_hash.encode("utf-8")).hexdigest()


def _hash_iterations(encoded_hash: str) -> int:
    """Report the work factor a stored hash was produced with.

    An unreadable hash reports the maximum, so it is never mistaken for one
    that wants strengthening -- `verify_password` will refuse it anyway.
    """

    try:
        _algorithm, raw_iterations, _salt, _digest = encoded_hash.split("$")
        return int(raw_iterations)
    except (TypeError, ValueError):
        return _MAX_PASSWORD_ITERATIONS


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded_hash.split("$")
        iterations = int(raw_iterations)
        if algorithm != "pbkdf2-sha256" or not 1 <= iterations <= _MAX_PASSWORD_ITERATIONS:
            return False
        salt = _decode(raw_salt)
        expected = _decode(raw_digest)
        if len(salt) < 16 or len(expected) != 32:
            return False
        password_bytes = _password_bytes(password, enforce_minimum=False)
    except (TypeError, ValueError):
        return False
    actual = pbkdf2_hmac(
        "sha256",
        password_bytes,
        salt,
        iterations,
        dklen=len(expected),
    )
    return secrets.compare_digest(actual, expected)


def _password_bytes(password: str, *, enforce_minimum: bool = True) -> bytes:
    if not isinstance(password, str):
        raise ValueError("local password must be text")
    if enforce_minimum and len(password) < MIN_PASSWORD_CHARACTERS:
        raise ValueError(
            f"local password must contain at least {MIN_PASSWORD_CHARACTERS} characters"
        )
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"local password must not exceed {MAX_PASSWORD_BYTES} UTF-8 bytes"
        )
    return encoded


def _normalize_role(role: str) -> str:
    if not isinstance(role, str) or not role.strip():
        raise ValueError("local user roles must be non-empty strings")
    return role.strip()


def _encode(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(value + padding)


def _restrict_to_owner(path: Path) -> None:
    """Keep the identity store readable only by the account that owns it.

    Split by platform because the two mechanisms have nothing in common, and
    kept as separate functions so each can be exercised wherever the tests run
    -- the first version of this could only ever be tested on the branch the
    host happened to take, and the untested branch was the broken one.
    """

    if os.name == "nt":
        _restrict_with_icacls(path)
    else:
        _restrict_with_chmod(path)


def _restrict_with_chmod(path: Path) -> None:
    """Restrict on POSIX, tolerating a filesystem that cannot express it.

    A mounted share or a container volume may refuse the mode. That leaves the
    file less protected than intended, which is not a reason to refuse to run.
    """

    with suppress(OSError):
        os.chmod(path, 0o600)


def _restrict_with_icacls(path: Path) -> None:
    """Restrict on Windows, where `chmod` moves the read-only flag and no more.

    Windows is this project's documented primary platform, so leaving the store
    at "POSIX only" left it least protected exactly where it is most used.
    Inheritance is dropped so the parent directory cannot grant anyone else in,
    and the owning account is granted full control.

    Best effort, like its counterpart: a machine with an unusual account setup
    should still be able to create a store. SQLite writes journal side-files
    beside this one and those inherit the *directory*, which is why the
    directory is worth restricting too.
    """

    sid = _current_windows_sid()
    if sid is None:
        return
    with suppress(OSError, subprocess.SubprocessError):
        restricted = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if restricted.returncode != 0 or not _can_reopen(path):
            _restore_windows_inheritance(path)


def _current_windows_sid() -> str | None:
    """Return the SID represented by this process token, never an env hint."""

    with suppress(OSError, subprocess.SubprocessError):
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            match = _WINDOWS_SID.search(result.stdout or "")
            if match is not None:
                return match.group(0)
    return None


def _can_reopen(path: Path) -> bool:
    try:
        with path.open("r+b"):
            return True
    except OSError:
        return False


def _restore_windows_inheritance(path: Path) -> None:
    """Best-effort recovery if applying the restrictive ACL went wrong."""

    with suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["icacls", str(path), "/inheritance:e"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
