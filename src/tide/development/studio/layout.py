"""The structure of a view: groups, sections, tracks and actions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


from tide.labels import humanize
from tide.compiler.normalized import NormalizedEntity, ResolvedView
from tide.development.designer import (
    DesignerDocumentReference,
    DesignerInsertSequenceItemCommand,
    DesignerRemoveValueCommand,
    PathPart,
)

from .contracts import (
    StudioError,
    StudioViewAvailableCollection,
    StudioViewAvailableField,
    StudioViewField,
    StudioViewGroup,
    StudioViewSection,
    StudioViewStructure,
    StudioViewTrack,
)
from .documents import (
    display_path,
    studio_sequence,
)




def append_view_track(
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
                label=view_field_label(name, field),
                field_type=view_field_type(field),
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



def view_layout_groups(
    layout: Any,
    *,
    source_paths: bool,
) -> tuple[StudioViewGroup, ...]:
    if not studio_sequence(layout):
        return ()
    groups: list[StudioViewGroup] = []
    for index, section in enumerate(layout):
        if not _view_group_section(section):
            continue
        rows = section["rows"]
        field_count = sum(
            1
            for row in rows
            if studio_sequence(row)
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


def view_layout_sections(
    layout: Any,
    *,
    source_paths: bool,
    entity: NormalizedEntity,
) -> tuple[StudioViewSection, ...]:
    if not studio_sequence(layout):
        return ()
    sections: list[StudioViewSection] = []
    for index, section in enumerate(layout):
        if not isinstance(section, Mapping):
            continue
        is_group = _view_group_section(section)
        raw_collection = section.get("collection")
        # Hold the narrowed name rather than a flag. The branches below want
        # the value, and a boolean throws the type away on the way to them.
        collection_name = (
            raw_collection
            if isinstance(raw_collection, str) and raw_collection in entity.fields
            else None
        )
        if not is_group and collection_name is None:
            continue
        source_path = ("layout", index) if source_paths else None
        if collection_name is not None:
            field = entity.field(collection_name)
            configured_actions = tuple(str(name) for name in section.get("actions", ()))
            actions = configured_actions or ("add", "apply", "remove")
            label = view_field_label(collection_name, field)
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
                collection=collection_name,
                inline_view=(
                    str(section["view"])
                    if collection_name is not None and section.get("view")
                    else None
                ),
                actions=actions,
                available_actions=(
                    ("add", "apply", "remove")
                    if collection_name is not None
                    else ()
                ),
                source_path=source_path,
                editable=source_path is not None,
                can_move_up=source_path is not None and index > 0,
                can_move_down=source_path is not None and index + 1 < len(layout),
                can_remove=source_path is not None and collection_name is not None,
            )
        )
    return tuple(sections)


def available_view_collections(
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
                label=view_field_label(name, field),
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
        and studio_sequence(value.get("rows"))
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


def require_view_group(
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


def require_view_section(
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


def normalize_optional_presentation_label(
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


def normalize_action_order(
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


def normalize_view_group_label(label: str) -> str:
    normalized = label.strip()
    if not normalized:
        raise StudioError("view group labels must not be empty")
    if len(normalized) > 80:
        raise StudioError("view group labels must not exceed 80 characters")
    if any(character in normalized for character in ("\r", "\n", "\x00")):
        raise StudioError("view group labels must fit on one safe text line")
    return normalized


def require_unique_view_group_label(
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


def view_field_capabilities(
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
                and same_layout_section(candidate, field)
            ),
            None,
        )
        right = next(
            (
                candidate
                for candidate in layout
                if candidate.track == "layout-right"
                and candidate.position == field.position
                and same_layout_section(candidate, field)
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


def same_layout_section(
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


def view_can_add_fields(
    view: ResolvedView,
    document: Mapping[str, Any],
    *,
    resolved_columns: tuple[str, ...],
) -> bool:
    raw_columns = document.get("columns")
    owns_columns = bool(
        studio_sequence(raw_columns)
        and tuple(str(name) for name in raw_columns) == resolved_columns
    )
    raw_layout = document.get("layout")
    owns_layout = bool(
        studio_sequence(raw_layout)
        and any(
            isinstance(section, Mapping) and studio_sequence(section.get("rows"))
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


def available_view_fields(
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
                label=view_field_label(name, field),
                field_type=view_field_type(field),
            )
        )
    return tuple(available)


def view_field_label(name: str, field: Any) -> str:
    if field is not None and field.metadata.get("label"):
        return str(field.metadata["label"])
    return humanize(name)


def view_field_type(field: Any) -> str:
    return (
        str(field.metadata.get("type", "unknown")) if field is not None else "unknown"
    )


def layout_add_command(
    target: DesignerDocumentReference,
    document: Mapping[str, Any],
    field_name: str,
    *,
    near: StudioViewField | None,
    destination_group: StudioViewGroup | None,
    balance_inline: bool,
) -> DesignerInsertSequenceItemCommand:
    layout = document.get("layout")
    if not studio_sequence(layout):
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
        if isinstance(section, Mapping) and studio_sequence(section.get("rows"))
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
        if studio_sequence(last_row) and (not balance_inline or len(last_row) < 2):
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


def layout_remove_command(
    target: DesignerDocumentReference,
    document: Mapping[str, Any],
    source_path: tuple[PathPart, ...],
) -> DesignerRemoveValueCommand:
    if len(source_path) < 5:
        raise StudioError("the selected field has no editable layout slot")
    row = _studio_node(document, source_path[:-1])
    if not studio_sequence(row):
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
        elif studio_sequence(node) and isinstance(part, int):
            node = node[part]
        else:
            raise StudioError(
                f"invalid local view structure path {display_path(path)}"
            )
    return node


def view_layout_slots(
    layout: Any,
    *,
    source_paths: bool,
) -> tuple[tuple[str, tuple[PathPart, ...] | None, str | None, int], ...]:
    slots: list[tuple[str, tuple[PathPart, ...] | None, str | None, int]] = []
    if not studio_sequence(layout):
        return ()
    for section_index, section in enumerate(layout):
        if not isinstance(section, Mapping):
            continue
        group_value = section.get("group")
        group = str(group_value) if group_value is not None else None
        rows = section.get("rows")
        if not studio_sequence(rows):
            continue
        for row_index, row in enumerate(rows):
            if not studio_sequence(row):
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


def view_origin(view: ResolvedView, property_name: str) -> str:
    origin = view.origins.get(property_name)
    return origin.layer if origin is not None else "generated"
