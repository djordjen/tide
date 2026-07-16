"""Production bearer authentication adapters for the TIDE application server."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import jwt

from tide.runtime import Principal


SUPPORTED_ASYMMETRIC_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)
DEFAULT_TOKEN_TYPES = ("at+jwt", "JWT")
_MAX_DISCOVERY_DOCUMENT_BYTES = 1024 * 1024
_MISSING = object()


class OidcDiscoveryError(ValueError):
    """The configured OpenID Provider did not publish usable metadata."""


class _SigningKeyClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class OidcJwtAuthenticator:
    """Validate OIDC-issued access tokens and map external roles to TIDE roles."""

    issuer: str
    audience: str
    role_claim: str
    role_map: Mapping[str, str]
    algorithms: tuple[str, ...]
    token_types: tuple[str, ...]
    leeway: float
    _jwks_client: _SigningKeyClient

    authentication_type = "oidc-jwt"
    production = True

    def __post_init__(self) -> None:
        _validate_https_url(self.issuer, label="OIDC issuer", allow_query=False)
        if not isinstance(self.audience, str) or not self.audience.strip():
            raise ValueError("OIDC audience must not be empty")
        if not isinstance(self.role_claim, str) or not self.role_claim.strip() or any(
            not part for part in self.role_claim.split(".")
        ):
            raise ValueError("OIDC role claim must be a dot-separated claim path")
        if not isinstance(self.leeway, (int, float)) or not math.isfinite(self.leeway):
            raise ValueError("OIDC clock leeway must be a finite number")
        if self.leeway < 0:
            raise ValueError("OIDC clock leeway must not be negative")
        object.__setattr__(self, "algorithms", tuple(self.algorithms))
        object.__setattr__(self, "token_types", tuple(self.token_types))
        if not self.algorithms:
            raise ValueError("at least one OIDC signing algorithm is required")
        if any(not isinstance(item, str) for item in self.algorithms):
            raise ValueError("OIDC signing algorithms must be strings")
        unsupported = set(self.algorithms) - SUPPORTED_ASYMMETRIC_ALGORITHMS
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"unsupported OIDC signing algorithm(s): {names}")
        if not self.token_types or any(
            not isinstance(item, str) or not item.strip() for item in self.token_types
        ):
            raise ValueError("at least one non-empty OIDC token type is required")
        normalized_map = dict(self.role_map)
        if any(
            not isinstance(source, str)
            or not isinstance(target, str)
            or not source.strip()
            or not target.strip()
            for source, target in normalized_map.items()
        ):
            raise ValueError("OIDC role mappings must use non-empty role names")
        object.__setattr__(self, "role_map", MappingProxyType(normalized_map))

    @classmethod
    def from_discovery(
        cls,
        *,
        issuer: str,
        audience: str,
        role_claim: str = "roles",
        role_map: Mapping[str, str] | None = None,
        algorithms: Sequence[str] = ("RS256",),
        token_types: Sequence[str] = DEFAULT_TOKEN_TYPES,
        leeway: float = 30.0,
        timeout: float = 5.0,
        http_client: httpx.Client | None = None,
    ) -> OidcJwtAuthenticator:
        """Resolve and verify OIDC discovery metadata before accepting traffic."""

        _validate_https_url(issuer, label="OIDC issuer", allow_query=False)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(
                "OIDC discovery timeout must be a finite number greater than zero"
            )
        discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        owns_client = http_client is None
        client = http_client or httpx.Client(
            timeout=timeout,
            follow_redirects=False,
        )
        try:
            try:
                response = client.get(discovery_url)
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise OidcDiscoveryError(
                    "could not retrieve OIDC discovery metadata"
                ) from error
            if len(response.content) > _MAX_DISCOVERY_DOCUMENT_BYTES:
                raise OidcDiscoveryError("OIDC discovery metadata is too large")
            try:
                metadata = response.json()
            except ValueError as error:
                raise OidcDiscoveryError(
                    "OIDC discovery metadata is not valid JSON"
                ) from error
        finally:
            if owns_client:
                client.close()

        if not isinstance(metadata, Mapping):
            raise OidcDiscoveryError("OIDC discovery metadata must be an object")
        discovered_issuer = metadata.get("issuer")
        if discovered_issuer != issuer:
            raise OidcDiscoveryError(
                "OIDC discovery issuer does not exactly match the configured issuer"
            )
        jwks_uri = metadata.get("jwks_uri")
        if not isinstance(jwks_uri, str):
            raise OidcDiscoveryError("OIDC discovery metadata has no JWKS URI")
        try:
            _validate_https_url(jwks_uri, label="OIDC JWKS URI", allow_query=True)
        except ValueError as error:
            raise OidcDiscoveryError(str(error)) from error

        return cls(
            issuer=issuer,
            audience=audience,
            role_claim=role_claim,
            role_map=role_map or {},
            algorithms=tuple(algorithms),
            token_types=tuple(token_types),
            leeway=leeway,
            _jwks_client=jwt.PyJWKClient(
                jwks_uri,
                cache_keys=False,
                cache_jwk_set=True,
                lifespan=300,
                timeout=timeout,
            ),
        )

    def authenticate(self, credential: str) -> Principal | None:
        """Return a server-controlled principal, failing closed for invalid tokens."""

        try:
            header = jwt.get_unverified_header(credential)
        except (jwt.PyJWTError, ValueError, TypeError):
            return None
        if header.get("alg") not in self.algorithms:
            return None
        if not isinstance(header.get("kid"), str) or not header["kid"].strip():
            return None
        if header.get("typ") not in self.token_types:
            return None

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(credential)
        except Exception:
            # Remote key rotation or availability must fail authentication, not open.
            return None
        key = getattr(signing_key, "key", signing_key)
        try:
            claims = jwt.decode(
                credential,
                key,
                algorithms=self.algorithms,
                issuer=self.issuer,
                audience=self.audience,
                leeway=self.leeway,
                options={"require": ["iss", "aud", "exp", "sub"]},
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            return None

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            return None
        external_roles = _claim_at_path(claims, self.role_claim)
        if external_roles is _MISSING:
            external_roles = ()
        if not isinstance(external_roles, (list, tuple)) or any(
            not isinstance(role, str) or not role.strip() for role in external_roles
        ):
            return None
        roles = frozenset(
            self.role_map[role]
            for role in external_roles
            if role in self.role_map
        )
        return Principal(f"oidc:{subject}", roles=roles)


def _claim_at_path(claims: Mapping[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _validate_https_url(url: str, *, label: str, allow_query: bool) -> None:
    if not isinstance(url, str):
        raise ValueError(f"{label} is not a valid URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{label} is not a valid URL") from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (parsed.query and not allow_query)
        or port is None and parsed.netloc.endswith(":")
    ):
        raise ValueError(
            f"{label} must be an absolute HTTPS URL without credentials or fragments"
        )
