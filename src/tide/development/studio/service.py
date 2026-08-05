"""The in-memory editing session every Studio renderer drives."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal


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
    DesignerSaveService,
)
from tide.runtime import Channel, Principal, RequestContext
from tide.security import SecurityEngine

from .contracts import (
    STUDIO_OPERATIONS,
    StudioDocumentDetails,
    StudioDocumentGroup,
    StudioDocumentNode,
    StudioError,
    StudioGroupKind,
    StudioPreviewAccess,
    StudioSaveResult,
    StudioSaveReview,
    StudioSessionState,
    StudioViewField,
    StudioViewGroup,
    StudioViewPreview,
    StudioViewSection,
    StudioViewStructure,
    StudioViewTrack,
    StudioWorkspace,
)
from .documents import (
    display_path,
    load_studio_yaml,
    studio_sequence,
)
from .layout import (
    append_view_track,
    available_view_collections,
    available_view_fields,
    layout_add_command,
    layout_remove_command,
    normalize_action_order,
    normalize_optional_presentation_label,
    normalize_view_group_label,
    require_unique_view_group_label,
    require_view_group,
    require_view_section,
    same_layout_section,
    view_can_add_fields,
    view_field_capabilities,
    view_field_label,
    view_field_type,
    view_layout_groups,
    view_layout_sections,
    view_layout_slots,
    view_origin,
)
from .preview import (
    preview_actions,
    preview_field,
    preview_minimum_height,
    preview_placements,
    preview_required_access,
    preview_sections,
    preview_widths,
)
from .properties import (
    document_label,
    document_properties,
    parse_scalar,
    project_identity,
    resolve_property,
    source_model,
)




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
        document = load_studio_yaml(content.file, content.content)
        properties = document_properties(document, source_model(target, document))
        return StudioDocumentDetails(
            target=target,
            title=document_label(target),
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
        document = load_studio_yaml(content.file, content.content)
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
                if studio_sequence(raw_columns)
                and tuple(str(name) for name in raw_columns) == resolved_columns
                else tuple(None for _name in resolved_columns)
            )
            label = "Lookup columns" if resolved.kind == "lookup" else "Table columns"
            append_view_track(
                tracks,
                fields,
                key="columns",
                label=label,
                entries=tuple(
                    (name, column_paths[index], None)
                    for index, name in enumerate(resolved_columns)
                ),
                entity=entity,
                origin=view_origin(resolved, "columns"),
            )

        if resolved.kind in {"form", "inline_edit"}:
            raw_layout = document.get("layout")
            layout = (
                raw_layout
                if studio_sequence(raw_layout)
                else resolved.data.get("layout", ())
            )
            slots = view_layout_slots(
                layout,
                source_paths=studio_sequence(raw_layout),
            )
            groups = view_layout_groups(
                layout,
                source_paths=studio_sequence(raw_layout),
            )
            sections = view_layout_sections(
                layout,
                source_paths=studio_sequence(raw_layout),
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
                    append_view_track(
                        tracks,
                        fields,
                        key=f"layout-{side}",
                        label=f"{prefix} · {side} column",
                        entries=tuple(
                            (name, path, group)
                            for name, path, group, _column in entries
                        ),
                        entity=entity,
                        origin=view_origin(resolved, "layout"),
                    )

        resolved_fields = view_field_capabilities(
            tuple(fields),
            kind=resolved.kind,
        )
        can_add = view_can_add_fields(
            resolved,
            document,
            resolved_columns=resolved_columns,
        )
        available_fields = (
            available_view_fields(resolved, entity, resolved_fields) if can_add else ()
        )
        owns_layout = bool(studio_sequence(raw_layout))
        available_collections = (
            available_view_collections(model, entity, sections)
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
                and studio_sequence(document.get("layout"))
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
            for operation in STUDIO_OPERATIONS
        )
        access_by_operation = {item.operation: item.allowed for item in access}
        may_write_record = bool(
            access_by_operation["create"] or access_by_operation["update"]
        )
        try:
            sections = preview_sections(view, entity)
            placements = preview_placements(view, entity, structure.kind)
        except ValueError as error:
            raise StudioError(str(error)) from error
        preview_fields = [
            preview_field(
                key,
                name,
                view_field_label(name, entity.field(name)),
                track,
                track_label,
                view_field_type(entity.field(name)),
                entity,
                view,
                security,
                context,
                may_write_record=may_write_record,
                view_kind=structure.kind,
            )
            for name, key, track, track_label in placements
        ]
        for section in sections:
            if section.kind != "collection" or section.collection is None:
                continue
            field = entity.field(section.collection)
            preview_fields.append(
                preview_field(
                    f"{section.key}:collection",
                    section.collection,
                    section.label,
                    section.key,
                    f"Collection · {section.label}",
                    view_field_type(field),
                    entity,
                    view,
                    security,
                    context,
                    may_write_record=may_write_record,
                    view_kind=structure.kind,
                )
            )
        actions = preview_actions(
            structure,
            entity,
            security,
            context,
            access_by_operation=access_by_operation,
            fields=tuple(preview_fields),
        )
        minimum_width, content_width = preview_widths(view, structure)
        minimum_height = preview_minimum_height(view, structure)
        warnings: list[str] = []
        exposed = bool(entity.metadata.get("expose", {}).get("tui", False))
        required_access = preview_required_access(
            structure.kind,
            access_by_operation,
        )
        if not exposed:
            warnings.append(f"{entity.name} is not exposed to the TUI.")
        if not required_access:
            warnings.append(
                f"Role {role or '(no role)'} cannot open this {structure.kind} view."
            )
        if minimum_width is not None and width < minimum_width:
            warnings.append(
                f"Width {width} is below the declared minimum of {minimum_width}."
            )
        elif content_width is not None and content_width > width:
            warnings.append(
                f"Content is {content_width} columns wide; horizontal "
                "scrolling or compression is required."
            )
        if minimum_height is not None and height < minimum_height:
            warnings.append(
                f"Height {height} is below the declared minimum of {minimum_height}."
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
        elif (minimum_width is not None and width < minimum_width) or (
            minimum_height is not None and height < minimum_height
        ):
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
            sections=sections,
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
            and same_layout_section(field, selected)
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
        document = load_studio_yaml(content.file, content.content)
        if not isinstance(document, Mapping):
            raise StudioError(f"cannot edit {content.file}: expected a mapping")
        near = next(
            (field for field in structure.fields if field.key == near_field_key),
            None,
        )
        destination_group = (
            require_view_group(structure, destination_group_key)
            if destination_group_key is not None
            else None
        )
        if destination_group is not None and not destination_group.editable:
            raise StudioError("fields can be added only to a local view group")
        commands: list[DesignerInsertSequenceItemCommand] = []
        raw_columns = document.get("columns")
        if structure.kind in {"browse", "lookup", "inline_edit"}:
            if not studio_sequence(raw_columns):
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
                layout_add_command(
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
        document = load_studio_yaml(content.file, content.content)
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
                layout_remove_command(target, document, layout_field.source_path)
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
        normalized = normalize_view_group_label(label)
        require_unique_view_group_label(structure, normalized)
        content = self._session.document(target)
        document = load_studio_yaml(content.file, content.content)
        if not isinstance(document, Mapping):
            raise StudioError(f"cannot edit {content.file}: expected a mapping")
        layout = document.get("layout")
        if not studio_sequence(layout):
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
        selected = require_view_group(structure, group_key)
        if not selected.editable or selected.source_path is None:
            raise StudioError("inherited or generated groups cannot be renamed")
        normalized = normalize_view_group_label(label)
        require_unique_view_group_label(
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
        selected = require_view_group(structure, group_key)
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
        selected = require_view_group(structure, group_key)
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
        selected = require_view_section(structure, section_key)
        if not selected.editable or selected.source_path is None:
            raise StudioError("inherited or generated layout sections cannot be edited")
        content = self._session.document(target)
        document = load_studio_yaml(content.file, content.content)
        normalized = normalize_optional_presentation_label(label, name="tab")
        path = (*selected.source_path, "tab")
        section = resolve_property(document, selected.source_path)
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
        selected = require_view_section(structure, section_key)
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
        document = load_studio_yaml(content.file, content.content)
        layout = document.get("layout") if isinstance(document, Mapping) else None
        if not studio_sequence(layout):
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
        selected = require_view_section(structure, section_key)
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
            section = require_view_section(structure, bar_key)
            if section.kind != "collection" or section.source_path is None:
                raise StudioError("collection action bars require a local collection")
            allowed = section.available_actions
            path = (*section.source_path, "actions")
        normalized = normalize_action_order(actions, allowed=allowed)
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
            raise StudioError(f"unknown Studio property {display_path(path)}")
        if not selected.editable:
            raise StudioError(
                f"Studio property {display_path(path)} is not directly editable"
            )
        if selected.choices and text not in selected.choices:
            options = ", ".join(selected.choices)
            raise StudioError(
                f"Studio property {display_path(path)} requires one of: {options}"
            )
        document = load_studio_yaml(details.file, details.source)
        current = resolve_property(document, path)
        value = parse_scalar(text, current, path)
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
                    label=document_label(target),
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
        application, application_version = project_identity(session, catalog.project)
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
