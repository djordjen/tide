"""Same-origin browser authentication over a configured OIDC provider."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import math
import secrets
from threading import Lock, RLock
import time
from typing import Any, Callable, Protocol
from urllib.parse import urlencode, urlsplit

import httpx

from tide.runtime import Principal


_MAX_PROVIDER_DOCUMENT_BYTES = 1024 * 1024
_MAX_TOKEN_RESPONSE_BYTES = 1024 * 1024
_MAX_AUTHORIZATION_CODE_CHARACTERS = 4096
_MAX_TOKEN_CHARACTERS = 64 * 1024
_DEFAULT_SCOPES = ("openid", "profile")


class BrowserAuthenticationError(ValueError):
    """The browser login or session could not be completed safely."""


class _TokenAuthenticator(Protocol):
    def authenticate(self, credential: str) -> Principal | None: ...


@dataclass(frozen=True, slots=True)
class BrowserLoginStart:
    authorization_url: str
    transaction_binding: str


@dataclass(frozen=True, slots=True)
class BrowserLoginResult:
    session_id: str
    return_to: str


@dataclass(frozen=True, slots=True)
class BrowserSessionAccess:
    principal: Principal
    csrf_token: str


@dataclass(frozen=True, slots=True)
class OidcBrowserProviderInfo:
    """Safe capability summary derived from verified provider metadata."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    response_types: tuple[str, ...]
    grant_types: tuple[str, ...]
    pkce_methods: tuple[str, ...]
    token_endpoint_auth_methods: tuple[str, ...]
    scopes: tuple[str, ...] | None
    warnings: tuple[str, ...]

    @property
    def refresh_token_advertised(self) -> bool:
        return "refresh_token" in self.grant_types

    def as_dict(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "response_types": list(self.response_types),
            "grant_types": list(self.grant_types),
            "pkce_methods": list(self.pkce_methods),
            "token_endpoint_auth_methods": list(
                self.token_endpoint_auth_methods
            ),
            "scopes": list(self.scopes) if self.scopes is not None else None,
            "refresh_token_advertised": self.refresh_token_advertised,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class _LoginTransaction:
    binding_digest: bytes
    code_verifier: str
    return_to: str
    expires_at: float


@dataclass(slots=True)
class _BrowserSession:
    access_token: str
    refresh_token: str | None
    access_expires_at: float
    absolute_expires_at: float
    principal_identifier: str
    csrf_token: str
    lock: Lock = field(default_factory=Lock)
    """Held across this session's provider calls, so they serialize alone."""


class OidcBrowserAuth:
    """Run an OIDC code/PKCE flow and retain tokens outside browser JavaScript.

    The built-in store is deliberately process-local: it keeps access and refresh
    tokens out of cookies and persistent application data. A process restart logs
    browser users out, and deployments must use one application process until a
    reviewed shared session-store adapter is introduced.
    """

    authentication_mode = "oidc"

    def __init__(
        self,
        *,
        authenticator: _TokenAuthenticator,
        authorization_endpoint: str,
        token_endpoint: str,
        client_id: str,
        redirect_uri: str,
        client_secret: str | None = None,
        scopes: Sequence[str] = _DEFAULT_SCOPES,
        transaction_lifetime_seconds: int = 300,
        session_lifetime_seconds: int = 8 * 60 * 60,
        refresh_leeway_seconds: int = 30,
        max_transactions: int = 1024,
        max_sessions: int = 4096,
        timeout: float = 5.0,
        http_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        provider_info: OidcBrowserProviderInfo | None = None,
    ) -> None:
        _validate_https_endpoint(
            authorization_endpoint,
            label="OIDC authorization endpoint",
        )
        _validate_https_endpoint(token_endpoint, label="OIDC token endpoint")
        secure_cookie = _validate_redirect_uri(redirect_uri)
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError("OIDC browser client ID must not be empty")
        if client_secret is not None and (
            not isinstance(client_secret, str) or not client_secret
        ):
            raise ValueError("OIDC browser client secret must not be empty")
        normalized_scopes = tuple(dict.fromkeys(scopes))
        if "openid" not in normalized_scopes:
            raise ValueError("OIDC browser scopes must include 'openid'")
        if any(
            not isinstance(scope, str)
            or not scope
            or any(character.isspace() for character in scope)
            for scope in normalized_scopes
        ):
            raise ValueError("OIDC browser scopes must be non-empty tokens")
        for label, value in (
            ("transaction lifetime", transaction_lifetime_seconds),
            ("session lifetime", session_lifetime_seconds),
            ("refresh leeway", refresh_leeway_seconds),
            ("maximum transactions", max_transactions),
            ("maximum sessions", max_sessions),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"OIDC browser {label} must be a positive integer")
        if refresh_leeway_seconds >= session_lifetime_seconds:
            raise ValueError(
                "OIDC browser refresh leeway must be shorter than session lifetime"
            )
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("OIDC browser timeout must be greater than zero")

        self.authenticator = authenticator
        self.authorization_endpoint = authorization_endpoint
        self.token_endpoint = token_endpoint
        self.client_id = client_id.strip()
        self.redirect_uri = redirect_uri
        self.client_secret = client_secret
        self.scopes = normalized_scopes
        self.transaction_lifetime_seconds = transaction_lifetime_seconds
        self.session_lifetime_seconds = session_lifetime_seconds
        self.refresh_leeway_seconds = refresh_leeway_seconds
        self.max_transactions = max_transactions
        self.max_sessions = max_sessions
        self.timeout = timeout
        self.provider_info = provider_info
        self.secure_cookie = secure_cookie
        self.transaction_cookie_name = (
            "__Host-tide_oidc_transaction"
            if secure_cookie
            else "tide_oidc_transaction"
        )
        self.session_cookie_name = (
            "__Host-tide_session" if secure_cookie else "tide_session"
        )
        self._http_client = http_client
        self._clock = clock
        self._transactions: OrderedDict[str, _LoginTransaction] = OrderedDict()
        self._sessions: OrderedDict[str, _BrowserSession] = OrderedDict()
        self._lock = RLock()

    @classmethod
    def from_discovery(
        cls,
        *,
        issuer: str,
        authenticator: _TokenAuthenticator,
        client_id: str,
        redirect_uri: str,
        client_secret: str | None = None,
        scopes: Sequence[str] = _DEFAULT_SCOPES,
        timeout: float = 5.0,
        http_client: httpx.Client | None = None,
        **configuration: Any,
    ) -> OidcBrowserAuth:
        """Discover the two browser endpoints from the exact configured issuer."""

        _validate_https_issuer(issuer)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("OIDC browser timeout must be greater than zero")
        requested_scopes = tuple(scopes)
        metadata = _load_discovery(
            issuer,
            timeout=timeout,
            http_client=http_client,
        )
        provider_info = _provider_info(
            metadata,
            issuer=issuer,
            confidential_client=client_secret is not None,
            requested_scopes=requested_scopes,
        )
        return cls(
            authenticator=authenticator,
            authorization_endpoint=provider_info.authorization_endpoint,
            token_endpoint=provider_info.token_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            client_secret=client_secret,
            scopes=requested_scopes,
            timeout=timeout,
            http_client=http_client,
            provider_info=provider_info,
            **configuration,
        )

    def begin_login(self, *, return_to: str = "/") -> BrowserLoginStart:
        safe_return_to = _safe_return_to(return_to)
        now = self._clock()
        state = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = urlsafe_b64encode(
            sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        transaction = _LoginTransaction(
            binding_digest=sha256(binding.encode("ascii")).digest(),
            code_verifier=verifier,
            return_to=safe_return_to,
            expires_at=now + self.transaction_lifetime_seconds,
        )
        with self._lock:
            self._prune_locked(now)
            self._transactions[state] = transaction
            while len(self._transactions) > self.max_transactions:
                self._transactions.popitem(last=False)
        authorization_query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in self.authorization_endpoint else "?"
        authorization_url = (
            f"{self.authorization_endpoint}{separator}{authorization_query}"
        )
        return BrowserLoginStart(authorization_url, binding)

    def complete_login(
        self,
        *,
        state: str,
        transaction_binding: str,
        code: str,
    ) -> BrowserLoginResult:
        if (
            not isinstance(state, str)
            or not state
            or not isinstance(transaction_binding, str)
            or not transaction_binding
            or not isinstance(code, str)
            or not code
            or len(code) > _MAX_AUTHORIZATION_CODE_CHARACTERS
        ):
            raise BrowserAuthenticationError("browser login response is invalid")
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            transaction = self._transactions.pop(state, None)
        if transaction is None or transaction.expires_at <= now:
            raise BrowserAuthenticationError("browser login transaction is invalid")
        binding_digest = sha256(transaction_binding.encode("utf-8")).digest()
        if not secrets.compare_digest(binding_digest, transaction.binding_digest):
            raise BrowserAuthenticationError("browser login transaction is invalid")

        tokens = self._exchange_tokens(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": transaction.code_verifier,
            }
        )
        access_token, refresh_token, expires_at = self._validated_tokens(
            tokens,
            now=now,
        )
        principal = self.authenticator.authenticate(access_token)
        if principal is None:
            raise BrowserAuthenticationError(
                "identity provider returned an invalid access token"
            )
        session_id = secrets.token_urlsafe(32)
        session = _BrowserSession(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=expires_at,
            absolute_expires_at=now + self.session_lifetime_seconds,
            principal_identifier=principal.identifier,
            csrf_token=secrets.token_urlsafe(32),
        )
        with self._lock:
            self._prune_locked(now)
            self._sessions[session_id] = session
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
        return BrowserLoginResult(session_id, transaction.return_to)

    def authenticate_session(self, session_id: str | None) -> BrowserSessionAccess | None:
        if not isinstance(session_id, str) or not session_id:
            return None
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if now >= session.absolute_expires_at:
                self._sessions.pop(session_id, None)
                return None
            self._sessions.move_to_end(session_id)

        # Everything below can reach the identity provider -- a refresh always
        # does, and verifying an access token may fetch signing keys -- so it
        # runs under this session's own lock and not the store's. Holding the
        # store-wide lock across a provider call made one slow response stall
        # every authenticated request in the process, including sessions
        # nowhere near needing a refresh.
        with session.lock:
            if now + self.refresh_leeway_seconds >= session.access_expires_at:
                if not self._refresh_session(session, now=now):
                    self._discard(session_id, session)
                    return None
            principal = self.authenticator.authenticate(session.access_token)
            if principal is None and self._refresh_session(session, now=now):
                principal = self.authenticator.authenticate(session.access_token)
            if principal is None or principal.identifier != session.principal_identifier:
                self._discard(session_id, session)
                return None
            csrf_token = session.csrf_token

        # A sign-out or an eviction may have landed while the provider was
        # answering. Asking again is what makes a logout win the race rather
        # than letting one in-flight request outlive it.
        with self._lock:
            if self._sessions.get(session_id) is not session:
                return None
        return BrowserSessionAccess(principal, csrf_token)

    def _discard(self, session_id: str, session: _BrowserSession) -> None:
        """Forget this session, unless the store already holds a different one."""

        with self._lock:
            if self._sessions.get(session_id) is session:
                self._sessions.pop(session_id, None)

    def end_session(self, session_id: str | None) -> None:
        if not isinstance(session_id, str) or not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def _refresh_session(self, session: _BrowserSession, *, now: float) -> bool:
        if not session.refresh_token:
            return False
        try:
            tokens = self._exchange_tokens(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": session.refresh_token,
                }
            )
            access_token, refresh_token, expires_at = self._validated_tokens(
                tokens,
                now=now,
                previous_refresh_token=session.refresh_token,
            )
        except BrowserAuthenticationError:
            return False
        principal = self.authenticator.authenticate(access_token)
        if principal is None or principal.identifier != session.principal_identifier:
            return False
        session.access_token = access_token
        session.refresh_token = refresh_token
        session.access_expires_at = min(expires_at, session.absolute_expires_at)
        return True

    def _exchange_tokens(self, payload: Mapping[str, str]) -> Mapping[str, Any]:
        form = {**payload, "client_id": self.client_id}
        authentication = (
            httpx.BasicAuth(self.client_id, self.client_secret)
            if self.client_secret is not None
            else None
        )
        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(
            timeout=self.timeout,
            follow_redirects=False,
        )
        try:
            try:
                response = client.post(
                    self.token_endpoint,
                    data=form,
                    auth=authentication,
                )
            except httpx.HTTPError as error:
                raise BrowserAuthenticationError(
                    "identity provider token exchange failed"
                ) from error
            if response.status_code != 200:
                raise BrowserAuthenticationError(
                    "identity provider token exchange failed"
                )
            if len(response.content) > _MAX_TOKEN_RESPONSE_BYTES:
                raise BrowserAuthenticationError(
                    "identity provider token response is too large"
                )
            try:
                tokens = response.json()
            except ValueError as error:
                raise BrowserAuthenticationError(
                    "identity provider token response is invalid"
                ) from error
        finally:
            if owns_client:
                client.close()
        if not isinstance(tokens, Mapping):
            raise BrowserAuthenticationError(
                "identity provider token response is invalid"
            )
        return tokens

    def _validated_tokens(
        self,
        tokens: Mapping[str, Any],
        *,
        now: float,
        previous_refresh_token: str | None = None,
    ) -> tuple[str, str | None, float]:
        access_token = tokens.get("access_token")
        token_type = tokens.get("token_type")
        if (
            not isinstance(access_token, str)
            or not access_token
            or len(access_token) > _MAX_TOKEN_CHARACTERS
            or not isinstance(token_type, str)
            or token_type.casefold() != "bearer"
        ):
            raise BrowserAuthenticationError(
                "identity provider token response is invalid"
            )
        refresh_token = tokens.get("refresh_token", previous_refresh_token)
        if refresh_token is not None and (
            not isinstance(refresh_token, str)
            or not refresh_token
            or len(refresh_token) > _MAX_TOKEN_CHARACTERS
        ):
            raise BrowserAuthenticationError(
                "identity provider token response is invalid"
            )
        expires_in = tokens.get("expires_in")
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or not math.isfinite(expires_in)
            or expires_in <= 0
        ):
            raise BrowserAuthenticationError(
                "identity provider token response has no valid expiry"
            )
        expires_at = now + float(expires_in)
        return access_token, refresh_token, expires_at

    def _prune_locked(self, now: float) -> None:
        expired_transactions = [
            key
            for key, transaction in self._transactions.items()
            if transaction.expires_at <= now
        ]
        for key in expired_transactions:
            self._transactions.pop(key, None)
        expired_sessions = [
            key
            for key, session in self._sessions.items()
            if session.absolute_expires_at <= now
        ]
        for key in expired_sessions:
            self._sessions.pop(key, None)


def _load_discovery(
    issuer: str,
    *,
    timeout: float,
    http_client: httpx.Client | None,
) -> Mapping[str, Any]:
    discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        try:
            response = client.get(discovery_url)
        except httpx.HTTPError as error:
            raise BrowserAuthenticationError(
                "could not retrieve OIDC discovery metadata"
            ) from error
        if response.status_code != 200:
            raise BrowserAuthenticationError(
                "could not retrieve OIDC discovery metadata"
            )
        if len(response.content) > _MAX_PROVIDER_DOCUMENT_BYTES:
            raise BrowserAuthenticationError("OIDC discovery metadata is too large")
        try:
            metadata = response.json()
        except ValueError as error:
            raise BrowserAuthenticationError(
                "OIDC discovery metadata is not valid JSON"
            ) from error
    finally:
        if owns_client:
            client.close()
    if not isinstance(metadata, Mapping):
        raise BrowserAuthenticationError("OIDC discovery metadata must be an object")
    if metadata.get("issuer") != issuer:
        raise BrowserAuthenticationError(
            "OIDC discovery issuer does not exactly match the configured issuer"
        )
    return metadata


def _provider_info(
    metadata: Mapping[str, Any],
    *,
    issuer: str,
    confidential_client: bool,
    requested_scopes: Sequence[str],
) -> OidcBrowserProviderInfo:
    authorization_endpoint = metadata.get("authorization_endpoint")
    token_endpoint = metadata.get("token_endpoint")
    if not isinstance(authorization_endpoint, str):
        raise BrowserAuthenticationError(
            "OIDC discovery metadata has no authorization endpoint"
        )
    if not isinstance(token_endpoint, str):
        raise BrowserAuthenticationError(
            "OIDC discovery metadata has no token endpoint"
        )
    _validate_https_endpoint(
        authorization_endpoint,
        label="OIDC authorization endpoint",
    )
    _validate_https_endpoint(token_endpoint, label="OIDC token endpoint")

    response_types = _metadata_string_array(
        metadata,
        "response_types_supported",
        required=True,
    )
    if "code" not in response_types:
        raise BrowserAuthenticationError(
            "OIDC provider does not advertise the authorization-code response type"
        )
    grant_types = _metadata_string_array(
        metadata,
        "grant_types_supported",
        default=("authorization_code", "implicit"),
    )
    if "authorization_code" not in grant_types:
        raise BrowserAuthenticationError(
            "OIDC provider does not advertise the authorization-code grant"
        )
    advertised_pkce_methods = _optional_metadata_string_array(
        metadata,
        "code_challenge_methods_supported",
    )
    pkce_methods = advertised_pkce_methods or ()
    if advertised_pkce_methods is not None and "S256" not in pkce_methods:
        raise BrowserAuthenticationError(
            "OIDC provider does not advertise PKCE S256"
        )
    token_auth_methods = _metadata_string_array(
        metadata,
        "token_endpoint_auth_methods_supported",
        default=("client_secret_basic",),
    )
    required_auth_method = (
        "client_secret_basic" if confidential_client else "none"
    )
    if required_auth_method not in token_auth_methods:
        client_kind = "confidential" if confidential_client else "public"
        raise BrowserAuthenticationError(
            "OIDC provider does not advertise "
            f"{required_auth_method!r} token authentication required by the "
            f"configured {client_kind} Web client"
        )

    scopes = _optional_metadata_string_array(metadata, "scopes_supported")
    if scopes is not None and "openid" not in scopes:
        raise BrowserAuthenticationError(
            "OIDC provider metadata does not advertise the required 'openid' scope"
        )
    warnings: list[str] = []
    if advertised_pkce_methods is None:
        warnings.append(
            "provider metadata does not advertise PKCE methods; TIDE will send "
            "S256, but interactive acceptance must confirm provider enforcement"
        )
    if scopes is not None:
        unadvertised_scopes = sorted(set(requested_scopes) - set(scopes))
        if unadvertised_scopes:
            warnings.append(
                "provider metadata does not advertise requested scope(s): "
                + ", ".join(unadvertised_scopes)
            )
    if (
        "offline_access" in requested_scopes
        and "refresh_token" not in grant_types
    ):
        warnings.append(
            "provider metadata does not advertise the refresh_token grant; "
            "interactive acceptance must confirm renewable sessions"
        )
    return OidcBrowserProviderInfo(
        issuer=issuer,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        response_types=response_types,
        grant_types=grant_types,
        pkce_methods=pkce_methods,
        token_endpoint_auth_methods=token_auth_methods,
        scopes=scopes,
        warnings=tuple(warnings),
    )


def _metadata_string_array(
    metadata: Mapping[str, Any],
    name: str,
    *,
    required: bool = False,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if name not in metadata:
        if required:
            raise BrowserAuthenticationError(
                f"OIDC discovery metadata has no {name!r} array"
            )
        return default
    value = metadata[name]
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise BrowserAuthenticationError(
            f"OIDC discovery metadata {name!r} must be a non-empty string array"
        )
    return tuple(dict.fromkeys(value))


def _optional_metadata_string_array(
    metadata: Mapping[str, Any],
    name: str,
) -> tuple[str, ...] | None:
    if name not in metadata:
        return None
    return _metadata_string_array(metadata, name)


def _safe_return_to(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise BrowserAuthenticationError("browser return path is invalid")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise BrowserAuthenticationError("browser return path is invalid") from error
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
    ):
        raise BrowserAuthenticationError("browser return path is invalid")
    return value


def _validate_https_issuer(issuer: str) -> None:
    if not isinstance(issuer, str):
        raise ValueError("OIDC issuer is not a valid URL")
    parsed = urlsplit(issuer)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "OIDC issuer must be an absolute HTTPS URL without credentials, "
            "query, or fragment"
        )


def _validate_https_endpoint(value: str, *, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} is not a valid URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(
            f"{label} must be an absolute HTTPS URL without credentials or fragment"
        )


def _validate_redirect_uri(value: str) -> bool:
    if not isinstance(value, str):
        raise ValueError("OIDC browser redirect URI is not a valid URL")
    parsed = urlsplit(value)
    secure = parsed.scheme.lower() == "https"
    loopback_http = parsed.scheme.lower() == "http" and parsed.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    if (
        not (secure or loopback_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "OIDC browser redirect URI must use HTTPS, except for an HTTP "
            "loopback test URI, and contain no credentials, query, or fragment"
        )
    return secure
