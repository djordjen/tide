"""The document tree, and the editable properties of one document."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
import math
import re
from typing import Any, Literal

from pydantic import BaseModel

from tide.development.designer import (
    DesignerDocumentReference,
    DesignerSession,
    PathPart,
)
from tide.model.source import (
    EntitySource,
    FormatsSource,
    PresentationDefaultsSource,
    PresetDocumentSource,
    ProjectSource,
    ReportSource,
    SecurityDocumentSource,
    ViewSource,
)

from .contracts import (
    StudioError,
    StudioProperty,
)
from .documents import (
    display_path,
    load_studio_yaml,
)




def project_identity(
    session: DesignerSession,
    fallback: str,
) -> tuple[str, str | None]:
    project = session.document(DesignerDocumentReference(kind="project"))
    document = load_studio_yaml(project.file, project.content)
    if not isinstance(document, Mapping):
        return fallback, None
    application = document.get("application")
    if not isinstance(application, Mapping):
        return fallback, None
    name = application.get("name")
    version = application.get("version")
    return (
        name if isinstance(name, str) and name else fallback,
        str(version) if version is not None else None,
    )


def document_label(target: DesignerDocumentReference) -> str:
    if target.kind == "project":
        return "Application manifest"
    assert target.name is not None
    return target.name


def document_properties(
    document: Any,
    source_model: type[BaseModel] | None,
) -> tuple[StudioProperty, ...]:
    if not isinstance(document, Mapping):
        return (
            StudioProperty(
                name="Document",
                path=(),
                value=_scalar_text(document),
                value_kind="scalar",
                editable=False,
                editor="text",
            ),
        )
    properties: list[StudioProperty] = []
    for name, value in document.items():
        if not isinstance(name, str):
            continue
        _append_property(properties, value, (name,), source_model)
    return tuple(properties)


def _append_property(
    properties: list[StudioProperty],
    value: Any,
    path: tuple[PathPart, ...],
    source_model: type[BaseModel] | None,
) -> None:
    kind = _property_kind(value)
    editor, choices = _property_editor(source_model, path, value)
    properties.append(
        StudioProperty(
            name=display_path(path),
            path=path,
            value=_property_text(value),
            value_kind=kind,
            editable=(
                kind == "scalar"
                and _editable_scalar(value)
                and not _identity_property(path)
            ),
            editor=editor,
            choices=choices,
        )
    )
    if isinstance(value, Mapping):
        for name, child in value.items():
            if isinstance(name, str):
                _append_property(properties, child, (*path, name), source_model)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _append_property(properties, child, (*path, index), source_model)


def source_model(
    target: DesignerDocumentReference,
    document: Any,
) -> type[BaseModel] | None:
    semantic: dict[str, type[BaseModel]] = {
        "project": ProjectSource,
        "entity": EntitySource,
        "view": ViewSource,
        "report": ReportSource,
    }
    if target.kind in semantic:
        return semantic[target.kind]
    if not isinstance(document, Mapping):
        return None
    signatures: tuple[tuple[str, type[BaseModel]], ...] = (
        ("entity", EntitySource),
        ("view", ViewSource),
        ("report", ReportSource),
        ("application", ProjectSource),
        ("presets", PresetDocumentSource),
        ("formats", FormatsSource),
        ("permissions", SecurityDocumentSource),
    )
    for key, model in signatures:
        if key in document:
            return model
    if any(key in document for key in ("browse", "form", "lookup", "inline_edit")):
        return PresentationDefaultsSource
    return None


def _property_editor(
    source_model: type[BaseModel] | None,
    path: tuple[PathPart, ...],
    value: Any,
) -> tuple[Literal["text", "choice", "boolean", "integer", "number"], tuple[str, ...]]:
    metadata = _schema_metadata(source_model, path)
    choices = metadata["choices"]
    if choices:
        return "choice", choices
    schema_types = metadata["types"]
    if "boolean" in schema_types or isinstance(value, bool):
        return "boolean", ("true", "false")
    if "integer" in schema_types or (
        isinstance(value, int) and not isinstance(value, bool)
    ):
        return "integer", ()
    if "number" in schema_types or isinstance(value, float):
        return "number", ()
    return "text", ()


def _schema_metadata(
    source_model: type[BaseModel] | None,
    path: tuple[PathPart, ...],
) -> dict[str, tuple[str, ...]]:
    if source_model is None:
        return {"choices": (), "types": ()}
    root = _model_schema(source_model)
    nodes: list[Mapping[str, Any]] = [root]
    for part in path:
        children: list[Mapping[str, Any]] = []
        for node in nodes:
            for variant in _schema_variants(root, node):
                if isinstance(part, str):
                    properties = variant.get("properties")
                    if isinstance(properties, Mapping) and isinstance(
                        properties.get(part), Mapping
                    ):
                        children.append(properties[part])
                        continue
                    additional = variant.get("additionalProperties")
                    if isinstance(additional, Mapping):
                        children.append(additional)
                else:
                    items = variant.get("items")
                    if isinstance(items, Mapping):
                        children.append(items)
        if not children:
            return {"choices": (), "types": ()}
        nodes = children
    choices: list[str] = []
    schema_types: list[str] = []
    for node in nodes:
        for variant in _schema_variants(root, node):
            values = variant.get("enum")
            if isinstance(values, list):
                for value in values:
                    if value is not None:
                        choice = _schema_value_text(value)
                        if choice not in choices:
                            choices.append(choice)
            elif "const" in variant and variant["const"] is not None:
                choice = _schema_value_text(variant["const"])
                if choice not in choices:
                    choices.append(choice)
            value_type = variant.get("type")
            if isinstance(value_type, str) and value_type != "null":
                if value_type not in schema_types:
                    schema_types.append(value_type)
    return {"choices": tuple(choices), "types": tuple(schema_types)}


@lru_cache(maxsize=None)
def _model_schema(source_model: type[BaseModel]) -> dict[str, Any]:
    return source_model.model_json_schema(by_alias=True)


def _schema_variants(
    root: Mapping[str, Any],
    node: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    reference = node.get("$ref")
    if isinstance(reference, str):
        resolved: Any = root
        for part in reference.removeprefix("#/").split("/"):
            if not isinstance(resolved, Mapping) or part not in resolved:
                return ()
            resolved = resolved[part]
        return _schema_variants(root, resolved) if isinstance(resolved, Mapping) else ()
    alternatives = node.get("anyOf") or node.get("oneOf")
    if isinstance(alternatives, list):
        variants: list[Mapping[str, Any]] = []
        for alternative in alternatives:
            if isinstance(alternative, Mapping):
                variants.extend(_schema_variants(root, alternative))
        return tuple(variants)
    combined = node.get("allOf")
    if (
        isinstance(combined, list)
        and len(combined) == 1
        and isinstance(combined[0], Mapping)
    ):
        return _schema_variants(root, combined[0])
    return (node,)


def _schema_value_text(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _identity_property(path: tuple[PathPart, ...]) -> bool:
    return len(path) == 1 and path[0] in {
        "entity",
        "view",
        "report",
        "schema_version",
    }


def _editable_scalar(value: Any) -> bool:
    if isinstance(value, str):
        return "\n" not in value and "\r" not in value
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def resolve_property(document: Any, path: tuple[PathPart, ...]) -> Any:
    current = document
    traversed: list[PathPart] = []
    for part in path:
        traversed.append(part)
        if isinstance(current, Mapping) and isinstance(part, str) and part in current:
            current = current[part]
            continue
        if (
            isinstance(current, Sequence)
            and not isinstance(current, (str, bytes, bytearray))
            and isinstance(part, int)
            and part < len(current)
        ):
            current = current[part]
            continue
        raise StudioError(f"unknown Studio property {display_path(tuple(traversed))}")
    return current


def parse_scalar(text: str, current: Any, path: tuple[PathPart, ...]) -> Any:
    label = display_path(path)
    if isinstance(current, str):
        return text
    normalized = text.strip()
    if isinstance(current, bool):
        if normalized.casefold() == "true":
            return True
        if normalized.casefold() == "false":
            return False
        raise StudioError(f"{label} requires true or false")
    if isinstance(current, int):
        if not re.fullmatch(r"[+-]?\d+", normalized):
            raise StudioError(f"{label} requires an integer")
        return int(normalized)
    if isinstance(current, float):
        try:
            value = float(normalized)
        except ValueError as error:
            raise StudioError(f"{label} requires a number") from error
        if not math.isfinite(value):
            raise StudioError(f"{label} requires a finite number")
        return value
    raise StudioError(f"{label} has an unsupported scalar type")


def _property_kind(value: Any) -> Literal["scalar", "mapping", "sequence"]:
    if isinstance(value, Mapping):
        return "mapping"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "sequence"
    return "scalar"


def _property_text(value: Any) -> str:
    if isinstance(value, Mapping):
        count = len(value)
        return f"{count} propert{'y' if count == 1 else 'ies'}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        count = len(value)
        return f"{count} item{'s' if count != 1 else ''}"
    return _scalar_text(value)


def _scalar_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)
