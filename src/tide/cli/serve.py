"""The `serve` command, and the startup checks it refuses to skip."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


from tide.api.config import (
    DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    DEFAULT_KEEP_ALIVE_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
    HttpServerLimits,
)
from tide.api.openapi import DEFAULT_BASE_PATH
from tide.api.server_lease import (
    DEFAULT_LEASE_TTL_SECONDS,
    ServerLeaseHolder,
    new_lease_id,
)
from tide.compiler.compiler import compile_project
from tide.compiler.normalized import ApplicationModel
from tide.data.attachment_files import FilesystemAttachmentBytes
from tide.observability import configure_runtime_logging
from tide.runtime import Principal
from tide.services import (
    ActionService,
    RecordsService,
)
from tide.services.attachment_store import (
    AttachmentStoreError,
    InMemoryAttachmentBytes,
    InMemoryAttachmentRows,
)
from tide.services.attachments import AttachmentService, file_fields

from .auth import parse_oidc_role_map, print_local_store_remedy
from .storage import RunStorage, open_run_storage


def add_serve_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Declare the `serve` arguments."""

    serve = commands.add_parser(
        "serve",
        help="run the FastAPI application server",
    )
    serve.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    serve_source = serve.add_mutually_exclusive_group()
    serve_source.add_argument(
        "--demo",
        action="store_true",
        help="execute application-owned demo_data.py in an in-memory repository",
    )
    serve_source.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        metavar="NAME",
        help=(
            "use a SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    serve.add_argument(
        "--create-schema",
        action="store_true",
        help="explicitly create missing managed application and TIDE system tables",
    )
    serve.add_argument(
        "--base-path",
        default=DEFAULT_BASE_PATH,
        help=f"REST base path (default: {DEFAULT_BASE_PATH})",
    )
    serve.add_argument(
        "--web-root",
        type=Path,
        help=(
            "serve a built TIDE Web renderer from this directory "
            "(must contain index.html)"
        ),
    )
    serve.add_argument(
        "--auth",
        choices=("development", "local", "oidc"),
        default="development",
        help="identity adapter (default: development)",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "interface to bind (non-loopback requires --ssl-certfile or "
            "--behind-tls-proxy, and a production identity adapter)"
        ),
    )
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--docs",
        dest="docs",
        action="store_true",
        default=None,
        help=(
            "serve /docs, /redoc and /openapi.json (default: on only where "
            "nothing beyond this machine can reach the server)"
        ),
    )
    serve.add_argument(
        "--no-docs",
        dest="docs",
        action="store_false",
        help="never serve the API description, even on loopback",
    )
    serve.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error", "critical"),
        default="info",
        help="minimum TIDE structured runtime log level (default: info)",
    )
    serve.add_argument(
        "--max-request-body-bytes",
        type=int,
        default=DEFAULT_MAX_REQUEST_BODY_BYTES,
        help=(
            "maximum HTTP request body size in bytes "
            f"(default: {DEFAULT_MAX_REQUEST_BODY_BYTES})"
        ),
    )
    serve.add_argument(
        "--max-concurrent-requests",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_REQUESTS,
        help=(
            "maximum concurrent HTTP requests "
            f"(default: {DEFAULT_MAX_CONCURRENT_REQUESTS})"
        ),
    )
    serve.add_argument(
        "--request-body-timeout",
        type=int,
        default=DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
        help=(
            "maximum request-body receive time in seconds "
            f"(default: {DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS})"
        ),
    )
    serve.add_argument(
        "--keep-alive-timeout",
        type=int,
        default=DEFAULT_KEEP_ALIVE_TIMEOUT_SECONDS,
        help=(
            "idle HTTP keep-alive timeout in seconds "
            f"(default: {DEFAULT_KEEP_ALIVE_TIMEOUT_SECONDS})"
        ),
    )
    serve.add_argument(
        "--graceful-shutdown-timeout",
        type=int,
        default=DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
        help=(
            "maximum graceful shutdown wait in seconds "
            f"(default: {DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS})"
        ),
    )
    serve.add_argument(
        "--ssl-certfile",
        type=Path,
        help="PEM certificate chain used for direct HTTPS",
    )
    serve.add_argument(
        "--ssl-keyfile",
        type=Path,
        help="PEM private key used for direct HTTPS",
    )
    serve.add_argument(
        "--ssl-keyfile-password-env",
        metavar="NAME",
        help="read the encrypted private-key password from environment variable NAME",
    )
    serve.add_argument(
        "--behind-tls-proxy",
        action="store_true",
        help=(
            "declare that a trusted reverse proxy terminates HTTPS in front of "
            "this server; the browser's scheme is https even though this socket "
            "speaks http"
        ),
    )
    serve.add_argument(
        "--forwarded-allow-ips",
        action="append",
        default=[],
        metavar="ADDRESS",
        help=(
            "trust X-Forwarded-* headers from this address or network; repeat "
            "for several. Without it forwarded headers stay disabled"
        ),
    )
    serve.add_argument(
        "--dev-token-env",
        default="TIDE_API_TOKEN",
        metavar="NAME",
        help="read the local-development bearer token from environment variable NAME",
    )
    serve.add_argument(
        "--lease-ttl",
        type=float,
        default=DEFAULT_LEASE_TTL_SECONDS,
        metavar="SECONDS",
        help=(
            "how long this server's claim survives without a heartbeat, where "
            "browser sessions cannot be shared (default: "
            f"{DEFAULT_LEASE_TTL_SECONDS:.0f}); a crashed server blocks a "
            "restart for at most this long"
        ),
    )
    serve.add_argument(
        "--local-auth-store",
        type=Path,
        help=(
            "TIDE-owned SQLite identity file created by 'tide auth create-user'"
        ),
    )
    serve.add_argument(
        "--role",
        action="append",
        default=[],
        help="server-assigned principal role; repeat for multiple roles",
    )
    serve.add_argument(
        "--principal",
        default="development:api",
        help="server-assigned development principal identifier",
    )
    serve.add_argument(
        "--attachments-root",
        metavar="DIRECTORY",
        help=(
            "where uploaded files are kept, for applications that declare a "
            "file field (default: the TIDE_ATTACHMENTS_ROOT environment "
            "variable)"
        ),
    )
    serve.add_argument(
        "--oidc-issuer",
        help="exact HTTPS issuer URL used for OIDC discovery and token validation",
    )
    serve.add_argument(
        "--oidc-audience",
        help="required access-token audience for this TIDE server",
    )
    serve.add_argument(
        "--oidc-role-claim",
        default="roles",
        help="dot-separated claim containing external roles (default: roles)",
    )
    serve.add_argument(
        "--oidc-role-map",
        action="append",
        default=[],
        metavar="EXTERNAL=TIDE_ROLE",
        help="map an external role to an application role; repeat as needed",
    )
    serve.add_argument(
        "--oidc-algorithm",
        action="append",
        default=[],
        metavar="NAME",
        help="accepted asymmetric JWT algorithm; repeat (default: RS256)",
    )
    serve.add_argument(
        "--oidc-token-type",
        action="append",
        default=[],
        metavar="TYPE",
        help="accepted JWT typ header; repeat (defaults: at+jwt and JWT)",
    )
    serve.add_argument(
        "--oidc-leeway",
        type=float,
        default=30.0,
        help="JWT clock-skew leeway in seconds (default: 30)",
    )
    serve.add_argument(
        "--oidc-timeout",
        type=float,
        default=5.0,
        help="OIDC discovery and JWKS timeout in seconds (default: 5)",
    )
    serve.add_argument(
        "--web-oidc-client-id",
        help="enable same-origin browser login with this OIDC client ID",
    )
    serve.add_argument(
        "--web-oidc-client-secret-env",
        metavar="NAME",
        help="read the optional confidential Web OIDC client secret from NAME",
    )
    serve.add_argument(
        "--web-oidc-redirect-uri",
        help=(
            "registered browser callback URI ending in "
            "/_tide/browser-auth/callback"
        ),
    )
    serve.add_argument(
        "--web-oidc-scope",
        action="append",
        default=[],
        metavar="SCOPE",
        help="browser OIDC scope; repeat (defaults: openid and profile)",
    )
    serve.add_argument(
        "--web-session-lifetime",
        type=int,
        default=8 * 60 * 60,
        metavar="SECONDS",
        help="absolute process-local browser session lifetime (default: 28800)",
    )
    serve.add_argument(
        "--mcp",
        action="store_true",
        help="mount the opt-in read-only runtime MCP server",
    )
    serve.add_argument(
        "--mcp-path",
        default="/mcp",
        help="Streamable HTTP MCP path (default: /mcp)",
    )
    serve.add_argument(
        "--mcp-resource-url",
        help=(
            "canonical externally reachable MCP resource URL; required for "
            "non-loopback serving"
        ),
    )
    serve.set_defaults(handler=_serve_api)


def _serve_api(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    if not 1 <= arguments.port <= 65535:
        print("API startup failed: port must be between 1 and 65535", file=sys.stderr)
        return 1
    try:
        limits = HttpServerLimits(
            max_request_body_bytes=arguments.max_request_body_bytes,
            request_body_timeout_seconds=arguments.request_body_timeout,
            max_concurrent_requests=arguments.max_concurrent_requests,
            keep_alive_timeout_seconds=arguments.keep_alive_timeout,
            graceful_shutdown_timeout_seconds=arguments.graceful_shutdown_timeout,
        )
    except ValueError as error:
        print(f"API startup failed: {error}", file=sys.stderr)
        return 1

    browser_oidc_requested = bool(
        arguments.web_oidc_client_id
        or arguments.web_oidc_client_secret_env
        or arguments.web_oidc_redirect_uri
        or arguments.web_oidc_scope
    )
    if browser_oidc_requested:
        if arguments.auth != "oidc":
            print(
                "API startup failed: browser OIDC login requires --auth oidc",
                file=sys.stderr,
            )
            return 1
        if not arguments.web_oidc_client_id or not arguments.web_oidc_redirect_uri:
            print(
                "API startup failed: browser OIDC login requires "
                "--web-oidc-client-id and --web-oidc-redirect-uri",
                file=sys.stderr,
            )
            return 1
        expected_callback_path = (
            f"{arguments.base_path.rstrip('/')}/_tide/browser-auth/callback"
        )
        try:
            callback_path = urlsplit(arguments.web_oidc_redirect_uri).path
        except ValueError:
            callback_path = ""
        if callback_path != expected_callback_path:
            print(
                "API startup failed: browser OIDC redirect URI path must be "
                f"{expected_callback_path!r}",
                file=sys.stderr,
            )
            return 1
    if arguments.auth == "local" and arguments.local_auth_store is None:
        print(
            "API startup failed: local authentication requires "
            "--local-auth-store",
            file=sys.stderr,
        )
        return 1
    if arguments.auth != "local" and arguments.local_auth_store is not None:
        print(
            "API startup failed: --local-auth-store requires --auth local",
            file=sys.stderr,
        )
        return 1
    if arguments.web_session_lifetime <= 0:
        print(
            "API startup failed: browser session lifetime must be positive",
            file=sys.stderr,
        )
        return 1

    try:
        certfile, keyfile, keyfile_password = _server_tls_configuration(arguments)
        exposure = _network_exposure(arguments, certfile=certfile)
        mcp_resource_url, mcp_issuer_url = _server_mcp_configuration(
            arguments,
            exposure=exposure,
        )
    except ValueError as error:
        print(f"API startup failed: {error}", file=sys.stderr)
        return 1

    try:
        from tide.api.server import (
            DevelopmentTokenAuthenticator,
            build_fastapi_app,
        )
        from tide.runtime.application import (
            ApplicationRuntimeError,
            configure_application_runtime,
        )
        import uvicorn
    except ModuleNotFoundError as error:
        if error.name in {"fastapi", "uvicorn"} or (error.name or "").startswith(
            ("fastapi.", "uvicorn.")
        ):
            print(
                "The FastAPI adapter is not installed. Install the 'api' extra "
                "(for example: uv sync --extra api).",
                file=sys.stderr,
            )
            return 1
        raise

    storage = open_run_storage(arguments, model, purpose="API")
    if storage is None:
        return 1
    try:
        identity_summary: str
        browser_auth: Any = None
        # A managed database carries a session table, and where there is one, the
        # sessions go in it: several TIDE processes behind one address then agree
        # about who is signed in, and a restart does not sign everybody out.
        #
        # OIDC is deliberately excluded. Its sessions hold the provider's access
        # and refresh tokens, and keeping those out of persistent application data
        # is the reason its store is process-local in the first place; writing them
        # to a plain table would trade a real secret for a convenience. That mode
        # keeps its single-process constraint until session state is encrypted at
        # rest, which is the next half of this same piece of work.
        shared_sessions = (
            storage.session_store if arguments.auth in {"development", "local"} else None
        )
        # The banner reports what the store says about itself rather than
        # inferring it from having one, so a store that is not shared cannot be
        # announced as though it were.
        session_summary = (
            "sessions: shared"
            if getattr(shared_sessions, "shared", False)
            else "sessions: this process"
        )
        if arguments.auth == "development":
            token = os.environ.get(arguments.dev_token_env)
            if not token:
                print(
                    "API startup failed: development bearer-token environment variable "
                    f"{arguments.dev_token_env!r} is not set",
                    file=sys.stderr,
                )
                return 1
            if len(token) < 32:
                print(
                    "API startup failed: development bearer token must contain at least "
                    "32 characters",
                    file=sys.stderr,
                )
                return 1
            roles = tuple(arguments.role)
            if not roles and arguments.demo and model.roles:
                roles = (max(model.roles, key=lambda role: len(model.roles[role])),)
            principal = Principal(arguments.principal, roles=frozenset(roles))
            authenticator: Any = DevelopmentTokenAuthenticator(token, principal)
            # The bearer token still guards the REST API, so `curl` and Swagger are
            # unchanged. What this adds is a way into the Web renderer that does not
            # involve moving a 32-character secret into a browser by hand: the same
            # principal, granted a session on request. It is reachable only from
            # this machine -- the bind was refused above if it is not loopback, and
            # the server rejects any request naming a host that is not loopback.
            from tide.api.development_auth import DevelopmentBrowserAuth

            browser_auth = DevelopmentBrowserAuth(
                principal,
                secure_cookie=exposure.external_https,
                session_lifetime_seconds=arguments.web_session_lifetime,
                sessions=shared_sessions,
            )
            identity_summary = (
                f"identity: {principal.identifier}; development auth only "
                "(browser sessions need no credential -- loopback only)"
            )
        elif arguments.auth == "local":
            if arguments.role or arguments.principal != "development:api":
                print(
                    "API startup failed: --role and --principal apply only to "
                    "development authentication",
                    file=sys.stderr,
                )
                return 1
            try:
                from tide.api.local_auth import LocalPasswordAuth, LocalUserStore

                store = LocalUserStore(
                    arguments.local_auth_store,
                    application=model.name,
                )
                authenticator = LocalPasswordAuth(
                    store,
                    allowed_roles=model.roles,
                    secure_cookie=exposure.external_https,
                    session_lifetime_seconds=arguments.web_session_lifetime,
                    sessions=shared_sessions,
                )
            except ValueError as error:
                print(f"API startup failed: {error}", file=sys.stderr)
                print_local_store_remedy(
                    arguments.local_auth_store,
                    arguments.project,
                    model,
                )
                return 1
            browser_auth = authenticator
            identity_summary = (
                f"identity: local username/password ({store.path}); browser login enabled"
            )
        else:
            if arguments.role or arguments.principal != "development:api":
                print(
                    "API startup failed: --role and --principal apply only to "
                    "development authentication",
                    file=sys.stderr,
                )
                return 1
            if not arguments.oidc_issuer or not arguments.oidc_audience:
                print(
                    "API startup failed: OIDC authentication requires --oidc-issuer "
                    "and --oidc-audience",
                    file=sys.stderr,
                )
                return 1
            try:
                from tide.api.auth import OidcJwtAuthenticator

                role_map = parse_oidc_role_map(arguments.oidc_role_map, model)
                authenticator = OidcJwtAuthenticator.from_discovery(
                    issuer=arguments.oidc_issuer,
                    audience=arguments.oidc_audience,
                    role_claim=arguments.oidc_role_claim,
                    role_map=role_map,
                    algorithms=tuple(arguments.oidc_algorithm) or ("RS256",),
                    token_types=tuple(arguments.oidc_token_type) or ("at+jwt", "JWT"),
                    leeway=arguments.oidc_leeway,
                    timeout=arguments.oidc_timeout,
                )
            except ModuleNotFoundError as error:
                if error.name in {"httpx", "jwt", "cryptography"} or (
                    error.name or ""
                ).startswith(("httpx.", "jwt.", "cryptography.")):
                    print(
                        "The OIDC adapter is not installed. Install the 'auth' extra "
                        "(for example: uv sync --extra api --extra auth).",
                        file=sys.stderr,
                    )
                    return 1
                raise
            except ValueError as error:
                print(f"API startup failed: {error}", file=sys.stderr)
                return 1
            identity_summary = f"identity: OIDC issuer {arguments.oidc_issuer}"
            if browser_oidc_requested:
                client_secret = None
                if arguments.web_oidc_client_secret_env:
                    client_secret = os.environ.get(
                        arguments.web_oidc_client_secret_env
                    )
                    if not client_secret:
                        print(
                            "API startup failed: browser OIDC client-secret environment "
                            f"variable {arguments.web_oidc_client_secret_env!r} is not set",
                            file=sys.stderr,
                        )
                        return 1
                try:
                    from tide.api.browser_auth import OidcBrowserAuth

                    browser_configuration: dict[str, Any] = {}
                    if arguments.web_oidc_scope:
                        browser_configuration["scopes"] = tuple(
                            arguments.web_oidc_scope
                        )
                    browser_auth = OidcBrowserAuth.from_discovery(
                        issuer=arguments.oidc_issuer,
                        authenticator=authenticator,
                        client_id=arguments.web_oidc_client_id,
                        client_secret=client_secret,
                        redirect_uri=arguments.web_oidc_redirect_uri,
                        session_lifetime_seconds=arguments.web_session_lifetime,
                        timeout=arguments.oidc_timeout,
                        **browser_configuration,
                    )
                except ValueError as error:
                    print(f"API startup failed: {error}", file=sys.stderr)
                    return 1
                identity_summary = f"{identity_summary}; browser login enabled"

        if arguments.demo:
            try:
                from tide.tui.demo import DemoDataError, seed_demo_data

                seed_demo_data(model, storage.repository)
            except DemoDataError as error:
                print(f"API demo startup failed: {error}", file=sys.stderr)
                return 1
        # Where the sessions cannot be shared, only one process may serve.
        # A process cannot see its siblings, but it can see their lease, so
        # this is the one place the misconfiguration can be refused rather
        # than met later as intermittent 401s that read like an expiry.
        # Nothing to take a lease with means a legacy database, where TIDE
        # may not create the table and the answer stays documentary.
        sessions_travel = getattr(shared_sessions, "shared", False)
        # Stamped only where sessions stay put, so the stamp's presence is
        # itself the statement that they do -- and a request carrying somebody
        # else's stamp can be answered precisely instead of as a bare 401.
        session_origin = None if sessions_travel else new_lease_id()[:12]
        lease_holder: ServerLeaseHolder | None = None
        if (
            browser_auth is not None
            and not sessions_travel
            and storage.lease_store is not None
        ):
            lease_holder = ServerLeaseHolder(
                storage.lease_store,
                lease_id=new_lease_id(),
                application=model.name,
                ttl=arguments.lease_ttl,
            )
            lease = lease_holder.acquire()
            if not lease.granted:
                waited = max(arguments.lease_ttl - (time.time() - lease.renewed_at), 0)
                print(
                    "API startup failed: another TIDE process is already serving "
                    f"{model.name!r} against this database, and "
                    f"--auth {arguments.auth} keeps browser sessions in the process "
                    "that issued them.\n"
                    "Stop the other server, or give this one its own database. If "
                    "that server is gone, its lease clears itself in about "
                    f"{waited:.0f}s.",
                    file=sys.stderr,
                )
                return 1

        try:
            attachments = _attachment_service(arguments, model, storage)
        except _AttachmentsNotConfigured as error:
            print(f"API startup failed: {error}", file=sys.stderr)
            return 1

        records = RecordsService(
            model,
            storage.repository,
            cursor_store=storage.cursor_store,
            audit_store=storage.execution_store,
            attachments=attachments,
        )
        actions = ActionService(
            model,
            records,
            execution_store=storage.execution_store,
        )
        try:
            configure_application_runtime(model, records, actions)
            app = build_fastapi_app(
                model,
                records,
                authenticator,
                actions=actions,
                attachments=attachments,
                base_path=arguments.base_path,
                max_request_body_bytes=limits.max_request_body_bytes,
                request_body_timeout_seconds=(
                    limits.request_body_timeout_seconds
                ),
                web_root=arguments.web_root,
                browser_auth=browser_auth,
                session_origin=session_origin if browser_auth is not None else None,
                # The description maps every entity, field and action, so it
                # follows the same rule as development authentication: fine on
                # the machine you are building on, not something a networked
                # deployment publishes because nobody said otherwise. A proxy
                # publishes a loopback bind, which is why this asks about the
                # exposure rather than the socket.
                docs=(
                    not exposure.published
                    if arguments.docs is None
                    else arguments.docs
                ),
            )
            if arguments.mcp:
                try:
                    from tide.mcp.runtime import RuntimeMcpService
                    from tide.mcp.server import (
                        build_runtime_mcp_server,
                        mount_runtime_mcp,
                    )
                except ModuleNotFoundError as error:
                    if error.name == "mcp" or (error.name or "").startswith("mcp."):
                        print(
                            "The MCP adapter is not installed. Install the 'mcp' "
                            "extra (for example: uv sync --extra api --extra mcp).",
                            file=sys.stderr,
                        )
                        return 1
                    raise
                assert mcp_resource_url is not None
                assert mcp_issuer_url is not None
                hosted_mcp = build_runtime_mcp_server(
                    RuntimeMcpService(
                        model,
                        records,
                        actions=actions,
                        audits=app.state.tide.audits,
                        reports=app.state.tide.reports,
                    ),
                    authenticator,
                    issuer_url=mcp_issuer_url,
                    resource_url=mcp_resource_url,
                    path=arguments.mcp_path,
                )
                mount_runtime_mcp(app, hosted_mcp)
        except (ApplicationRuntimeError, ValueError) as error:
            print(f"API startup failed: {error}", file=sys.stderr)
            return 1
        scheme = "https" if certfile is not None else "http"
        mcp_summary = (
            f"; MCP: {mcp_resource_url}" if mcp_resource_url is not None else ""
        )
        browser_summary = (
            f"; {session_summary}" if browser_auth is not None else ""
        )
        if lease_holder is not None:
            browser_summary = f"{browser_summary} (this server only)"
        # The scheme above is this socket's, not the deployment's: printing
        # https for a proxied server would name an address nothing listens on.
        proxy_summary = (
            "; TLS terminated by a trusted proxy" if exposure.proxied else ""
        )
        if exposure.trusted_proxies:
            proxy_summary += (
                "; forwarded headers trusted from "
                f"{', '.join(exposure.trusted_proxies)}"
            )
        print(
            f"Serving {model.name} at {scheme}://{arguments.host}:{arguments.port} "
            f"(docs: /docs; {identity_summary}{browser_summary}"
            f"{proxy_summary}{mcp_summary})."
        )
        configuration: dict[str, Any] = {
            "host": arguments.host,
            "port": arguments.port,
            "log_level": arguments.log_level,
            "access_log": False,
            "proxy_headers": bool(exposure.trusted_proxies),
            "server_header": False,
            "limit_concurrency": limits.max_concurrent_requests,
            "timeout_keep_alive": limits.keep_alive_timeout_seconds,
            "timeout_graceful_shutdown": (
                limits.graceful_shutdown_timeout_seconds
            ),
        }
        if exposure.trusted_proxies:
            configuration["forwarded_allow_ips"] = ",".join(
                exposure.trusted_proxies
            )
        if certfile is not None and keyfile is not None:
            configuration.update(
                ssl_certfile=str(certfile),
                ssl_keyfile=str(keyfile),
            )
            if keyfile_password is not None:
                configuration["ssl_keyfile_password"] = keyfile_password
        if lease_holder is not None:
            lease_holder.start()
        try:
            with configure_runtime_logging(arguments.log_level):
                uvicorn.run(app, **configuration)
        finally:
            if lease_holder is not None:
                lease_holder.stop()
        return 0
    finally:
        storage.dispose()


class _AttachmentsNotConfigured(Exception):
    """This application keeps files and this server was not told where."""


def _attachment_service(
    arguments: argparse.Namespace,
    model: ApplicationModel,
    storage: RunStorage,
) -> AttachmentService | None:
    """Build the file store this application needs, or explain what is missing.

    Nothing is built for an application that declares no file field: the
    requirement belongs to the application rather than to the framework, and
    a server that keeps no files should not be asked where it keeps them.

    Where the records are thrown away when the process stops -- `--demo`, or
    any in-memory run -- the files go with them, for the same reason and
    without a root. A durable run needs a real one, and refuses to start
    without it rather than accepting uploads it would drop.
    """

    declaring = [name for name, entity in model.entities.items() if file_fields(entity)]
    if not declaring:
        return None
    if storage.attachment_rows is None:
        return AttachmentService(
            model, InMemoryAttachmentRows(), InMemoryAttachmentBytes()
        )
    root = arguments.attachments_root or os.environ.get("TIDE_ATTACHMENTS_ROOT")
    if not root:
        raise _AttachmentsNotConfigured(
            f"{declaring[0]} declares a file field, so this server needs "
            "somewhere to keep files: pass --attachments-root or set "
            "TIDE_ATTACHMENTS_ROOT"
        )
    try:
        return AttachmentService(
            model, storage.attachment_rows, FilesystemAttachmentBytes(root)
        )
    except AttachmentStoreError as error:
        raise _AttachmentsNotConfigured(str(error)) from error


def _server_tls_configuration(
    arguments: argparse.Namespace,
) -> tuple[Path | None, Path | None, str | None]:
    certfile = arguments.ssl_certfile
    keyfile = arguments.ssl_keyfile
    if (certfile is None) != (keyfile is None):
        raise ValueError("--ssl-certfile and --ssl-keyfile must be supplied together")
    if arguments.ssl_keyfile_password_env and keyfile is None:
        raise ValueError(
            "--ssl-keyfile-password-env requires --ssl-certfile and --ssl-keyfile"
        )
    for label, path in (("certificate", certfile), ("private key", keyfile)):
        if path is not None and not path.is_file():
            raise ValueError(f"TLS {label} file does not exist: {path}")
    password = None
    if arguments.ssl_keyfile_password_env:
        password = os.environ.get(arguments.ssl_keyfile_password_env)
        if not password:
            raise ValueError(
                "TLS private-key password environment variable "
                f"{arguments.ssl_keyfile_password_env!r} is not set"
            )
    return certfile, keyfile, password


@dataclass(frozen=True, slots=True)
class NetworkExposure:
    """What can reach this server, which the bind address alone cannot answer.

    `_is_loopback_host` was doing two jobs: naming the socket, and standing in
    for "only this machine can arrive". A reverse proxy is exactly the thing
    that separates them -- it forwards the world to a loopback port -- so every
    decision that read the bind address to mean the second thing now reads this
    instead. The bind address still decides what uvicorn listens on and what
    scheme this socket speaks; nothing else.
    """

    published: bool
    """Something other than this machine can reach the application."""

    external_https: bool
    """The address the browser used is https, whoever terminated it."""

    proxied: bool
    """A declared reverse proxy terminates HTTPS in front of this server."""

    trusted_proxies: tuple[str, ...]
    """Peers whose `X-Forwarded-*` headers are believed. Empty means none."""


def _network_exposure(
    arguments: argparse.Namespace,
    *,
    certfile: Path | None,
) -> NetworkExposure:
    """Decide what the network can see, refusing the combinations that lie."""

    proxied = bool(arguments.behind_tls_proxy)
    trusted_proxies = _trusted_proxies(arguments.forwarded_allow_ips)
    is_loopback = _is_loopback_host(arguments.host)
    if proxied and certfile is not None:
        raise ValueError(
            "--behind-tls-proxy means a proxy terminates HTTPS, so this server "
            "may not also be given --ssl-certfile"
        )
    if arguments.auth == "development":
        # Development authentication grants a browser session to whoever asks.
        # A loopback bind was what kept that honest, and a proxy is a front
        # door -- so the bind is no longer the question worth asking.
        if proxied:
            raise ValueError(
                "development authentication may not be published through a proxy"
            )
        if not is_loopback:
            raise ValueError(
                "development authentication may listen only on a loopback interface"
            )
    if arguments.auth in {"local", "oidc"} and not is_loopback:
        if certfile is None and not proxied:
            raise ValueError(
                "non-loopback serving requires --ssl-certfile and --ssl-keyfile, "
                "or --behind-tls-proxy"
            )
    return NetworkExposure(
        published=not is_loopback or proxied,
        external_https=certfile is not None or proxied,
        proxied=proxied,
        trusted_proxies=trusted_proxies,
    )


def _trusted_proxies(values: list[str]) -> tuple[str, ...]:
    """Validate the allowlist, because uvicorn's own `*` is not an allowlist."""

    trusted: list[str] = []
    for value in values:
        entry = value.strip()
        if entry == "*":
            raise ValueError(
                "--forwarded-allow-ips must name addresses or networks; '*' "
                "would trust a forwarded header from any peer"
            )
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError as error:
            raise ValueError(
                "--forwarded-allow-ips entry is not an address or network: "
                f"{entry!r}"
            ) from error
        if entry not in trusted:
            trusted.append(entry)
    return tuple(trusted)


def _server_mcp_configuration(
    arguments: argparse.Namespace,
    *,
    exposure: NetworkExposure,
) -> tuple[str | None, str | None]:
    if not arguments.mcp:
        if arguments.mcp_resource_url is not None:
            raise ValueError("--mcp-resource-url requires --mcp")
        return None, None
    path = arguments.mcp_path.strip()
    if (
        not path.startswith("/")
        or path == "/"
        or path.endswith("/")
        or "?" in path
        or "#" in path
    ):
        raise ValueError(
            "--mcp-path must be an absolute non-root path without a trailing slash"
        )
    resource_url = arguments.mcp_resource_url
    if resource_url is None:
        # Deriving one from the bind address names this socket, which behind a
        # proxy is not the address any client will ever use.
        if exposure.proxied:
            raise ValueError("proxied MCP serving requires --mcp-resource-url")
        if exposure.published:
            raise ValueError(
                "non-loopback MCP serving requires --mcp-resource-url"
            )
        host = arguments.host.strip()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        scheme = "https" if exposure.external_https else "http"
        resource_url = f"{scheme}://{host}:{arguments.port}{path}"
    try:
        parsed = urlsplit(resource_url)
        _port = parsed.port
    except ValueError as error:
        raise ValueError("MCP resource URL is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != path
    ):
        raise ValueError(
            "MCP resource URL must be an absolute HTTP or HTTPS URL whose path "
            "exactly matches --mcp-path"
        )
    resource_is_loopback = _is_loopback_host(parsed.hostname)
    if parsed.scheme != "https" and not resource_is_loopback:
        raise ValueError("non-loopback MCP resource URLs must use HTTPS")
    if arguments.auth == "development" and not resource_is_loopback:
        raise ValueError("development MCP resource URLs must use a loopback host")
    if arguments.auth == "oidc":
        issuer_url = arguments.oidc_issuer
    else:
        issuer_url = f"{parsed.scheme}://{parsed.netloc}"
    return resource_url, issuer_url


def _is_loopback_host(host: str) -> bool:
    value = host.strip()
    if value.lower() == "localhost":
        return True
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False
