"""Framework-owned username/password identities and opaque local sessions."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import OrderedDict, deque
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import pbkdf2_hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
from threading import RLock
import time
from typing import Callable, Iterator

from tide.api.browser_auth import BrowserSessionAccess
from tide.runtime import Principal


DEFAULT_PASSWORD_ITERATIONS = 600_000
MAX_PASSWORD_BYTES = 1024
MIN_PASSWORD_CHARACTERS = 12
_MAX_PASSWORD_ITERATIONS = 2_000_000
_SCHEMA_VERSION = "1"
_USERNAME = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?")


class LocalAuthenticationError(ValueError):
    """A local identity operation could not be completed safely."""


@dataclass(frozen=True, slots=True)
class LocalUser:
    username: str
    display_name: str
    password_hash: str
    enabled: bool
    roles: frozenset[str]


@dataclass(frozen=True, slots=True)
class LocalLoginResult:
    session_id: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class _LocalSession:
    principal: Principal
    csrf_token: str
    expires_at: float


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
        if os.name != "nt":
            os.chmod(self.path, 0o600)

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
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
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
    """Authenticate local users and retain bounded, process-local sessions."""

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
        max_sessions: int = 4096,
        max_failures: int = 5,
        max_failure_subjects: int = 4096,
        failure_window_seconds: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        for label, value in (
            ("session lifetime", session_lifetime_seconds),
            ("maximum sessions", max_sessions),
            ("maximum failures", max_failures),
            ("maximum failure subjects", max_failure_subjects),
            ("failure window", failure_window_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"local authentication {label} must be positive")
        store.validate()
        self.store = store
        self.allowed_roles = frozenset(allowed_roles)
        self.secure_cookie = secure_cookie
        self.session_lifetime_seconds = session_lifetime_seconds
        self.max_sessions = max_sessions
        self.max_failures = max_failures
        self.max_failure_subjects = max_failure_subjects
        self.failure_window_seconds = failure_window_seconds
        self.session_cookie_name = (
            "__Host-tide_session" if secure_cookie else "tide_session"
        )
        self._clock = clock
        self._sessions: OrderedDict[str, _LocalSession] = OrderedDict()
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = RLock()
        self._dummy_hash = hash_password(
            secrets.token_urlsafe(24),
            iterations=store.password_iterations,
        )

    def login(self, *, username: str, password: str) -> LocalLoginResult:
        now = self._clock()
        try:
            normalized_username = normalize_username(username)
            username_valid = True
        except ValueError:
            normalized_username = "invalid"
            username_valid = False
        with self._lock:
            self._prune_locked(now)
            failures = self._failure_bucket_locked(normalized_username, now=now)
            throttled = len(failures) >= self.max_failures

        user = self.store.get_user(normalized_username)
        password_hash = user.password_hash if user is not None else self._dummy_hash
        password_matches = verify_password(password, password_hash)
        roles = (
            user.roles.intersection(self.allowed_roles)
            if user is not None
            else frozenset()
        )
        if (
            throttled
            or not username_valid
            or user is None
            or not user.enabled
            or not password_matches
            or not roles
        ):
            with self._lock:
                failures = self._failure_bucket_locked(
                    normalized_username,
                    now=now,
                )
                if len(failures) < self.max_failures:
                    failures.append(now)
            raise LocalAuthenticationError("username or password is incorrect")

        principal = Principal(
            f"local:{user.username}",
            roles=frozenset(roles),
        )
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with self._lock:
            self._failures.pop(normalized_username, None)
            self._prune_locked(now)
            self._sessions[session_id] = _LocalSession(
                principal=principal,
                csrf_token=csrf_token,
                expires_at=now + self.session_lifetime_seconds,
            )
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
        return LocalLoginResult(session_id, csrf_token)

    def authenticate(self, credential: str) -> Principal | None:
        access = self.authenticate_session(credential)
        return access.principal if access is not None else None

    def authenticate_session(self, session_id: str | None) -> BrowserSessionAccess | None:
        if not isinstance(session_id, str) or not session_id:
            return None
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            session = self._sessions.get(session_id)
            if session is None:
                return None
            self._sessions.move_to_end(session_id)
            return BrowserSessionAccess(session.principal, session.csrf_token)

    def end_session(self, session_id: str | None) -> None:
        if not isinstance(session_id, str) or not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def _prune_locked(self, now: float) -> None:
        for key in [
            key
            for key, session in self._sessions.items()
            if session.expires_at <= now
        ]:
            self._sessions.pop(key, None)
        for username in list(self._failures):
            failures = self._failures[username]
            self._prune_failures(failures, now=now)
            if not failures:
                self._failures.pop(username, None)

    def _prune_failures(self, failures: deque[float], *, now: float) -> None:
        boundary = now - self.failure_window_seconds
        while failures and failures[0] <= boundary:
            failures.popleft()

    def _failure_bucket_locked(
        self,
        username: str,
        *,
        now: float,
    ) -> deque[float]:
        failures = self._failures.setdefault(username, deque())
        self._prune_failures(failures, now=now)
        self._failures.move_to_end(username)
        while len(self._failures) > self.max_failure_subjects:
            self._failures.popitem(last=False)
        return failures


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


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
