"""Headless, no-write designer commands and in-memory editing sessions."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from copy import deepcopy
from dataclasses import dataclass
from difflib import unified_diff
from hashlib import sha256
from io import StringIO
import math
from pathlib import Path, PurePosixPath
from secrets import token_hex
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import ScalarString

from tide.compiler.compiler import compile_project
from tide.compiler.normalized import ApplicationModel
from tide.diagnostics import CompilationFailed


MAX_DESIGNER_SOURCE_FILES = 1_000
MAX_DESIGNER_SOURCE_BYTES = 16 * 1024 * 1024
MAX_DESIGNER_PATH_DEPTH = 32
MAX_DESIGNER_BATCH_COMMANDS = 100
MAX_DESIGNER_HISTORY = 100

PathPart = str | int
DocumentKind = Literal["project", "entity", "view", "report", "source"]


class DesignerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DesignerDocumentReference(DesignerModel):
    """A semantic document identity with a source-path fallback."""

    kind: DocumentKind
    name: str | None = None

    @model_validator(mode="after")
    def valid_identity(self) -> DesignerDocumentReference:
        if self.kind == "project":
            if self.name is not None:
                raise ValueError("project document references do not accept a name")
            return self
        if not self.name:
            raise ValueError(f"{self.kind} document references require a name")
        if self.kind == "source":
            normalized = _portable_relative_path(self.name)
            if normalized != self.name:
                raise ValueError("source document names must use canonical POSIX paths")
            if PurePosixPath(normalized).suffix.lower() not in {".yaml", ".yml"}:
                raise ValueError("source document references must identify YAML files")
        return self


class DesignerCommandModel(DesignerModel):
    target: DesignerDocumentReference


class DesignerSetValueCommand(DesignerCommandModel):
    operation: Literal["set_value"] = "set_value"
    path: tuple[PathPart, ...] = Field(min_length=1, max_length=MAX_DESIGNER_PATH_DEPTH)
    value: Any

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: tuple[PathPart, ...]) -> tuple[PathPart, ...]:
        return _validate_model_path(value)

    @field_validator("value")
    @classmethod
    def valid_value(cls, value: Any) -> Any:
        _validate_json_value(value)
        return value


class DesignerRemoveValueCommand(DesignerCommandModel):
    operation: Literal["remove_value"] = "remove_value"
    path: tuple[PathPart, ...] = Field(min_length=1, max_length=MAX_DESIGNER_PATH_DEPTH)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: tuple[PathPart, ...]) -> tuple[PathPart, ...]:
        return _validate_model_path(value)


class DesignerRenameKeyCommand(DesignerCommandModel):
    operation: Literal["rename_key"] = "rename_key"
    path: tuple[PathPart, ...] = Field(default=(), max_length=MAX_DESIGNER_PATH_DEPTH)
    old_key: str = Field(min_length=1)
    new_key: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: tuple[PathPart, ...]) -> tuple[PathPart, ...]:
        return _validate_model_path(value)


class DesignerReorderMappingCommand(DesignerCommandModel):
    operation: Literal["reorder_mapping"] = "reorder_mapping"
    path: tuple[PathPart, ...] = Field(default=(), max_length=MAX_DESIGNER_PATH_DEPTH)
    keys: tuple[str, ...] = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: tuple[PathPart, ...]) -> tuple[PathPart, ...]:
        return _validate_model_path(value)

    @field_validator("keys")
    @classmethod
    def unique_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not key for key in value):
            raise ValueError("mapping order keys must not be empty")
        if len(set(value)) != len(value):
            raise ValueError("mapping order keys must not be repeated")
        return value


class DesignerInsertSequenceItemCommand(DesignerCommandModel):
    operation: Literal["insert_sequence_item"] = "insert_sequence_item"
    path: tuple[PathPart, ...] = Field(default=(), max_length=MAX_DESIGNER_PATH_DEPTH)
    index: int = Field(ge=0)
    value: Any

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: tuple[PathPart, ...]) -> tuple[PathPart, ...]:
        return _validate_model_path(value)

    @field_validator("value")
    @classmethod
    def valid_value(cls, value: Any) -> Any:
        _validate_json_value(value)
        return value


class DesignerMoveSequenceItemCommand(DesignerCommandModel):
    operation: Literal["move_sequence_item"] = "move_sequence_item"
    path: tuple[PathPart, ...] = Field(default=(), max_length=MAX_DESIGNER_PATH_DEPTH)
    from_index: int = Field(ge=0)
    to_index: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: tuple[PathPart, ...]) -> tuple[PathPart, ...]:
        return _validate_model_path(value)


DesignerCommand = Annotated[
    Union[
        DesignerSetValueCommand,
        DesignerRemoveValueCommand,
        DesignerRenameKeyCommand,
        DesignerReorderMappingCommand,
        DesignerInsertSequenceItemCommand,
        DesignerMoveSequenceItemCommand,
    ],
    Field(discriminator="operation"),
]


class DesignerCommandBatch(DesignerModel):
    """Commands committed to history as one undoable designer transaction."""

    label: str | None = Field(default=None, max_length=120)
    commands: tuple[DesignerCommand, ...] = Field(
        min_length=1,
        max_length=MAX_DESIGNER_BATCH_COMMANDS,
    )


class DesignerDocumentContent(DesignerModel):
    target: DesignerDocumentReference
    file: str
    content: str
    candidate_fingerprint: str
    writes_performed: Literal[False] = False


class DesignerDocumentDescriptor(DesignerModel):
    target: DesignerDocumentReference
    file: str


class DesignerDocumentCatalog(DesignerModel):
    project: str
    candidate_fingerprint: str
    documents: tuple[DesignerDocumentDescriptor, ...]
    writes_performed: Literal[False] = False


class DesignerSnapshot(DesignerModel):
    """Serializable state returned to future TUI, GUI, Web, and MCP adapters."""

    session_id: str
    project: str
    base_fingerprint: str
    candidate_fingerprint: str
    valid: bool
    dirty: bool
    can_undo: bool
    can_redo: bool
    undo_depth: int
    redo_depth: int
    changed_files: tuple[str, ...] = ()
    diff: str = ""
    diagnostics: tuple[dict[str, Any], ...] = ()
    application: str | None = None
    application_version: str | None = None
    entity_count: int = 0
    view_count: int = 0
    report_count: int = 0
    writes_performed: Literal[False] = False
    temporary_candidate_used: Literal[True] = True
    temporary_candidate_deleted: bool
    external_commands_executed: Literal[False] = False
    application_database_accessed: Literal[False] = False
    round_trip_yaml_used: Literal[True] = True


class DesignerError(ValueError):
    """A structured command could not be applied to the in-memory tree."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class _Evaluation:
    valid: bool
    diagnostics: tuple[dict[str, Any], ...]
    model: ApplicationModel | None
    temporary_candidate_deleted: bool


@dataclass(frozen=True, slots=True)
class _DesignerSessionState:
    root: Path
    project_file: str
    session_id: str
    base_files: dict[str, bytes]
    working_files: dict[str, bytes]


class DesignerService:
    """Open bounded designer sessions without mutating application sources."""

    def __init__(self, project: str | Path) -> None:
        project_path = Path(project).resolve()
        self.project_file = (
            project_path if project_path.is_file() else project_path / "tide.yaml"
        )
        self.root = self.project_file.parent
        if not self.root.is_dir():
            raise DesignerError("TIDEDES001", "project root does not exist")
        if not self.project_file.is_file():
            raise DesignerError(
                "TIDEDES001", "project configuration tide.yaml was not found"
            )
        try:
            self.project_file.relative_to(self.root)
        except ValueError as error:
            raise DesignerError(
                "TIDEDES001",
                "project configuration must remain inside the project root",
            ) from error

    def open_session(self) -> DesignerSession:
        files = _read_project_sources(self.root, self.project_file)
        return DesignerSession(
            root=self.root,
            project_file=self.project_file.relative_to(self.root).as_posix(),
            files=files,
        )


class DesignerSession:
    """One mutable, process-local designer working tree with undo and redo."""

    def __init__(
        self,
        *,
        root: Path,
        project_file: str,
        files: Mapping[str, bytes],
    ) -> None:
        self.root = root
        self.project_file = project_file
        self._base_files = dict(files)
        self._working_files = dict(files)
        self._undo: list[dict[str, bytes]] = []
        self._redo: list[dict[str, bytes]] = []
        self.base_fingerprint = _fingerprint_files(self._base_files)
        self.session_id = "tide-designer-" + token_hex(12)
        _index_documents(self._working_files, self.project_file)

    def snapshot(self) -> DesignerSnapshot:
        evaluation = self._evaluate()
        changed = _changed_files(self._base_files, self._working_files)
        model = evaluation.model
        return DesignerSnapshot(
            session_id=self.session_id,
            project=self.root.name,
            base_fingerprint=self.base_fingerprint,
            candidate_fingerprint=_fingerprint_files(self._working_files),
            valid=evaluation.valid,
            dirty=bool(changed),
            can_undo=bool(self._undo),
            can_redo=bool(self._redo),
            undo_depth=len(self._undo),
            redo_depth=len(self._redo),
            changed_files=changed,
            diff=_source_diff(self._base_files, self._working_files),
            diagnostics=evaluation.diagnostics,
            application=model.name if model is not None else None,
            application_version=model.version if model is not None else None,
            entity_count=len(model.entities) if model is not None else 0,
            view_count=len(model.views) if model is not None else 0,
            report_count=len(model.reports) if model is not None else 0,
            temporary_candidate_deleted=evaluation.temporary_candidate_deleted,
        )

    def document(
        self,
        target: DesignerDocumentReference,
    ) -> DesignerDocumentContent:
        relative = _resolve_document(
            target,
            _index_documents(self._working_files, self.project_file),
        )
        return DesignerDocumentContent(
            target=target,
            file=relative,
            content=self._working_files[relative].decode("utf-8"),
            candidate_fingerprint=_fingerprint_files(self._working_files),
        )

    def documents(self) -> DesignerDocumentCatalog:
        index = _index_documents(self._working_files, self.project_file)
        descriptors = tuple(
            DesignerDocumentDescriptor(
                target=DesignerDocumentReference(kind=kind, name=name),
                file=relative,
            )
            for (kind, name), relative in sorted(
                index.items(),
                key=lambda item: (
                    item[0][0],
                    (item[0][1] or "").casefold(),
                ),
            )
            if relative is not None
        )
        return DesignerDocumentCatalog(
            project=self.root.name,
            candidate_fingerprint=_fingerprint_files(self._working_files),
            documents=descriptors,
        )

    def execute(self, command: DesignerCommand) -> DesignerSnapshot:
        return self.execute_batch(DesignerCommandBatch(commands=(command,)))

    def execute_batch(self, batch: DesignerCommandBatch) -> DesignerSnapshot:
        before = dict(self._working_files)
        candidate = dict(self._working_files)
        try:
            for command in batch.commands:
                _apply_command(candidate, self.project_file, command)
        except DesignerError:
            raise
        except Exception as error:  # pragma: no cover - defensive normalization
            raise DesignerError(
                "TIDEDES009",
                f"unexpected command failure: {type(error).__name__}",
            ) from error
        if candidate != before:
            self._undo.append(before)
            if len(self._undo) > MAX_DESIGNER_HISTORY:
                del self._undo[0]
            self._working_files = candidate
            self._redo.clear()
        return self.snapshot()

    def undo(self) -> DesignerSnapshot:
        if not self._undo:
            raise DesignerError("TIDEDES010", "there is no designer command to undo")
        self._redo.append(dict(self._working_files))
        self._working_files = self._undo.pop()
        return self.snapshot()

    def redo(self) -> DesignerSnapshot:
        if not self._redo:
            raise DesignerError("TIDEDES011", "there is no designer command to redo")
        self._undo.append(dict(self._working_files))
        self._working_files = self._redo.pop()
        return self.snapshot()

    def _capture_save_state(self) -> _DesignerSessionState:
        """Return an isolated state copy for the package save boundary."""

        return _DesignerSessionState(
            root=self.root,
            project_file=self.project_file,
            session_id=self.session_id,
            base_files=dict(self._base_files),
            working_files=dict(self._working_files),
        )

    def _mark_saved(
        self,
        candidate_fingerprint: str,
        saved_files: Mapping[str, bytes],
    ) -> None:
        """Advance the clean base only when this session still has that candidate."""

        if _fingerprint_files(self._working_files) != candidate_fingerprint:
            return
        self._base_files = dict(saved_files)
        self.base_fingerprint = candidate_fingerprint

    def _evaluate(self) -> _Evaluation:
        return _evaluate_project(self.project_file, self._working_files)


def _evaluate_project(
    project_file: str,
    files: Mapping[str, bytes],
) -> _Evaluation:
    temporary_root: Path | None = None
    model: ApplicationModel | None = None
    diagnostics: tuple[dict[str, Any], ...]
    with TemporaryDirectory(prefix="tide-designer-") as temporary:
        temporary_root = Path(temporary) / "candidate"
        temporary_root.mkdir()
        _write_project_sources(temporary_root, files)
        project = temporary_root / PurePosixPath(project_file)
        try:
            model = compile_project(project)
        except CompilationFailed as error:
            diagnostics = tuple(
                diagnostic.as_dict(root=temporary_root)
                for diagnostic in error.diagnostics
            )
        else:
            diagnostics = tuple(
                diagnostic.as_dict(root=temporary_root)
                for diagnostic in model.diagnostics
            )
    return _Evaluation(
        valid=model is not None,
        diagnostics=diagnostics,
        model=model,
        temporary_candidate_deleted=bool(
            temporary_root is not None and not temporary_root.parent.exists()
        ),
    )


def _read_project_sources(root: Path, project_file: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total_bytes = 0
    candidates = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".py"}
        ),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )
    if project_file not in candidates:
        candidates.insert(0, project_file)
    if len(candidates) > MAX_DESIGNER_SOURCE_FILES:
        raise DesignerError(
            "TIDEDES002",
            f"project exceeds the designer limit of {MAX_DESIGNER_SOURCE_FILES} source files",
        )
    portable_paths: set[str] = set()
    for path in candidates:
        if path.is_symlink():
            raise DesignerError(
                "TIDEDES003",
                f"designer source files must not be symbolic links: {path.name}",
            )
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as error:
            raise DesignerError(
                "TIDEDES003",
                "designer source files must remain inside the project root",
            ) from error
        portable = relative.casefold()
        if portable in portable_paths:
            raise DesignerError(
                "TIDEDES003",
                f"designer source paths collide case-insensitively: {relative}",
            )
        portable_paths.add(portable)
        try:
            content = path.read_bytes()
            if path.suffix.lower() in {".yaml", ".yml", ".py"}:
                content.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise DesignerError(
                "TIDEDES003",
                f"cannot read UTF-8 designer source {relative}: {error}",
            ) from error
        total_bytes += len(content)
        if total_bytes > MAX_DESIGNER_SOURCE_BYTES:
            raise DesignerError(
                "TIDEDES002",
                "project source exceeds the 16 MiB designer preview limit",
            )
        files[relative] = content
    return files


def _write_project_sources(root: Path, files: Mapping[str, bytes]) -> None:
    for relative, content in files.items():
        destination = root / PurePosixPath(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _index_documents(
    files: Mapping[str, bytes],
    project_file: str,
) -> dict[tuple[DocumentKind, str | None], str | None]:
    index: dict[tuple[DocumentKind, str | None], str | None] = {}
    for relative, content in files.items():
        if PurePosixPath(relative).suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            document = _load_round_trip_yaml(relative, content)
        except DesignerError:
            if relative == project_file:
                raise
            # Unrelated YAML files may coexist in an application tree. They
            # are copied into the candidate but are not designer-addressable.
            continue
        _add_document(index, ("source", relative), relative)
        if relative == project_file:
            _add_document(index, ("project", None), relative)
        if not isinstance(document, Mapping):
            continue
        # Views and reports also contain an ``entity`` property. Classify the
        # document by its own identity key before indexing that semantic name.
        for kind in ("view", "report", "entity"):
            name = document.get(kind)
            if isinstance(name, str) and name:
                _add_document(index, (kind, name), relative)
                break
    if ("project", None) not in index:
        raise DesignerError("TIDEDES004", "project document is not available")
    return index


def _add_document(
    index: dict[tuple[DocumentKind, str | None], str | None],
    key: tuple[DocumentKind, str | None],
    relative: str,
) -> None:
    if key in index and index[key] != relative:
        index[key] = None
    else:
        index[key] = relative


def _resolve_document(
    target: DesignerDocumentReference,
    index: Mapping[tuple[DocumentKind, str | None], str | None],
) -> str:
    key = (target.kind, target.name)
    if key not in index:
        label = target.kind if target.name is None else f"{target.kind} {target.name!r}"
        raise DesignerError("TIDEDES004", f"unknown designer document {label}")
    relative = index[key]
    if relative is None:
        raise DesignerError(
            "TIDEDES004",
            f"designer document {target.kind} {target.name!r} is ambiguous",
        )
    return relative


def _apply_command(
    files: dict[str, bytes],
    project_file: str,
    command: DesignerCommand,
) -> None:
    index = _index_documents(files, project_file)
    relative = _resolve_document(command.target, index)
    document = _load_round_trip_yaml(relative, files[relative])
    if isinstance(command, DesignerSetValueCommand):
        parent, part = _resolve_parent(document, command.path)
        _set_child(parent, part, _round_trip_value(command.value), command.path)
    elif isinstance(command, DesignerRemoveValueCommand):
        parent, part = _resolve_parent(document, command.path)
        _remove_child(parent, part, command.path)
    elif isinstance(command, DesignerRenameKeyCommand):
        mapping = _resolve_node(document, command.path)
        _rename_key(mapping, command.old_key, command.new_key, command.path)
    elif isinstance(command, DesignerReorderMappingCommand):
        mapping = _resolve_node(document, command.path)
        _reorder_mapping(mapping, command.keys, command.path)
    elif isinstance(command, DesignerInsertSequenceItemCommand):
        sequence = _resolve_node(document, command.path)
        _insert_sequence_item(sequence, command.index, command.value, command.path)
    elif isinstance(command, DesignerMoveSequenceItemCommand):
        sequence = _resolve_node(document, command.path)
        _move_sequence_item(
            sequence,
            command.from_index,
            command.to_index,
            command.path,
        )
    else:  # pragma: no cover - discriminated union is exhaustive
        raise DesignerError("TIDEDES009", "unsupported designer command")
    files[relative] = _dump_round_trip_yaml(document, files[relative])


def _load_round_trip_yaml(relative: str, content: bytes) -> Any:
    yaml = _round_trip_yaml()
    try:
        document = yaml.load(content.decode("utf-8"))
    except (UnicodeDecodeError, YAMLError) as error:
        raise DesignerError(
            "TIDEDES003",
            f"cannot load designer source {relative}: {error}",
        ) from error
    if document is None:
        raise DesignerError("TIDEDES003", f"designer source {relative} is empty")
    return document


def _dump_round_trip_yaml(document: Any, original: bytes) -> bytes:
    yaml = _round_trip_yaml()
    yaml.line_break = "\r\n" if b"\r\n" in original else "\n"
    stream = StringIO()
    yaml.dump(document, stream)
    return stream.getvalue().encode("utf-8")


def _round_trip_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.allow_duplicate_keys = False
    yaml.preserve_quotes = True
    yaml.width = 10_000
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _resolve_parent(document: Any, path: tuple[PathPart, ...]) -> tuple[Any, PathPart]:
    return _resolve_node(document, path[:-1]), path[-1]


def _resolve_node(document: Any, path: Sequence[PathPart]) -> Any:
    current = document
    traversed: list[PathPart] = []
    for part in path:
        traversed.append(part)
        if isinstance(current, Mapping) and isinstance(part, str):
            if part not in current:
                raise DesignerError(
                    "TIDEDES005",
                    f"designer path {_display_path(traversed)} does not exist",
                )
            current = current[part]
            continue
        if _is_mutable_sequence(current) and isinstance(part, int):
            if part >= len(current):
                raise DesignerError(
                    "TIDEDES005",
                    f"designer path {_display_path(traversed)} is out of range",
                )
            current = current[part]
            continue
        raise DesignerError(
            "TIDEDES006",
            f"designer path {_display_path(traversed)} has an incompatible container",
        )
    return current


def _set_child(
    parent: Any, part: PathPart, value: Any, path: Sequence[PathPart]
) -> None:
    if isinstance(parent, MutableMapping) and isinstance(part, str):
        existing = parent.get(part)
        if isinstance(existing, ScalarString) and isinstance(value, str):
            value = type(existing)(value)
        parent[part] = value
        return
    if _is_mutable_sequence(parent) and isinstance(part, int):
        if part >= len(parent):
            raise DesignerError(
                "TIDEDES005",
                f"designer path {_display_path(path)} is out of range",
            )
        parent[part] = value
        return
    raise DesignerError(
        "TIDEDES006",
        f"designer path {_display_path(path)} has an incompatible container",
    )


def _remove_child(parent: Any, part: PathPart, path: Sequence[PathPart]) -> None:
    if isinstance(parent, MutableMapping) and isinstance(part, str):
        if part not in parent:
            raise DesignerError(
                "TIDEDES005",
                f"designer path {_display_path(path)} does not exist",
            )
        del parent[part]
        return
    if _is_mutable_sequence(parent) and isinstance(part, int):
        if part >= len(parent):
            raise DesignerError(
                "TIDEDES005",
                f"designer path {_display_path(path)} is out of range",
            )
        del parent[part]
        return
    raise DesignerError(
        "TIDEDES006",
        f"designer path {_display_path(path)} has an incompatible container",
    )


def _rename_key(mapping: Any, old: str, new: str, path: Sequence[PathPart]) -> None:
    if not isinstance(mapping, MutableMapping):
        raise DesignerError(
            "TIDEDES006",
            f"designer path {_display_path(path)} is not a mapping",
        )
    if old not in mapping:
        raise DesignerError("TIDEDES005", f"mapping key {old!r} does not exist")
    if old != new and new in mapping:
        raise DesignerError("TIDEDES007", f"mapping key {new!r} already exists")
    if old == new:
        return
    position = list(mapping).index(old)
    value = mapping.pop(old)
    if isinstance(mapping, CommentedMap):
        mapping.insert(position, new, value)
        if old in mapping.ca.items:
            mapping.ca.items[new] = mapping.ca.items.pop(old)
    else:
        items = list(mapping.items())
        mapping.clear()
        items.insert(position, (new, value))
        mapping.update(items)


def _reorder_mapping(
    mapping: Any,
    keys: tuple[str, ...],
    path: Sequence[PathPart],
) -> None:
    if not isinstance(mapping, MutableMapping):
        raise DesignerError(
            "TIDEDES006",
            f"designer path {_display_path(path)} is not a mapping",
        )
    existing = tuple(str(key) for key in mapping)
    if set(keys) != set(existing) or len(keys) != len(existing):
        missing = sorted(set(existing) - set(keys))
        unknown = sorted(set(keys) - set(existing))
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise DesignerError(
            "TIDEDES007",
            "mapping order must name every existing key exactly once"
            + (f" ({'; '.join(details)})" if details else ""),
        )
    if isinstance(mapping, CommentedMap):
        for key in keys:
            mapping.move_to_end(key)
        return
    values = {key: mapping[key] for key in keys}
    mapping.clear()
    mapping.update(values)


def _insert_sequence_item(
    sequence: Any,
    index: int,
    value: Any,
    path: Sequence[PathPart],
) -> None:
    if not _is_mutable_sequence(sequence):
        raise DesignerError(
            "TIDEDES006",
            f"designer path {_display_path(path)} is not a sequence",
        )
    if index > len(sequence):
        raise DesignerError("TIDEDES005", "sequence insertion index is out of range")
    sequence.insert(index, _round_trip_value(value))


def _move_sequence_item(
    sequence: Any,
    from_index: int,
    to_index: int,
    path: Sequence[PathPart],
) -> None:
    if not _is_mutable_sequence(sequence):
        raise DesignerError(
            "TIDEDES006",
            f"designer path {_display_path(path)} is not a sequence",
        )
    if from_index >= len(sequence) or to_index >= len(sequence):
        raise DesignerError("TIDEDES005", "sequence move index is out of range")
    if from_index == to_index:
        return
    value = sequence.pop(from_index)
    sequence.insert(to_index, value)


def _is_mutable_sequence(value: Any) -> bool:
    return isinstance(value, MutableSequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _round_trip_value(value: Any) -> Any:
    if isinstance(value, dict):
        return CommentedMap(
            (str(key), _round_trip_value(child)) for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return CommentedSeq(_round_trip_value(child) for child in value)
    return deepcopy(value)


def _validate_model_path(value: tuple[PathPart, ...]) -> tuple[PathPart, ...]:
    for part in value:
        if isinstance(part, str):
            if not part or "\x00" in part:
                raise ValueError("designer string path parts must not be empty")
        elif isinstance(part, int):
            if part < 0:
                raise ValueError("designer sequence indexes must not be negative")
        else:
            raise ValueError(
                "designer paths accept only string keys and integer indexes"
            )
    return value


def _validate_json_value(
    value: Any, *, depth: int = 0, nodes: list[int] | None = None
) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > 10_000 or depth > MAX_DESIGNER_PATH_DEPTH:
        raise ValueError("designer values exceed the bounded JSON value limit")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("designer numeric values must be finite")
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _validate_json_value(child, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("designer object keys must be non-empty strings")
            _validate_json_value(child, depth=depth + 1, nodes=nodes)
        return
    raise ValueError("designer values must be JSON-compatible")


def _portable_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("source document names must be safe relative paths")
    if "\\" in value or "\x00" in value:
        raise ValueError("source document names must use safe POSIX separators")
    if len(value.encode("utf-8")) > 512 or any(
        len(part.encode("utf-8")) > 120 for part in path.parts
    ):
        raise ValueError("source document names must use portable path lengths")
    windows_devices = {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    for part in path.parts:
        if (
            any(character in '<>:"|?*' for character in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].casefold() in windows_devices
        ):
            raise ValueError("source document names must use portable path parts")
    return path.as_posix()


def _fingerprint_files(files: Mapping[str, bytes]) -> str:
    digest = sha256()
    for path in sorted(files, key=str.casefold):
        content = files[path]
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(sha256(content).digest())
    return "sha256:" + digest.hexdigest()


def _changed_files(
    base: Mapping[str, bytes],
    candidate: Mapping[str, bytes],
) -> tuple[str, ...]:
    return tuple(
        path
        for path in sorted(set(base) | set(candidate), key=str.casefold)
        if base.get(path) != candidate.get(path)
    )


def _source_diff(base: Mapping[str, bytes], candidate: Mapping[str, bytes]) -> str:
    parts: list[str] = []
    for path in _changed_files(base, candidate):
        before = base.get(path, b"").decode("utf-8").splitlines(keepends=True)
        after = candidate.get(path, b"").decode("utf-8").splitlines(keepends=True)
        parts.extend(
            unified_diff(
                before,
                after,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    return "".join(parts)


def _display_path(path: Sequence[PathPart]) -> str:
    return ".".join(str(part) for part in path) if path else "<document>"
