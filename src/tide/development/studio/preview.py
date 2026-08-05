"""What a view would look like to a chosen role."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal


from tide.labels import humanize
from tide.presentation import action_label, browse_columns, form_layout_sections
from tide.compiler.normalized import NormalizedEntity, ResolvedView
from tide.runtime import RequestContext
from tide.security import SecurityEngine

from .contracts import (
    StudioOperation,
    StudioPreviewAction,
    StudioPreviewField,
    StudioViewSection,
    StudioViewStructure,
)




def preview_sections(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[StudioViewSection, ...]:
    """Resolve the sections a renderer will actually draw.

    The editor's structure is source-shaped: it lists what the document says, so
    a designer can move and remove it. A preview answers a different question --
    what appears on screen -- and the two are not the same document.
    ``settings.compact_groups`` merges every scalar group into one, so the
    invoicing form authors three groups and every renderer draws one.
    """

    return tuple(
        StudioViewSection(
            key=f"preview-section:{section.index}",
            kind=section.kind,
            label=section.label,
            position=position,
            tab=section.tab,
            collection=section.collection,
            inline_view=section.inline_view,
            actions=section.actions,
            available_actions=(),
            source_path=None,
            editable=False,
            can_move_up=False,
            can_move_down=False,
            can_remove=False,
        )
        for position, section in enumerate(form_layout_sections(view, entity))
    )


def preview_placements(
    view: ResolvedView,
    entity: NormalizedEntity,
    kind: str,
) -> tuple[tuple[str, str, str, str], ...]:
    """Return ``(name, key, track, track label)`` for what a renderer will draw."""

    if kind in {"browse", "lookup"}:
        label = "Lookup columns" if kind == "lookup" else "Table columns"
        return tuple(
            (name, f"preview-columns:{name}", "columns", label)
            for name in browse_columns(view, entity)
        )
    return tuple(
        (
            name,
            f"preview-section:{section.index}:{name}",
            f"preview-section:{section.index}",
            section.label,
        )
        for section in form_layout_sections(view, entity)
        if section.kind == "group"
        for name in section.fields
    )


def preview_field(
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


def preview_actions(
    structure: StudioViewStructure,
    entity: NormalizedEntity,
    security: SecurityEngine,
    context: RequestContext,
    *,
    access_by_operation: Mapping[StudioOperation, bool],
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
                label=action_label(name, action),
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
                    }.get(name, humanize(name)),
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


def preview_widths(
    view: ResolvedView,
    structure: StudioViewStructure,
) -> tuple[int | None, int | None]:
    """Return the declared minimum width and content width, or ``None``.

    Nothing outside Studio reads ``surfaces.tui``, so these are the author's own
    assertions rather than anything the runtime enforces -- which is exactly why
    Studio must not invent them. It previously fell back to 80 or 60 columns and
    assumed 14 for every unconfigured column, then reported both as fact. The
    terminal auto-sizes a column with no declared width, so a content estimate is
    only meaningful when every displayed column declares one.
    """

    surfaces = view.data.get("surfaces", {})
    tui = surfaces.get("tui", {}) if isinstance(surfaces, Mapping) else {}
    declared = tui.get("minimum_width") if isinstance(tui, Mapping) else None
    minimum = int(declared) if isinstance(declared, int) else None
    if structure.kind not in {"browse", "lookup"}:
        return minimum, None

    field_settings = view.data.get("fields", {})
    widths: list[int] = []
    for name in view.data.get("columns", ()):
        configuration = (
            field_settings.get(name, {})
            if isinstance(field_settings, Mapping)
            else {}
        )
        configured = (
            configuration.get("width") if isinstance(configuration, Mapping) else None
        )
        if not isinstance(configured, int):
            return minimum, None
        widths.append(configured)
    if not widths:
        return minimum, None
    return minimum, sum(widths) + max(0, len(widths) - 1) * 3 + 4


def preview_minimum_height(
    view: ResolvedView,
    structure: StudioViewStructure,
) -> int | None:
    """Return the declared minimum height, or ``None``.

    The previous estimate summed invented per-section costs onto an invented
    floor and called the total an estimated minimum. No renderer refuses to draw
    at any height, so there was nothing for it to be an estimate of.
    """

    surfaces = view.data.get("surfaces", {})
    tui = surfaces.get("tui", {}) if isinstance(surfaces, Mapping) else {}
    declared = tui.get("minimum_height") if isinstance(tui, Mapping) else None
    return int(declared) if isinstance(declared, int) else None


def preview_required_access(
    kind: str,
    access: Mapping[StudioOperation, bool],
) -> bool:
    if kind in {"browse", "lookup"}:
        return bool(access.get("list"))
    if kind == "form":
        return bool(access.get("read") or access.get("create"))
    return bool(access.get("read") or access.get("update"))
