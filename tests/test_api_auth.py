from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from tide.api.auth import OidcDiscoveryError, OidcJwtAuthenticator
from tide.runtime import Principal


ISSUER = "https://identity.example.test/tenant"
AUDIENCE = "tide-api"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()


@dataclass(frozen=True)
class _SigningKey:
    key: Any


class _StaticJwksClient:
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey(PUBLIC_KEY)


def test_oidc_authenticator_validates_token_and_maps_only_configured_roles() -> None:
    authenticator = _authenticator()

    principal = authenticator.authenticate(
        _token(roles=["external-sales", "unmapped-administrator"])
    )

    assert principal == Principal(
        "oidc:user-123",
        roles=frozenset({"sales_clerk"}),
    )
    assert authenticator.authentication_type == "oidc-jwt"
    assert authenticator.production is True


@pytest.mark.parametrize(
    ("overrides", "header_overrides"),
    [
        ({"iss": "https://other.example.test"}, {}),
        ({"aud": "another-api"}, {}),
        ({"exp": int(time.time()) - 120}, {}),
        ({"sub": ""}, {}),
        ({"roles": "external-sales"}, {}),
        ({}, {"typ": "id+jwt"}),
        ({}, {"kid": ""}),
    ],
)
def test_oidc_authenticator_rejects_invalid_headers_and_claims(
    overrides: dict[str, Any],
    header_overrides: dict[str, Any],
) -> None:
    assert _authenticator().authenticate(_token(overrides, header_overrides)) is None


def test_oidc_authenticator_rejects_unconfigured_signing_algorithm() -> None:
    token = jwt.encode(
        _claims(),
        "not-a-production-secret-with-at-least-32-bytes",
        algorithm="HS256",
        headers={"kid": "test-key", "typ": "at+jwt"},
    )

    assert _authenticator().authenticate(token) is None


def test_oidc_authenticator_accepts_missing_role_claim_without_granting_roles() -> None:
    claims = _claims()
    del claims["roles"]

    assert _authenticator().authenticate(_token(claims=claims)) == Principal(
        "oidc:user-123"
    )


def test_oidc_discovery_requires_exact_issuer_and_https_jwks() -> None:
    requested: list[str] = []

    def discovery(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "issuer": ISSUER,
                "jwks_uri": "https://identity.example.test/tenant/keys",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(discovery)) as client:
        authenticator = OidcJwtAuthenticator.from_discovery(
            issuer=ISSUER,
            audience=AUDIENCE,
            role_map={"external-sales": "sales_clerk"},
            http_client=client,
        )

    assert authenticator.issuer == ISSUER
    assert requested == [f"{ISSUER}/.well-known/openid-configuration"]


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "issuer": "https://unexpected.example.test",
            "jwks_uri": "https://identity.example.test/keys",
        },
        {"issuer": ISSUER, "jwks_uri": "http://identity.example.test/keys"},
    ],
)
def test_oidc_discovery_rejects_untrusted_metadata(metadata: dict[str, str]) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=metadata))

    with httpx.Client(transport=transport) as client:
        with pytest.raises(OidcDiscoveryError):
            OidcJwtAuthenticator.from_discovery(
                issuer=ISSUER,
                audience=AUDIENCE,
                http_client=client,
            )


def test_oidc_discovery_rejects_insecure_configured_issuer() -> None:
    with pytest.raises(ValueError, match="absolute HTTPS"):
        OidcJwtAuthenticator.from_discovery(
            issuer="http://identity.example.test",
            audience=AUDIENCE,
        )


@pytest.mark.parametrize("leeway", [math.nan, math.inf, -1.0])
def test_oidc_authenticator_rejects_unsafe_clock_leeway(leeway: float) -> None:
    with pytest.raises(ValueError, match="clock leeway"):
        OidcJwtAuthenticator(
            issuer=ISSUER,
            audience=AUDIENCE,
            role_claim="roles",
            role_map={},
            algorithms=("RS256",),
            token_types=("at+jwt",),
            leeway=leeway,
            _jwks_client=_StaticJwksClient(),
        )


def _authenticator() -> OidcJwtAuthenticator:
    return OidcJwtAuthenticator(
        issuer=ISSUER,
        audience=AUDIENCE,
        role_claim="roles",
        role_map={"external-sales": "sales_clerk"},
        algorithms=("RS256",),
        token_types=("at+jwt", "JWT"),
        leeway=30,
        _jwks_client=_StaticJwksClient(),
    )


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "iat": now,
        "exp": now + 300,
        "roles": ["external-sales"],
    }
    claims.update(overrides)
    return claims


def _token(
    overrides: dict[str, Any] | None = None,
    header_overrides: dict[str, Any] | None = None,
    *,
    claims: dict[str, Any] | None = None,
    roles: list[str] | None = None,
) -> str:
    payload = dict(claims or _claims())
    payload.update(overrides or {})
    if roles is not None:
        payload["roles"] = roles
    headers = {"kid": "test-key", "typ": "at+jwt"}
    headers.update(header_overrides or {})
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256", headers=headers)
