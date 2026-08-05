"""What Studio hands its callers: one screen's worth of an
in-memory metadata candidate, and the error it raises."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tide.development.designer import (
    DesignerDocumentReference,
    PathPart,
)
from tide.development.designer_recovery import (
    DesignerRecoveryPreparation,
)
from tide.development.designer_save import (
    DesignerSavePreparation,
)


StudioGroupKind = Literal["project", "entity", "view", "report", "source"]

#: The entity operations Studio resolves access for, in display order.
StudioOperation = Literal["list", "read", "create", "update", "delete"]
STUDIO_OPERATIONS: tuple[StudioOperation, ...] = (
    "list",
    "read",
    "create",
    "update",
    "delete",
)


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


class StudioViewField(StudioModel):
    """One field placement in a resolved view structure track."""

    key: str
    name: str
    label: str
    field_type: str
    track: str
    track_label: str
    position: int
    source_group: str | None = None
    source_group_key: str | None = None
    source_path: tuple[PathPart, ...] | None = None
    origin: str
    editable: bool
    can_move_up: bool
    can_move_down: bool
    can_move_left: bool = False
    can_move_right: bool = False
    can_remove: bool = False


class StudioViewAvailableField(StudioModel):
    """One entity field that can be added to a locally owned view structure."""

    name: str
    label: str
    field_type: str


class StudioViewTrack(StudioModel):
    """One ordered TUI placement track such as columns or a form side."""

    key: str
    label: str
    fields: tuple[str, ...]


class StudioViewGroup(StudioModel):
    """One field-bearing layout group in its resolved document order."""

    key: str
    label: str
    position: int
    field_count: int
    source_path: tuple[PathPart, ...] | None = None
    editable: bool
    can_move_up: bool
    can_move_down: bool
    can_remove: bool


class StudioViewSection(StudioModel):
    """One ordered form layout section, including groups and collections."""

    key: str
    kind: Literal["group", "collection"]
    label: str
    position: int
    tab: str | None = None
    collection: str | None = None
    inline_view: str | None = None
    actions: tuple[str, ...] = ()
    available_actions: tuple[str, ...] = ()
    source_path: tuple[PathPart, ...] | None = None
    editable: bool
    can_move_up: bool
    can_move_down: bool
    can_remove: bool


class StudioViewAvailableCollection(StudioModel):
    """One unused collection field and its compatible inline editor views."""

    name: str
    label: str
    target_entity: str
    inline_views: tuple[str, ...]


class StudioViewStructure(StudioModel):
    """Resolved view placement data shared by structural designer adapters."""

    target: DesignerDocumentReference
    view: str
    entity: str
    kind: str
    file: str
    candidate_fingerprint: str
    tracks: tuple[StudioViewTrack, ...]
    fields: tuple[StudioViewField, ...]
    editable: bool
    groups: tuple[StudioViewGroup, ...] = ()
    sections: tuple[StudioViewSection, ...] = ()
    available_fields: tuple[StudioViewAvailableField, ...] = ()
    available_collections: tuple[StudioViewAvailableCollection, ...] = ()
    record_actions: tuple[str, ...] = ()
    available_record_actions: tuple[str, ...] = ()
    actions_editable: bool = False
    can_create_group: bool = False
    writes_performed: Literal[False] = False
    application_database_accessed: Literal[False] = False


class StudioPreviewAccess(StudioModel):
    """One entity operation as resolved for a preview role."""

    operation: StudioOperation
    allowed: bool
    permission: str | None = None


class StudioPreviewField(StudioModel):
    """One role-aware field placement in a renderer-neutral preview."""

    key: str
    name: str
    label: str
    track: str
    track_label: str
    field_type: str
    status: Literal["editable", "conditional", "read_only", "protected", "hidden"]
    reason: str


class StudioPreviewAction(StudioModel):
    """One record or collection action shown by the preview contract."""

    name: str
    label: str
    bar: str
    enabled: bool
    runtime_condition: bool = False
    reason: str


class StudioViewPreview(StudioModel):
    """Database-free role and terminal-size preview of one resolved view."""

    target: DesignerDocumentReference
    view: str
    entity: str
    kind: str
    role: str | None
    available_roles: tuple[str, ...]
    effective_permissions: tuple[str, ...]
    width: int
    height: int
    minimum_width: int | None = None
    minimum_height: int | None = None
    content_width: int | None = None
    fit: Literal["fits", "constrained", "blocked"]
    access: tuple[StudioPreviewAccess, ...]
    fields: tuple[StudioPreviewField, ...]
    sections: tuple[StudioViewSection, ...]
    actions: tuple[StudioPreviewAction, ...]
    warnings: tuple[str, ...]
    candidate_fingerprint: str
    writes_performed: Literal[False] = False
    application_database_accessed: Literal[False] = False
    application_code_executed: Literal[False] = False


class StudioSaveReview(StudioModel):
    """No-write save review plus optional interrupted-transaction guidance."""

    preparation: DesignerSavePreparation
    recovery: DesignerRecoveryPreparation | None = None
    recovery_command: str | None = None
    writes_performed: Literal[False] = False
    application_database_accessed: Literal[False] = False


class StudioSaveResult(StudioModel):
    """Saved source receipt plus the freshly reopened clean Studio state."""

    state: StudioSessionState
    approval_id: str
    changed_files: tuple[str, ...]
    receipt_path: str
    saved_at: str
    writes_performed: Literal[True] = True
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
