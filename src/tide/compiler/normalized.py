"""Immutable normalized application model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

@dataclass(frozen=True, slots=True)
class NormalizedField:
    name: str
    metadata: Mapping[str, Any]
    target_entity: str | None = None
    dependencies: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result = deep_thaw(self.metadata)
        result.update(
            name=self.name,
            target_entity=self.target_entity,
            dependencies=list(self.dependencies),
        )
        return result


@dataclass(frozen=True, slots=True)
class NormalizedEntity:
    name: str
    label: str
    display: str | None
    source_file: Path
    metadata: Mapping[str, Any]
    fields: Mapping[str, NormalizedField]
    actions: Mapping[str, Mapping[str, Any]]

    def field(self, name: str) -> NormalizedField:
        return self.fields[name]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "display": self.display,
            "source_file": self.source_file.as_posix(),
            "metadata": deep_thaw(self.metadata),
            "fields": {name: field.as_dict() for name, field in self.fields.items()},
            "actions": deep_thaw(self.actions),
        }


@dataclass(frozen=True, slots=True)
class PropertyOrigin:
    layer: str
    file: Path | None
    path: tuple[str | int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "file": self.file.as_posix() if self.file else None,
            "path": list(self.path),
        }


@dataclass(frozen=True, slots=True)
class ResolvedView:
    name: str
    entity: str
    kind: str
    data: Mapping[str, Any]
    origins: Mapping[str, PropertyOrigin]

    def as_dict(self, *, include_provenance: bool = True) -> dict[str, Any]:
        result = deep_thaw(self.data)
        if include_provenance:
            result["provenance"] = {
                path: origin.as_dict() for path, origin in self.origins.items()
            }
        return result


@dataclass(frozen=True, slots=True)
class ApplicationModel:
    schema_version: str
    name: str
    version: str
    project_root: Path
    entities: Mapping[str, NormalizedEntity]
    views: Mapping[str, ResolvedView]
    reports: Mapping[str, Mapping[str, Any]]
    presets: frozenset[str]
    permissions: frozenset[str]
    roles: Mapping[str, tuple[str, ...]]
    row_policies: tuple[Mapping[str, Any], ...]
    field_policies: tuple[Mapping[str, Any], ...]

    def entity(self, name: str) -> NormalizedEntity:
        return self.entities[name]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "application": {"name": self.name, "version": self.version},
            "entities": {name: entity.as_dict() for name, entity in self.entities.items()},
            "views": {
                name: view.as_dict() for name, view in self.views.items()
            },
            "reports": deep_thaw(self.reports),
            "presets": sorted(self.presets),
            "permissions": sorted(self.permissions),
            "roles": {name: list(grants) for name, grants in self.roles.items()},
            "row_policies": deep_thaw(self.row_policies),
            "field_policies": deep_thaw(self.field_policies),
        }


def immutable_mapping(values: dict[str, Any]) -> Mapping[str, Any]:
    return deep_freeze(values)


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: deep_freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(child) for child in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(child) for child in value)
    return value


def deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: deep_thaw(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [deep_thaw(child) for child in value]
    if isinstance(value, (set, frozenset)):
        return sorted(deep_thaw(child) for child in value)
    return value
