"""What a TLS-terminating reverse proxy in front of `tide serve` is allowed to be.

TIDE gained shared browser sessions so several processes could stand behind one
address. It had nowhere to stand: a non-loopback bind demanded a certificate
uvicorn would read itself, which is exactly what a deployment does not have when
the proxy terminates HTTPS, and the only arrangement that started -- bind
loopback, proxy on the same host -- decided `secure_cookie` from that absent
certificate and handed the browser a session cookie without `Secure` on a site
whose address bar says `https`.

The fix is not to relax the TLS check. It is to let a deployment *say* what is
in front of it, and then to make every decision that was reading the bind
address read that declaration instead -- because `is_loopback` was never
answering "can a stranger reach this", it was only ever answering "is the socket
local", and a proxy is precisely the thing that separates the two.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import uvicorn

from tide.api.local_auth import LocalUserStore
from tide.cli import main

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.fixture(name="launched", autouse=True)
def _launched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run `tide serve` up to the point uvicorn would take over.

    Autouse, deliberately: half of this file asserts that startup is *refused*,
    and a refusal test has no use for the capture -- until the refusal it is
    testing is broken, at which point `main()` sails past every check and calls
    the real `uvicorn.run`, which serves port 8000 forever. A broken guard must
    turn a test red, not hang the suite; patching in every test is what makes
    the worst case a failure instead of a stuck pytest.
    """

    captured: dict[str, Any] = {}

    def fake_run(app: Any, **configuration: Any) -> None:
        captured["app"] = app
        captured["configuration"] = configuration

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return captured


@pytest.fixture(name="identity_store")
def _identity_store(tmp_path: Path) -> Path:
    store = LocalUserStore(
        tmp_path / "local-auth.sqlite3",
        application="TIDE Invoicing",
        password_iterations=1_000,
    )
    store.initialize()
    store.create_user("alice", "correct horse battery staple", roles=("sales_clerk",))
    return store.path


def _serve(*arguments: str, identity_store: Path | None = None) -> list[str]:
    command = ["serve", str(INVOICING), "--demo", *arguments]
    if identity_store is not None:
        command += ["--auth", "local", "--local-auth-store", str(identity_store)]
    return command


# --- the cookie ------------------------------------------------------------


def test_a_proxy_that_terminates_tls_makes_the_session_cookie_secure(
    launched: dict[str, Any],
    identity_store: Path,
) -> None:
    """The defect this whole option exists for.

    `secure_cookie` was `certfile is not None`. Behind a proxy there is no
    certfile and the scheme is still `https`, so the browser was handed a
    session cookie it would happily send over a plaintext downgrade.
    """

    assert main(_serve("--behind-tls-proxy", identity_store=identity_store)) == 0

    browser_auth = launched["app"].state.tide.browser_auth
    assert browser_auth.secure_cookie is True
    assert browser_auth.session_cookie_name == "__Host-tide_session"


def test_a_server_terminating_its_own_tls_is_unchanged(
    launched: dict[str, Any],
    identity_store: Path,
    tmp_path: Path,
) -> None:
    """A control: the certificate path must keep deciding what it decided."""

    certfile = tmp_path / "server-cert.pem"
    keyfile = tmp_path / "server-key.pem"
    certfile.write_text("test certificate", encoding="utf-8")
    keyfile.write_text("test key", encoding="utf-8")

    assert (
        main(
            _serve(
                "--ssl-certfile",
                str(certfile),
                "--ssl-keyfile",
                str(keyfile),
                identity_store=identity_store,
            )
        )
        == 0
    )

    assert launched["app"].state.tide.browser_auth.secure_cookie is True


def test_a_plain_server_still_issues_a_plain_cookie(
    launched: dict[str, Any],
    identity_store: Path,
) -> None:
    """The other control: nothing about the default may move."""

    assert main(_serve(identity_store=identity_store)) == 0

    browser_auth = launched["app"].state.tide.browser_auth
    assert browser_auth.secure_cookie is False
    assert browser_auth.session_cookie_name == "tide_session"


# --- the bind --------------------------------------------------------------


def test_a_proxied_server_may_bind_a_routable_interface_without_a_certificate(
    launched: dict[str, Any],
    identity_store: Path,
) -> None:
    """The shape the shared session store was built for, and could not start in."""

    assert (
        main(
            _serve(
                "--host",
                "0.0.0.0",
                "--behind-tls-proxy",
                "--forwarded-allow-ips",
                "10.0.0.7",
                identity_store=identity_store,
            )
        )
        == 0
    )

    assert launched["configuration"]["host"] == "0.0.0.0"


def test_a_routable_bind_without_tls_or_a_proxy_is_still_refused(
    capsys: pytest.CaptureFixture[str],
    identity_store: Path,
) -> None:
    """The check is redirected, not removed."""

    assert main(_serve("--host", "0.0.0.0", identity_store=identity_store)) == 1
    assert capsys.readouterr().err == (
        "API startup failed: non-loopback serving requires --ssl-certfile and "
        "--ssl-keyfile, or --behind-tls-proxy\n"
    )


def test_terminating_tls_twice_is_refused(
    capsys: pytest.CaptureFixture[str],
    identity_store: Path,
    tmp_path: Path,
) -> None:
    certfile = tmp_path / "server-cert.pem"
    keyfile = tmp_path / "server-key.pem"
    certfile.write_text("test certificate", encoding="utf-8")
    keyfile.write_text("test key", encoding="utf-8")

    assert (
        main(
            _serve(
                "--behind-tls-proxy",
                "--ssl-certfile",
                str(certfile),
                "--ssl-keyfile",
                str(keyfile),
                identity_store=identity_store,
            )
        )
        == 1
    )
    assert capsys.readouterr().err == (
        "API startup failed: --behind-tls-proxy means a proxy terminates HTTPS, "
        "so this server may not also be given --ssl-certfile\n"
    )


# --- the forwarding headers ------------------------------------------------


def test_forwarded_headers_stay_disabled_until_a_proxy_is_named(
    launched: dict[str, Any],
    identity_store: Path,
) -> None:
    assert main(_serve("--behind-tls-proxy", identity_store=identity_store)) == 0

    configuration = launched["configuration"]
    assert configuration["proxy_headers"] is False
    assert "forwarded_allow_ips" not in configuration


def test_naming_trusted_proxies_enables_forwarded_headers(
    launched: dict[str, Any],
    identity_store: Path,
) -> None:
    assert (
        main(
            _serve(
                "--forwarded-allow-ips",
                "10.0.0.7",
                "--forwarded-allow-ips",
                "10.0.0.0/24",
                identity_store=identity_store,
            )
        )
        == 0
    )

    configuration = launched["configuration"]
    assert configuration["proxy_headers"] is True
    assert configuration["forwarded_allow_ips"] == "10.0.0.7,10.0.0.0/24"


def test_trusting_every_peer_is_refused(
    capsys: pytest.CaptureFixture[str],
    identity_store: Path,
) -> None:
    """uvicorn accepts `*`. A reviewed allowlist is the whole point, so TIDE does not."""

    assert (
        main(_serve("--forwarded-allow-ips", "*", identity_store=identity_store)) == 1
    )
    assert capsys.readouterr().err == (
        "API startup failed: --forwarded-allow-ips must name addresses or "
        "networks; '*' would trust a forwarded header from any peer\n"
    )


def test_an_allowlist_entry_that_is_not_an_address_is_refused(
    capsys: pytest.CaptureFixture[str],
    identity_store: Path,
) -> None:
    assert (
        main(
            _serve(
                "--forwarded-allow-ips",
                "proxy.example.test",
                identity_store=identity_store,
            )
        )
        == 1
    )
    assert capsys.readouterr().err == (
        "API startup failed: --forwarded-allow-ips entry is not an address or "
        "network: 'proxy.example.test'\n"
    )


# --- everything else that was reading the bind address ---------------------


def test_a_proxied_server_does_not_publish_its_api_description(
    launched: dict[str, Any],
    identity_store: Path,
) -> None:
    """`docs` defaulted to on for a loopback bind, meaning 'nobody outside'.

    A proxy is the thing that makes that untrue while the bind stays loopback,
    so the default has to follow the exposure rather than the socket.
    """

    assert main(_serve("--behind-tls-proxy", identity_store=identity_store)) == 0

    assert launched["app"].openapi_url is None


def test_a_proxied_server_still_publishes_its_description_when_asked(
    launched: dict[str, Any],
    identity_store: Path,
) -> None:
    """The default moved; the explicit choice did not."""

    assert (
        main(_serve("--behind-tls-proxy", "--docs", identity_store=identity_store)) == 0
    )

    assert launched["app"].openapi_url == "/openapi.json"


def test_a_loopback_server_still_publishes_its_description(
    launched: dict[str, Any],
    identity_store: Path,
) -> None:
    """The control for the two above."""

    assert main(_serve(identity_store=identity_store)) == 0

    assert launched["app"].openapi_url == "/openapi.json"


def test_development_authentication_may_not_be_published_through_a_proxy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--auth development` grants a browser session to anyone who asks.

    It was kept honest by a loopback bind. A proxy in front is a front door,
    and the bind is no longer the thing that answers the question.
    """

    assert main(_serve("--behind-tls-proxy")) == 1
    assert capsys.readouterr().err == (
        "API startup failed: development authentication may not be published "
        "through a proxy\n"
    )


def test_a_proxied_mcp_server_must_declare_its_external_url(
    capsys: pytest.CaptureFixture[str],
    identity_store: Path,
) -> None:
    """A derived `http://127.0.0.1:8000/mcp` names the socket, not the deployment."""

    assert (
        main(_serve("--behind-tls-proxy", "--mcp", identity_store=identity_store)) == 1
    )
    assert capsys.readouterr().err == (
        "API startup failed: proxied MCP serving requires --mcp-resource-url\n"
    )


# --- what the operator is told ---------------------------------------------


def test_the_banner_reports_the_proxy_rather_than_claiming_direct_tls(
    launched: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    identity_store: Path,
) -> None:
    assert (
        main(
            _serve(
                "--host",
                "0.0.0.0",
                "--behind-tls-proxy",
                "--forwarded-allow-ips",
                "10.0.0.7",
                identity_store=identity_store,
            )
        )
        == 0
    )

    banner = capsys.readouterr().out
    assert "http://0.0.0.0:8000" in banner, (
        "the address printed is where uvicorn listens; claiming https here "
        "would describe the proxy's socket rather than this one"
    )
    assert "TLS terminated by a trusted proxy" in banner
    assert "forwarded headers trusted from 10.0.0.7" in banner
