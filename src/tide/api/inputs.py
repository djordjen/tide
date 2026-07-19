"""Shared generated mutation-input models for REST and MCP adapters."""

from __future__ import annotations

from collections.abc import Collection
import re
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, create_model

from tide.api.openapi import writable_scalar_annotation
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField


def build_writable_models(
    model: ApplicationModel,
    operations_by_entity: Mapping[str, Collection[str]],
) -> tuple[dict[str, type[BaseModel]], dict[str, type[BaseModel]]]:
    """Generate strict create/update payloads for explicitly exposed operations."""

    create_models: dict[str, type[BaseModel]] = {}
    update_models: dict[str, type[BaseModel]] = {}
    nested_cache: dict[tuple[str, str], type[BaseModel]] = {}

    def nested_model(
        entity: NormalizedEntity,
        *,
        excluded_field: str | None,
        stack: frozenset[str],
    ) -> type[BaseModel]:
        cache_key = entity.name, excluded_field or ""
        cached = nested_cache.get(cache_key)
        if cached is not None:
            return cached
        if entity.name in stack:
            raise ValueError(
                f"writable collection cascade contains a cycle through {entity.name}"
            )
        fields = _writable_fields(
            model,
            entity,
            mode="nested",
            excluded_field=excluded_field,
            nested_factory=lambda target, inverse: nested_model(
                target,
                excluded_field=inverse,
                stack=stack | {entity.name},
            ),
        )
        generated = create_model(
            _component_name(entity.name, "NestedInput"),
            __config__=_input_model_config(),
            __module__=__name__,
            **fields,
        )
        nested_cache[cache_key] = generated
        return generated

    for entity_name, operations in operations_by_entity.items():
        entity = model.entity(entity_name)
        if "create" in operations:
            create_models[entity_name] = create_model(
                _component_name(entity_name, "CreateInput"),
                __config__=_input_model_config(),
                __module__=__name__,
                **_writable_fields(
                    model,
                    entity,
                    mode="create",
                    nested_factory=lambda target, inverse: nested_model(
                        target,
                        excluded_field=inverse,
                        stack=frozenset({entity.name}),
                    ),
                ),
            )
        if "update" in operations:
            update_models[entity_name] = create_model(
                _component_name(entity_name, "UpdateInput"),
                __config__=_input_model_config(),
                __module__=__name__,
                **_writable_fields(
                    model,
                    entity,
                    mode="update",
                    nested_factory=lambda target, inverse: nested_model(
                        target,
                        excluded_field=inverse,
                        stack=frozenset({entity.name}),
                    ),
                ),
            )
    return create_models, update_models


def _writable_fields(
    model: ApplicationModel,
    entity: NormalizedEntity,
    *,
    mode: str,
    nested_factory: Any,
    excluded_field: str | None = None,
) -> dict[str, tuple[Any, Any]]:
    result: dict[str, tuple[Any, Any]] = {}
    used_names: set[str] = set()
    for index, (field_name, field) in enumerate(entity.fields.items()):
        if field_name == excluded_field or not field_is_writable(field, mode):
            continue
        internal_name = _model_field_name(field_name, index, used_names)
        used_names.add(internal_name)
        metadata = field.metadata
        if metadata["type"] == "collection":
            if not field.target_entity:
                raise ValueError(
                    f"collection field {entity.name}.{field_name} has no target"
                )
            inverse = metadata.get("inverse")
            item_model = nested_factory(model.entity(field.target_entity), inverse)
            annotation: Any = list[item_model]
        else:
            annotation = writable_scalar_annotation(model, field)

        required = (
            mode in {"create", "nested"}
            and bool(metadata.get("required"))
            and "default" not in metadata
            and "default_factory" not in metadata
            and not metadata.get("primary_key")
        )
        default: Any = ... if required else None
        if not required:
            annotation = annotation | None
        result[internal_name] = (
            annotation,
            Field(
                default,
                alias=field_name,
                title=str(metadata.get("label") or _humanize(field_name)),
                description=_input_field_description(field, mode),
            ),
        )
    return result


def field_is_writable(field: NormalizedField, mode: str) -> bool:
    """Return whether metadata permits a field in this mutation input mode."""

    metadata = field.metadata
    if metadata.get("computed") or metadata.get("readonly"):
        return False
    if metadata.get("write", "normal") != "normal":
        return False
    if metadata.get("primary_key"):
        return mode == "nested"
    if metadata["type"] == "collection":
        required_cascade = "create" if mode in {"create", "nested"} else "update"
        return required_cascade in metadata.get("cascade", ())
    return True


def _input_model_config() -> ConfigDict:
    return ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        regex_engine="python-re",
    )


def _input_field_description(field: NormalizedField, mode: str) -> str | None:
    help_text = field.metadata.get("help")
    if help_text:
        return str(help_text)
    if field.metadata.get("primary_key") and mode == "nested":
        return "Existing child identity; omit for a new child."
    if field.metadata["type"] == "collection":
        return "Complete replacement for this writable child collection."
    return None


def _component_name(entity_name: str, suffix: str) -> str:
    return "".join(_pascal_case(part) for part in entity_name.split(".")) + suffix


def _model_field_name(
    source_name: str,
    index: int,
    used_names: set[str],
) -> str:
    candidate = source_name
    if (
        not candidate.isidentifier()
        or candidate.startswith("_")
        or hasattr(BaseModel, candidate)
        or candidate in used_names
    ):
        candidate = f"tide_field_{index}"
    while candidate in used_names:
        index += 1
        candidate = f"tide_field_{index}"
    return candidate


def _pascal_case(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", value) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").title()
