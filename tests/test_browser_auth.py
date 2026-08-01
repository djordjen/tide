from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tide.api.browser_auth import BrowserAuthenticationError, OidcBrowserAuth
from tide.runtime import Principal


ISSUER = "https://identity.example.test/tenant"


class _Authenticator:
    def authenticate(self, credential: str) -> Principal | None:
        if credential in {"access-one", "access-two"}:
            return Principal(
                "oidc:user-123",
                roles=frozenset({"sales_clerk"}),
            )
        return None


def test_browser_auth_code_pkce_session_and_refresh() -> None:
    now = [1000.0]
    requests: list[tuple[str, dict[str, list[str]]]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize?ui=modern",
                    "token_endpoint": f"{ISSUER}/token",
                    "response_types_supported": ["code"],
                    "grant_types_supported": [
                        "authorization_code",
                        "refresh_token",
                    ],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": [
                        "client_secret_basic"
                    ],
                    "scopes_supported": [
                        "openid",
                        "profile",
                        "offline_access",
                    ],
                },
            )
        form = parse_qs(request.content.decode("ascii"))
        requests.append((request.headers.get("authorization", ""), form))
        if form["grant_type"] == ["authorization_code"]:
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": "access-one",
                    "refresh_token": "refresh-one",
                    "expires_in": 60,
                },
            )
        return httpx.Response(
            200,
            json={
                "token_type": "bearer",
                "access_token": "access-two",
                "refresh_token": "refresh-two",
                "expires_in": 120,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        browser = OidcBrowserAuth.from_discovery(
            issuer=ISSUER,
            authenticator=_Authenticator(),
            client_id="tide-web",
            client_secret="provider-secret",
            redirect_uri="https://tide.example.test/api/v1/_tide/auth/callback",
            scopes=("openid", "profile", "offline_access"),
            http_client=client,
            clock=lambda: now[0],
        )
        assert browser.provider_info is not None
        assert browser.provider_info.refresh_token_advertised is True
        assert browser.provider_info.warnings == ()
        started = browser.begin_login(return_to="/?view=sales")
        authorization = urlsplit(started.authorization_url)
        query = parse_qs(authorization.query)
        assert authorization.path.endswith("/authorize")
        assert query["ui"] == ["modern"]
        assert query["response_type"] == ["code"]
        assert query["client_id"] == ["tide-web"]
        assert query["scope"] == ["openid profile offline_access"]
        assert query["code_challenge_method"] == ["S256"]

        result = browser.complete_login(
            state=query["state"][0],
            transaction_binding=started.transaction_binding,
            code="provider-code",
        )
        assert result.return_to == "/?view=sales"
        access = browser.authenticate_session(result.session_id)
        assert access is not None
        assert access.principal.identifier == "oidc:user-123"
        assert access.principal.roles == frozenset({"sales_clerk"})
        assert access.csrf_token

        code_form = requests[0][1]
        verifier = code_form["code_verifier"][0]
        expected_challenge = urlsafe_b64encode(
            sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert query["code_challenge"] == [expected_challenge]
        assert code_form["code"] == ["provider-code"]
        assert requests[0][0].startswith("Basic ")

        now[0] += 45
        refreshed = browser.authenticate_session(result.session_id)
        assert refreshed is not None
        assert requests[1][1]["grant_type"] == ["refresh_token"]
        assert requests[1][1]["refresh_token"] == ["refresh-one"]

        browser.end_session(result.session_id)
        assert browser.authenticate_session(result.session_id) is None


def test_browser_auth_transaction_is_bound_one_time_and_rejects_open_redirect() -> None:
    browser = OidcBrowserAuth(
        authenticator=_Authenticator(),
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        client_id="tide-web",
        redirect_uri="http://127.0.0.1:8000/api/v1/_tide/auth/callback",
    )
    assert browser.secure_cookie is False
    assert browser.session_cookie_name == "tide_session"

    with pytest.raises(BrowserAuthenticationError, match="return path"):
        browser.begin_login(return_to="https://attacker.example.test/")

    started = browser.begin_login()
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    with pytest.raises(BrowserAuthenticationError, match="transaction"):
        browser.complete_login(
            state=state,
            transaction_binding="different-browser",
            code="provider-code",
        )
    with pytest.raises(BrowserAuthenticationError, match="transaction"):
        browser.complete_login(
            state=state,
            transaction_binding=started.transaction_binding,
            code="provider-code",
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "issuer": "https://unexpected.example.test",
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
        },
        {
            "issuer": ISSUER,
            "authorization_endpoint": "http://identity.example.test/authorize",
            "token_endpoint": f"{ISSUER}/token",
        },
    ],
)
def test_browser_auth_discovery_fails_closed(metadata: dict[str, str]) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=metadata)
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises((BrowserAuthenticationError, ValueError)):
            OidcBrowserAuth.from_discovery(
                issuer=ISSUER,
                authenticator=_Authenticator(),
                client_id="tide-web",
                redirect_uri="https://tide.example.test/api/v1/_tide/auth/callback",
                http_client=client,
            )


@pytest.mark.parametrize(
    ("metadata_change", "message"),
    [
        ({"response_types_supported": ["id_token"]}, "authorization-code response"),
        ({"code_challenge_methods_supported": ["plain"]}, "PKCE S256"),
        (
            {"token_endpoint_auth_methods_supported": ["client_secret_post"]},
            "client_secret_basic",
        ),
    ],
)
def test_browser_auth_rejects_incompatible_provider_capabilities(
    metadata_change: dict[str, list[str]],
    message: str,
) -> None:
    metadata: dict[str, object] = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
    }
    metadata.update(metadata_change)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=metadata)
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(BrowserAuthenticationError, match=message):
            OidcBrowserAuth.from_discovery(
                issuer=ISSUER,
                authenticator=_Authenticator(),
                client_id="tide-web",
                client_secret="provider-secret",
                redirect_uri=(
                    "https://tide.example.test/api/v1/_tide/auth/callback"
                ),
                http_client=client,
            )


def test_browser_auth_reports_provider_warnings_for_public_client() -> None:
    metadata = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "response_types_supported": ["code"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["openid", "profile"],
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=metadata)
    )
    with httpx.Client(transport=transport) as client:
        browser = OidcBrowserAuth.from_discovery(
            issuer=ISSUER,
            authenticator=_Authenticator(),
            client_id="tide-web",
            redirect_uri="https://tide.example.test/api/v1/_tide/auth/callback",
            scopes=("openid", "profile", "offline_access"),
            http_client=client,
        )

    assert browser.provider_info is not None
    assert browser.provider_info.token_endpoint_auth_methods == ("none",)
    assert browser.provider_info.warnings == (
        "provider metadata does not advertise PKCE methods; TIDE will send "
        "S256, but interactive acceptance must confirm provider enforcement",
        "provider metadata does not advertise requested scope(s): offline_access",
        "provider metadata does not advertise the refresh_token grant; "
        "interactive acceptance must confirm renewable sessions",
    )
