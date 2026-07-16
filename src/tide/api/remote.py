"""Service-shaped facades over :mod:`tide.api.client` for remote renderers."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any, Mapping

from tide.api.client import TideApiClient, TideApiClientError
from tide.api.contracts import TideSessionInfo
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data import FilterCondition, QuerySpec, SortField
from tide.runtime import (
    AuthorizationError,
    RequestContext,
    ValidationFailed,
)
from tide.runtime.errors import ValidationIssue
from tide.security import PROTECTED
from tide.services import QueryPage
from tide.sessions import RecordSession


class RemoteSecurityView:
    """Advisory UI capabilities from the authenticated server session.

    The server independently authorizes every request. This view exists only so
    renderers can hide controls they already know the principal cannot use.
    """

    def __init__(self, model: ApplicationModel, session: TideSessionInfo) -> None:
        self.model = model
        self.session = session

    def can_access_entity(
        self,
        entity: NormalizedEntity,
        operation: str,
        _context: RequestContext,
    ) -> bool:
        capabilities = self.session.entities.get(entity.name)
        translated = "get" if operation == "read" else operation
        return bool(
            capabilities
            and (
                translated in capabilities.operations
                or translated in capabilities.draft_operations
            )
        )

    def can_read_field(
        self,
        entity_name: str,
        field_name: str,
        _context: RequestContext,
    ) -> bool:
        capabilities = self.session.entities.get(entity_name)
        return bool(capabilities and field_name in capabilities.readable_fields)

    def can_write_field(
        self,
        entity_name: str,
        field_name: str,
        _context: RequestContext,
    ) -> bool:
        capabilities = self.session.entities.get(entity_name)
        return bool(capabilities and field_name in capabilities.writable_fields)

    def can_execute_action(
        self,
        action: Mapping[str, Any],
        _context: RequestContext,
    ) -> bool:
        for entity_name, entity in self.model.entities.items():
            for action_name, candidate in entity.actions.items():
                if candidate is action or candidate == action:
                    capabilities = self.session.entities.get(entity_name)
                    return bool(
                        capabilities and action_name in capabilities.actions
                    )
        return False

    def has_permission(
        self,
        _context: RequestContext,
        _permission: str | None,
    ) -> bool:
        """Permissions are intentionally not disclosed by the session contract."""

        return False

    def can_access_report(
        self,
        _report: Mapping[str, Any],
        _context: RequestContext,
    ) -> bool:
        return False


class RemoteRecordsService:
    """RecordsService-compatible operations backed exclusively by HTTP."""

    def __init__(
        self,
        model: ApplicationModel,
        client: TideApiClient,
        session: TideSessionInfo,
    ) -> None:
        self.model = model
        self.client = client
        self.security = RemoteSecurityView(model, session)

    def create(
        self,
        entity_name: str,
        context: RequestContext,
        values: Mapping[str, Any] | None = None,
    ) -> RecordSession:
        entity = self.model.entity(entity_name)
        self._require(entity, "create", context)
        defaults: dict[str, Any] = {}
        for field_name, field in entity.fields.items():
            metadata = field.metadata
            if field.target_entity and metadata["type"] == "collection":
                defaults[field_name] = []
            elif metadata.get("default_factory") == "today":
                defaults[field_name] = date.today()
            elif "default" in metadata:
                defaults[field_name] = deepcopy(metadata["default"])
        initial = deepcopy(defaults)
        initial.update(deepcopy(dict(values or {})))
        version = _version_field(entity)
        return RecordSession(
            entity=entity_name,
            identity=initial.get(_primary_key(entity).name),
            original=defaults,
            values=initial,
            expected_version=initial.get(version.name) if version else None,
            is_new=True,
        )

    def begin_edit(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
    ) -> RecordSession:
        entity = self.model.entity(entity_name)
        self._require(entity, "read", context)
        self._require(entity, "update", context)
        remote = self.client.get_record(entity_name, identity)
        values = remote.values
        return RecordSession(
            entity=entity_name,
            identity=identity,
            original=deepcopy(values),
            values=deepcopy(values),
            expected_version=_etag_version(remote.etag, entity, values),
        )

    def get(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
    ) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        self._require(entity, "read", context)
        return self.client.get_record(entity_name, identity).values

    def query_page(
        self,
        entity_name: str,
        query: QuerySpec,
        context: RequestContext,
    ) -> QueryPage:
        entity = self.model.entity(entity_name)
        self._require(entity, "list", context)
        if query.after is not None:
            raise ValueError("query cursor boundaries are server-owned")
        remote = self.client.query_records(entity_name, query)
        return QueryPage(remote.records, remote.next_cursor)

    def lookup_records(
        self,
        entity_name: str,
        search_fields: tuple[str, ...],
        search_text: str,
        context: RequestContext,
        *,
        limit: int = 20,
    ) -> tuple[dict[str, Any], ...]:
        if not search_fields:
            raise ValueError("lookup search requires at least one field")
        if len(set(search_fields)) != len(search_fields):
            raise ValueError("lookup search fields must not be repeated")
        if limit < 1 or limit > 500:
            raise ValueError("lookup limit must be between 1 and 500")
        entity = self.model.entity(entity_name)
        primary_key = _primary_key(entity).name
        candidate = search_text.strip()
        sort = (SortField(search_fields[0]),)
        if not candidate:
            return self.query_page(
                entity_name,
                QuerySpec(sort=sort, limit=limit),
                context,
            ).records
        matches: dict[Any, dict[str, Any]] = {}
        for field_name in search_fields:
            page = self.query_page(
                entity_name,
                QuerySpec(
                    filters=(
                        FilterCondition(field_name, "icontains", candidate),
                    ),
                    sort=sort,
                    limit=limit,
                ),
                context,
            )
            for record in page.records:
                matches.setdefault(record[primary_key], record)
                if len(matches) >= limit:
                    return tuple(matches.values())
        return tuple(matches.values())

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: Mapping[str, Any],
        identity: Any,
        _context: RequestContext,
    ) -> dict[str, Any]:
        return self.client.apply_reference_selection(
            entity_name,
            field_name,
            values,
            identity,
        )

    def commit(
        self,
        session: RecordSession,
        context: RequestContext,
        **_arguments: Any,
    ) -> dict[str, Any]:
        session.ensure_active()
        entity = self.model.entity(session.entity)
        operation = "create" if session.is_new else "update"
        self._require(entity, operation, context)
        if session.is_new:
            payload = _mutation_payload(
                self.model,
                entity,
                session.values,
                operation="create",
            )
            remote = self._translate_validation(
                lambda: self.client.create_record(entity.name, payload)
            )
        else:
            changed = {
                field_name: session.values.get(field_name)
                for field_name in session.changed_fields
            }
            payload = _mutation_payload(
                self.model,
                entity,
                changed,
                operation="update",
            )
            if not payload:
                stored = deepcopy(session.original)
                session.mark_committed(stored)
                return stored
            remote = self._translate_validation(
                lambda: self.client.update_record(
                    entity.name,
                    session.identity,
                    payload,
                    if_match=session.expected_version,
                )
            )
        stored = remote.values
        session.identity = stored[_primary_key(entity).name]
        session.expected_version = _etag_version(remote.etag, entity, stored)
        session.mark_committed(stored)
        return stored

    def rollback(self, session: RecordSession) -> None:
        session.rollback()

    def _require(
        self,
        entity: NormalizedEntity,
        operation: str,
        context: RequestContext,
    ) -> None:
        if not self.security.can_access_entity(entity, operation, context):
            raise AuthorizationError(
                f"remote principal may not {operation} {entity.name}"
            )

    @staticmethod
    def _translate_validation(operation: Any) -> Any:
        try:
            return operation()
        except TideApiClientError as error:
            if error.code == "validation_failed":
                raise ValidationFailed(
                    [ValidationIssue("remote", str(error))]
                ) from error
            raise


class RemoteActionService:
    """ActionService-compatible execution over the TIDE HTTP client."""

    def __init__(self, client: TideApiClient) -> None:
        self.client = client

    def execute(
        self,
        entity_name: str,
        action_name: str,
        identity: Any,
        payload: Mapping[str, Any],
        _context: RequestContext,
        *,
        idempotency_key: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        try:
            return self.client.execute_action(
                entity_name,
                action_name,
                identity,
                payload,
                if_match=expected_version,
                idempotency_key=idempotency_key,
            ).values
        except TideApiClientError as error:
            if error.code == "validation_failed":
                raise ValidationFailed(
                    [ValidationIssue("remote", str(error))]
                ) from error
            raise


class RemoteReportService:
    """Fail-closed placeholder until reports receive an explicit HTTP contract."""

    def can_generate(
        self,
        _report_name: str,
        _context: RequestContext,
    ) -> bool:
        return False

    def build_for_record(self, *_arguments: Any, **_keywords: Any) -> Any:
        raise ValueError("reports are not exposed by the remote TIDE server yet")


def _mutation_payload(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
    *,
    operation: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field_name, value in values.items():
        if field_name not in entity.fields or value is PROTECTED:
            continue
        field = entity.field(field_name)
        metadata = field.metadata
        if (
            metadata.get("primary_key")
            or metadata.get("computed")
            or metadata.get("readonly")
            or metadata.get("write", "normal") != "normal"
        ):
            continue
        if metadata["type"] == "collection":
            if operation not in metadata.get("cascade", ()) or not field.target_entity:
                continue
            target = model.entity(field.target_entity)
            inverse = metadata.get("inverse")
            result[field_name] = [
                _nested_payload(model, target, item, excluded_field=inverse)
                for item in (value or ())
            ]
        else:
            result[field_name] = deepcopy(value)
    return result


def _nested_payload(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
    *,
    excluded_field: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field_name, value in values.items():
        if field_name == excluded_field or field_name not in entity.fields:
            continue
        field = entity.field(field_name)
        metadata = field.metadata
        if value is PROTECTED or metadata.get("computed"):
            continue
        if metadata.get("primary_key"):
            if value is not None:
                result[field_name] = deepcopy(value)
            continue
        if metadata.get("readonly") or metadata.get("write", "normal") != "normal":
            continue
        if metadata["type"] == "collection":
            if "update" not in metadata.get("cascade", ()) or not field.target_entity:
                continue
            target = model.entity(field.target_entity)
            result[field_name] = [
                _nested_payload(
                    model,
                    target,
                    item,
                    excluded_field=metadata.get("inverse"),
                )
                for item in (value or ())
            ]
        else:
            result[field_name] = deepcopy(value)
    return result


def _primary_key(entity: NormalizedEntity) -> NormalizedField:
    return next(
        field for field in entity.fields.values() if field.metadata.get("primary_key")
    )


def _version_field(entity: NormalizedEntity) -> NormalizedField | None:
    return next(
        (
            field
            for field in entity.fields.values()
            if field.metadata.get("concurrency_token")
        ),
        None,
    )


def _etag_version(
    etag: str | None,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> int | None:
    if etag and len(etag) >= 3 and etag.startswith('"') and etag.endswith('"'):
        try:
            return int(etag[1:-1])
        except ValueError:
            pass
    version = _version_field(entity)
    value = values.get(version.name) if version else None
    return int(value) if value is not None else None
