"""Immutable normalized application model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from tide.diagnostics import Diagnostic


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

    @property
    def primary_key(self) -> NormalizedField:
        """Return the field that identifies a record of this entity.

        Schema v0.1 requires exactly one, so this is a question the model can
        answer; every module used to rescan the fields for it, in two different
        return shapes, which is how the answer drifted.
        """

        for field in self.fields.values():
            if field.metadata.get("primary_key"):
                return field
        raise ValueError(f"entity {self.name!r} declares no primary key")

    @property
    def version_field(self) -> NormalizedField | None:
        """Return the concurrency token, when this entity declares one."""

        return next(
            (
                field
                for field in self.fields.values()
                if field.metadata.get("concurrency_token")
            ),
            None,
        )

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
class NavigationItem:
    view: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"view": self.view, "label": self.label}


@dataclass(frozen=True, slots=True)
class NavigationGroup:
    label: str
    items: tuple[NavigationItem, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "items": [item.as_dict() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class ApplicationModel:
    schema_version: str
    name: str
    version: str
    project_root: Path
    database: Mapping[str, Any]
    entities: Mapping[str, NormalizedEntity]
    views: Mapping[str, ResolvedView]
    navigation: tuple[NavigationGroup, ...]
    reports: Mapping[str, Mapping[str, Any]]
    formats: Mapping[str, Mapping[str, Any]]
    presets: frozenset[str]
    permissions: frozenset[str]
    roles: Mapping[str, tuple[str, ...]]
    row_policies: tuple[Mapping[str, Any], ...]
    field_policies: tuple[Mapping[str, Any], ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    def entity(self, name: str) -> NormalizedEntity:
        return self.entities[name]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "application": {"name": self.name, "version": self.version},
            "database": deep_thaw(self.database),
            "entities": {name: entity.as_dict() for name, entity in self.entities.items()},
            "views": {
                name: view.as_dict() for name, view in self.views.items()
            },
            "navigation": [group.as_dict() for group in self.navigation],
            "reports": deep_thaw(self.reports),
            "formats": deep_thaw(self.formats),
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


def field_is_writable(field: NormalizedField, mode: str) -> bool:
    """Return whether metadata permits a field in this mutation input mode.

    The one spelling of write-side assignability: the generated REST/MCP
    input models and the service-level mass-assignment gate all read it, so
    the wire contract and the service can never disagree about which fields
    a caller may set.
    """

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
