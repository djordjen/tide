"""Presentation and in-memory editing models for TIDE Studio adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
import math
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from tide.development.designer import (
    DesignerDocumentReference,
    DesignerError,
    DesignerReplaceDocumentSourceCommand,
    DesignerService,
    DesignerSession,
    DesignerSetValueCommand,
    DesignerSnapshot,
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


StudioGroupKind = Literal["project", "entity", "view", "report", "source"]


class StudioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StudioDocumentNode(StudioModel):
    """One selectable semantic or source document in the Studio tree."""

    target: DesignerDocumentReference
    label: str
    file: str


class StudioDocumentGroup(StudioModel):
    """A stable top-level group in the Studio navigation tree."""

    kind: StudioGroupKind
    label: str
    documents: tuple[StudioDocumentNode, ...]


class StudioWorkspace(StudioModel):
    """Project summary displayed by a Studio adapter."""

    project: str
    application: str
    application_version: str | None
    candidate_fingerprint: str
    valid: bool
    diagnostic_count: int
    entity_count: int
    view_count: int
    report_count: int
    groups: tuple[StudioDocumentGroup, ...]
    writes_performed: Literal[False] = False
    application_database_accessed: Literal[False] = False


class StudioProperty(StudioModel):
    """One YAML node rendered by the Studio property inspector."""

    name: str
    path: tuple[PathPart, ...]
    value: str
    value_kind: Literal["scalar", "mapping", "sequence"]
    editable: bool
    editor: Literal["text", "choice", "boolean", "integer", "number"]
    choices: tuple[str, ...] = ()


class StudioSessionState(StudioModel):
    """Compiler and history state after an in-memory Studio operation."""

    workspace: StudioWorkspace
    valid: bool
    dirty: bool
    can_undo: bool
    can_redo: bool
    changed_files: tuple[str, ...]
    diff: str
    diagnostics: tuple[dict[str, Any], ...]
    writes_performed: Literal[False] = False
    application_database_accessed: Literal[False] = False


class StudioDocumentDetails(StudioModel):
    """Source and property data for the selected Studio document."""

    target: DesignerDocumentReference
    title: str
    file: str
    properties: tuple[StudioProperty, ...]
    source: str
    candidate_fingerprint: str
    writes_performed: Literal[False] = False


class StudioError(ValueError):
    """A Studio presentation document could not be displayed."""


class StudioService:
    """Adapt the headless Designer session for visual clients."""

    _GROUPS: tuple[tuple[StudioGroupKind, str], ...] = (
        ("project", "Application"),
        ("entity", "Entities"),
        ("view", "Views"),
        ("report", "Reports"),
        ("source", "Source files"),
    )

    def __init__(self, project: str | Path) -> None:
        self.project = Path(project)
        self._designer = DesignerService(project)
        self._session = self._designer.open_session()
        self._state = self._build_state(self._session)

    @property
    def workspace(self) -> StudioWorkspace:
        return self._state.workspace

    @property
    def state(self) -> StudioSessionState:
        return self._state

    def document(
        self,
        target: DesignerDocumentReference,
    ) -> StudioDocumentDetails:
        content = self._session.document(target)
        document = _load_studio_yaml(content.file, content.content)
        properties = _document_properties(document, _source_model(target, document))
        return StudioDocumentDetails(
            target=target,
            title=_document_label(target),
            file=content.file,
            properties=properties,
            source=content.content,
            candidate_fingerprint=content.candidate_fingerprint,
        )

    def set_property(
        self,
        target: DesignerDocumentReference,
        path: tuple[PathPart, ...],
        text: str,
    ) -> StudioSessionState:
        """Apply one typed scalar edit to the process-local candidate."""

        details = self.document(target)
        selected = next(
            (item for item in details.properties if item.path == path), None
        )
        if selected is None:
            raise StudioError(f"unknown Studio property {_display_path(path)}")
        if not selected.editable:
            raise StudioError(
                f"Studio property {_display_path(path)} is not directly editable"
            )
        if selected.choices and text not in selected.choices:
            options = ", ".join(selected.choices)
            raise StudioError(
                f"Studio property {_display_path(path)} requires one of: {options}"
            )
        document = _load_studio_yaml(details.file, details.source)
        current = _resolve_property(document, path)
        value = _parse_scalar(text, current, path)
        snapshot = self._session.execute(
            DesignerSetValueCommand(target=target, path=path, value=value)
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def replace_document_source(
        self,
        target: DesignerDocumentReference,
        source: str,
    ) -> StudioSessionState:
        """Apply an expert whole-document edit to the in-memory candidate."""

        details = self.document(target)
        source_target = DesignerDocumentReference(kind="source", name=details.file)
        try:
            snapshot = self._session.execute(
                DesignerReplaceDocumentSourceCommand(
                    target=source_target,
                    source=source,
                )
            )
        except DesignerError as error:
            raise StudioError(str(error)) from error
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def undo(self) -> StudioSessionState:
        snapshot = self._session.undo()
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def redo(self) -> StudioSessionState:
        snapshot = self._session.redo()
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def refresh(self) -> StudioWorkspace:
        """Discard the process-local session and reload source files from disk."""

        self._session = self._designer.open_session()
        self._state = self._build_state(self._session)
        return self._state.workspace

    @classmethod
    def _build_state(
        cls,
        session: DesignerSession,
        snapshot: DesignerSnapshot | None = None,
    ) -> StudioSessionState:
        snapshot = snapshot or session.snapshot()
        catalog = session.documents()
        grouped: dict[StudioGroupKind, list[StudioDocumentNode]] = {
            kind: [] for kind, _label in cls._GROUPS
        }
        for descriptor in catalog.documents:
            target = descriptor.target
            grouped[target.kind].append(
                StudioDocumentNode(
                    target=target,
                    label=_document_label(target),
                    file=descriptor.file,
                )
            )
        groups = tuple(
            StudioDocumentGroup(
                kind=kind,
                label=label,
                documents=tuple(grouped[kind]),
            )
            for kind, label in cls._GROUPS
        )
        application, application_version = _project_identity(session, catalog.project)
        workspace = StudioWorkspace(
            project=catalog.project,
            application=snapshot.application or application,
            application_version=snapshot.application_version or application_version,
            candidate_fingerprint=catalog.candidate_fingerprint,
            valid=snapshot.valid,
            diagnostic_count=len(snapshot.diagnostics),
            entity_count=(
                snapshot.entity_count if snapshot.valid else len(grouped["entity"])
            ),
            view_count=snapshot.view_count if snapshot.valid else len(grouped["view"]),
            report_count=(
                snapshot.report_count if snapshot.valid else len(grouped["report"])
            ),
            groups=groups,
        )
        return StudioSessionState(
            workspace=workspace,
            valid=snapshot.valid,
            dirty=snapshot.dirty,
            can_undo=snapshot.can_undo,
            can_redo=snapshot.can_redo,
            changed_files=snapshot.changed_files,
            diff=snapshot.diff,
            diagnostics=snapshot.diagnostics,
        )


def _yaml() -> YAML:
    loader = YAML(typ="safe", pure=True)
    loader.allow_duplicate_keys = False
    return loader


def _load_studio_yaml(file: str, source: str) -> Any:
    try:
        return _yaml().load(source)
    except YAMLError as error:
        raise StudioError(f"cannot display {file}: invalid YAML") from error


def _project_identity(
    session: DesignerSession,
    fallback: str,
) -> tuple[str, str | None]:
    project = session.document(DesignerDocumentReference(kind="project"))
    document = _load_studio_yaml(project.file, project.content)
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


def _document_label(target: DesignerDocumentReference) -> str:
    if target.kind == "project":
        return "Application manifest"
    assert target.name is not None
    return target.name


def _document_properties(
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
            name=_display_path(path),
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


def _source_model(
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


def _resolve_property(document: Any, path: tuple[PathPart, ...]) -> Any:
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
        raise StudioError(f"unknown Studio property {_display_path(tuple(traversed))}")
    return current


def _parse_scalar(text: str, current: Any, path: tuple[PathPart, ...]) -> Any:
    label = _display_path(path)
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


def _display_path(path: tuple[PathPart, ...]) -> str:
    if not path:
        return "Document"
    parts: list[str] = []
    for part in path:
        if isinstance(part, int):
            parts[-1] += f"[{part}]"
        else:
            parts.append(part)
    return ".".join(parts)


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
