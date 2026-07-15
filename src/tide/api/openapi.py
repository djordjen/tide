"""Generate a read-only OpenAPI preview from a compiled application model.

The preview intentionally has no dependency on FastAPI.  It gives adapters and
developers one deterministic contract while the HTTP runtime remains a later
milestone.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Annotated, Any, ForwardRef, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.json_schema import models_json_schema

from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField

OPENAPI_VERSION = "3.1.0"
DEFAULT_BASE_PATH = "/api/v1"
READ_OPERATIONS = frozenset({"list", "get"})


class TideProtectionMetadata(BaseModel):
    """Wire metadata distinguishing protected values from genuine nulls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protected_fields: list[str] = Field(min_length=1)


class TideApiError(BaseModel):
    """Stable error envelope available to generated adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class OpenApiPreview:
    """Generated document and the Pydantic models used to describe it."""

    document: dict[str, Any]
    record_models: Mapping[str, type[BaseModel]]
    page_models: Mapping[str, type[BaseModel]]

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self.document)


@dataclass(frozen=True, slots=True)
class _RestExposure:
    path: str
    operations: frozenset[str]


def build_openapi_preview(
    model: ApplicationModel,
    *,
    base_path: str = DEFAULT_BASE_PATH,
) -> OpenApiPreview:
    """Build read-only Pydantic models and an OpenAPI 3.1 preview.

    Entity exposure remains deny-by-default.  Boolean ``rest: true`` is a safe
    shorthand for both read operations; mapping exposure honors only explicitly
    listed ``list`` and ``get`` operations.  Mutation and action routes are
    deliberately omitted from this milestone even when their future exposure is
    declared in the application model.
    """

    normalized_base_path = _normalize_base_path(base_path)
    exposures = _rest_exposures(model)
    record_models = _build_record_models(model)
    page_models = _build_page_models(record_models, exposures)
    schemas = _component_schemas(record_models, page_models, exposures)
    paths = _paths(
        model,
        exposures,
        normalized_base_path,
        record_models,
        page_models,
    )
    document: dict[str, Any] = {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": f"{model.name} API",
            "version": model.version,
            "description": (
                "Read-only TIDE API preview generated from the compiled application "
                "model. Runtime HTTP hosting is not enabled by this document."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": (
                        "The hosting adapter maps the authenticated identity to a TIDE "
                        "Principal and reauthorizes every operation."
                    ),
                }
            },
        },
        "x-tide": {
            "preview": True,
            "read_only": True,
            "schema_version": model.schema_version,
        },
    }
    return OpenApiPreview(
        document=document,
        record_models=MappingProxyType(record_models),
        page_models=MappingProxyType(page_models),
    )


def generate_openapi(
    model: ApplicationModel,
    *,
    base_path: str = DEFAULT_BASE_PATH,
) -> dict[str, Any]:
    """Return just the generated OpenAPI document."""

    return build_openapi_preview(model, base_path=base_path).as_dict()


def _build_record_models(
    model: ApplicationModel,
) -> dict[str, type[BaseModel]]:
    component_names = {
        entity_name: _component_name(entity_name, "Record")
        for entity_name in model.entities
    }
    if len(set(component_names.values())) != len(component_names):
        raise ValueError("entity names produce duplicate API component names")

    result: dict[str, type[BaseModel]] = {}
    for entity_name, entity in model.entities.items():
        fields: dict[str, tuple[Any, Any]] = {}
        used_names: set[str] = set()
        for index, (field_name, field) in enumerate(entity.fields.items()):
            internal_name = _model_field_name(field_name, index, used_names)
            used_names.add(internal_name)
            annotation = _field_annotation(model, field, component_names) | None
            fields[internal_name] = (
                annotation,
                Field(
                    ...,
                    alias=field_name,
                    title=str(field.metadata.get("label") or _humanize(field_name)),
                    description=_field_description(field),
                ),
            )
        metadata_name = _model_field_name("tide_metadata", len(fields), used_names)
        fields[metadata_name] = (
            TideProtectionMetadata | None,
            Field(
                default=None,
                alias="_tide",
                title="TIDE metadata",
                description=(
                    "Present when one or more fields are null because their values are "
                    "protected for the current principal."
                ),
            ),
        )
        result[entity_name] = create_model(
            component_names[entity_name],
            __config__=ConfigDict(
                extra="forbid",
                frozen=True,
                populate_by_name=True,
            ),
            __module__=__name__,
            **fields,
        )

    namespace: dict[str, Any] = {
        generated.__name__: generated for generated in result.values()
    }
    namespace["TideProtectionMetadata"] = TideProtectionMetadata
    for generated in result.values():
        generated.model_rebuild(_types_namespace=namespace)
    return result


def _build_page_models(
    record_models: Mapping[str, type[BaseModel]],
    exposures: Mapping[str, _RestExposure],
) -> dict[str, type[BaseModel]]:
    pages: dict[str, type[BaseModel]] = {}
    for entity_name, exposure in exposures.items():
        if "list" not in exposure.operations:
            continue
        record_model = record_models[entity_name]
        pages[entity_name] = create_model(
            _component_name(entity_name, "Page"),
            __config__=ConfigDict(extra="forbid", frozen=True),
            __module__=__name__,
            records=(list[record_model], ...),
            next_cursor=(
                str | None,
                Field(
                    default=None,
                    description=(
                        "Opaque continuation token. Repeat the same query shape when "
                        "requesting the next page."
                    ),
                ),
            ),
        )
    return pages


def _component_schemas(
    record_models: Mapping[str, type[BaseModel]],
    page_models: Mapping[str, type[BaseModel]],
    exposures: Mapping[str, _RestExposure],
) -> dict[str, Any]:
    schema_models: list[type[BaseModel]] = []
    for entity_name, exposure in exposures.items():
        if exposure.operations:
            schema_models.append(record_models[entity_name])
        if "list" in exposure.operations:
            schema_models.append(page_models[entity_name])
    if schema_models:
        schema_models.append(TideApiError)
    unique_models = list(dict.fromkeys(schema_models))
    if not unique_models:
        return {}
    _roots, definitions = models_json_schema(
        [(generated, "serialization") for generated in unique_models],
        by_alias=True,
        ref_template="#/components/schemas/{model}",
    )
    return dict(definitions.get("$defs", {}))


def _paths(
    model: ApplicationModel,
    exposures: Mapping[str, _RestExposure],
    base_path: str,
    record_models: Mapping[str, type[BaseModel]],
    page_models: Mapping[str, type[BaseModel]],
) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for entity_name, exposure in exposures.items():
        if not exposure.operations:
            continue
        entity = model.entity(entity_name)
        resource_path = f"{base_path}/{exposure.path}"
        tag = entity.label
        if "list" in exposure.operations:
            paths[resource_path] = {
                "get": {
                    "operationId": f"list{_component_name(entity_name)}",
                    "summary": f"List {entity.label}",
                    "tags": [tag],
                    "security": [{"bearerAuth": []}],
                    "parameters": [_limit_parameter(), _cursor_parameter()],
                    "responses": {
                        "200": _json_response(
                            "A secured page of records",
                            page_models[entity_name].__name__,
                        ),
                        "400": _error_response("Invalid query or cursor"),
                        "401": _error_response("Authentication required"),
                        "403": _error_response("Operation not permitted"),
                    },
                    "x-tide-entity": entity_name,
                    "x-tide-operation": "list",
                }
            }
        if "get" in exposure.operations:
            primary_key = _primary_key(entity)
            item_path = f"{resource_path}/{{{primary_key.name}}}"
            paths[item_path] = {
                "get": {
                    "operationId": f"get{_component_name(entity_name)}",
                    "summary": f"Get one {entity.label}",
                    "tags": [tag],
                    "security": [{"bearerAuth": []}],
                    "parameters": [_identity_parameter(model, primary_key)],
                    "responses": {
                        "200": _json_response(
                            "A secured record",
                            record_models[entity_name].__name__,
                        ),
                        "401": _error_response("Authentication required"),
                        "403": _error_response("Operation not permitted"),
                        "404": _error_response("Record not found"),
                    },
                    "x-tide-entity": entity_name,
                    "x-tide-operation": "get",
                }
            }
    return paths


def _rest_exposures(model: ApplicationModel) -> dict[str, _RestExposure]:
    result: dict[str, _RestExposure] = {}
    claimed_paths: dict[str, str] = {}
    for entity_name, entity in model.entities.items():
        rest = entity.metadata.get("expose", {}).get("rest", False)
        if rest is False or rest is None:
            continue
        if rest is True:
            configured_path = None
            operations = READ_OPERATIONS
        elif isinstance(rest, Mapping):
            configured_path = rest.get("path")
            operations = frozenset(rest.get("operations", ())) & READ_OPERATIONS
        else:
            raise ValueError(f"invalid REST exposure for {entity_name}")
        path = _normalize_resource_path(
            str(configured_path) if configured_path is not None else None,
            entity_name,
        )
        previous = claimed_paths.get(path)
        if previous is not None:
            raise ValueError(
                f"REST path {path!r} is shared by {previous} and {entity_name}"
            )
        claimed_paths[path] = entity_name
        result[entity_name] = _RestExposure(path=path, operations=operations)
    return result


def _field_annotation(
    model: ApplicationModel,
    field: NormalizedField,
    component_names: Mapping[str, str],
) -> Any:
    field_type = str(field.metadata["type"])
    if field_type == "collection":
        if field.target_entity is None:
            raise ValueError(f"collection field {field.name!r} has no target")
        return list[ForwardRef(component_names[field.target_entity])]
    if field_type == "reference":
        if field.target_entity is None:
            raise ValueError(f"reference field {field.name!r} has no target")
        target_key = _primary_key(model.entity(field.target_entity))
        return _scalar_annotation(target_key)
    return _scalar_annotation(field)


def _scalar_annotation(field: NormalizedField) -> Any:
    metadata = field.metadata
    field_type = str(metadata["type"])
    constraints: dict[str, Any] = {}
    if metadata.get("minimum") is not None:
        constraints["ge"] = metadata["minimum"]
    if metadata.get("maximum") is not None:
        constraints["le"] = metadata["maximum"]

    if field_type == "string":
        if metadata.get("required"):
            constraints["min_length"] = 1
        if metadata.get("length") is not None:
            constraints["max_length"] = metadata["length"]
        annotation: Any = str
    elif field_type == "choice":
        choices = tuple(metadata.get("choices", ()))
        annotation = Literal.__getitem__(choices)
    elif field_type == "integer":
        annotation = int
    elif field_type == "decimal":
        if metadata.get("precision") is not None:
            constraints["max_digits"] = metadata["precision"]
        if metadata.get("scale") is not None:
            constraints["decimal_places"] = metadata["scale"]
        annotation = Decimal
    elif field_type == "boolean":
        annotation = bool
    elif field_type == "date":
        annotation = date
    elif field_type == "datetime":
        annotation = datetime
    else:
        raise ValueError(f"unsupported API field type {field_type!r}")
    if constraints:
        return Annotated[annotation, Field(**constraints)]
    return annotation


def _identity_parameter(
    model: ApplicationModel,
    primary_key: NormalizedField,
) -> dict[str, Any]:
    schema = _identity_schema(model, primary_key)
    return {
        "name": primary_key.name,
        "in": "path",
        "required": True,
        "description": "Record identity",
        "schema": schema,
    }


def _identity_schema(
    model: ApplicationModel,
    field: NormalizedField,
) -> dict[str, Any]:
    field_type = str(field.metadata["type"])
    if field_type == "reference" and field.target_entity:
        return _identity_schema(model, _primary_key(model.entity(field.target_entity)))
    if field_type == "integer":
        result: dict[str, Any] = {"type": "integer"}
    elif field_type in {"string", "choice"}:
        result = {"type": "string"}
        if field_type == "choice":
            result["enum"] = list(field.metadata.get("choices", ()))
        if field.metadata.get("length") is not None:
            result["maxLength"] = field.metadata["length"]
    elif field_type == "decimal":
        result = {"type": "string", "format": "decimal"}
    else:
        raise ValueError(f"unsupported primary-key API type {field_type!r}")
    if field.metadata.get("minimum") is not None and result["type"] != "string":
        result["minimum"] = field.metadata["minimum"]
    if field.metadata.get("maximum") is not None and result["type"] != "string":
        result["maximum"] = field.metadata["maximum"]
    return result


def _limit_parameter() -> dict[str, Any]:
    return {
        "name": "limit",
        "in": "query",
        "required": False,
        "description": "Maximum records in this page.",
        "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
    }


def _cursor_parameter() -> dict[str, Any]:
    return {
        "name": "cursor",
        "in": "query",
        "required": False,
        "description": "Opaque continuation token returned by the previous page.",
        "schema": {"type": "string", "minLength": 1},
    }


def _json_response(description: str, component: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{component}"}
            }
        },
    }


def _error_response(description: str) -> dict[str, Any]:
    return _json_response(description, TideApiError.__name__)


def _primary_key(entity: NormalizedEntity) -> NormalizedField:
    return next(
        field for field in entity.fields.values() if field.metadata.get("primary_key")
    )


def _normalize_base_path(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("API base path must start with '/'")
    normalized = value.rstrip("/")
    if not normalized or not _valid_literal_path(normalized[1:]):
        raise ValueError("API base path must contain a literal path")
    return normalized


def _normalize_resource_path(value: str | None, entity_name: str) -> str:
    path = value or _default_resource_path(entity_name)
    path = path.strip("/")
    if not _valid_literal_path(path):
        raise ValueError(f"invalid REST path {path!r} for {entity_name}")
    return path


def _valid_literal_path(path: str) -> bool:
    return bool(path) and all(
        segment not in {"", ".", ".."}
        and re.fullmatch(r"[A-Za-z0-9._~-]+", segment) is not None
        for segment in path.split("/")
    )


def _default_resource_path(entity_name: str) -> str:
    return "/".join(_kebab_case(part) for part in entity_name.split("."))


def _component_name(entity_name: str, suffix: str = "") -> str:
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


def _kebab_case(value: str) -> str:
    separated = re.sub(r"(?<!^)(?=[A-Z])", "-", value).replace("_", "-")
    return separated.lower()


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").title()


def _field_description(field: NormalizedField) -> str | None:
    description = field.metadata.get("help")
    if description:
        return str(description)
    if field.metadata.get("concurrency_token"):
        return "Integer optimistic-concurrency token."
    if field.metadata.get("computed"):
        return "Framework-computed value."
    return None
