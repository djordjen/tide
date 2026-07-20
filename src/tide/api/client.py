"""Typed synchronous client for a remote TIDE application server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import httpx
from pydantic import ValidationError

from tide.api.contracts import (
    TideAuditHistory,
    TideFilterInput,
    TideQueryInput,
    TideReferenceSelectionInput,
    TideReferenceSelectionResult,
    TideReportDocument,
    TideSessionInfo,
    TideSortInput,
)
from tide.api.openapi import DEFAULT_BASE_PATH, REST_OPERATIONS, rest_exposures
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data import QuerySpec
from tide.runtime import TideRuntimeError
from tide.reporting.document import (
    ReportCell,
    ReportColumn,
    ReportDocument,
    ReportTable,
    ReportValue,
)
from tide.security import PROTECTED
from tide.services import (
    ActionAuditEvent,
    AuditEvent,
    AuditFieldChange,
    AuditOutcome,
    AuditValueMode,
    RecordAuditEvent,
    RecordAuditOperation,
)


CLIENT_OPERATIONS = REST_OPERATIONS
_STRONG_ETAG = re.compile(r'^"\d+"$')


@dataclass(frozen=True, slots=True)
class TideApiRecord:
    """A secured record plus the HTTP version observed by the client."""

    values: dict[str, Any]
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class TideApiPage:
    """One remote page and its opaque continuation cursor."""

    records: tuple[dict[str, Any], ...]
    next_cursor: str | None = None


class TideApiClientError(TideRuntimeError):
    """A stable error returned by the remote TIDE service."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class TideApiTransportError(TideRuntimeError):
    """The remote service could not be reached safely."""

    code = "api_unavailable"


class TideApiContractError(TideRuntimeError):
    """The local application and remote wire contract are incompatible."""

    code = "api_contract_error"


class TideApiClient:
    """Invoke TIDE HTTP routes without exposing persistence details to a client."""

    def __init__(
        self,
        model: ApplicationModel,
        base_url: str,
        token: str,
        *,
        base_path: str = DEFAULT_BASE_PATH,
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise ValueError("API bearer token must not be empty")
        self.model = model
        self.base_url = _validated_base_url(base_url)
        self.base_path = _normalized_base_path(base_path)
        self._token = token
        self._exposures = rest_exposures(
            model,
            allowed_operations=CLIENT_OPERATIONS,
        )
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )
        self._session: TideSessionInfo | None = None

    def __enter__(self) -> TideApiClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    @property
    def session(self) -> TideSessionInfo | None:
        return self._session

    def connect(self) -> TideSessionInfo:
        """Authenticate and reject a server compiled from a different application."""

        response = self._request(
            "GET",
            f"{self.base_path}/_tide/session",
            expected=(200,),
        )
        payload = self._json_object(response)
        try:
            session = TideSessionInfo.model_validate(payload)
        except ValidationError as error:
            raise TideApiContractError(
                "server returned an invalid TIDE session contract"
            ) from error
        expected = (self.model.name, self.model.version, self.model.schema_version)
        actual = (
            session.application,
            session.application_version,
            session.schema_version,
        )
        if actual != expected:
            raise TideApiContractError(
                "server application does not match the local compiled model: "
                f"expected {expected!r}, received {actual!r}"
            )
        self._session = session
        return session

    def list_records(
        self,
        entity_name: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> TideApiPage:
        resource = self._resource(entity_name, "list")
        parameters: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            parameters["cursor"] = cursor
        response = self._request(
            "GET",
            resource,
            expected=(200,),
            params=parameters,
        )
        payload = self._json_object(response)
        raw_records = payload.get("records")
        next_cursor = payload.get("next_cursor")
        if not isinstance(raw_records, list) or not (
            next_cursor is None or isinstance(next_cursor, str)
        ):
            raise TideApiContractError("server returned an invalid record page")
        entity = self.model.entity(entity_name)
        return TideApiPage(
            records=tuple(_decode_record(self.model, entity, item) for item in raw_records),
            next_cursor=next_cursor,
        )

    def get_record(self, entity_name: str, identity: Any) -> TideApiRecord:
        entity = self.model.entity(entity_name)
        resource = self._resource(entity_name, "get")
        response = self._request(
            "GET",
            f"{resource}/{_identity_segment(identity)}",
            expected=(200,),
        )
        return self._record_response(entity, response)

    def query_records(
        self,
        entity_name: str,
        query: QuerySpec,
    ) -> TideApiPage:
        """Execute a typed, secured query without putting values in the URL."""

        if query.after is not None:
            raise ValueError("query cursor boundaries are server-owned")
        entity = self.model.entity(entity_name)
        resource = self._resource(entity_name, "list")
        payload = TideQueryInput(
            filters=tuple(
                TideFilterInput(
                    field=item.field,
                    operator=item.operator,
                    value=_encode_generic(item.value),
                )
                for item in query.filters
            ),
            sort=tuple(
                TideSortInput(
                    field=item.field,
                    descending=item.descending,
                )
                for item in query.sort
            ),
            limit=query.limit,
            cursor=query.cursor,
        )
        response = self._request(
            "POST",
            f"{resource}/_query",
            expected=(200,),
            json=payload.model_dump(mode="json"),
        )
        body = self._json_object(response)
        raw_records = body.get("records")
        next_cursor = body.get("next_cursor")
        if not isinstance(raw_records, list) or not (
            next_cursor is None or isinstance(next_cursor, str)
        ):
            raise TideApiContractError("server returned an invalid record page")
        return TideApiPage(
            records=tuple(
                _decode_record(self.model, entity, item) for item in raw_records
            ),
            next_cursor=next_cursor,
        )

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: Mapping[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        """Ask the server to apply declarative on-select assignments to a draft."""

        entity = self.model.entity(entity_name)
        request = TideReferenceSelectionInput(
            entity=entity_name,
            field=field_name,
            values=_encode_draft(self.model, entity, values),
            identity=_encode_generic(identity),
        )
        response = self._request(
            "POST",
            f"{self.base_path}/_tide/reference-selection",
            expected=(200,),
            json=request.model_dump(mode="json"),
        )
        try:
            result = TideReferenceSelectionResult.model_validate(
                self._json_object(response)
            )
        except ValidationError as error:
            raise TideApiContractError(
                "server returned an invalid reference-selection result"
            ) from error
        updated = dict(values)
        updated.update(_decode_draft(self.model, entity, result.values))
        return updated

    def create_record(
        self,
        entity_name: str,
        values: Mapping[str, Any],
    ) -> TideApiRecord:
        entity = self.model.entity(entity_name)
        response = self._request(
            "POST",
            self._resource(entity_name, "create"),
            expected=(201,),
            json=_encode_record(self.model, entity, values),
        )
        return self._record_response(entity, response)

    def update_record(
        self,
        entity_name: str,
        identity: Any,
        values: Mapping[str, Any],
        *,
        if_match: str | int | None = None,
    ) -> TideApiRecord:
        entity = self.model.entity(entity_name)
        resource = self._resource(entity_name, "update")
        headers = _precondition_headers(if_match=if_match)
        response = self._request(
            "PATCH",
            f"{resource}/{_identity_segment(identity)}",
            expected=(200,),
            headers=headers,
            json=_encode_record(self.model, entity, values),
        )
        return self._record_response(entity, response)

    def delete_record(
        self,
        entity_name: str,
        identity: Any,
        *,
        if_match: str | int | None = None,
    ) -> None:
        resource = self._resource(entity_name, "delete")
        self._request(
            "DELETE",
            f"{resource}/{_identity_segment(identity)}",
            expected=(204,),
            headers=_precondition_headers(if_match=if_match),
        )

    def execute_action(
        self,
        entity_name: str,
        action_name: str,
        identity: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        if_match: str | int | None = None,
        idempotency_key: str | None = None,
    ) -> TideApiRecord:
        entity = self.model.entity(entity_name)
        action = entity.actions.get(action_name)
        if action is None or action.get("expose", {}).get("rest") is not True:
            raise TideApiContractError(
                f"action {entity_name}.{action_name} is not exposed through REST"
            )
        resource = self._resource_path(entity_name)
        headers = _precondition_headers(
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        response = self._request(
            "POST",
            f"{resource}/{_identity_segment(identity)}/actions/{quote(action_name, safe='')}",
            expected=(200,),
            headers=headers,
            json=_encode_generic(payload or {}),
        )
        return self._record_response(entity, response)

    def audit_history(
        self,
        entity_name: str,
        identity: Any,
        *,
        limit: int = 100,
    ) -> tuple[AuditEvent, ...]:
        """Return bounded safe action and CRUD history for one record."""

        if limit < 1 or limit > 500:
            raise ValueError("audit limit must be between 1 and 500")
        resource = self._resource(entity_name, "get")
        response = self._request(
            "GET",
            f"{resource}/{_identity_segment(identity)}/_audit",
            expected=(200,),
            params={"limit": limit},
        )
        try:
            history = TideAuditHistory.model_validate(self._json_object(response))
        except ValidationError as error:
            raise TideApiContractError(
                "server returned an invalid audit-history contract"
            ) from error
        entity = self.model.entity(entity_name)
        try:
            history_identity = _decode_field(
                self.model,
                _primary_key(entity),
                history.identity,
            )
        except (TypeError, ValueError, InvalidOperation) as error:
            raise TideApiContractError(
                "server returned an invalid audit-history identity"
            ) from error
        if history.entity != entity_name or history_identity != identity:
            raise TideApiContractError(
                "server returned audit history for a different record"
            )
        events: list[AuditEvent] = []
        for event in history.events:
            try:
                event_identity = _decode_field(
                    self.model,
                    _primary_key(entity),
                    event.identity,
                )
            except (TypeError, ValueError, InvalidOperation) as error:
                raise TideApiContractError(
                    "server returned an invalid audit-event identity"
                ) from error
            if event.entity != entity_name or event_identity != identity:
                raise TideApiContractError(
                    "server returned an audit event for a different record"
                )
            if event.started_at.tzinfo is None or event.started_at.utcoffset() is None:
                raise TideApiContractError(
                    "audit event start timestamp lacks a timezone"
                )
            if event.finished_at is not None and (
                event.finished_at.tzinfo is None
                or event.finished_at.utcoffset() is None
            ):
                raise TideApiContractError(
                    "audit event finish timestamp lacks a timezone"
                )
            if event.kind == "action":
                events.append(
                    ActionAuditEvent(
                        event_id=event.event_id,
                        entity=event.entity,
                        action=str(event.action),
                        identity=event_identity,
                        principal=event.principal,
                        channel=event.channel,
                        correlation_id=event.correlation_id,
                        started_at=event.started_at,
                        outcome=AuditOutcome(str(event.outcome)),
                        finished_at=event.finished_at,
                        error_code=event.error_code,
                    )
                )
                continue
            changes: list[AuditFieldChange] = []
            for change in event.changes:
                field = entity.fields.get(change.field)
                if field is None:
                    raise TideApiContractError(
                        "server returned audit details for an unknown field"
                    )
                before = change.before
                after = change.after
                if change.value_mode == "recorded":
                    try:
                        if change.before_present:
                            before = _decode_field(self.model, field, before)
                        if change.after_present:
                            after = _decode_field(self.model, field, after)
                    except (TypeError, ValueError, InvalidOperation) as error:
                        raise TideApiContractError(
                            "server returned an invalid audit field value"
                        ) from error
                changes.append(
                    AuditFieldChange(
                        field=change.field,
                        before_present=change.before_present,
                        after_present=change.after_present,
                        value_mode=AuditValueMode(change.value_mode),
                        before=before,
                        after=after,
                    )
                )
            events.append(
                RecordAuditEvent(
                    event_id=event.event_id,
                    entity=event.entity,
                    operation=RecordAuditOperation(str(event.operation)),
                    identity=event_identity,
                    principal=event.principal,
                    channel=event.channel,
                    correlation_id=event.correlation_id,
                    occurred_at=event.started_at,
                    source=str(event.source),
                    changes=tuple(changes),
                )
            )
        return tuple(events)

    def build_report_for_record(
        self,
        report_name: str,
        identity: Any,
    ) -> ReportDocument:
        """Build an authorized renderer-neutral report on the remote server."""

        report = self.model.reports.get(report_name)
        if report is None or report.get("expose", {}).get("rest") is not True:
            raise TideApiContractError(
                f"report {report_name!r} is not exposed through REST"
            )
        response = self._request(
            "GET",
            (
                f"{self.base_path}/_tide/reports/"
                f"{quote(report_name, safe='')}/records/{_identity_segment(identity)}"
            ),
            expected=(200,),
        )
        return self._report_document(response, report_name)

    def build_report(
        self,
        report_name: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ReportDocument:
        """Build an authorized renderer-neutral summary report."""

        report = self.model.reports.get(report_name)
        if (
            report is None
            or report.get("kind", "record") != "summary"
            or report.get("expose", {}).get("rest") is not True
        ):
            raise TideApiContractError(
                f"summary report {report_name!r} is not exposed through REST"
            )
        response = self._request(
            "POST",
            f"{self.base_path}/_tide/reports/{quote(report_name, safe='')}",
            json=dict(parameters or {}),
            expected=(200,),
        )
        return self._report_document(response, report_name)

    def _report_document(
        self,
        response: httpx.Response,
        report_name: str,
    ) -> ReportDocument:
        try:
            wire = TideReportDocument.model_validate(self._json_object(response))
        except ValidationError as error:
            raise TideApiContractError(
                "server returned an invalid report document"
            ) from error
        if wire.report != report_name:
            raise TideApiContractError(
                "server returned a different report than requested"
            )
        if wire.application != self.model.name:
            raise TideApiContractError(
                "report application does not match the connected application"
            )
        _validate_report_filename(wire.suggested_filename)
        if wire.generated_at.tzinfo is None or wire.generated_at.utcoffset() is None:
            raise TideApiContractError("report generation timestamp lacks a timezone")
        column_count = len(wire.detail.columns)
        if any(len(row) != column_count for row in wire.detail.rows):
            raise TideApiContractError(
                "report detail rows do not match the declared columns"
            )
        return ReportDocument(
            report=wire.report,
            title=wire.title,
            application=wire.application,
            generated_at=wire.generated_at,
            header_text=wire.header_text,
            record_values=tuple(
                ReportValue(value.label, value.text, value.alignment)
                for value in wire.record_values
            ),
            detail=ReportTable(
                columns=tuple(
                    ReportColumn(column.name, column.label, column.alignment)
                    for column in wire.detail.columns
                ),
                rows=tuple(
                    tuple(
                        ReportCell(cell.text, cell.alignment) for cell in row
                    )
                    for row in wire.detail.rows
                ),
            ),
            footer_values=tuple(
                ReportValue(value.label, value.text, value.alignment)
                for value in wire.footer_values
            ),
            page_footer_template=wire.page_footer_template,
            suggested_filename=wire.suggested_filename,
        )

    def _resource(self, entity_name: str, operation: str) -> str:
        exposure = self._exposures.get(entity_name)
        if exposure is None or operation not in exposure.operations:
            raise TideApiContractError(
                f"{entity_name}.{operation} is not exposed through REST"
            )
        return self._resource_path(entity_name)

    def _resource_path(self, entity_name: str) -> str:
        exposure = self._exposures.get(entity_name)
        if exposure is None:
            raise TideApiContractError(f"{entity_name} is not exposed through REST")
        return f"{self.base_path}/{exposure.path}"

    def _record_response(
        self,
        entity: NormalizedEntity,
        response: httpx.Response,
    ) -> TideApiRecord:
        return TideApiRecord(
            values=_decode_record(self.model, entity, self._json_object(response)),
            etag=response.headers.get("ETag"),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...],
        headers: Mapping[str, str] | None = None,
        **arguments: Any,
    ) -> httpx.Response:
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        request_headers.update(headers or {})
        try:
            response = self._http.request(
                method,
                path,
                headers=request_headers,
                **arguments,
            )
        except httpx.RequestError as error:
            raise TideApiTransportError(
                f"TIDE API request failed: {error.__class__.__name__}"
            ) from error
        if response.status_code in expected:
            return response
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            code = (
                str(payload.get("code"))
                if isinstance(payload, Mapping) and payload.get("code")
                else "http_error"
            )
            message = (
                str(payload.get("message"))
                if isinstance(payload, Mapping) and payload.get("message")
                else f"TIDE API returned HTTP {response.status_code}"
            )
            raise TideApiClientError(response.status_code, code, message)
        raise TideApiContractError(
            f"TIDE API returned unexpected HTTP {response.status_code}"
        )

    @staticmethod
    def _json_object(response: httpx.Response) -> Mapping[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise TideApiContractError("server response is not valid JSON") from error
        if not isinstance(payload, Mapping):
            raise TideApiContractError("server response must be a JSON object")
        return payload


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API URL must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API URL must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("API URL path must be configured with base_path instead")
    loopback = parsed.hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "http" and not loopback:
        raise ValueError("unencrypted API URLs are allowed only for loopback hosts")
    return value.rstrip("/")


def _normalized_base_path(value: str) -> str:
    normalized = "/" + value.strip("/")
    if normalized == "/" or "//" in normalized:
        raise ValueError("API base path must contain one or more path segments")
    return normalized


def _identity_segment(identity: Any) -> str:
    return quote(str(identity), safe="")


def _precondition_headers(
    *,
    if_match: str | int | None,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if if_match is not None:
        etag = f'"{if_match}"' if isinstance(if_match, int) else if_match
        if not _STRONG_ETAG.fullmatch(etag):
            raise ValueError('If-Match must be a strong integer ETag such as "3"')
        headers["If-Match"] = etag
    if idempotency_key is not None:
        if not idempotency_key.strip():
            raise ValueError("idempotency key must not be blank")
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _decode_record(
    model: ApplicationModel,
    entity: NormalizedEntity,
    raw: Any,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TideApiContractError(f"{entity.name} record must be a JSON object")
    metadata = raw.get("_tide")
    protected: set[str] = set()
    if metadata is not None:
        if not isinstance(metadata, Mapping) or not isinstance(
            metadata.get("protected_fields"), list
        ):
            raise TideApiContractError(f"{entity.name} protection metadata is invalid")
        protected = {str(name) for name in metadata["protected_fields"]}
        if not protected <= set(entity.fields):
            raise TideApiContractError(f"{entity.name} protects an unknown field")
    result: dict[str, Any] = {}
    for field_name, field in entity.fields.items():
        if field_name not in raw:
            raise TideApiContractError(
                f"{entity.name} response omitted field {field_name!r}"
            )
        if field_name in protected:
            result[field_name] = PROTECTED
            continue
        try:
            result[field_name] = _decode_field(model, field, raw[field_name])
        except (TypeError, ValueError, InvalidOperation) as error:
            raise TideApiContractError(
                f"{entity.name}.{field_name} has an invalid wire value"
            ) from error
    return result


def _decode_draft(
    model: ApplicationModel,
    entity: NormalizedEntity,
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    unknown = set(raw) - set(entity.fields)
    if unknown:
        raise TideApiContractError(
            f"{entity.name} draft contains unknown field(s): "
            + ", ".join(sorted(unknown))
        )
    result: dict[str, Any] = {}
    for field_name, value in raw.items():
        try:
            result[field_name] = _decode_field(
                model,
                entity.field(field_name),
                value,
            )
        except (TypeError, ValueError, InvalidOperation) as error:
            raise TideApiContractError(
                f"{entity.name}.{field_name} has an invalid draft wire value"
            ) from error
    return result


def _decode_field(model: ApplicationModel, field: NormalizedField, value: Any) -> Any:
    if value is None:
        return None
    field_type = str(field.metadata["type"])
    if field_type == "collection":
        if field.target_entity is None or not isinstance(value, list):
            raise TypeError
        target = model.entity(field.target_entity)
        return [_decode_record(model, target, item) for item in value]
    if field_type == "reference":
        if field.target_entity is None:
            raise TypeError
        return _decode_field(model, _primary_key(model.entity(field.target_entity)), value)
    if field_type == "decimal":
        if not isinstance(value, str):
            raise TypeError
        return Decimal(value)
    if field_type == "date":
        if not isinstance(value, str):
            raise TypeError
        return date.fromisoformat(value)
    if field_type == "datetime":
        if not isinstance(value, str):
            raise TypeError
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if field_type == "integer" and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        raise TypeError
    if field_type == "boolean" and not isinstance(value, bool):
        raise TypeError
    if field_type in {"string", "choice"} and not isinstance(value, str):
        raise TypeError
    return value


def _encode_record(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    unknown = set(values) - set(entity.fields)
    if unknown:
        raise ValueError(
            f"{entity.name} contains unknown field(s): {', '.join(sorted(unknown))}"
        )
    return {
        field_name: _encode_field(model, entity.field(field_name), value)
        for field_name, value in values.items()
    }


def _encode_draft(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        field_name: _encode_field(model, entity.field(field_name), value)
        for field_name, value in values.items()
        if field_name in entity.fields and value is not PROTECTED
    }


def _encode_field(model: ApplicationModel, field: NormalizedField, value: Any) -> Any:
    if value is PROTECTED:
        raise ValueError(f"protected field {field.name!r} cannot be sent to the server")
    if value is None:
        return None
    field_type = str(field.metadata["type"])
    if field_type == "collection":
        if field.target_entity is None or not isinstance(value, (list, tuple)):
            raise TypeError(f"collection field {field.name!r} requires a sequence")
        target = model.entity(field.target_entity)
        return [_encode_record(model, target, item) for item in value]
    if field_type == "reference":
        if field.target_entity is None:
            raise TypeError(f"reference field {field.name!r} has no target")
        return _encode_field(model, _primary_key(model.entity(field.target_entity)), value)
    return _encode_generic(value)


def _encode_generic(value: Any) -> Any:
    if value is PROTECTED:
        raise ValueError("protected values cannot be sent to the server")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _encode_generic(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_generic(child) for child in value]
    return value


def _primary_key(entity: NormalizedEntity) -> NormalizedField:
    return next(
        field for field in entity.fields.values() if field.metadata.get("primary_key")
    )


def _validate_report_filename(value: str) -> None:
    if (
        not value
        or len(value) > 160
        or re.fullmatch(r"[A-Za-z0-9._-]+", value) is None
        or value in {".", ".."}
    ):
        raise TideApiContractError("report suggested filename is unsafe")
    device = value.split(".", 1)[0].upper()
    if device in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(
        r"(?:COM|LPT)[1-9]",
        device,
    ):
        raise TideApiContractError("report suggested filename is unsafe")
