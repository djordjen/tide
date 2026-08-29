"""Shared exact-value wire conversion used by REST and MCP adapters."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from tide.runtime.errors import QueryFieldError
from tide.api.contracts import TideAuditEvent, TideAuditFieldChange
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.security import PROTECTED
from tide.services import (
    ActionAuditEvent,
    NO_REFERENCE_DISPLAYS,
    RecordAuditEvent,
    ReferenceDisplays,
)


def wire_record(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
    displays: ReferenceDisplays = NO_REFERENCE_DISPLAYS,
) -> dict[str, Any]:
    """Project a secured record with structured protected-field metadata.

    ``displays`` carries how the records this one points at name themselves,
    resolved once for the whole page. A reference with no entry simply gets
    none: the client still has the identity and can ask, which is what it
    did for every reference before any of this was resolved server-side.
    """

    result: dict[str, Any] = {}
    protected: list[str] = []
    references: dict[str, str] = {}
    for field_name, field in entity.fields.items():
        value = values.get(field_name)
        if value is PROTECTED:
            result[field_name] = None
            protected.append(field_name)
        elif field.metadata["type"] == "collection" and field.target_entity:
            target = model.entity(field.target_entity)
            result[field_name] = [
                wire_record(model, target, child, displays)
                for child in (value or ())
            ]
        else:
            result[field_name] = value
            if field.metadata["type"] == "reference" and field.target_entity:
                display = displays.display(field.target_entity, value)
                if display is not None:
                    references[field_name] = display
    if protected:
        result.setdefault("_tide", {})["protected_fields"] = protected
    if references:
        result.setdefault("_tide", {})["references"] = references
    return result


def wire_audit_event(event: ActionAuditEvent | RecordAuditEvent) -> TideAuditEvent:
    """Project one stored audit event without exposing protected raw values."""

    if isinstance(event, ActionAuditEvent):
        return TideAuditEvent(
            event_id=event.event_id,
            entity=event.entity,
            kind="action",
            action=event.action,
            identity=event.identity,
            principal=event.principal,
            channel=event.channel,
            correlation_id=event.correlation_id,
            started_at=event.started_at,
            outcome=str(event.outcome),
            finished_at=event.finished_at,
            error_code=event.error_code,
        )
    return TideAuditEvent(
        event_id=event.event_id,
        entity=event.entity,
        kind="record",
        operation=str(event.operation),
        identity=event.identity,
        principal=event.principal,
        channel=event.channel,
        correlation_id=event.correlation_id,
        started_at=event.occurred_at,
        source=event.source,
        changes=tuple(
            TideAuditFieldChange(
                field=change.field,
                before_present=change.before_present,
                after_present=change.after_present,
                value_mode=str(change.value_mode),
                before=change.before,
                after=change.after,
            )
            for change in event.changes
        ),
    )


def primary_key(entity: NormalizedEntity) -> NormalizedField:
    return entity.primary_key


def coerce_identity(
    model: ApplicationModel,
    field: NormalizedField,
    value: Any,
) -> Any:
    field_type = str(field.metadata["type"])
    if field_type == "reference" and field.target_entity:
        return coerce_identity(model, primary_key(model.entity(field.target_entity)), value)
    if field_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ValueError
        return int(value)
    if field_type in {"string", "choice"}:
        return str(value)
    if field_type == "decimal":
        return value if isinstance(value, Decimal) else Decimal(str(value))
    if field_type == "uuid":
        # A malformed identity in a URL is a bad request, not a server fault,
        # which is the distinction between the ValueError and the TypeError.
        return value if isinstance(value, UUID) else UUID(str(value))
    raise TypeError


def decode_filter_value(
    model: ApplicationModel,
    entity: NormalizedEntity,
    field_name: str,
    value: Any,
    operator: str = "eq",
) -> Any:
    if field_name not in entity.fields:
        raise QueryFieldError(f"unknown query field {field_name!r}")
    if operator == "in":
        # Membership takes a list of typed elements; a null element means
        # blanks count as chosen. The list itself must be a list -- one
        # string is one value, not a set of characters.
        if not isinstance(value, list) or not value:
            raise QueryFieldError(
                f"'in' filter for {field_name!r} requires a non-empty list"
            )
        return tuple(
            decode_wire_value(model, entity.field(field_name), element)
            for element in value
        )
    return decode_wire_value(model, entity.field(field_name), value)


def decode_wire_value(
    model: ApplicationModel,
    field: NormalizedField,
    value: Any,
) -> Any:
    if value is None:
        return None
    field_type = str(field.metadata["type"])
    if field_type == "reference":
        if field.target_entity is None:
            raise TypeError
        return decode_wire_value(
            model,
            primary_key(model.entity(field.target_entity)),
            value,
        )
    if field_type == "collection":
        if field.target_entity is None or not isinstance(value, list):
            raise TypeError
        target = model.entity(field.target_entity)
        if not all(isinstance(item, Mapping) for item in value):
            raise TypeError
        decoded: list[dict[str, Any]] = []
        for item in value:
            unknown = set(item) - set(target.fields)
            if unknown:
                raise ValueError(
                    f"unknown draft field(s): {', '.join(sorted(unknown))}"
                )
            decoded.append(
                {
                    name: decode_wire_value(model, target.field(name), child)
                    for name, child in item.items()
                }
            )
        return decoded
    if field_type == "decimal":
        if not isinstance(value, str):
            raise TypeError
        return Decimal(value)
    if field_type == "date":
        if not isinstance(value, str):
            raise TypeError
        from datetime import date

        return date.fromisoformat(value)
    if field_type == "datetime":
        if not isinstance(value, str):
            raise TypeError
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if field_type == "uuid":
        if not isinstance(value, str):
            raise TypeError
        return UUID(value)
    if field_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError
        return value
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise TypeError
        return value
    if field_type in {"string", "choice"}:
        if not isinstance(value, str):
            raise TypeError
        return value
    if field_type == "file":
        # A write names the attachment by its key. What the file *is* travels
        # the other way, as the record's projection of that key.
        if not isinstance(value, str):
            raise TypeError
        return value
    raise TypeError
