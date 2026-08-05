"""Local identities, and checking an OpenID Connect provider."""

from __future__ import annotations

import argparse
from getpass import getpass
import os
import sys
from pathlib import Path
from typing import Any


from tide.compiler.compiler import compile_project
from tide.compiler.normalized import ApplicationModel

from .output import print_json


def add_authentication_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Declare the `authentication` arguments."""

    authentication = commands.add_parser(
        "auth",
        help="manage local users or inspect optional provider compatibility",
    )
    authentication_commands = authentication.add_subparsers(
        dest="authentication_command"
    )
    check_oidc = authentication_commands.add_parser(
        "check-oidc",
        help="preflight an OIDC bearer and browser-login configuration",
    )
    check_oidc.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    check_oidc.add_argument(
        "--oidc-issuer",
        required=True,
        help="exact HTTPS issuer URL used for OIDC discovery",
    )
    check_oidc.add_argument(
        "--oidc-audience",
        required=True,
        help="required access-token audience for this TIDE server",
    )
    check_oidc.add_argument(
        "--oidc-role-claim",
        default="roles",
        help="dot-separated claim containing external roles (default: roles)",
    )
    check_oidc.add_argument(
        "--oidc-role-map",
        action="append",
        default=[],
        metavar="EXTERNAL=TIDE_ROLE",
        help="map an external role to an application role; repeat as needed",
    )
    check_oidc.add_argument(
        "--oidc-algorithm",
        action="append",
        default=[],
        metavar="NAME",
        help="accepted asymmetric JWT algorithm; repeat (default: RS256)",
    )
    check_oidc.add_argument(
        "--oidc-token-type",
        action="append",
        default=[],
        metavar="TYPE",
        help="accepted JWT typ header; repeat (defaults: at+jwt and JWT)",
    )
    check_oidc.add_argument(
        "--oidc-leeway",
        type=float,
        default=30.0,
        help="JWT clock-skew leeway in seconds (default: 30)",
    )
    check_oidc.add_argument(
        "--oidc-timeout",
        type=float,
        default=5.0,
        help="OIDC discovery timeout in seconds (default: 5)",
    )
    check_oidc.add_argument(
        "--web-oidc-client-id",
        required=True,
        help="registered Web OIDC client ID",
    )
    check_oidc.add_argument(
        "--web-oidc-client-secret-env",
        metavar="NAME",
        help="read the optional confidential Web OIDC client secret from NAME",
    )
    check_oidc.add_argument(
        "--web-oidc-redirect-uri",
        required=True,
        help="exact registered browser callback URI",
    )
    check_oidc.add_argument(
        "--web-oidc-scope",
        action="append",
        default=[],
        metavar="SCOPE",
        help="browser OIDC scope; repeat (defaults: openid and profile)",
    )
    check_oidc.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable compatibility result",
    )
    check_oidc.set_defaults(handler=_auth_check_oidc)

    create_user = authentication_commands.add_parser(
        "create-user",
        help="create a username/password identity in a TIDE-owned local store",
    )
    _add_local_user_arguments(create_user, require_roles=True)
    create_user.set_defaults(handler=_auth_create_user)

    set_password = authentication_commands.add_parser(
        "set-password",
        help="replace one local user's password",
    )
    _add_local_user_arguments(set_password, require_roles=False)
    set_password.set_defaults(handler=_auth_set_password)

    disable_user = authentication_commands.add_parser(
        "disable-user",
        help="refuse further sign-ins for one local user without deleting it",
    )
    _add_local_user_arguments(disable_user, require_roles=False, password=False)
    disable_user.set_defaults(handler=_auth_disable_user, enabled=False)

    enable_user = authentication_commands.add_parser(
        "enable-user",
        help="allow sign-ins again for a disabled local user",
    )
    _add_local_user_arguments(enable_user, require_roles=False, password=False)
    enable_user.set_defaults(handler=_auth_disable_user, enabled=True)

    set_roles = authentication_commands.add_parser(
        "set-roles",
        help="replace one local user's application roles",
    )
    _add_local_user_arguments(set_roles, require_roles=True, password=False)
    set_roles.set_defaults(handler=_auth_set_roles)


def _add_local_user_arguments(
    parser: argparse.ArgumentParser,
    *,
    require_roles: bool,
    password: bool = True,
) -> None:
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    parser.add_argument(
        "--store",
        type=Path,
        required=True,
        help="TIDE-owned local identity SQLite file",
    )
    parser.add_argument(
        "--username",
        help="local sign-in name (prompted when omitted)",
    )
    if password:
        parser.add_argument(
            "--password-env",
            metavar="NAME",
            help=(
                "read the password from environment variable NAME instead of "
                "prompting"
            ),
        )
    if require_roles:
        if password:
            parser.add_argument(
                "--display-name",
                help="optional display name (defaults to the username)",
            )
        parser.add_argument(
            "--role",
            action="append",
            default=[],
            required=True,
            help="application role assigned by the server; repeat as needed",
        )


def _auth_create_user(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    unknown_roles = sorted(set(arguments.role).difference(model.roles))
    if unknown_roles:
        print(
            "Local user creation failed: unknown application role(s): "
            + ", ".join(unknown_roles),
            file=sys.stderr,
        )
        return 1
    username = _read_local_username(arguments.username)
    if username is None:
        return 1
    password = _read_local_password(arguments.password_env)
    if password is None:
        return 1
    try:
        from tide.api.local_auth import (
            LocalAuthenticationError,
            LocalUserStore,
            validate_password,
        )

        validate_password(password)
        store = LocalUserStore(arguments.store, application=model.name)
        store.initialize()
        user = store.create_user(
            username,
            password,
            roles=arguments.role,
            display_name=arguments.display_name,
        )
    except (LocalAuthenticationError, ValueError) as error:
        print(f"Local user creation failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Created local user {user.username!r} for {model.name} with role(s): "
        + ", ".join(sorted(user.roles))
    )
    print(f"Identity store: {store.path}")
    return 0


def _auth_set_password(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    username = _read_local_username(arguments.username)
    if username is None:
        return 1
    password = _read_local_password(arguments.password_env)
    if password is None:
        return 1
    try:
        from tide.api.local_auth import LocalAuthenticationError, LocalUserStore

        store = LocalUserStore(arguments.store, application=model.name)
        store.set_password(username, password)
    except (LocalAuthenticationError, ValueError) as error:
        print(f"Local password update failed: {error}", file=sys.stderr)
        return 1
    print(f"Updated the password for local user {username!r}.")
    return 0


def _auth_disable_user(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    username = _read_local_username(arguments.username)
    if username is None:
        return 1
    try:
        from tide.api.local_auth import LocalAuthenticationError, LocalUserStore

        store = LocalUserStore(arguments.store, application=model.name)
        store.set_enabled(username, arguments.enabled)
    except (LocalAuthenticationError, ValueError) as error:
        print(f"Local user update failed: {error}", file=sys.stderr)
        return 1
    if arguments.enabled:
        print(f"Enabled local user {username!r}.")
    else:
        # Sessions live in the serving process, which this one is not, so say
        # what actually happens rather than implying an instant cut-off.
        print(
            f"Disabled local user {username!r}. Sign-ins are refused now; any "
            "open session ends at its next revalidation."
        )
    return 0


def _auth_set_roles(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    unknown_roles = sorted(set(arguments.role).difference(model.roles))
    if unknown_roles:
        print(
            "Local role update failed: unknown application role(s): "
            + ", ".join(unknown_roles),
            file=sys.stderr,
        )
        return 1
    username = _read_local_username(arguments.username)
    if username is None:
        return 1
    try:
        from tide.api.local_auth import LocalAuthenticationError, LocalUserStore

        store = LocalUserStore(arguments.store, application=model.name)
        roles = store.set_roles(username, arguments.role)
    except (LocalAuthenticationError, ValueError) as error:
        print(f"Local role update failed: {error}", file=sys.stderr)
        return 1
    print(f"Local user {username!r} now holds: {', '.join(sorted(roles))}.")
    return 0


def _read_local_username(configured: str | None) -> str | None:
    if configured is not None:
        return configured
    try:
        username = input("Username: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nLocal authentication cancelled.", file=sys.stderr)
        return None
    if not username:
        print("Local authentication failed: username is required", file=sys.stderr)
        return None
    return username


def _read_local_password(environment_name: str | None) -> str | None:
    if environment_name:
        password = os.environ.get(environment_name)
        if password is None:
            print(
                "Local authentication failed: password environment variable "
                f"{environment_name!r} is not set",
                file=sys.stderr,
            )
            return None
        return password
    try:
        password = getpass("Password: ")
        confirmation = getpass("Confirm password: ")
    except (EOFError, KeyboardInterrupt):
        print("\nLocal authentication cancelled.", file=sys.stderr)
        return None
    if password != confirmation:
        print("Local authentication failed: passwords do not match", file=sys.stderr)
        return None
    return password


def _auth_check_oidc(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    client_secret = None
    if arguments.web_oidc_client_secret_env:
        client_secret = os.environ.get(arguments.web_oidc_client_secret_env)
        if not client_secret:
            print(
                "OIDC preflight failed: browser client-secret environment "
                f"variable {arguments.web_oidc_client_secret_env!r} is not set",
                file=sys.stderr,
            )
            return 1
    try:
        from tide.api.auth import OidcJwtAuthenticator
        from tide.api.browser_auth import OidcBrowserAuth
    except ModuleNotFoundError as error:
        if error.name in {"httpx", "jwt", "cryptography"} or (
            error.name or ""
        ).startswith(("httpx.", "jwt.", "cryptography.")):
            print(
                "The OIDC adapter is not installed. Install the 'auth' extra "
                "(for example: uv sync --extra auth).",
                file=sys.stderr,
            )
            return 1
        raise

    try:
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
        browser_configuration: dict[str, Any] = {}
        if arguments.web_oidc_scope:
            browser_configuration["scopes"] = tuple(arguments.web_oidc_scope)
        browser_auth = OidcBrowserAuth.from_discovery(
            issuer=arguments.oidc_issuer,
            authenticator=authenticator,
            client_id=arguments.web_oidc_client_id,
            client_secret=client_secret,
            redirect_uri=arguments.web_oidc_redirect_uri,
            timeout=arguments.oidc_timeout,
            **browser_configuration,
        )
    except ValueError as error:
        print(f"OIDC preflight failed: {error}", file=sys.stderr)
        return 1

    provider = browser_auth.provider_info
    if provider is None:
        print(
            "OIDC preflight failed: provider capability information is unavailable",
            file=sys.stderr,
        )
        return 1
    requested_scopes = tuple(arguments.web_oidc_scope) or (
        "openid",
        "profile",
    )
    client_authentication = (
        "client_secret_basic" if client_secret is not None else "none"
    )
    limitation = (
        "Discovery cannot prove client/callback registration, issued access-token "
        "claims, mapped roles, or refresh-token issuance; complete one interactive "
        "browser sign-in before deployment."
    )
    result = {
        "compatible": True,
        "application": model.name,
        "application_version": model.version,
        "audience": arguments.oidc_audience,
        "role_claim": arguments.oidc_role_claim,
        "role_map": role_map,
        "browser_client": {
            "client_id": arguments.web_oidc_client_id,
            "client_authentication": client_authentication,
            "redirect_uri": arguments.web_oidc_redirect_uri,
            "requested_scopes": list(requested_scopes),
        },
        "provider": provider.as_dict(),
        "limitations": [limitation],
    }
    if arguments.json:
        print_json(result)
        return 0

    print(
        f"OIDC preflight passed for {model.name} {model.version}: "
        f"{provider.issuer}"
    )
    print(
        "Browser flow: authorization code + PKCE S256; token client "
        f"authentication: {client_authentication}."
    )
    print(
        f"Requested scopes: {', '.join(requested_scopes)}; "
        f"role mapping(s): {len(role_map)}."
    )
    for warning in provider.warnings:
        print(f"Warning: {warning}")
    print(f"Next: {limitation}")
    return 0


def parse_oidc_role_map(
    values: list[str],
    model: ApplicationModel,
) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for value in values:
        external, separator, tide_role = value.partition("=")
        external = external.strip()
        tide_role = tide_role.strip()
        if not separator or not external or not tide_role:
            raise ValueError(
                "OIDC role mappings must use EXTERNAL=TIDE_ROLE"
            )
        if external in mappings:
            raise ValueError(f"duplicate OIDC role mapping for {external!r}")
        if tide_role not in model.roles:
            raise ValueError(
                f"OIDC role mapping targets unknown application role {tide_role!r}"
            )
        mappings[external] = tide_role
    return mappings
