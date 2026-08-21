"""Administering the identities TIDE owns.

Roles and permissions are compiled from the application's metadata and are not
editable at runtime. What this administers is *assignment*: which identity
holds which declared role, and whether an account may sign in at all. Creating
a role, or changing what one grants, is an authoring change that goes through
the compiler like every other.

It exists exactly where TIDE owns the identities -- the local username and
password store. Development authentication has no accounts, and an identity
provider administers its own; in both, this has nothing to reach.

The permission is `tide.users.administer`, declared by the application and
granted through a role like any other, so the one role expansion in
:class:`~tide.security.SecurityEngine` still decides everything. Nothing here
is a second authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from tide.api.local_auth import (
    LocalAuthenticationError,
    LocalUserStore,
    LocalUserSummary,
    normalize_username,
    validate_password,
)
from tide.compiler.normalized import ApplicationModel
from tide.model.source import FRAMEWORK_PERMISSIONS
from tide.runtime import Principal, RequestContext
from tide.security import SecurityEngine

ADMINISTER = "tide.users.administer"
assert ADMINISTER in FRAMEWORK_PERMISSIONS

#: How many accounts one listing answers with. A local store belongs to a
#: deployment small enough to keep its identities in a file, so this is a
#: bound on the response rather than a page: reaching it is reported.
MAX_LISTED_USERS = 500


class AdministrationError(ValueError):
    """An administration request was refused for a reason worth reading."""


class AdministrationDenied(AdministrationError):
    """The caller may not administer identities here."""


class AdministrationConflict(AdministrationError):
    """The store's current state refuses this, rather than the request being
    malformed: an account that already exists, or the last way back in."""


class UnknownLocalUser(AdministrationError):
    """The named account does not exist in this store."""


@dataclass(frozen=True, slots=True)
class RoleGrants:
    """One compiled role and the permissions it carries."""

    name: str
    grants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UserListing:
    """The accounts in this store, and whether the bound was reached."""

    users: tuple[LocalUserSummary, ...]
    truncated: bool


class UserAdministration:
    """Assignment of compiled roles to the identities TIDE owns."""

    def __init__(
        self,
        store: LocalUserStore,
        model: ApplicationModel,
        security: SecurityEngine,
        *,
        max_listed_users: int = MAX_LISTED_USERS,
    ) -> None:
        if (
            isinstance(max_listed_users, bool)
            or not isinstance(max_listed_users, int)
            or max_listed_users <= 0
        ):
            raise ValueError("listed user maximum must be a positive integer")
        self.store = store
        self.model = model
        self.security = security
        self.max_listed_users = max_listed_users

    def can_administer(self, context: RequestContext) -> bool:
        return self.security.has_permission(context, ADMINISTER)

    def roles(self, context: RequestContext) -> tuple[RoleGrants, ...]:
        """Every compiled role, with what it grants. Read-only, deliberately.

        Authorized like everything else here: what each role can do is a map
        of the application's security, and an administrator is who needs it.
        """

        self._authorize(context)
        return tuple(
            RoleGrants(name=name, grants=tuple(self.model.roles[name]))
            for name in sorted(self.model.roles)
        )

    def list_users(self, context: RequestContext) -> UserListing:
        self._authorize(context)
        found = self.store.list_users(limit=self.max_listed_users + 1)
        return UserListing(
            users=found[: self.max_listed_users],
            truncated=len(found) > self.max_listed_users,
        )

    def user(self, context: RequestContext, username: str) -> LocalUserSummary:
        self._authorize(context)
        return self._require(username)

    def create_user(
        self,
        context: RequestContext,
        *,
        username: str,
        password: str,
        roles: Iterable[str],
        display_name: str | None = None,
    ) -> LocalUserSummary:
        self._authorize(context)
        checked = self._checked_roles(roles)
        try:
            normalized = normalize_username(username)
            validate_password(password)
        except ValueError as error:
            raise AdministrationError(str(error)) from error
        try:
            self.store.create_user(
                normalized,
                password,
                roles=checked,
                display_name=display_name,
            )
        except LocalAuthenticationError as error:
            raise AdministrationConflict(str(error)) from error
        except ValueError as error:
            raise AdministrationError(str(error)) from error
        return self._require(normalized)

    def set_roles(
        self,
        context: RequestContext,
        username: str,
        roles: Iterable[str],
    ) -> LocalUserSummary:
        self._authorize(context)
        checked = self._checked_roles(roles)
        current = self._require(username)
        if (
            current.enabled
            and self._administers(current.roles)
            and not self._administers(checked)
        ):
            self._require_another_administrator(current.username)
        try:
            self.store.set_roles(current.username, checked)
        except (LocalAuthenticationError, ValueError) as error:
            raise AdministrationError(str(error)) from error
        return self._require(current.username)

    def set_enabled(
        self,
        context: RequestContext,
        username: str,
        enabled: bool,
    ) -> LocalUserSummary:
        self._authorize(context)
        current = self._require(username)
        if not enabled and current.enabled and self._administers(current.roles):
            self._require_another_administrator(current.username)
        try:
            self.store.set_enabled(current.username, enabled)
        except (LocalAuthenticationError, ValueError) as error:
            raise AdministrationError(str(error)) from error
        return self._require(current.username)

    def set_password(
        self,
        context: RequestContext,
        username: str,
        password: str,
    ) -> None:
        """Replace an account's password, ending the sessions it opened.

        The store stamps a session with a digest of the hash it was issued
        against, so this signs the account out everywhere as a consequence of
        the reset rather than as a second step somebody has to remember.
        """

        self._authorize(context)
        current = self._require(username)
        try:
            validate_password(password)
        except ValueError as error:
            raise AdministrationError(str(error)) from error
        try:
            self.store.set_password(current.username, password)
        except (LocalAuthenticationError, ValueError) as error:
            raise AdministrationError(str(error)) from error

    def _authorize(self, context: RequestContext) -> None:
        if not self.can_administer(context):
            raise AdministrationDenied(
                f"{context.principal.identifier!r} may not administer identities"
            )

    def _checked_roles(self, roles: Iterable[str]) -> tuple[str, ...]:
        """Every role must name one the application compiled.

        The store deliberately knows nothing about the model, so this is where
        a role that does not exist is caught -- and the refusal lists what the
        application does define, because the alternative is a caller guessing.
        """

        named: list[str] = []
        for role in roles:
            if not isinstance(role, str) or not role.strip():
                raise AdministrationError("a role must be a non-empty name")
            if role.strip() not in named:
                named.append(role.strip())
        if not named:
            raise AdministrationError("an account must keep at least one role")
        unknown = sorted(role for role in named if role not in self.model.roles)
        if unknown:
            defined = ", ".join(sorted(self.model.roles))
            raise AdministrationError(
                f"{self.model.name} defines no role "
                f"{', '.join(repr(role) for role in unknown)}; "
                f"roles it defines: {defined}"
            )
        return tuple(named)

    def _administers(self, roles: Sequence[str] | frozenset[str]) -> bool:
        return ADMINISTER in self.security.effective_permissions(
            Principal("local:candidate", roles=frozenset(roles))
        )

    def _require_another_administrator(self, username: str) -> None:
        remaining = {
            user.username
            for user in self.store.list_users()
            if user.enabled and self._administers(user.roles)
        } - {username}
        if not remaining:
            raise AdministrationConflict(
                f"{username!r} is the only enabled account that may administer "
                "identities; grant another account the administering role first"
            )

    def _require(self, username: str) -> LocalUserSummary:
        try:
            normalized = normalize_username(username)
        except ValueError as error:
            raise UnknownLocalUser(str(error)) from error
        for user in self.store.list_users():
            if user.username == normalized:
                return user
        raise UnknownLocalUser(f"local user {normalized!r} does not exist")
