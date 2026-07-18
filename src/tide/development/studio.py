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

from tide.compiler.normalized import NormalizedEntity, ResolvedView
from tide.development.designer import (
    DesignerCommandBatch,
    DesignerDocumentReference,
    DesignerError,
    DesignerInsertSequenceItemCommand,
    DesignerMoveSequenceItemCommand,
    DesignerRemoveValueCommand,
    DesignerReplaceDocumentSourceCommand,
    DesignerService,
    DesignerSession,
    DesignerSetValueCommand,
    DesignerSnapshot,
    PathPart,
)
from tide.development.designer_recovery import (
    DesignerRecoveryError,
    DesignerRecoveryPreparation,
    DesignerRecoveryService,
)
from tide.development.designer_save import (
    DesignerSaveApproval,
    DesignerSaveError,
    DesignerSavePreparation,
    DesignerSaveService,
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
from tide.runtime import Channel, Principal, RequestContext
from tide.security import SecurityEngine


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

    operation: Literal["list", "read", "create", "update", "delete"]
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
    minimum_width: int
    minimum_height: int
    content_width: int
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
        self._save = DesignerSaveService()
        self._recovery = DesignerRecoveryService(project)
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

    def view_structure(
        self,
        target: DesignerDocumentReference,
    ) -> StudioViewStructure:
        """Return the resolved TUI field tracks for one view document."""

        if target.kind != "view" or target.name is None:
            raise StudioError("Studio view structure requires a view document")
        content = self._session.document(target)
        document = _load_studio_yaml(content.file, content.content)
        if not isinstance(document, Mapping):
            raise StudioError(f"cannot structure {content.file}: expected a mapping")
        try:
            model = self._session.application_model()
        except DesignerError as error:
            raise StudioError(str(error)) from error
        resolved = model.views.get(target.name)
        if resolved is None:
            raise StudioError(f"unknown resolved Studio view {target.name}")
        entity = model.entity(resolved.entity)
        tracks: list[StudioViewTrack] = []
        fields: list[StudioViewField] = []
        groups: tuple[StudioViewGroup, ...] = ()
        sections: tuple[StudioViewSection, ...] = ()
        raw_layout: Any = None

        resolved_columns = tuple(str(name) for name in resolved.data.get("columns", ()))
        if resolved_columns:
            raw_columns = document.get("columns")
            column_paths = (
                tuple(("columns", index) for index in range(len(raw_columns)))
                if _studio_sequence(raw_columns)
                and tuple(str(name) for name in raw_columns) == resolved_columns
                else tuple(None for _name in resolved_columns)
            )
            label = "Lookup columns" if resolved.kind == "lookup" else "Table columns"
            _append_view_track(
                tracks,
                fields,
                key="columns",
                label=label,
                entries=tuple(
                    (name, column_paths[index], None)
                    for index, name in enumerate(resolved_columns)
                ),
                entity=entity,
                origin=_view_origin(resolved, "columns"),
            )

        if resolved.kind in {"form", "inline_edit"}:
            raw_layout = document.get("layout")
            layout = (
                raw_layout
                if _studio_sequence(raw_layout)
                else resolved.data.get("layout", ())
            )
            slots = _view_layout_slots(
                layout,
                source_paths=_studio_sequence(raw_layout),
            )
            groups = _view_layout_groups(
                layout,
                source_paths=_studio_sequence(raw_layout),
            )
            sections = _view_layout_sections(
                layout,
                source_paths=_studio_sequence(raw_layout),
                entity=entity,
            )
            if resolved.kind == "inline_edit":
                left = tuple(slot for slot in slots if slot[3] == 0)
                right = tuple(slot for slot in slots if slot[3] == 1)
                prefix = "Line editor"
            else:
                left = slots[::2]
                right = slots[1::2]
                prefix = "Form"
            for side, entries in (("left", left), ("right", right)):
                if entries:
                    _append_view_track(
                        tracks,
                        fields,
                        key=f"layout-{side}",
                        label=f"{prefix} · {side} column",
                        entries=tuple(
                            (name, path, group)
                            for name, path, group, _column in entries
                        ),
                        entity=entity,
                        origin=_view_origin(resolved, "layout"),
                    )

        resolved_fields = _view_field_capabilities(
            tuple(fields),
            kind=resolved.kind,
        )
        can_add = _view_can_add_fields(
            resolved,
            document,
            resolved_columns=resolved_columns,
        )
        available_fields = (
            _available_view_fields(resolved, entity, resolved_fields) if can_add else ()
        )
        owns_layout = bool(_studio_sequence(raw_layout))
        available_collections = (
            _available_view_collections(model, entity, sections)
            if resolved.kind == "form" and owns_layout
            else ()
        )
        configured_actions = tuple(
            str(name) for name in resolved.data.get("actions", ())
        )
        available_record_actions = (
            ("cancel", "save", *entity.actions) if resolved.kind == "form" else ()
        )
        record_actions = (
            configured_actions or available_record_actions
            if resolved.kind == "form"
            else ()
        )

        return StudioViewStructure(
            target=target,
            view=resolved.name,
            entity=resolved.entity,
            kind=resolved.kind,
            file=content.file,
            candidate_fingerprint=content.candidate_fingerprint,
            tracks=tuple(tracks),
            fields=resolved_fields,
            groups=groups,
            sections=sections,
            available_fields=available_fields,
            available_collections=available_collections,
            record_actions=record_actions,
            available_record_actions=available_record_actions,
            actions_editable=resolved.kind == "form",
            can_create_group=bool(
                resolved.kind in {"form", "inline_edit"}
                and _studio_sequence(document.get("layout"))
            ),
            editable=bool(
                available_fields
                or available_collections
                or resolved.kind == "form"
                or any(section.editable for section in sections)
                or any(group.editable for group in groups)
                or any(field.editable or field.can_remove for field in resolved_fields)
            ),
        )

    def preview_view(
        self,
        target: DesignerDocumentReference,
        *,
        role: str | None,
        width: int,
        height: int,
    ) -> StudioViewPreview:
        """Resolve a database-free view preview for one role and terminal size."""

        if target.kind != "view" or target.name is None:
            raise StudioError("Studio view preview requires a view document")
        if width < 40 or width > 300:
            raise StudioError("preview width must be between 40 and 300 columns")
        if height < 12 or height > 120:
            raise StudioError("preview height must be between 12 and 120 rows")
        try:
            model = self._session.application_model()
        except DesignerError as error:
            raise StudioError(str(error)) from error
        if role is not None and role not in model.roles:
            raise StudioError(f"unknown Studio preview role {role!r}")
        view = model.views.get(target.name)
        if view is None:
            raise StudioError(f"unknown resolved Studio view {target.name}")
        structure = self.view_structure(target)
        entity = model.entity(view.entity)
        security = SecurityEngine(model)
        context = RequestContext(
            principal=Principal(
                f"studio-preview:{role or 'no-role'}",
                roles=frozenset({role}) if role is not None else frozenset(),
            ),
            channel=Channel.TUI,
        )
        access = tuple(
            StudioPreviewAccess(
                operation=operation,
                allowed=security.can_access_entity(entity, operation, context),
                permission=entity.metadata.get("permissions", {}).get(operation),
            )
            for operation in ("list", "read", "create", "update", "delete")
        )
        access_by_operation = {item.operation: item.allowed for item in access}
        may_write_record = bool(
            access_by_operation["create"] or access_by_operation["update"]
        )
        preview_fields = [
            _preview_field(
                placement.key,
                placement.name,
                placement.label,
                placement.track,
                placement.track_label,
                placement.field_type,
                entity,
                view,
                security,
                context,
                may_write_record=may_write_record,
                view_kind=structure.kind,
            )
            for placement in structure.fields
        ]
        for section in structure.sections:
            if section.kind != "collection" or section.collection is None:
                continue
            field = entity.field(section.collection)
            preview_fields.append(
                _preview_field(
                    f"{section.key}:collection",
                    section.collection,
                    section.label,
                    section.key,
                    f"Collection · {section.label}",
                    _view_field_type(field),
                    entity,
                    view,
                    security,
                    context,
                    may_write_record=may_write_record,
                    view_kind=structure.kind,
                )
            )
        actions = _preview_actions(
            structure,
            entity,
            security,
            context,
            access_by_operation=access_by_operation,
            fields=tuple(preview_fields),
        )
        minimum_width, content_width = _preview_widths(view, structure)
        minimum_height = _preview_minimum_height(view, structure)
        warnings: list[str] = []
        exposed = bool(entity.metadata.get("expose", {}).get("tui", False))
        required_access = _preview_required_access(
            structure.kind,
            access_by_operation,
        )
        if not exposed:
            warnings.append(f"{entity.name} is not exposed to the TUI.")
        if not required_access:
            warnings.append(
                f"Role {role or '(no role)'} cannot open this {structure.kind} view."
            )
        if width < minimum_width:
            warnings.append(
                f"Width {width} is below the declared minimum of {minimum_width}."
            )
        elif content_width > width:
            warnings.append(
                f"Content is approximately {content_width} columns wide; horizontal "
                "scrolling or compression is required."
            )
        if height < minimum_height:
            warnings.append(
                f"Height {height} is below the estimated minimum of {minimum_height}."
            )
        row_policy_count = sum(
            policy.get("entity") == entity.name for policy in model.row_policies
        )
        if row_policy_count:
            warnings.append(
                f"{row_policy_count} row policy rule(s) still evaluate against actual "
                "records at runtime."
            )
        protected_count = sum(
            field.status == "protected" for field in preview_fields
        )
        if protected_count:
            warnings.append(
                f"{protected_count} field placement(s) will display protected values."
            )
        if not exposed or not required_access:
            fit: Literal["fits", "constrained", "blocked"] = "blocked"
        elif width < minimum_width or height < minimum_height:
            fit = "constrained"
        else:
            fit = "fits"
        return StudioViewPreview(
            target=target,
            view=view.name,
            entity=entity.name,
            kind=structure.kind,
            role=role,
            available_roles=tuple(sorted(model.roles)),
            effective_permissions=tuple(
                sorted(security.effective_permissions(context.principal))
            ),
            width=width,
            height=height,
            minimum_width=minimum_width,
            minimum_height=minimum_height,
            content_width=content_width,
            fit=fit,
            access=access,
            fields=tuple(preview_fields),
            sections=structure.sections,
            actions=actions,
            warnings=tuple(warnings),
            candidate_fingerprint=self._state.workspace.candidate_fingerprint,
        )

    def move_view_field(
        self,
        target: DesignerDocumentReference,
        field_key: str,
        offset: Literal[-1, 1],
    ) -> StudioSessionState:
        """Move a view field within its current resolved TUI placement track."""

        if offset not in {-1, 1}:
            raise StudioError("Studio view fields move by exactly one position")
        structure = self.view_structure(target)
        selected = next(
            (field for field in structure.fields if field.key == field_key),
            None,
        )
        if selected is None:
            raise StudioError(f"unknown Studio view field {field_key}")
        if (offset < 0 and not selected.can_move_up) or (
            offset > 0 and not selected.can_move_down
        ):
            raise StudioError(
                "the Studio view field cannot cross its current group boundary"
            )
        track = next(item for item in structure.tracks if item.key == selected.track)
        destination = selected.position + offset
        if destination < 0 or destination >= len(track.fields):
            raise StudioError("the Studio view field is already at that boundary")
        neighbor_key = track.fields[destination]
        neighbor = next(
            field for field in structure.fields if field.key == neighbor_key
        )
        if selected.source_path is None or neighbor.source_path is None:
            raise StudioError(
                "inherited or generated view fields are not directly reorderable"
            )

        selected_parent = selected.source_path[:-1]
        neighbor_parent = neighbor.source_path[:-1]
        selected_index = selected.source_path[-1]
        neighbor_index = neighbor.source_path[-1]
        if (
            selected.track == "columns"
            and selected_parent == neighbor_parent
            and isinstance(selected_index, int)
            and isinstance(neighbor_index, int)
        ):
            snapshot = self._session.execute(
                DesignerMoveSequenceItemCommand(
                    target=target,
                    path=selected_parent,
                    from_index=selected_index,
                    to_index=neighbor_index,
                )
            )
        else:
            snapshot = self._session.execute_batch(
                DesignerCommandBatch(
                    commands=(
                        DesignerSetValueCommand(
                            target=target,
                            path=selected.source_path,
                            value=neighbor.name,
                        ),
                        DesignerSetValueCommand(
                            target=target,
                            path=neighbor.source_path,
                            value=selected.name,
                        ),
                    )
                )
            )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def move_view_field_across(
        self,
        target: DesignerDocumentReference,
        field_key: str,
        direction: Literal[-1, 1],
    ) -> StudioSessionState:
        """Swap a layout field with the same-position field across its group."""

        if direction not in {-1, 1}:
            raise StudioError("Studio view fields swap by exactly one column")
        structure = self.view_structure(target)
        selected = next(
            (field for field in structure.fields if field.key == field_key),
            None,
        )
        if selected is None:
            raise StudioError(f"unknown Studio view field {field_key}")
        allowed = selected.can_move_left if direction < 0 else selected.can_move_right
        if not allowed:
            raise StudioError(
                "cross-column movement requires a local field at the same "
                "position in the same layout group"
            )
        destination_track = "layout-left" if direction < 0 else "layout-right"
        neighbor = next(
            field
            for field in structure.fields
            if field.track == destination_track
            and field.position == selected.position
            and _same_layout_section(field, selected)
        )
        assert selected.source_path is not None
        assert neighbor.source_path is not None
        snapshot = self._session.execute_batch(
            DesignerCommandBatch(
                label=f"Swap {selected.name} across view columns",
                commands=(
                    DesignerSetValueCommand(
                        target=target,
                        path=selected.source_path,
                        value=neighbor.name,
                    ),
                    DesignerSetValueCommand(
                        target=target,
                        path=neighbor.source_path,
                        value=selected.name,
                    ),
                ),
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def add_view_field(
        self,
        target: DesignerDocumentReference,
        field_name: str,
        *,
        near_field_key: str | None = None,
        destination_group_key: str | None = None,
    ) -> StudioSessionState:
        """Add an existing entity field to a locally owned view structure."""

        structure = self.view_structure(target)
        available = {field.name: field for field in structure.available_fields}
        if field_name not in available:
            raise StudioError(f"view field {field_name!r} is not available to add")
        content = self._session.document(target)
        document = _load_studio_yaml(content.file, content.content)
        if not isinstance(document, Mapping):
            raise StudioError(f"cannot edit {content.file}: expected a mapping")
        near = next(
            (field for field in structure.fields if field.key == near_field_key),
            None,
        )
        destination_group = (
            _require_view_group(structure, destination_group_key)
            if destination_group_key is not None
            else None
        )
        if destination_group is not None and not destination_group.editable:
            raise StudioError("fields can be added only to a local view group")
        commands: list[DesignerInsertSequenceItemCommand] = []
        raw_columns = document.get("columns")
        if structure.kind in {"browse", "lookup", "inline_edit"}:
            if not _studio_sequence(raw_columns):
                raise StudioError("the view does not own a local columns sequence")
            commands.append(
                DesignerInsertSequenceItemCommand(
                    target=target,
                    path=("columns",),
                    index=len(raw_columns),
                    value=field_name,
                )
            )
        if structure.kind in {"form", "inline_edit"}:
            commands.append(
                _layout_add_command(
                    target,
                    document,
                    field_name,
                    near=near,
                    destination_group=destination_group,
                    balance_inline=structure.kind == "inline_edit",
                )
            )
        snapshot = self._session.execute_batch(
            DesignerCommandBatch(
                label=f"Add {field_name} to {structure.view}",
                commands=tuple(commands),
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def remove_view_field(
        self,
        target: DesignerDocumentReference,
        field_key: str,
    ) -> StudioSessionState:
        """Remove a locally owned field placement without changing its entity."""

        structure = self.view_structure(target)
        selected = next(
            (field for field in structure.fields if field.key == field_key),
            None,
        )
        if selected is None:
            raise StudioError(f"unknown Studio view field {field_key}")
        if not selected.can_remove:
            raise StudioError("the selected view field cannot be removed safely")
        content = self._session.document(target)
        document = _load_studio_yaml(content.file, content.content)
        if not isinstance(document, Mapping):
            raise StudioError(f"cannot edit {content.file}: expected a mapping")
        commands: list[DesignerRemoveValueCommand] = []
        column_field = next(
            (
                field
                for field in structure.fields
                if field.track == "columns" and field.name == selected.name
            ),
            None,
        )
        layout_field = next(
            (
                field
                for field in structure.fields
                if field.track.startswith("layout-") and field.name == selected.name
            ),
            None,
        )
        if column_field is not None and column_field.source_path is not None:
            commands.append(
                DesignerRemoveValueCommand(
                    target=target,
                    path=column_field.source_path,
                )
            )
        if layout_field is not None and layout_field.source_path is not None:
            commands.append(
                _layout_remove_command(target, document, layout_field.source_path)
            )
        snapshot = self._session.execute_batch(
            DesignerCommandBatch(
                label=f"Remove {selected.name} from {structure.view}",
                commands=tuple(commands),
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def create_view_group(
        self,
        target: DesignerDocumentReference,
        label: str,
    ) -> StudioSessionState:
        """Append one empty local field group to a form or inline layout."""

        structure = self.view_structure(target)
        if structure.kind not in {"form", "inline_edit"}:
            raise StudioError("only form and inline views contain field groups")
        normalized = _normalize_view_group_label(label)
        _require_unique_view_group_label(structure, normalized)
        content = self._session.document(target)
        document = _load_studio_yaml(content.file, content.content)
        if not isinstance(document, Mapping):
            raise StudioError(f"cannot edit {content.file}: expected a mapping")
        layout = document.get("layout")
        if not _studio_sequence(layout):
            raise StudioError("the view does not own a local layout sequence")
        snapshot = self._session.execute(
            DesignerInsertSequenceItemCommand(
                target=target,
                path=("layout",),
                index=len(layout),
                value={"group": normalized, "rows": []},
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def rename_view_group(
        self,
        target: DesignerDocumentReference,
        group_key: str,
        label: str,
    ) -> StudioSessionState:
        """Rename one locally owned field group without changing its fields."""

        structure = self.view_structure(target)
        selected = _require_view_group(structure, group_key)
        if not selected.editable or selected.source_path is None:
            raise StudioError("inherited or generated groups cannot be renamed")
        normalized = _normalize_view_group_label(label)
        _require_unique_view_group_label(
            structure,
            normalized,
            except_key=selected.key,
        )
        snapshot = self._session.execute(
            DesignerSetValueCommand(
                target=target,
                path=(*selected.source_path, "group"),
                value=normalized,
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def move_view_group(
        self,
        target: DesignerDocumentReference,
        group_key: str,
        offset: Literal[-1, 1],
    ) -> StudioSessionState:
        """Move a local field group across one adjacent field group."""

        if offset not in {-1, 1}:
            raise StudioError("Studio view groups move by exactly one position")
        structure = self.view_structure(target)
        selected = _require_view_group(structure, group_key)
        if (offset < 0 and not selected.can_move_up) or (
            offset > 0 and not selected.can_move_down
        ):
            raise StudioError(
                "the view group cannot cross a collection or layout boundary"
            )
        assert selected.source_path is not None
        snapshot = self._session.execute(
            DesignerMoveSequenceItemCommand(
                target=target,
                path=selected.source_path[:-1],
                from_index=selected.position,
                to_index=selected.position + offset,
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def remove_view_group(
        self,
        target: DesignerDocumentReference,
        group_key: str,
    ) -> StudioSessionState:
        """Remove a locally owned group only after all fields leave it."""

        structure = self.view_structure(target)
        selected = _require_view_group(structure, group_key)
        if not selected.can_remove or selected.source_path is None:
            raise StudioError("only an empty local view group can be removed")
        snapshot = self._session.execute(
            DesignerRemoveValueCommand(
                target=target,
                path=selected.source_path,
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def set_view_section_tab(
        self,
        target: DesignerDocumentReference,
        section_key: str,
        label: str | None,
    ) -> StudioSessionState:
        """Assign or clear a portable tab label on one local layout section."""

        structure = self.view_structure(target)
        selected = _require_view_section(structure, section_key)
        if not selected.editable or selected.source_path is None:
            raise StudioError("inherited or generated layout sections cannot be edited")
        content = self._session.document(target)
        document = _load_studio_yaml(content.file, content.content)
        normalized = _normalize_optional_presentation_label(label, name="tab")
        path = (*selected.source_path, "tab")
        section = _resolve_property(document, selected.source_path)
        if normalized is None:
            if not isinstance(section, Mapping) or "tab" not in section:
                return self._state
            command: DesignerRemoveValueCommand | DesignerSetValueCommand = (
                DesignerRemoveValueCommand(target=target, path=path)
            )
        else:
            command = DesignerSetValueCommand(
                target=target,
                path=path,
                value=normalized,
            )
        snapshot = self._session.execute(command)
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def move_view_section(
        self,
        target: DesignerDocumentReference,
        section_key: str,
        offset: Literal[-1, 1],
    ) -> StudioSessionState:
        """Move one local group or collection section by one layout position."""

        if offset not in {-1, 1}:
            raise StudioError("Studio layout sections move by exactly one position")
        structure = self.view_structure(target)
        selected = _require_view_section(structure, section_key)
        allowed = selected.can_move_up if offset < 0 else selected.can_move_down
        if not allowed or selected.source_path is None:
            raise StudioError("the layout section is already at that boundary")
        snapshot = self._session.execute(
            DesignerMoveSequenceItemCommand(
                target=target,
                path=selected.source_path[:-1],
                from_index=selected.position,
                to_index=selected.position + offset,
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def add_view_collection(
        self,
        target: DesignerDocumentReference,
        collection_name: str,
        inline_view: str,
    ) -> StudioSessionState:
        """Append an unused collection field with a compatible inline view."""

        structure = self.view_structure(target)
        available = {
            collection.name: collection
            for collection in structure.available_collections
        }
        collection = available.get(collection_name)
        if collection is None:
            raise StudioError(f"view collection {collection_name!r} is not available")
        if inline_view not in collection.inline_views:
            raise StudioError(
                f"{inline_view!r} is not an inline view for {collection.target_entity}"
            )
        content = self._session.document(target)
        document = _load_studio_yaml(content.file, content.content)
        layout = document.get("layout") if isinstance(document, Mapping) else None
        if not _studio_sequence(layout):
            raise StudioError("the view does not own a local layout sequence")
        snapshot = self._session.execute(
            DesignerInsertSequenceItemCommand(
                target=target,
                path=("layout",),
                index=len(layout),
                value={
                    "collection": collection_name,
                    "view": inline_view,
                    "actions": ["add", "apply", "remove"],
                },
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def remove_view_collection(
        self,
        target: DesignerDocumentReference,
        section_key: str,
    ) -> StudioSessionState:
        """Remove one local collection placement without changing the entity field."""

        structure = self.view_structure(target)
        selected = _require_view_section(structure, section_key)
        if (
            selected.kind != "collection"
            or not selected.can_remove
            or selected.source_path is None
        ):
            raise StudioError("only local collection sections can be removed")
        snapshot = self._session.execute(
            DesignerRemoveValueCommand(target=target, path=selected.source_path)
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

    def set_view_action_order(
        self,
        target: DesignerDocumentReference,
        bar_key: str,
        actions: tuple[str, ...],
    ) -> StudioSessionState:
        """Set one local form or collection action bar as an ordered sequence."""

        structure = self.view_structure(target)
        if bar_key == "record":
            if not structure.actions_editable:
                raise StudioError("record action bars require a form view")
            allowed = structure.available_record_actions
            path: tuple[PathPart, ...] = ("actions",)
        else:
            section = _require_view_section(structure, bar_key)
            if section.kind != "collection" or section.source_path is None:
                raise StudioError("collection action bars require a local collection")
            allowed = section.available_actions
            path = (*section.source_path, "actions")
        normalized = _normalize_action_order(actions, allowed=allowed)
        snapshot = self._session.execute(
            DesignerSetValueCommand(
                target=target,
                path=path,
                value=list(normalized),
            )
        )
        self._state = self._build_state(self._session, snapshot)
        return self._state

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

    def prepare_save(self) -> StudioSaveReview:
        """Inspect the exact candidate and live base without writing files."""

        try:
            preparation = self._save.prepare(self._session)
            recovery: DesignerRecoveryPreparation | None = None
            recovery_command: str | None = None
            if any(blocker.code == "TIDEDSAVE006" for blocker in preparation.blockers):
                recovery = self._recovery.prepare()
                recovery_command = (
                    "uv run tide designer recover "
                    f'"{preparation.project_path}" --preview'
                )
        except (DesignerSaveError, DesignerRecoveryError) as error:
            raise StudioError(str(error)) from error
        return StudioSaveReview(
            preparation=preparation,
            recovery=recovery,
            recovery_command=recovery_command,
        )

    def save(
        self,
        review: StudioSaveReview,
        approval_text: str,
    ) -> StudioSaveResult:
        """Save one exactly reviewed candidate through DesignerSaveService."""

        preparation = review.preparation
        if not preparation.ready or preparation.approval_prompt is None:
            if preparation.blockers:
                blocker = preparation.blockers[0]
                raise StudioError(f"{blocker.code}: {blocker.message}")
            raise StudioError("the Studio candidate is not ready to save")
        if approval_text != preparation.approval_prompt:
            raise StudioError("the save approval phrase does not match")
        try:
            approval = DesignerSaveApproval.from_preparation(preparation)
            result = self._save.save(self._session, approval)
        except (DesignerSaveError, ValueError) as error:
            self._state = self._build_state(self._session)
            message = str(error)
            if isinstance(error, DesignerSaveError) and error.code == "TIDEDSAVE010":
                message += (
                    "; close Studio and inspect recovery with "
                    f'uv run tide designer recover "{preparation.project_path}" '
                    "--preview"
                )
            raise StudioError(message) from error

        self._session = self._designer.open_session()
        self._state = self._build_state(self._session)
        return StudioSaveResult(
            state=self._state,
            approval_id=result.approval_id,
            changed_files=result.changed_files,
            receipt_path=result.receipt_path,
            saved_at=result.saved_at,
        )

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


def _append_view_track(
    tracks: list[StudioViewTrack],
    fields: list[StudioViewField],
    *,
    key: str,
    label: str,
    entries: tuple[tuple[str, tuple[PathPart, ...] | None, str | None], ...],
    entity: NormalizedEntity,
    origin: str,
) -> None:
    field_keys: list[str] = []
    for position, (name, source_path, source_group) in enumerate(entries):
        field = entity.fields.get(name)
        field_key = f"{key}:{name}"
        field_keys.append(field_key)
        previous = entries[position - 1] if position > 0 else None
        following = entries[position + 1] if position + 1 < len(entries) else None
        previous_path = (
            previous[1]
            if previous is not None
            and _same_track_group(previous, (name, source_path, source_group))
            else None
        )
        next_path = (
            following[1]
            if following is not None
            and _same_track_group(following, (name, source_path, source_group))
            else None
        )
        fields.append(
            StudioViewField(
                key=field_key,
                name=name,
                label=_view_field_label(name, field),
                field_type=_view_field_type(field),
                track=key,
                track_label=label,
                position=position,
                source_group=source_group,
                source_group_key=_view_group_key_from_path(source_path),
                source_path=source_path,
                origin=origin,
                editable=source_path is not None,
                can_move_up=source_path is not None and previous_path is not None,
                can_move_down=source_path is not None and next_path is not None,
            )
        )
    tracks.append(StudioViewTrack(key=key, label=label, fields=tuple(field_keys)))


def _same_track_group(
    left: tuple[str, tuple[PathPart, ...] | None, str | None],
    right: tuple[str, tuple[PathPart, ...] | None, str | None],
) -> bool:
    if left[2] != right[2]:
        return False
    left_path = left[1]
    right_path = right[1]
    if (
        left_path is not None
        and right_path is not None
        and left_path[0] == "layout"
        and right_path[0] == "layout"
    ):
        return left_path[:2] == right_path[:2]
    return True


def _view_layout_groups(
    layout: Any,
    *,
    source_paths: bool,
) -> tuple[StudioViewGroup, ...]:
    if not _studio_sequence(layout):
        return ()
    groups: list[StudioViewGroup] = []
    for index, section in enumerate(layout):
        if not _view_group_section(section):
            continue
        rows = section["rows"]
        field_count = sum(
            1
            for row in rows
            if _studio_sequence(row)
            for name in row
            if isinstance(name, str)
        )
        source_path = ("layout", index) if source_paths else None
        previous_is_group = index > 0 and _view_group_section(layout[index - 1])
        next_is_group = index + 1 < len(layout) and _view_group_section(
            layout[index + 1]
        )
        groups.append(
            StudioViewGroup(
                key=f"layout-group:{index}",
                label=str(section["group"]),
                position=index,
                field_count=field_count,
                source_path=source_path,
                editable=source_path is not None,
                can_move_up=source_path is not None and previous_is_group,
                can_move_down=source_path is not None and next_is_group,
                can_remove=source_path is not None and field_count == 0,
            )
        )
    return tuple(groups)


def _view_layout_sections(
    layout: Any,
    *,
    source_paths: bool,
    entity: NormalizedEntity,
) -> tuple[StudioViewSection, ...]:
    if not _studio_sequence(layout):
        return ()
    sections: list[StudioViewSection] = []
    for index, section in enumerate(layout):
        if not isinstance(section, Mapping):
            continue
        is_group = _view_group_section(section)
        collection_name = section.get("collection")
        is_collection = (
            isinstance(collection_name, str) and collection_name in entity.fields
        )
        if not is_group and not is_collection:
            continue
        source_path = ("layout", index) if source_paths else None
        if is_collection:
            field = entity.field(collection_name)
            configured_actions = tuple(str(name) for name in section.get("actions", ()))
            actions = configured_actions or ("add", "apply", "remove")
            label = _view_field_label(collection_name, field)
            kind: Literal["group", "collection"] = "collection"
        else:
            actions = ()
            label = str(section["group"])
            kind = "group"
        sections.append(
            StudioViewSection(
                key=f"layout-section:{index}",
                kind=kind,
                label=label,
                position=index,
                tab=(str(section["tab"]) if section.get("tab") else None),
                collection=(str(collection_name) if is_collection else None),
                inline_view=(
                    str(section["view"])
                    if is_collection and section.get("view")
                    else None
                ),
                actions=actions,
                available_actions=(("add", "apply", "remove") if is_collection else ()),
                source_path=source_path,
                editable=source_path is not None,
                can_move_up=source_path is not None and index > 0,
                can_move_down=source_path is not None and index + 1 < len(layout),
                can_remove=source_path is not None and is_collection,
            )
        )
    return tuple(sections)


def _available_view_collections(
    model: Any,
    entity: NormalizedEntity,
    sections: tuple[StudioViewSection, ...],
) -> tuple[StudioViewAvailableCollection, ...]:
    used = {section.collection for section in sections if section.collection}
    available: list[StudioViewAvailableCollection] = []
    for name, field in entity.fields.items():
        metadata = field.metadata
        target = metadata.get("target")
        if name in used or metadata.get("type") != "collection" or not target:
            continue
        inline_views = tuple(
            view.name
            for view in model.views.values()
            if view.kind == "inline_edit" and view.entity == target
        )
        if not inline_views:
            continue
        available.append(
            StudioViewAvailableCollection(
                name=name,
                label=_view_field_label(name, field),
                target_entity=str(target),
                inline_views=inline_views,
            )
        )
    return tuple(available)


def _view_group_section(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and isinstance(value.get("group"), str)
        and value.get("group")
        and _studio_sequence(value.get("rows"))
    )


def _view_group_key_from_path(
    source_path: tuple[PathPart, ...] | None,
) -> str | None:
    if (
        source_path is not None
        and len(source_path) >= 2
        and source_path[0] == "layout"
        and isinstance(source_path[1], int)
    ):
        return f"layout-group:{source_path[1]}"
    return None


def _require_view_group(
    structure: StudioViewStructure,
    group_key: str,
) -> StudioViewGroup:
    selected = next(
        (group for group in structure.groups if group.key == group_key),
        None,
    )
    if selected is None:
        raise StudioError(f"unknown Studio view group {group_key}")
    return selected


def _require_view_section(
    structure: StudioViewStructure,
    section_key: str,
) -> StudioViewSection:
    selected = next(
        (section for section in structure.sections if section.key == section_key),
        None,
    )
    if selected is None:
        raise StudioError(f"unknown Studio view section {section_key}")
    return selected


def _normalize_optional_presentation_label(
    label: str | None,
    *,
    name: str,
) -> str | None:
    normalized = label.strip() if label is not None else ""
    if not normalized:
        return None
    if len(normalized) > 80:
        raise StudioError(f"{name} labels must not exceed 80 characters")
    if any(character in normalized for character in ("\r", "\n", "\x00")):
        raise StudioError(f"{name} labels must fit on one safe text line")
    return normalized


def _normalize_action_order(
    actions: tuple[str, ...],
    *,
    allowed: tuple[str, ...],
) -> tuple[str, ...]:
    if len(set(actions)) != len(actions):
        raise StudioError("action bars cannot contain duplicate actions")
    unknown = tuple(action for action in actions if action not in allowed)
    if unknown:
        raise StudioError("unknown action bar items: " + ", ".join(unknown))
    return actions


def _normalize_view_group_label(label: str) -> str:
    normalized = label.strip()
    if not normalized:
        raise StudioError("view group labels must not be empty")
    if len(normalized) > 80:
        raise StudioError("view group labels must not exceed 80 characters")
    if any(character in normalized for character in ("\r", "\n", "\x00")):
        raise StudioError("view group labels must fit on one safe text line")
    return normalized


def _require_unique_view_group_label(
    structure: StudioViewStructure,
    label: str,
    *,
    except_key: str | None = None,
) -> None:
    duplicate = next(
        (
            group
            for group in structure.groups
            if group.key != except_key and group.label.casefold() == label.casefold()
        ),
        None,
    )
    if duplicate is not None:
        raise StudioError(f"view group {label!r} already exists")


def _view_field_capabilities(
    fields: tuple[StudioViewField, ...],
    *,
    kind: str,
) -> tuple[StudioViewField, ...]:
    columns = tuple(field for field in fields if field.track == "columns")
    layout = tuple(field for field in fields if field.track.startswith("layout-"))
    updated: list[StudioViewField] = []
    for field in fields:
        left = next(
            (
                candidate
                for candidate in layout
                if candidate.track == "layout-left"
                and candidate.position == field.position
                and _same_layout_section(candidate, field)
            ),
            None,
        )
        right = next(
            (
                candidate
                for candidate in layout
                if candidate.track == "layout-right"
                and candidate.position == field.position
                and _same_layout_section(candidate, field)
            ),
            None,
        )
        column = next(
            (candidate for candidate in columns if candidate.name == field.name),
            None,
        )
        layout_copy = next(
            (candidate for candidate in layout if candidate.name == field.name),
            None,
        )
        if field.track == "columns":
            can_remove = bool(
                field.source_path is not None
                and len(columns) > 1
                and (
                    kind != "inline_edit"
                    or layout_copy is None
                    or layout_copy.source_path is not None
                )
            )
        else:
            can_remove = bool(
                field.source_path is not None
                and (
                    (kind == "form" and len(layout) > 1)
                    or (
                        kind == "inline_edit"
                        and len(columns) > 1
                        and column is not None
                        and column.source_path is not None
                    )
                )
            )
        updated.append(
            field.model_copy(
                update={
                    "can_move_left": bool(
                        field.track == "layout-right"
                        and left is not None
                        and left.source_path is not None
                        and field.source_path is not None
                    ),
                    "can_move_right": bool(
                        field.track == "layout-left"
                        and right is not None
                        and right.source_path is not None
                        and field.source_path is not None
                    ),
                    "can_remove": can_remove,
                }
            )
        )
    return tuple(updated)


def _same_layout_section(
    left: StudioViewField,
    right: StudioViewField,
) -> bool:
    if left.source_path is None or right.source_path is None:
        return False
    return (
        len(left.source_path) >= 2
        and len(right.source_path) >= 2
        and left.source_path[:2] == right.source_path[:2]
    )


def _view_can_add_fields(
    view: ResolvedView,
    document: Mapping[str, Any],
    *,
    resolved_columns: tuple[str, ...],
) -> bool:
    raw_columns = document.get("columns")
    owns_columns = bool(
        _studio_sequence(raw_columns)
        and tuple(str(name) for name in raw_columns) == resolved_columns
    )
    raw_layout = document.get("layout")
    owns_layout = bool(
        _studio_sequence(raw_layout)
        and any(
            isinstance(section, Mapping) and _studio_sequence(section.get("rows"))
            for section in raw_layout
        )
    )
    if view.kind in {"browse", "lookup"}:
        return owns_columns
    if view.kind == "form":
        return owns_layout
    if view.kind == "inline_edit":
        return owns_columns and owns_layout
    return False


def _available_view_fields(
    view: ResolvedView,
    entity: NormalizedEntity,
    fields: tuple[StudioViewField, ...],
) -> tuple[StudioViewAvailableField, ...]:
    if view.kind in {"browse", "lookup", "inline_edit"}:
        used = {field.name for field in fields if field.track == "columns"}
    else:
        used = {field.name for field in fields if field.track.startswith("layout-")}
    view_fields = view.data.get("fields", {})
    available: list[StudioViewAvailableField] = []
    for name, field in entity.fields.items():
        metadata = field.metadata
        if name in used or metadata.get("type") == "collection":
            continue
        field_view = (
            view_fields.get(name, {}) if isinstance(view_fields, Mapping) else {}
        )
        if isinstance(field_view, Mapping) and field_view.get("hidden", False):
            continue
        if view.kind == "inline_edit" and (
            metadata.get("readonly", False) or metadata.get("computed") is not None
        ):
            continue
        available.append(
            StudioViewAvailableField(
                name=name,
                label=_view_field_label(name, field),
                field_type=_view_field_type(field),
            )
        )
    return tuple(available)


def _view_field_label(name: str, field: Any) -> str:
    if field is not None and field.metadata.get("label"):
        return str(field.metadata["label"])
    return name.replace("_", " ").title()


def _view_field_type(field: Any) -> str:
    return (
        str(field.metadata.get("type", "unknown")) if field is not None else "unknown"
    )


def _preview_field(
    key: str,
    name: str,
    label: str,
    track: str,
    track_label: str,
    field_type: str,
    entity: NormalizedEntity,
    view: ResolvedView,
    security: SecurityEngine,
    context: RequestContext,
    *,
    may_write_record: bool,
    view_kind: str,
) -> StudioPreviewField:
    field = entity.field(name)
    configuration = view.data.get("fields", {}).get(name, {})
    hidden = bool(
        isinstance(configuration, Mapping) and configuration.get("hidden", False)
    )
    if hidden:
        status: Literal[
            "editable", "conditional", "read_only", "protected", "hidden"
        ] = "hidden"
        reason = "hidden by view metadata"
    elif not security.can_read_field(entity.name, name, context):
        status = "protected"
        reason = "field read permission denied"
    elif view_kind not in {"form", "inline_edit"}:
        status = "read_only"
        reason = "displayed by a read-only view"
    elif field.metadata.get("readonly") or field.metadata.get("computed") is not None:
        status = "read_only"
        reason = "field metadata is read-only"
    elif field.metadata.get("write", "normal") != "normal":
        status = "read_only"
        reason = f"field write ownership is {field.metadata.get('write')}"
    elif not may_write_record:
        status = "read_only"
        reason = "role lacks entity create/update access"
    elif not security.can_write_field(entity.name, name, context):
        status = "read_only"
        reason = "field write permission denied"
    elif field.metadata.get("immutable_when"):
        status = "conditional"
        reason = "editable only when the record-dependent rule allows it"
    else:
        status = "editable"
        reason = "editable for this role"
    return StudioPreviewField(
        key=key,
        name=name,
        label=label,
        track=track,
        track_label=track_label,
        field_type=field_type,
        status=status,
        reason=reason,
    )


def _preview_actions(
    structure: StudioViewStructure,
    entity: NormalizedEntity,
    security: SecurityEngine,
    context: RequestContext,
    *,
    access_by_operation: Mapping[str, bool],
    fields: tuple[StudioPreviewField, ...],
) -> tuple[StudioPreviewAction, ...]:
    actions: list[StudioPreviewAction] = []
    may_write_record = bool(
        access_by_operation.get("create") or access_by_operation.get("update")
    )
    for name in structure.record_actions:
        if name == "cancel":
            actions.append(
                StudioPreviewAction(
                    name=name,
                    label="Cancel",
                    bar="record",
                    enabled=True,
                    reason="local form navigation",
                )
            )
            continue
        if name == "save":
            actions.append(
                StudioPreviewAction(
                    name=name,
                    label="Save",
                    bar="record",
                    enabled=may_write_record,
                    reason=(
                        "role may create or update records"
                        if may_write_record
                        else "role lacks entity create/update access"
                    ),
                )
            )
            continue
        action = entity.actions.get(name)
        if action is None:
            continue
        permitted = security.can_execute_action(action, context)
        conditional = bool(action.get("enabled_when") or action.get("visible_when"))
        actions.append(
            StudioPreviewAction(
                name=name,
                label=str(action.get("label") or name.replace("_", " ").title()),
                bar="record",
                enabled=permitted,
                runtime_condition=conditional,
                reason=(
                    "permission granted; final state depends on the record"
                    if permitted and conditional
                    else (
                        "action permission granted"
                        if permitted
                        else "action permission denied"
                    )
                ),
            )
        )
    fields_by_name = {field.name: field for field in fields}
    for section in structure.sections:
        if section.kind != "collection" or section.collection is None:
            continue
        collection_field = fields_by_name.get(section.collection)
        enabled = bool(
            collection_field is not None
            and collection_field.status in {"editable", "conditional"}
        )
        for name in section.actions:
            actions.append(
                StudioPreviewAction(
                    name=name,
                    label={
                        "add": "Add line",
                        "apply": "Apply line",
                        "remove": "Remove line",
                    }.get(name, name.replace("_", " ").title()),
                    bar=section.key,
                    enabled=enabled,
                    runtime_condition=bool(
                        enabled and name in {"apply", "remove"}
                    ),
                    reason=(
                        "collection is writable; selection may also be required"
                        if enabled
                        else "collection is protected or read-only for this role"
                    ),
                )
            )
    return tuple(actions)


def _preview_widths(
    view: ResolvedView,
    structure: StudioViewStructure,
) -> tuple[int, int]:
    surfaces = view.data.get("surfaces", {})
    tui = surfaces.get("tui", {}) if isinstance(surfaces, Mapping) else {}
    declared = tui.get("minimum_width") if isinstance(tui, Mapping) else None
    minimum = int(declared) if isinstance(declared, int) else (
        80 if structure.kind in {"form", "inline_edit"} else 60
    )
    if structure.kind in {"browse", "lookup"}:
        field_settings = view.data.get("fields", {})
        widths = []
        for name in view.data.get("columns", ()):
            configuration = (
                field_settings.get(name, {})
                if isinstance(field_settings, Mapping)
                else {}
            )
            configured = (
                configuration.get("width")
                if isinstance(configuration, Mapping)
                else None
            )
            widths.append(int(configured) if isinstance(configured, int) else 14)
        content = max(minimum, sum(widths) + max(0, len(widths) - 1) * 3 + 4)
    else:
        content = minimum
    return minimum, content


def _preview_minimum_height(
    view: ResolvedView,
    structure: StudioViewStructure,
) -> int:
    surfaces = view.data.get("surfaces", {})
    tui = surfaces.get("tui", {}) if isinstance(surfaces, Mapping) else {}
    declared = tui.get("minimum_height") if isinstance(tui, Mapping) else None
    if isinstance(declared, int):
        return declared
    if structure.kind in {"browse", "lookup"}:
        return 16
    if structure.kind == "inline_edit":
        return 12
    collection_height = (
        int(tui.get("collection_height", 10))
        if isinstance(tui, Mapping)
        else 10
    )
    section_costs: dict[str, int] = {}
    tabbed = any(section.tab for section in structure.sections)
    for section in view.data.get("layout", ()):
        if not isinstance(section, Mapping):
            continue
        tab = str(section.get("tab") or "General") if tabbed else "all"
        if "collection" in section:
            cost = collection_height
        else:
            rows = section.get("rows", ())
            cost = len(rows) if isinstance(rows, (list, tuple)) else 0
        section_costs[tab] = section_costs.get(tab, 0) + cost
    content_height = max(section_costs.values(), default=4)
    return max(16, 8 + content_height + (1 if tabbed else 0))


def _preview_required_access(
    kind: str,
    access: Mapping[str, bool],
) -> bool:
    if kind in {"browse", "lookup"}:
        return bool(access.get("list"))
    if kind == "form":
        return bool(access.get("read") or access.get("create"))
    return bool(access.get("read") or access.get("update"))


def _layout_add_command(
    target: DesignerDocumentReference,
    document: Mapping[str, Any],
    field_name: str,
    *,
    near: StudioViewField | None,
    destination_group: StudioViewGroup | None,
    balance_inline: bool,
) -> DesignerInsertSequenceItemCommand:
    layout = document.get("layout")
    if not _studio_sequence(layout):
        raise StudioError("the view does not own a local layout sequence")
    preferred_section = destination_group.position if destination_group else None
    if (
        preferred_section is None
        and near is not None
        and near.source_path is not None
        and len(near.source_path) >= 2
        and near.source_path[0] == "layout"
        and isinstance(near.source_path[1], int)
    ):
        preferred_section = near.source_path[1]
    section_indexes = tuple(
        index
        for index, section in enumerate(layout)
        if isinstance(section, Mapping) and _studio_sequence(section.get("rows"))
    )
    if not section_indexes:
        raise StudioError("the local layout has no editable field group")
    section_index = (
        preferred_section
        if preferred_section in section_indexes
        else section_indexes[0]
    )
    section = layout[section_index]
    rows = section["rows"]
    if rows:
        last_row = rows[-1]
        if _studio_sequence(last_row) and (not balance_inline or len(last_row) < 2):
            return DesignerInsertSequenceItemCommand(
                target=target,
                path=("layout", section_index, "rows", len(rows) - 1),
                index=len(last_row),
                value=field_name,
            )
    return DesignerInsertSequenceItemCommand(
        target=target,
        path=("layout", section_index, "rows"),
        index=len(rows),
        value=[field_name],
        flow_style=True,
    )


def _layout_remove_command(
    target: DesignerDocumentReference,
    document: Mapping[str, Any],
    source_path: tuple[PathPart, ...],
) -> DesignerRemoveValueCommand:
    if len(source_path) < 5:
        raise StudioError("the selected field has no editable layout slot")
    row = _studio_node(document, source_path[:-1])
    if not _studio_sequence(row):
        raise StudioError("the selected layout slot is not inside a row")
    return DesignerRemoveValueCommand(
        target=target,
        path=source_path[:-1] if len(row) == 1 else source_path,
    )


def _studio_node(document: Any, path: tuple[PathPart, ...]) -> Any:
    node = document
    for part in path:
        if isinstance(node, Mapping) and isinstance(part, str):
            node = node[part]
        elif _studio_sequence(node) and isinstance(part, int):
            node = node[part]
        else:
            raise StudioError(
                f"invalid local view structure path {_display_path(path)}"
            )
    return node


def _view_layout_slots(
    layout: Any,
    *,
    source_paths: bool,
) -> tuple[tuple[str, tuple[PathPart, ...] | None, str | None, int], ...]:
    slots: list[tuple[str, tuple[PathPart, ...] | None, str | None, int]] = []
    if not _studio_sequence(layout):
        return ()
    for section_index, section in enumerate(layout):
        if not isinstance(section, Mapping):
            continue
        group_value = section.get("group")
        group = str(group_value) if group_value is not None else None
        rows = section.get("rows")
        if not _studio_sequence(rows):
            continue
        for row_index, row in enumerate(rows):
            if not _studio_sequence(row):
                continue
            for column_index, name in enumerate(row):
                if not isinstance(name, str):
                    continue
                path = (
                    ("layout", section_index, "rows", row_index, column_index)
                    if source_paths
                    else None
                )
                slots.append((name, path, group, column_index))
    return tuple(slots)


def _view_origin(view: ResolvedView, property_name: str) -> str:
    origin = view.origins.get(property_name)
    return origin.layer if origin is not None else "generated"


def _studio_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


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
