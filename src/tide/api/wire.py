"""Shared exact-value wire conversion used by REST and MCP adapters."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.security import PROTECTED


def wire_record(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a secured record with structured protected-field metadata."""

    result: dict[str, Any] = {}
    protected: list[str] = []
    for field_name, field in entity.fields.items():
        value = values.get(field_name)
        if value is PROTECTED:
            result[field_name] = None
            protected.append(field_name)
        elif field.metadata["type"] == "collection" and field.target_entity:
            target = model.entity(field.target_entity)
            result[field_name] = [
                wire_record(model, target, child) for child in (value or ())
            ]
        else:
            result[field_name] = value
    if protected:
        result["_tide"] = {"protected_fields": protected}
    return result


def primary_key(entity: NormalizedEntity) -> NormalizedField:
    return next(
        field for field in entity.fields.values() if field.metadata.get("primary_key")
    )


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
    raise TypeError


def decode_filter_value(
    model: ApplicationModel,
    entity: NormalizedEntity,
    field_name: str,
    value: Any,
) -> Any:
    if field_name not in entity.fields:
        raise ValueError(f"unknown query field {field_name!r}")
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
    raise TypeError
