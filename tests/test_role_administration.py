"""Administering the identities TIDE owns, from inside the running application.

Roles and permissions are compiled. They are not editable at runtime, and this
does not make them so: what an administrator changes here is *assignment* --
which identity holds which declared role, and whether an account may sign in.

Until now that lived only in `tide auth`, which means a console on the server.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from tide import compile_project
from tide.api.administration import (
    ADMINISTER,
    AdministrationDenied,
    AdministrationError,
    UnknownLocalUser,
    UserAdministration,
)
from tide.api.local_auth import LocalPasswordAuth, LocalUserStore
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.runtime import Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.security import SecurityEngine
from tide.services import ActionService, RecordsService

TOKEN = "tide-development-token-that-is-long-enough"

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
PASSWORD = "correct horse battery staple"


def test_the_store_lists_its_accounts_without_their_hashes(tmp_path: Path) -> None:
    """A listing that never loads a hash cannot leak one.

    The wire projection would withhold it either way; this is the half that
    does not depend on remembering to.
    """

    store = _store(tmp_path)
    store.create_user("auditor", PASSWORD, roles={"auditor"})
    store.set_enabled("auditor", False)

    users = store.list_users()

    assert [user.username for user in users] == ["admin", "auditor", "clerk"]
    listed = {user.username: user for user in users}
    assert listed["clerk"].roles == frozenset({"sales_clerk"})
    assert listed["clerk"].enabled is True
    assert listed["auditor"].enabled is False
    assert listed["admin"].display_name == "admin"
    assert not any(hasattr(user, "password_hash") for user in users)
    assert all(user.created_at for user in users)


def test_the_store_lists_an_account_that_holds_several_roles(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.set_roles("clerk", {"sales_clerk", "auditor"})

    listed = {user.username: user for user in store.list_users()}

    assert listed["clerk"].roles == frozenset({"auditor", "sales_clerk"})


def test_a_listing_can_be_bounded(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert [user.username for user in store.list_users(limit=2)] == [
        "admin",
        "clerk",
    ]
    assert [user.username for user in store.list_users(limit=1)] == ["admin"]
    # Roles belong to the accounts on the page, not to the ones it stopped at.
    assert store.list_users(limit=1)[0].roles == frozenset({"administrator"})


def test_administration_needs_the_framework_permission(tmp_path: Path) -> None:
    """Granted through a role like everything else, and checked like everything
    else -- there is no second authority for this."""

    administration = _administration(tmp_path)

    assert administration.can_administer(_context("administrator")) is True
    assert administration.can_administer(_context("sales_clerk")) is False
    with pytest.raises(AdministrationDenied):
        administration.list_users(_context("sales_clerk"))


def test_administration_reports_the_compiled_roles_and_what_they_grant(
    tmp_path: Path,
) -> None:
    """The permissions half of the feature, read-only on purpose: roles are
    compiled, so this says what they are rather than offering to change them.
    """

    administration = _administration(tmp_path)

    catalogue = administration.roles(_context("administrator"))
    roles = {role.name: role.grants for role in catalogue}

    assert roles["administrator"] == ("tide.users.administer",)
    assert "sales.invoice.post" in roles["sales_clerk"]
    assert "sales.invoice.post" not in roles["auditor"]
    with pytest.raises(AdministrationDenied):
        administration.roles(_context("sales_clerk"))


def test_roles_are_replaced_and_must_name_something_the_model_compiled(
    tmp_path: Path,
) -> None:
    administration = _administration(tmp_path)
    context = _context("administrator")

    changed = administration.set_roles(context, "clerk", ["auditor"])
    assert changed.roles == frozenset({"auditor"})

    with pytest.raises(AdministrationError) as caught:
        administration.set_roles(context, "clerk", ["auditor", "warehouse"])
    assert "warehouse" in str(caught.value)
    # The refusal names what the application does define, the way the missing
    # store refusal does, rather than leaving the caller to guess.
    assert "sales_clerk" in str(caught.value)
    # Refused entirely: the good half of a bad request is not applied.
    assert administration.user(context, "clerk").roles == frozenset({"auditor"})


def test_an_account_may_not_be_left_with_no_roles(tmp_path: Path) -> None:
    administration = _administration(tmp_path)

    with pytest.raises(AdministrationError):
        administration.set_roles(_context("administrator"), "clerk", [])


def test_changing_an_account_that_does_not_exist_is_refused(
    tmp_path: Path,
) -> None:
    administration = _administration(tmp_path)

    with pytest.raises(UnknownLocalUser):
        administration.set_enabled(_context("administrator"), "nobody", False)


def test_the_last_enabled_administrator_cannot_be_disabled(
    tmp_path: Path,
) -> None:
    """Locking every administrator out of a running application leaves a
    console on the server as the only way back in."""

    administration = _administration(tmp_path)
    context = _context("administrator")

    with pytest.raises(AdministrationError) as caught:
        administration.set_enabled(context, "admin", False)
    assert "administer" in str(caught.value)

    administration.create_user(
        context,
        username="deputy",
        password=PASSWORD,
        roles=["administrator"],
    )
    # With a second one it is an ordinary change, including on yourself.
    assert administration.set_enabled(context, "admin", False).enabled is False


def test_the_last_enabled_administrator_cannot_be_demoted(tmp_path: Path) -> None:
    administration = _administration(tmp_path)
    context = _context("administrator")

    with pytest.raises(AdministrationError):
        administration.set_roles(context, "admin", ["sales_clerk"])

    administration.create_user(
        context,
        username="deputy",
        password=PASSWORD,
        roles=["administrator"],
    )
    assert administration.set_roles(context, "admin", ["sales_clerk"]).roles == (
        frozenset({"sales_clerk"})
    )


def test_a_disabled_administrator_does_not_count_as_one(tmp_path: Path) -> None:
    """The guard is about who can still sign in, not who is on the list."""

    administration = _administration(tmp_path)
    context = _context("administrator")
    administration.create_user(
        context,
        username="deputy",
        password=PASSWORD,
        roles=["administrator"],
    )
    administration.set_enabled(context, "deputy", False)

    with pytest.raises(AdministrationError):
        administration.set_enabled(context, "admin", False)


def test_two_racing_demotions_cannot_disable_every_administrator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check-then-act loses this race; the write transaction must not.

    Two concurrent demotions each saw the other administrator still
    enabled, both committed, and the store was left with no enabled
    administrator at all -- the exact state the guard exists to refuse.
    The second demotion runs here inside the first's window between its
    check and its write, which is the interleaving a threadpool produces.
    """

    administration = _administration(tmp_path)
    context = _context("administrator")
    administration.create_user(
        context,
        username="second",
        password=PASSWORD,
        roles=["administrator"],
    )
    other = UserAdministration(
        LocalUserStore(
            tmp_path / "auth.sqlite3",
            application="TIDE Invoicing",
            password_iterations=1_000,
        ),
        administration.model,
        administration.security,
    )

    surprises: list[BaseException] = []
    original = administration.store.set_enabled

    def racing_set_enabled(*args: object, **kwargs: object) -> object:
        try:
            other.set_enabled(_context("administrator"), "second", False)
        except AdministrationError as error:
            surprises.append(error)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        administration.store, "set_enabled", racing_set_enabled
    )

    with pytest.raises(AdministrationError):
        administration.set_enabled(context, "admin", False)

    assert surprises == []
    users = {user.username: user for user in other.store.list_users()}
    assert users["second"].enabled is False
    assert users["admin"].enabled is True


def test_a_created_account_is_normalized_checked_and_enabled(
    tmp_path: Path,
) -> None:
    administration = _administration(tmp_path)
    context = _context("administrator")

    created = administration.create_user(
        context,
        username="New.Clerk",
        password=PASSWORD,
        roles=["sales_clerk"],
        display_name="New Clerk",
    )

    assert created.username == "new.clerk"
    assert created.display_name == "New Clerk"
    assert created.enabled is True
    assert created.roles == frozenset({"sales_clerk"})
    with pytest.raises(AdministrationError):
        administration.create_user(
            context,
            username="short",
            password="tiny",
            roles=["sales_clerk"],
        )
    with pytest.raises(AdministrationError):
        administration.create_user(
            context,
            username="unknown.role",
            password=PASSWORD,
            roles=["warehouse"],
        )
    assert [user.username for user in administration.list_users(context).users] == [
        "admin",
        "clerk",
        "new.clerk",
    ]


def test_creating_an_account_that_already_exists_is_refused(
    tmp_path: Path,
) -> None:
    administration = _administration(tmp_path)
    context = _context("administrator")

    with pytest.raises(AdministrationError) as caught:
        administration.create_user(
            context,
            username="clerk",
            password=PASSWORD,
            roles=["sales_clerk"],
        )

    assert "clerk" in str(caught.value)


def test_a_password_reset_is_checked_and_leaves_nothing_readable(
    tmp_path: Path,
) -> None:
    administration = _administration(tmp_path)
    context = _context("administrator")

    with pytest.raises(AdministrationError):
        administration.set_password(context, "clerk", "tiny")

    administration.set_password(context, "clerk", "a new long passphrase")

    # Nothing about the credential comes back out of the listing.
    listed = administration.list_users(context).users
    assert all(not hasattr(user, "password_hash") for user in listed)


def test_every_change_needs_the_permission(tmp_path: Path) -> None:
    """One check, on every way in -- not on the listing alone."""

    administration = _administration(tmp_path)
    clerk = _context("sales_clerk")

    with pytest.raises(AdministrationDenied):
        administration.create_user(
            clerk, username="x", password=PASSWORD, roles=["sales_clerk"]
        )
    with pytest.raises(AdministrationDenied):
        administration.set_roles(clerk, "clerk", ["auditor"])
    with pytest.raises(AdministrationDenied):
        administration.set_enabled(clerk, "clerk", False)
    with pytest.raises(AdministrationDenied):
        administration.set_password(clerk, "clerk", PASSWORD)
    with pytest.raises(AdministrationDenied):
        administration.user(clerk, "clerk")


def test_the_session_says_whether_this_principal_may_administer(
    tmp_path: Path,
) -> None:
    """One flag, true only when both halves hold: the caller may administer,
    and there are identities here to administer."""

    async def exercise() -> None:
        async with _client(_app("administrator", tmp_path)) as client:
            response = await client.get(
                "/api/v1/_tide/session", headers=_authorization()
            )
        assert response.json()["administration"] is True

        async with _client(_app("sales_clerk", tmp_path / "clerk")) as client:
            response = await client.get(
                "/api/v1/_tide/session", headers=_authorization()
            )
        assert response.json()["administration"] is False

    asyncio.run(exercise())


def test_administration_is_absent_where_tide_does_not_own_the_identities(
    tmp_path: Path,
) -> None:
    """Development authentication has no accounts and a provider administers
    its own. The permission is not the only condition."""

    async def exercise() -> None:
        async with _client(_app("administrator", tmp_path, store=False)) as client:
            session = await client.get(
                "/api/v1/_tide/session", headers=_authorization()
            )
            users = await client.get(
                "/api/v1/_tide/administration/users", headers=_authorization()
            )
        assert session.json()["administration"] is False
        assert users.status_code == 404

    asyncio.run(exercise())


def test_the_routes_read_and_change_the_accounts(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _client(_app("administrator", tmp_path)) as client:
            listed = await client.get(
                "/api/v1/_tide/administration/users", headers=_authorization()
            )
            roles = await client.get(
                "/api/v1/_tide/administration/roles", headers=_authorization()
            )
            created = await client.post(
                "/api/v1/_tide/administration/users",
                headers=_authorization(),
                json={
                    "username": "New.Clerk",
                    "password": PASSWORD,
                    "roles": ["sales_clerk"],
                    "display_name": "New Clerk",
                },
            )
            regraded = await client.patch(
                "/api/v1/_tide/administration/users/new.clerk",
                headers=_authorization(),
                json={"roles": ["auditor"]},
            )
            disabled = await client.patch(
                "/api/v1/_tide/administration/users/new.clerk",
                headers=_authorization(),
                json={"enabled": False},
            )
            reset = await client.post(
                "/api/v1/_tide/administration/users/new.clerk/password",
                headers=_authorization(),
                json={"password": "another long enough passphrase"},
            )

        assert listed.status_code == 200
        body = listed.json()
        assert [user["username"] for user in body["users"]] == ["admin", "clerk"]
        assert body["truncated"] is False
        assert body["users"][1]["roles"] == ["sales_clerk"]
        assert body["users"][0]["enabled"] is True

        catalogue = {role["name"]: role["grants"] for role in roles.json()["roles"]}
        assert catalogue["administrator"] == ["tide.users.administer"]
        assert "sales.invoice.post" in catalogue["sales_clerk"]

        assert created.status_code == 201
        assert created.json()["username"] == "new.clerk"
        assert created.json()["display_name"] == "New Clerk"
        # The whole key set, not a search for the word: an account on the wire
        # carries when its password last changed and nothing else about it.
        assert set(created.json()) == {
            "username",
            "display_name",
            "enabled",
            "roles",
            "created_at",
            "password_changed_at",
        }

        assert regraded.json()["roles"] == ["auditor"]
        assert disabled.json()["enabled"] is False
        assert reset.status_code == 204

    asyncio.run(exercise())


def test_the_routes_refuse_a_caller_without_the_permission(
    tmp_path: Path,
) -> None:
    """Every way in, not the listing alone."""

    async def exercise() -> None:
        async with _client(_app("sales_clerk", tmp_path)) as client:
            calls = [
                await client.get(
                    "/api/v1/_tide/administration/users", headers=_authorization()
                ),
                await client.get(
                    "/api/v1/_tide/administration/roles", headers=_authorization()
                ),
                await client.post(
                    "/api/v1/_tide/administration/users",
                    headers=_authorization(),
                    json={
                        "username": "intruder",
                        "password": PASSWORD,
                        "roles": ["sales_clerk"],
                    },
                ),
                await client.patch(
                    "/api/v1/_tide/administration/users/clerk",
                    headers=_authorization(),
                    json={"roles": ["administrator"]},
                ),
                await client.post(
                    "/api/v1/_tide/administration/users/clerk/password",
                    headers=_authorization(),
                    json={"password": PASSWORD},
                ),
            ]
        assert [call.status_code for call in calls] == [403, 403, 403, 403, 403]

    asyncio.run(exercise())


def test_the_routes_need_an_identity_at_all(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _client(_app("administrator", tmp_path)) as client:
            response = await client.get("/api/v1/_tide/administration/users")
        assert response.status_code == 401

    asyncio.run(exercise())


def test_the_routes_answer_conflicts_and_unknown_names_apart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _client(_app("administrator", tmp_path)) as client:
            duplicate = await client.post(
                "/api/v1/_tide/administration/users",
                headers=_authorization(),
                json={
                    "username": "clerk",
                    "password": PASSWORD,
                    "roles": ["sales_clerk"],
                },
            )
            last_administrator = await client.patch(
                "/api/v1/_tide/administration/users/admin",
                headers=_authorization(),
                json={"enabled": False},
            )
            unknown_user = await client.patch(
                "/api/v1/_tide/administration/users/nobody",
                headers=_authorization(),
                json={"enabled": False},
            )
            unknown_role = await client.patch(
                "/api/v1/_tide/administration/users/clerk",
                headers=_authorization(),
                json={"roles": ["warehouse"]},
            )
            nothing_asked = await client.patch(
                "/api/v1/_tide/administration/users/clerk",
                headers=_authorization(),
                json={},
            )
            short_password = await client.post(
                "/api/v1/_tide/administration/users/clerk/password",
                headers=_authorization(),
                json={"password": "tiny"},
            )

        assert duplicate.status_code == 409
        assert last_administrator.status_code == 409
        assert "administer" in last_administrator.json()["message"]
        assert unknown_user.status_code == 404
        assert unknown_role.status_code == 400
        assert "warehouse" in unknown_role.json()["message"]
        assert nothing_asked.status_code == 400
        assert short_password.status_code == 400
        # The refusal describes the policy, never the value it refused.
        assert "tiny" not in short_password.text

    asyncio.run(exercise())


def test_every_change_is_recorded_without_the_credential(tmp_path: Path) -> None:
    """An administration change is one of the few things worth finding in a
    log a year later -- and a password is one of the few things that must
    never be in one."""

    logger, handler = _recording_logger()

    async def exercise() -> None:
        async with _client(
            _app("administrator", tmp_path, logger=logger)
        ) as client:
            await client.post(
                "/api/v1/_tide/administration/users",
                headers=_authorization(),
                json={
                    "username": "auditor.two",
                    "password": PASSWORD,
                    "roles": ["auditor"],
                },
            )
            await client.patch(
                "/api/v1/_tide/administration/users/auditor.two",
                headers=_authorization(),
                json={"enabled": False},
            )
            await client.post(
                "/api/v1/_tide/administration/users/auditor.two/password",
                headers=_authorization(),
                json={"password": "yet another long passphrase"},
            )

    asyncio.run(exercise())

    events = [
        record
        for record in handler.records
        if str(getattr(record, "tide_event", "")).startswith("administration.")
    ]
    assert [record.tide_event for record in events] == [
        "administration.user_created",
        "administration.user_updated",
        "administration.password_reset",
    ]
    written = " ".join(str(record.__dict__) for record in events)
    assert "auditor.two" in written
    assert "api:test" in written
    assert PASSWORD not in written
    assert "passphrase" not in written


def _app(
    role: str,
    tmp_path: Path,
    *,
    store: bool = True,
    logger: logging.Logger | None = None,
) -> Any:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    browser_auth = (
        LocalPasswordAuth(
            _store(tmp_path),
            allowed_roles=model.roles,
            secure_cookie=False,
        )
        if store
        else None
    )
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({role})),
        ),
        actions=actions,
        logger=logger,
        browser_auth=browser_auth,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _recording_logger() -> tuple[logging.Logger, _RecordingHandler]:
    logger = logging.Logger("tide.test.runtime", level=logging.DEBUG)
    handler = _RecordingHandler()
    logger.addHandler(handler)
    return logger, handler


def _administration(tmp_path: Path) -> UserAdministration:
    model = compile_project(INVOICING)
    assert ADMINISTER in model.permissions
    return UserAdministration(
        _store(tmp_path),
        model,
        SecurityEngine(model),
    )


def _context(*roles: str) -> RequestContext:
    return RequestContext(principal=Principal("local:test", roles=frozenset(roles)))


def _store(tmp_path: Path) -> LocalUserStore:
    store = LocalUserStore(
        tmp_path / "auth.sqlite3",
        application="TIDE Invoicing",
        password_iterations=1_000,
    )
    store.initialize()
    store.create_user("admin", PASSWORD, roles={"administrator"})
    store.create_user("clerk", PASSWORD, roles={"sales_clerk"})
    return store
