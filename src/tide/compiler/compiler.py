"""Project discovery, validation, reference resolution, and normalization."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, TypeVar

from pydantic import BaseModel, ValidationError

from tide.labels import humanize as _humanize
from tide.compiler.expressions import (
    POLICY_PARAMETERS,
    ExpressionResult,
    validate_expression,
)
from tide.compiler.normalized import (
    ApplicationModel,
    NavigationGroup,
    NavigationItem,
    NormalizedEntity,
    NormalizedField,
    PropertyOrigin,
    ResolvedView,
    deep_freeze,
    deep_thaw,
    immutable_mapping,
)
from tide.compiler.source import SourceDocument, YamlSourceError, load_yaml_document
from tide.diagnostics import CompilationFailed, Diagnostic, Severity, SourceLocation
from tide.model.source import (
    BROWSE_EDIT_MODES,
    FRAMEWORK_PERMISSION_PREFIX,
    FRAMEWORK_PERMISSIONS,
    MAX_FILE_SIZE_BYTES,
    RESERVED_ACTION_NAMES,
    SUMMARIZABLE_FIELD_TYPES,
    ActionSource,
    EntitySource,
    FieldSource,
    FormatsSource,
    PresentationDefaultsSource,
    PresetDocumentSource,
    ProjectSource,
    ReportSource,
    SecurityDocumentSource,
    ViewSource,
    parse_size_literal,
)

SourceType = TypeVar("SourceType", bound=BaseModel)
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DISPLAY_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
FRAMEWORK_VIEW_DEFAULTS: dict[str, dict[str, Any]] = {
    "browse": {
        "page_size": 50,
        "incremental_search": True,
        "confirm_delete": True,
        "edit": "form",
    },
    "form": {"show_required_indicator": True, "validate_on_leave": True},
    "lookup": {"page_size": 20, "incremental_search": True, "close_after_selection": True},
    "inline_edit": {"show_column_headers": True, "allow_reorder": True},
}


def compile_project(project: str | Path = ".") -> ApplicationModel:
    project_path = Path(project).resolve()
    project_file = project_path if project_path.is_file() else project_path / "tide.yaml"
    root = project_file.parent
    diagnostics: list[Diagnostic] = []

    project_parsed = _parse_file(project_file, ProjectSource, diagnostics)
    if project_parsed is None:
        if not diagnostics:
            diagnostics.append(
                Diagnostic(
                    code="TIDE010",
                    message="project configuration tide.yaml was not found",
                    location=SourceLocation(project_file),
                )
            )
        raise CompilationFailed(diagnostics)
    project_source, project_document = project_parsed

    model_files = _discover_paths(root, project_source.model.paths, project_document, ("model", "paths"), diagnostics)
    view_files = _discover_paths(root, project_source.views.paths, project_document, ("views", "paths"), diagnostics)
    report_files = _discover_paths(root, project_source.reports.paths, project_document, ("reports", "paths"), diagnostics)
    security_files = _discover_paths(root, project_source.security.paths, project_document, ("security", "paths"), diagnostics)
    preset_files = _discover_paths(root, project_source.presentation.presets, project_document, ("presentation", "presets"), diagnostics)

    entity_items = _parse_files(model_files, EntitySource, diagnostics)
    view_items = _parse_files(view_files, ViewSource, diagnostics)
    report_items = _parse_files(report_files, ReportSource, diagnostics)
    security_items = _parse_files(security_files, SecurityDocumentSource, diagnostics)
    preset_items = _parse_files(preset_files, PresetDocumentSource, diagnostics)

    defaults_source = PresentationDefaultsSource()
    defaults_document: SourceDocument | None = None
    if project_source.presentation.defaults:
        defaults_file = _resolve_config_file(root, project_source.presentation.defaults, project_document, ("presentation", "defaults"), diagnostics)
        if defaults_file:
            parsed_defaults = _parse_file(defaults_file, PresentationDefaultsSource, diagnostics)
            if parsed_defaults:
                defaults_source, defaults_document = parsed_defaults

    formats: dict[str, dict[str, Any]] = {}
    if project_source.presentation.formats:
        formats_file = _resolve_config_file(root, project_source.presentation.formats, project_document, ("presentation", "formats"), diagnostics)
        if formats_file:
            parsed_formats = _parse_file(formats_file, FormatsSource, diagnostics)
            if parsed_formats:
                formats.update(parsed_formats[0].formats)

    entities, entity_documents = _unique_by_name(
        entity_items, "entity", "entity", "TIDE200", diagnostics
    )
    views, view_documents = _unique_by_name(view_items, "view", "view", "TIDE230", diagnostics)
    reports, report_documents = _unique_by_name(
        report_items, "report", "report", "TIDE250", diagnostics
    )

    presets: dict[str, Any] = {}
    preset_documents: dict[str, SourceDocument] = {}
    for preset_document, document in preset_items:
        for name, preset in preset_document.presets.items():
            if name in presets:
                diagnostics.append(
                    Diagnostic(
                        code="TIDE240",
                        message=f"duplicate preset {name!r}",
                        location=document.location_for(("presets", name)),
                        path=("presets", name),
                    )
                )
            presets[name] = preset
            preset_documents[name] = document

    entities = _apply_transitions(entities, entity_documents, diagnostics)

    dependency_map: dict[tuple[str, str], tuple[str, ...]] = {}
    _validate_entities(
        entities,
        entity_documents,
        set(formats),
        dependency_map,
        diagnostics,
        root,
        project_source.database.mode,
    )
    _validate_views(
        views,
        view_documents,
        entities,
        entity_documents,
        presets,
        diagnostics,
    )
    _validate_navigation(
        defaults_source,
        defaults_document,
        views,
        diagnostics,
    )
    # A browse `edit:` setting can be declared in four places; the closed
    # set holds in all of them, because a typo that silently fell back to
    # form editing would be a guard nobody knows is ignored.
    if defaults_document is not None:
        _validate_browse_edit_mode(
            defaults_source.browse, defaults_document, ("browse",), diagnostics
        )
    for preset_name, preset in presets.items():
        if preset.kind == "browse":
            _validate_browse_edit_mode(
                preset.settings,
                preset_documents[preset_name],
                ("presets", preset_name, "settings"),
                diagnostics,
            )
    for entity_name, entity in entities.items():
        browse_presentation = entity.presentation.get("browse")
        if browse_presentation:
            _validate_browse_edit_mode(
                browse_presentation,
                entity_documents[entity_name],
                ("presentation", "browse"),
                diagnostics,
            )
    _validate_reports(reports, report_documents, entities, set(formats), diagnostics)
    permissions, roles, row_policies, field_policies = _validate_security(
        security_items,
        entities,
        entity_documents,
        reports,
        report_documents,
        diagnostics,
    )

    if any(diagnostic.severity is Severity.ERROR for diagnostic in diagnostics):
        raise CompilationFailed(diagnostics)

    resolved_views = _resolve_views(
        views,
        view_documents,
        entities,
        entity_documents,
        defaults_source,
        defaults_document,
        presets,
        preset_documents,
    )

    normalized_entities: dict[str, NormalizedEntity] = {}
    for entity_name in sorted(entities):
        entity = entities[entity_name]
        normalized_fields = {
            field_name: NormalizedField(
                name=field_name,
                metadata=immutable_mapping(
                    field.model_dump(mode="python", by_alias=True, exclude_none=True)
                ),
                target_entity=field.target,
                dependencies=dependency_map.get((entity_name, field_name), ()),
            )
            for field_name, field in sorted(entity.fields.items())
        }
        normalized_entities[entity_name] = NormalizedEntity(
            name=entity_name,
            label=entity.label or _humanize(entity_name.rsplit(".", 1)[-1]),
            display=entity.display,
            source_file=entity_documents[entity_name].file,
            metadata=immutable_mapping(
                entity.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude={"fields", "actions"},
                    exclude_none=True,
                )
            ),
            fields=immutable_mapping(normalized_fields),
            actions=immutable_mapping(
                {
                    # `by_alias` so a transition's `from` reaches the model, the
                    # wire and the MCP schema under the name it was written as,
                    # rather than as the `from_` Python needs it to be.
                    name: action.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    )
                    for name, action in sorted(entity.actions.items())
                }
            ),
        )

    navigation = tuple(
        NavigationGroup(
            label=group.label.strip(),
            items=tuple(
                NavigationItem(
                    view=item.view,
                    label=(
                        item.label.strip()
                        if item.label is not None
                        else normalized_entities[
                            resolved_views[item.view].entity
                        ].label
                    ),
                )
                for item in group.items
            ),
        )
        for group in defaults_source.navigation
    )

    return ApplicationModel(
        schema_version=project_source.schema_version,
        name=project_source.application.name,
        version=project_source.application.version,
        project_root=root,
        database=immutable_mapping(
            project_source.database.model_dump(mode="json", exclude_none=True)
        ),
        entities=immutable_mapping(normalized_entities),
        views=immutable_mapping(resolved_views),
        navigation=navigation,
        reports=immutable_mapping(
            {
                name: report.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude=(
                        {"group_by", "columns", "aggregates"}
                        if report.kind == "record"
                        else None
                    ),
                )
                for name, report in sorted(reports.items())
            }
        ),
        formats=immutable_mapping(formats),
        presets=frozenset(presets),
        permissions=frozenset(permissions),
        roles=immutable_mapping(roles),
        row_policies=tuple(deep_freeze(policy) for policy in row_policies),
        field_policies=tuple(deep_freeze(policy) for policy in field_policies),
        diagnostics=tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.severity is Severity.WARNING
        ),
    )


def _parse_file(
    file: Path, model_type: type[SourceType], diagnostics: list[Diagnostic]
) -> tuple[SourceType, SourceDocument] | None:
    if not file.is_file():
        diagnostics.append(
            Diagnostic(
                code="TIDE011",
                message="configured source file does not exist",
                location=SourceLocation(file),
            )
        )
        return None
    try:
        document = load_yaml_document(file)
    except YamlSourceError as error:
        diagnostics.append(error.diagnostic)
        return None
    try:
        return model_type.model_validate(document.data), document
    except ValidationError as error:
        diagnostics.extend(_pydantic_diagnostics(error, document))
        return None


def _parse_files(
    files: Iterable[Path], model_type: type[SourceType], diagnostics: list[Diagnostic]
) -> list[tuple[SourceType, SourceDocument]]:
    result: list[tuple[SourceType, SourceDocument]] = []
    for file in files:
        parsed = _parse_file(file, model_type, diagnostics)
        if parsed:
            result.append(parsed)
    return result


def _pydantic_diagnostics(error: ValidationError, document: SourceDocument) -> list[Diagnostic]:
    result: list[Diagnostic] = []
    for item in error.errors(include_url=False):
        path = tuple(item["loc"])
        error_type = str(item["type"])
        if error_type == "extra_forbidden":
            code = "TIDE102"
            message = "unknown metadata property"
        elif error_type == "missing":
            code = "TIDE101"
            message = "required metadata property is missing"
        elif error_type == "literal_error" and path == ("schema_version",):
            code = "TIDE100"
            message = "unsupported schema_version; supported versions: 0.1"
        else:
            code = "TIDE103"
            message = str(item["msg"])
        result.append(
            Diagnostic(
                code=code,
                message=message,
                location=document.location_for(path),
                path=path,
            )
        )
    return result


def _discover_paths(
    root: Path,
    configured: Iterable[str],
    project_document: SourceDocument,
    path: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> tuple[Path, ...]:
    files: set[Path] = set()
    for index, configured_path in enumerate(configured):
        candidate = (root / configured_path).resolve()
        if not _is_within(candidate, root):
            diagnostics.append(
                Diagnostic(
                    code="TIDE012",
                    message="configured paths must remain inside the project root",
                    location=project_document.location_for((*path, index)),
                    path=(*path, index),
                )
            )
            continue
        if candidate.is_file():
            files.add(candidate)
        elif candidate.is_dir():
            files.update(candidate.rglob("*.yaml"))
            files.update(candidate.rglob("*.yml"))
        else:
            diagnostics.append(
                Diagnostic(
                    code="TIDE011",
                    message=f"configured path does not exist: {configured_path}",
                    location=project_document.location_for((*path, index)),
                    path=(*path, index),
                )
            )
    return tuple(sorted(files))


def _resolve_config_file(
    root: Path,
    configured: str,
    project_document: SourceDocument,
    path: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> Path | None:
    candidate = (root / configured).resolve()
    if not _is_within(candidate, root):
        diagnostics.append(
            Diagnostic(
                code="TIDE012",
                message="configured files must remain inside the project root",
                location=project_document.location_for(path),
                path=path,
            )
        )
        return None
    return candidate


def _unique_by_name(
    items: Iterable[tuple[SourceType, SourceDocument]],
    attribute: str,
    path_key: str,
    code: str,
    diagnostics: list[Diagnostic],
) -> tuple[dict[str, Any], dict[str, SourceDocument]]:
    values: dict[str, Any] = {}
    documents: dict[str, SourceDocument] = {}
    for value, document in items:
        name = getattr(value, attribute)
        if name in values:
            diagnostics.append(
                Diagnostic(
                    code=code,
                    message=f"duplicate {path_key} identifier {name!r}",
                    location=document.location_for((path_key,)),
                    path=(path_key,),
                    hint=f"first declared in {documents[name].file}",
                )
            )
            continue
        values[name] = value
        documents[name] = document
    return values, documents


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_migration_metadata(
    entities: dict[str, EntitySource],
    documents: dict[str, SourceDocument],
    diagnostics: list[Diagnostic],
    database_mode: str,
) -> None:
    migration_ids: dict[str, tuple[str, tuple[str | int, ...]]] = {}
    current_tables: dict[tuple[str, str], str] = {}
    previous_tables: dict[tuple[str, str], str] = {}

    def register_migration_id(
        value: str | None,
        entity_name: str,
        path: tuple[str | int, ...],
    ) -> None:
        if value is None:
            return
        document = documents[entity_name]
        if not IDENTIFIER.fullmatch(value):
            _add(
                diagnostics,
                "TIDE245",
                "migration_id must be a qualified dotted identifier",
                document,
                path,
            )
            return
        key = value.casefold()
        previous = migration_ids.get(key)
        if previous is not None:
            previous_entity, previous_path = previous
            _add(
                diagnostics,
                "TIDE246",
                f"migration_id {value!r} is already used by {previous_entity} at "
                + ".".join(str(part) for part in previous_path),
                document,
                path,
            )
            return
        migration_ids[key] = (entity_name, path)

    for entity_name, entity in entities.items():
        document = documents[entity_name]
        storage = entity.storage
        table_name = storage.table if storage and storage.table else _managed_table_name(
            entity_name
        )
        schema = storage.schema_ if storage and storage else None
        table_key = ((schema or "").casefold(), table_name.casefold())
        previous_entity = current_tables.get(table_key)
        if previous_entity is not None:
            _add(
                diagnostics,
                "TIDE248",
                f"physical table {table_name!r} is already mapped by {previous_entity}",
                document,
                ("storage", "table"),
            )
        else:
            current_tables[table_key] = entity_name

        if storage is not None:
            register_migration_id(
                storage.migration_id,
                entity_name,
                ("storage", "migration_id"),
            )

    for entity_name, entity in entities.items():
        document = documents[entity_name]
        storage = entity.storage
        table_name = storage.table if storage and storage.table else _managed_table_name(
            entity_name
        )
        schema = storage.schema_ if storage and storage else None
        if storage is not None and storage.renamed_from is not None:
            path = ("storage", "renamed_from")
            previous = storage.renamed_from
            previous_schema = previous.schema_ if previous.schema_ is not None else schema
            previous_key = (
                (previous_schema or "").casefold(),
                previous.table.casefold(),
            )
            current_key = ((schema or "").casefold(), table_name.casefold())
            if database_mode != "managed":
                _add(
                    diagnostics,
                    "TIDE247",
                    "renamed_from is allowed only for managed database storage",
                    document,
                    path,
                )
            if storage.migration_id is None:
                _add(
                    diagnostics,
                    "TIDE247",
                    "a table rename requires storage.migration_id",
                    document,
                    path,
                )
            if previous_key == current_key:
                _add(
                    diagnostics,
                    "TIDE247",
                    "renamed_from must identify a different physical table",
                    document,
                    path,
                )
            else:
                current_owner = current_tables.get(previous_key)
                if current_owner is not None:
                    _add(
                        diagnostics,
                        "TIDE248",
                        f"rename source is the current table mapped by {current_owner}",
                        document,
                        path,
                    )
                previous_owner = previous_tables.get(previous_key)
                if previous_owner is not None:
                    _add(
                        diagnostics,
                        "TIDE248",
                        f"rename source is already claimed by {previous_owner}",
                        document,
                        path,
                    )
                else:
                    previous_tables[previous_key] = entity_name

        current_columns: dict[str, str] = {}
        previous_columns: dict[str, str] = {}
        for field_name, field in entity.fields.items():
            field_path = ("fields", field_name)
            register_migration_id(
                field.migration_id,
                entity_name,
                (*field_path, "migration_id"),
            )
            if not _is_persisted_field(field):
                if field.renamed_from is not None:
                    _add(
                        diagnostics,
                        "TIDE247",
                        "renamed_from is allowed only for persisted fields",
                        document,
                        (*field_path, "renamed_from"),
                    )
                continue
            column_name = _source_column_name(field_name, field)
            column_key = column_name.casefold()
            previous_field = current_columns.get(column_key)
            if previous_field is not None:
                _add(
                    diagnostics,
                    "TIDE248",
                    f"physical column {column_name!r} is already mapped by field "
                    f"{previous_field!r}",
                    document,
                    (*field_path, "storage" if field.type == "reference" else "column"),
                )
            else:
                current_columns[column_key] = field_name

        for field_name, field in entity.fields.items():
            if field.renamed_from is None:
                continue
            field_path = ("fields", field_name, "renamed_from")
            previous_key = field.renamed_from.casefold()
            if database_mode != "managed":
                _add(
                    diagnostics,
                    "TIDE247",
                    "renamed_from is allowed only for managed database storage",
                    document,
                    field_path,
                )
            if field.migration_id is None:
                _add(
                    diagnostics,
                    "TIDE247",
                    "a column rename requires migration_id",
                    document,
                    field_path,
                )
            current_key = _source_column_name(field_name, field).casefold()
            if previous_key == current_key:
                _add(
                    diagnostics,
                    "TIDE247",
                    "renamed_from must identify a different physical column",
                    document,
                    field_path,
                )
            else:
                current_owner = current_columns.get(previous_key)
                if current_owner is not None:
                    _add(
                        diagnostics,
                        "TIDE248",
                        f"rename source is the current column mapped by field "
                        f"{current_owner!r}",
                        document,
                        field_path,
                    )
                previous_owner = previous_columns.get(previous_key)
                if previous_owner is not None:
                    _add(
                        diagnostics,
                        "TIDE248",
                        f"rename source is already claimed by field {previous_owner!r}",
                        document,
                        field_path,
                    )
                else:
                    previous_columns[previous_key] = field_name


def _managed_table_name(entity_name: str) -> str:
    return "_".join(
        re.sub(r"(?<!^)(?=[A-Z])", "_", part).lower()
        for part in entity_name.split(".")
    )


def _source_column_name(field_name: str, field: FieldSource) -> str:
    if field.type == "reference":
        return field.storage or f"{field_name}_id"
    return field.column or field_name


_STAMP_FIELD_TYPES = {"now": "datetime", "principal": "string"}


def _mentions(expression: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", expression) is not None


def _state_predicate(field_name: str, states: Iterable[str]) -> str:
    return " or ".join(f"{field_name} == {state!r}" for state in sorted(states))


def _apply_transitions(
    entities: dict[str, EntitySource],
    documents: Mapping[str, SourceDocument],
    diagnostics: list[Diagnostic],
) -> dict[str, EntitySource]:
    """Check each entity's declared state machine and derive its guards.

    Runs before `_validate_entities` so the derived expressions are checked
    like any other, and before normalization so every renderer, the REST
    contract and the MCP schema see ordinary `enabled_when`/`immutable_when`
    without needing to know transitions exist.
    """

    return {
        entity_name: _derive_entity_guards(entity, documents[entity_name], diagnostics)
        for entity_name, entity in entities.items()
    }


def _derive_entity_guards(
    entity: EntitySource,
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> EntitySource:
    transitions = {
        name: action.transition
        for name, action in sorted(entity.actions.items())
        if action.transition is not None
    }
    if not transitions:
        return entity

    sound = _sound_state_fields(entity, transitions, document, diagnostics)
    for action_name, transition in transitions.items():
        _check_transition_states(entity, action_name, transition, document, diagnostics)
        _check_stamp_targets(entity, action_name, transition, document, diagnostics)
    _check_every_state_is_reachable(entity, transitions, sound, document, diagnostics)

    locked = {
        field_name: {
            transition.to
            for transition in transitions.values()
            if transition.field == field_name and transition.locks_record
        }
        for field_name in sound
    }
    return entity.model_copy(
        update={
            "actions": _actions_with_state_guards(
                entity, transitions, sound, document, diagnostics
            ),
            "fields": _fields_with_derived_immutability(
                entity, locked, document, diagnostics
            ),
        }
    )


def _sound_state_fields(
    entity: EntitySource,
    transitions: Mapping[str, Any],
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> set[str]:
    """Field names fit to carry a machine, reported once however many use them."""

    sound: set[str] = set()
    for field_name in sorted({transition.field for transition in transitions.values()}):
        field = entity.fields.get(field_name)
        if field is None or field.type != "choice":
            _add(
                diagnostics,
                "TIDE270",
                f"transition field {field_name!r} must be a declared choice field",
                document,
                ("fields", field_name),
            )
            continue
        usable = True
        if field.write != "action_only":
            _add(
                diagnostics,
                "TIDE272",
                f"state field {field_name!r} must be write: action_only, or an "
                "ordinary update moves the record without running the action",
                document,
                ("fields", field_name, "write"),
            )
            usable = False
        if field.default is None:
            _add(
                diagnostics,
                "TIDE272",
                f"state field {field_name!r} must declare a default, which is the "
                "state a new record starts in",
                document,
                ("fields", field_name, "default"),
            )
            usable = False
        if usable:
            sound.add(field_name)
    return sound


def _check_transition_states(
    entity: EntitySource,
    action_name: str,
    transition: Any,
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    field = entity.fields.get(transition.field)
    if field is None or field.type != "choice":
        return
    named = [("from", state) for state in transition.from_] + [("to", transition.to)]
    for role, state in named:
        if state not in field.choices:
            _add(
                diagnostics,
                "TIDE271",
                f"transition {role} {state!r} is not one of the choices declared "
                f"for {transition.field!r}",
                document,
                ("actions", action_name, "transition", role),
            )


def _check_stamp_targets(
    entity: EntitySource,
    action_name: str,
    transition: Any,
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    """A stamp is checked, not written: the handler still records the change.

    What the compiler can tell is whether the fields it names could hold the
    value and whether anything but the action could forge one.
    """

    for stamp_field, value in sorted(transition.stamp.items()):
        path = ("actions", action_name, "transition", "stamp", stamp_field)
        field = entity.fields.get(stamp_field)
        if field is None:
            _add(
                diagnostics,
                "TIDE275",
                f"transition stamps {stamp_field!r}, which the entity does not declare",
                document,
                path,
            )
            continue
        expected = _STAMP_FIELD_TYPES[value]
        if field.type != expected:
            _add(
                diagnostics,
                "TIDE275",
                f"transition stamps {value!r} into {stamp_field!r}, which is "
                f"{field.type!r} rather than {expected!r}",
                document,
                path,
            )
        if field.write != "action_only":
            _add(
                diagnostics,
                "TIDE275",
                f"stamp field {stamp_field!r} must be write: action_only, or an "
                "ordinary update could forge a transition that never happened",
                document,
                ("fields", stamp_field, "write"),
            )


def _check_every_state_is_reachable(
    entity: EntitySource,
    transitions: Mapping[str, Any],
    sound: set[str],
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    """A declared state nothing can produce is a record nobody can create.

    `sales.Invoice` declared `cancelled` with no action that reached it, while
    the demo data seeded a row already in it: that row could not be posted,
    because it was not a draft, and could not be edited, for the same reason.
    """

    for field_name in sorted(sound):
        field = entity.fields[field_name]
        reached = {field.default} | {
            transition.to
            for transition in transitions.values()
            if transition.field == field_name
        }
        for state in field.choices:
            if state not in reached:
                _add(
                    diagnostics,
                    "TIDE274",
                    f"state {state!r} of {field_name!r} is declared but no transition "
                    "reaches it, and it is not the initial state",
                    document,
                    ("fields", field_name, "choices"),
                )


def _actions_with_state_guards(
    entity: EntitySource,
    transitions: Mapping[str, Any],
    sound: set[str],
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> dict[str, ActionSource]:
    actions = dict(entity.actions)
    for action_name, transition in transitions.items():
        if transition.field not in sound:
            continue
        action = actions[action_name]
        authored = action.enabled_when
        if authored and _mentions(authored, transition.field):
            _add(
                diagnostics,
                "TIDE273",
                f"enabled_when may not test {transition.field!r}: the transition's "
                "from state already says when this action applies",
                document,
                ("actions", action_name, "enabled_when"),
            )
            continue
        guard = _state_predicate(transition.field, transition.from_)
        actions[action_name] = action.model_copy(
            update={"enabled_when": f"{guard} and ({authored})" if authored else guard}
        )
    return actions


def _fields_with_derived_immutability(
    entity: EntitySource,
    locked: Mapping[str, set[str]],
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> dict[str, FieldSource]:
    """Freeze the ordinarily writable fields in every locked state.

    Fields the workflow already owns are left alone: their writability was
    never in question, and a primary key is not editable in the first place.
    """

    predicates = {
        field_name: _state_predicate(field_name, states)
        for field_name, states in locked.items()
        if states
    }
    if not predicates:
        return dict(entity.fields)

    fields = dict(entity.fields)
    for field_name, field in sorted(entity.fields.items()):
        if field.immutable_when and any(
            _mentions(field.immutable_when, state_field) for state_field in predicates
        ):
            _add(
                diagnostics,
                "TIDE273",
                f"immutable_when on {field_name!r} may not test a state field: a "
                "transition with locks_record already freezes this field",
                document,
                ("fields", field_name, "immutable_when"),
            )
            continue
        if (
            field.write != "normal"
            or field.readonly
            or field.primary_key
            or field.computed is not None
            or field_name in predicates
        ):
            continue
        derived = " or ".join(predicates[name] for name in sorted(predicates))
        if field.type == "file":
            # A lock freezes a file field only once it holds a file. The
            # countersigned copy of a document arrives *after* the record it
            # belongs to is posted, which is the real order of events, so a
            # locked record accepts a document it does not have yet and
            # refuses to change its mind afterwards. An author who wants a
            # file field frozen outright writes `immutable_when` and gets it
            # verbatim, below.
            derived = f"({derived}) and {field_name} != null"
        combined = (
            f"({derived}) or ({field.immutable_when})"
            if field.immutable_when
            else derived
        )
        fields[field_name] = field.model_copy(update={"immutable_when": combined})
    return fields


def _validate_file_fields(
    entity: EntitySource,
    document: SourceDocument,
    database_mode: str,
    diagnostics: list[Diagnostic],
) -> None:
    """What a `file` field may and may not say about itself.

    The field holds a key into the attachment store rather than a value of
    its own, so every declaration that decides something *about a value* --
    comparing it, defaulting it, masking it, computing it -- is refused
    here rather than accepted and ignored.
    """

    for field_name, field in entity.fields.items():
        if field.type != "file":
            if field.max_size is not None or field.accept:
                _add(
                    diagnostics,
                    "TIDE291",
                    "max_size and accept belong to file fields alone",
                    document,
                    ("fields", field_name),
                )
            continue

        if database_mode == "legacy":
            _add(
                diagnostics,
                "TIDE290",
                "file fields need a managed database; a legacy schema is not "
                "TIDE's to add the column or the attachment table to",
                document,
                ("fields", field_name),
            )

        if field.max_size is None:
            _add(
                diagnostics,
                "TIDE287",
                "a file field must declare max_size",
                document,
                ("fields", field_name),
            )
        elif parse_size_literal(field.max_size) > MAX_FILE_SIZE_BYTES:
            _add(
                diagnostics,
                "TIDE288",
                f"max_size must not exceed {MAX_FILE_SIZE_BYTES // (1024 * 1024)}mb",
                document,
                ("fields", field_name, "max_size"),
            )

        for label, declared in (
            ("primary_key", field.primary_key),
            ("unique", field.unique),
            ("concurrency_token", field.concurrency_token),
            ("choices", bool(field.choices)),
            ("values", bool(field.values)),
            ("target", field.target is not None),
            ("default", field.default is not None),
            ("default_factory", field.default_factory is not None),
            ("server_default", field.server_default is not None),
            ("edit_mask", field.edit_mask is not None),
            ("computed", field.computed is not None),
            ("format", field.format is not None),
            ("length", field.length is not None),
        ):
            if declared:
                _add(
                    diagnostics,
                    "TIDE289",
                    f"a file field may not declare {label}",
                    document,
                    ("fields", field_name),
                )


def _validate_entities(
    entities: dict[str, EntitySource],
    documents: dict[str, SourceDocument],
    formats: set[str],
    dependencies: dict[tuple[str, str], tuple[str, ...]],
    diagnostics: list[Diagnostic],
    project_root: Path,
    database_mode: str,
) -> None:
    _validate_migration_metadata(
        entities,
        documents,
        diagnostics,
        database_mode,
    )
    for entity_name, entity in entities.items():
        document = documents[entity_name]
        if not IDENTIFIER.fullmatch(entity_name):
            _add(
                diagnostics,
                "TIDE201",
                "entity identifiers must be qualified dotted names",
                document,
                ("entity",),
            )

        if database_mode == "legacy" and (
            entity.storage is None or entity.storage.table is None
        ):
            _add(
                diagnostics,
                "TIDE228",
                "legacy database entities must declare their physical storage table",
                document,
                ("storage",),
            )

        primary_keys = [name for name, field in entity.fields.items() if field.primary_key]
        if len(primary_keys) != 1:
            _add(
                diagnostics,
                "TIDE202",
                "an entity must declare exactly one primary key in schema v0.1",
                document,
                ("fields",),
            )

        concurrency_tokens = [
            name for name, field in entity.fields.items() if field.concurrency_token
        ]
        if len(concurrency_tokens) > 1:
            _add(
                diagnostics,
                "TIDE203",
                "an entity may declare at most one concurrency token",
                document,
                ("fields",),
            )

        _validate_file_fields(entity, document, database_mode, diagnostics)

        for search_field in entity.search_fields:
            _require_field(
                entity,
                search_field,
                document,
                ("search_fields", entity.search_fields.index(search_field)),
                diagnostics,
            )

        if entity.display:
            display_fields = DISPLAY_FIELD.findall(entity.display)
            if not display_fields and "{" not in entity.display:
                display_fields = [entity.display]
            for display_field in display_fields:
                _require_field(
                    entity, display_field, document, ("display",), diagnostics
                )

        shortcut_actions: dict[str, str] = {}
        for operation, permission in entity.permissions.model_dump(by_alias=True).items():
            if permission and not IDENTIFIER.fullmatch(permission):
                _add(
                    diagnostics,
                    "TIDE216",
                    f"{operation} permission must be a qualified dotted name",
                    document,
                    ("permissions", operation),
                )
        for action_name, action in entity.actions.items():
            if action_name in RESERVED_ACTION_NAMES:
                _add(
                    diagnostics,
                    "TIDE276",
                    f"action {action_name!r} collides with the form action bar's "
                    "built-in of that name and would be silently dropped from "
                    "every form",
                    document,
                    ("actions", action_name),
                )
            if not action.permission and not action.unrestricted:
                _add(
                    diagnostics,
                    "TIDE226",
                    f"action {action_name!r} must declare a permission or explicitly set "
                    "unrestricted: true",
                    document,
                    ("actions", action_name),
                )
            if action.permission and action.unrestricted:
                _add(
                    diagnostics,
                    "TIDE227",
                    f"action {action_name!r} cannot declare both permission and unrestricted",
                    document,
                    ("actions", action_name),
                )
            if not IDENTIFIER.fullmatch(action.execute):
                _add(
                    diagnostics,
                    "TIDE220",
                    "action handlers must be qualified dotted names",
                    document,
                    ("actions", action_name, "execute"),
                )
            else:
                _validate_handler_reference(
                    action.execute,
                    project_root,
                    document,
                    ("actions", action_name, "execute"),
                    diagnostics,
                )
            if action.shortcut:
                shortcut = action.shortcut.casefold()
                if shortcut in shortcut_actions:
                    _add(
                        diagnostics,
                        "TIDE221",
                        f"shortcut conflicts with action {shortcut_actions[shortcut]!r}",
                        document,
                        ("actions", action_name, "shortcut"),
                    )
                shortcut_actions[shortcut] = action_name
            for parameter_name in action.parameters:
                if not PARAMETER_NAME.fullmatch(parameter_name):
                    _add(
                        diagnostics,
                        "TIDE292",
                        f"parameter {parameter_name!r} must be a plain "
                        "identifier: each one becomes a field on the "
                        "generated MCP tool arguments",
                        document,
                        ("actions", action_name, "parameters", parameter_name),
                    )
            for property_name in ("enabled_when", "visible_when"):
                expression = getattr(action, property_name)
                if expression:
                    _validate_expression_at(
                        expression,
                        entity,
                        entities,
                        document,
                        ("actions", action_name, property_name),
                        diagnostics,
                        expected_type="boolean",
                    )

        declared_rules: set[str] = set()
        for index, rule in enumerate(entity.appearance):
            rule_path: tuple[str | int, ...] = ("appearance", index)
            if rule.name in declared_rules:
                _add(
                    diagnostics,
                    "TIDE280",
                    f"appearance rule {rule.name!r} is declared twice; the "
                    "first match owns a target, so the second could only be "
                    "read by counting",
                    document,
                    (*rule_path, "name"),
                )
            declared_rules.add(rule.name)
            for effect in ("enabled", "visible"):
                if getattr(rule, effect) is True:
                    _add(
                        diagnostics,
                        "TIDE281",
                        f"appearance rule {rule.name!r} may not grant "
                        f"{effect}: a rule subtracts, and granting would have "
                        "to overrule the workflow lock or the permission that "
                        "withheld it",
                        document,
                        (*rule_path, effect),
                    )
            if rule.visible is False and not rule.fields:
                _add(
                    diagnostics,
                    "TIDE282",
                    f"appearance rule {rule.name!r} hides a record rather than "
                    "a field; narrowing which records appear is a named "
                    "filter or a row policy, both of which paging and counts "
                    "already account for",
                    document,
                    (*rule_path, "visible"),
                )
            # A boolean, checked here rather than at evaluation: a rule keyed
            # on a string fires for every record that has one, and the only
            # symptom is a screen that is the wrong colour everywhere.
            _validate_expression_at(
                rule.when,
                entity,
                entities,
                document,
                (*rule_path, "when"),
                diagnostics,
                expected_type="boolean",
            )
            for field_name in rule.fields:
                _require_field(
                    entity,
                    field_name,
                    document,
                    (*rule_path, "fields"),
                    diagnostics,
                )

        for field_name, field in entity.fields.items():
            field_path = ("fields", field_name)
            if database_mode == "legacy" and _is_persisted_field(field):
                mapping_property = "storage" if field.type == "reference" else "column"
                if getattr(field, mapping_property) is None:
                    _add(
                        diagnostics,
                        "TIDE229",
                        f"legacy database field {field_name!r} must declare its physical "
                        f"{mapping_property}",
                        document,
                        (*field_path, mapping_property),
                    )
            if field.type in {"reference", "collection"}:
                if not field.target:
                    _add(
                        diagnostics,
                        "TIDE204",
                        f"{field.type} fields require a target",
                        document,
                        field_path,
                    )
                    continue
                target = entities.get(field.target)
                if target is None:
                    _add(
                        diagnostics,
                        "TIDE205",
                        f"unknown relationship target {field.target!r}",
                        document,
                        (*field_path, "target"),
                    )
                    continue
                if field.inverse:
                    inverse = target.fields.get(field.inverse)
                    if inverse is None:
                        _add(
                            diagnostics,
                            "TIDE206",
                            f"target {field.target!r} has no inverse field {field.inverse!r}",
                            document,
                            (*field_path, "inverse"),
                        )
                    elif inverse.target != entity_name:
                        _add(
                            diagnostics,
                            "TIDE207",
                            f"inverse field {field.target}.{field.inverse} does not target {entity_name}",
                            document,
                            (*field_path, "inverse"),
                        )
                if field.order_by and field.order_by not in target.fields:
                    _add(
                        diagnostics,
                        "TIDE208",
                        f"target {field.target!r} has no order field {field.order_by!r}",
                        document,
                        (*field_path, "order_by"),
                    )
            elif field.target:
                _add(
                    diagnostics,
                    "TIDE209",
                    "only reference and collection fields may declare target",
                    document,
                    (*field_path, "target"),
                )

            if field.on_select:
                if field.type != "reference":
                    _add(
                        diagnostics,
                        "TIDE219",
                        "only reference fields may declare on_select assignments",
                        document,
                        (*field_path, "on_select"),
                    )
                elif field.target and field.target in entities:
                    _validate_selection_assignments(
                        entity,
                        field_name,
                        field,
                        entities[field.target],
                        document,
                        diagnostics,
                    )

            if field.type == "choice" and not field.choices:
                _add(
                    diagnostics,
                    "TIDE210",
                    "choice fields require at least one choice",
                    document,
                    (*field_path, "choices"),
                )
            if field.values:
                _validate_value_map(field, document, field_path, diagnostics)
            if field.type == "decimal":
                if field.precision == 0:
                    _add(
                        diagnostics,
                        "TIDE243",
                        "decimal precision must be positive",
                        document,
                        (*field_path, "precision"),
                    )
                if (
                    field.precision is not None
                    and field.scale is not None
                    and field.scale > field.precision
                ):
                    _add(
                        diagnostics,
                        "TIDE243",
                        "decimal scale cannot exceed precision",
                        document,
                        (*field_path, "scale"),
                    )
            elif field.precision is not None or field.scale is not None:
                _add(
                    diagnostics,
                    "TIDE243",
                    "precision and scale apply only to decimal fields",
                    document,
                    field_path,
                )
            if field.format and field.format not in formats:
                _add(
                    diagnostics,
                    "TIDE211",
                    f"unknown semantic format {field.format!r}",
                    document,
                    (*field_path, "format"),
                )
            if field.edit_mask is not None:
                _validate_edit_mask(field, document, field_path, diagnostics)
            if field.write in {"action_only", "system"} and not field.readonly:
                _add(
                    diagnostics,
                    "TIDE212",
                    f"{field.write} fields must also be readonly to adapters",
                    document,
                    (*field_path, "readonly"),
                )
            if field.concurrency_token and field.type != "integer":
                _add(
                    diagnostics,
                    "TIDE213",
                    "schema v0.1 concurrency tokens must be integers",
                    document,
                    (*field_path, "concurrency_token"),
                )
            if field.default_factory == "today" and field.type != "date":
                _add(
                    diagnostics,
                    "TIDE217",
                    "the today default factory requires a date field",
                    document,
                    (*field_path, "default_factory"),
                )
            if field.default_factory is not None and field.default is not None:
                _add(
                    diagnostics,
                    "TIDE218",
                    "fields cannot declare both default and default_factory",
                    document,
                    (*field_path, "default_factory"),
                )

            if field.computed:
                result = _validate_expression_at(
                    field.computed.expression,
                    entity,
                    entities,
                    document,
                    (*field_path, "computed", "expression"),
                    diagnostics,
                    expected_type=field.type,
                )
                dependencies[(entity_name, field_name)] = result.dependencies
            if field.immutable_when:
                _validate_expression_at(
                    field.immutable_when,
                    entity,
                    entities,
                    document,
                    (*field_path, "immutable_when"),
                    diagnostics,
                    expected_type="boolean",
                )
            if field.generated_by:
                _validate_handler_reference(
                    field.generated_by,
                    project_root,
                    document,
                    (*field_path, "generated_by"),
                    diagnostics,
                )

        for validation_index, validation in enumerate(entity.validations):
            validation_path = ("validations", validation_index)
            if not validation.assert_ and not validation.handler:
                _add(
                    diagnostics,
                    "TIDE222",
                    "validation requires either assert or handler",
                    document,
                    validation_path,
                )
            for field_name in validation.fields:
                _require_field(
                    entity, field_name, document, (*validation_path, "fields"), diagnostics
                )
            if validation.when:
                _validate_expression_at(
                    validation.when,
                    entity,
                    entities,
                    document,
                    (*validation_path, "when"),
                    diagnostics,
                    expected_type="boolean",
                )
            if validation.assert_:
                _validate_expression_at(
                    validation.assert_,
                    entity,
                    entities,
                    document,
                    (*validation_path, "assert"),
                    diagnostics,
                    expected_type="boolean",
                )
            if validation.handler:
                _validate_handler_reference(
                    validation.handler,
                    project_root,
                    document,
                    (*validation_path, "handler"),
                    diagnostics,
                )

        for filter_name, filter_ in entity.filters.items():
            _validate_expression_at(
                filter_.criteria,
                entity,
                entities,
                document,
                ("filters", filter_name, "criteria"),
                diagnostics,
                expected_type="boolean",
            )

    _validate_computed_cycles(entities, documents, dependencies, diagnostics)


def _is_persisted_field(field: Any) -> bool:
    if field.type == "collection":
        return False
    return field.computed is None or field.computed.materialization != "virtual"


def _validate_edit_mask(
    field: FieldSource,
    document: SourceDocument,
    field_path: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> None:
    mask = field.edit_mask
    if isinstance(mask, str):
        match = re.fullmatch(r"0(?:([.,])(0+))?", mask)
        if field.type not in {"integer", "decimal"} or match is None:
            _add(
                diagnostics,
                "TIDE243",
                "typed edit masks use 0, 0.00, or 0,00 on numeric fields",
                document,
                (*field_path, "edit_mask"),
            )
            return
        fractional_digits = len(match.group(2) or "")
        if field.type == "integer" and fractional_digits:
            _add(
                diagnostics,
                "TIDE243",
                "integer edit masks cannot contain fractional digits",
                document,
                (*field_path, "edit_mask"),
            )
        if field.type == "decimal":
            if field.scale is None:
                _add(
                    diagnostics,
                    "TIDE243",
                    "decimal edit masks require a declared scale",
                    document,
                    (*field_path, "edit_mask"),
                )
            elif fractional_digits > field.scale:
                _add(
                    diagnostics,
                    "TIDE243",
                    f"edit mask has {fractional_digits} decimal places but field "
                    f"scale is {field.scale}",
                    document,
                    (*field_path, "edit_mask"),
                )
        return

    if field.type != "string":
        _add(
            diagnostics,
            "TIDE243",
            "regular-expression edit masks require a string field",
            document,
            (*field_path, "edit_mask"),
        )
        return
    try:
        re.compile(mask.regex)
    except re.error as error:
        _add(
            diagnostics,
            "TIDE243",
            f"invalid edit-mask regular expression: {error}",
            document,
            (*field_path, "edit_mask", "regex"),
        )


def _validate_computed_cycles(
    entities: dict[str, EntitySource],
    documents: dict[str, SourceDocument],
    dependencies: dict[tuple[str, str], tuple[str, ...]],
    diagnostics: list[Diagnostic],
) -> None:
    for entity_name, entity in entities.items():
        computed = {name for name, field in entity.fields.items() if field.computed}
        graph = {
            name: {
                dependency.split(".", 1)[0]
                for dependency in dependencies.get((entity_name, name), ())
                if dependency.split(".", 1)[0] in computed
            }
            for name in computed
        }
        _report_computed_cycles(graph, documents[entity_name], diagnostics)


def _report_computed_cycles(
    graph: dict[str, set[str]],
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    """Walk one entity's computed-field dependencies and report every cycle.

    Its own function rather than a closure in the caller's loop: the traversal
    state is per-entity, and a helper defined inside a loop reads whichever
    entity the loop reached last if it is ever called after the iteration that
    made it. Nothing calls this one late today, but the shape is the hazard.
    """

    visiting: list[str] = []
    visited: set[str] = set()
    reported: set[frozenset[str]] = set()

    def visit(name: str) -> None:
        if name in visiting:
            cycle = visiting[visiting.index(name) :] + [name]
            identity = frozenset(cycle)
            if identity not in reported:
                reported.add(identity)
                _add(
                    diagnostics,
                    "TIDE214",
                    "computed-field cycle: " + " -> ".join(cycle),
                    document,
                    ("fields", name, "computed", "expression"),
                )
            return
        if name in visited:
            return
        visiting.append(name)
        for dependency in graph[name]:
            visit(dependency)
        visiting.pop()
        visited.add(name)

    for field_name in graph:
        visit(field_name)


#: The field types a `values:` map may caption. A code stands for something in
#: the application that wrote it, and only these types carry one: a decimal or
#: a date is a quantity, and `choice` already names its own members.
VALUE_MAP_TYPES: dict[str, type] = {
    "integer": int,
    "string": str,
    "boolean": bool,
}


def _validate_value_map(
    field: FieldSource,
    document: SourceDocument,
    field_path: tuple[str | int, ...],
    diagnostics: list[Diagnostic],
) -> None:
    """A value map has to fit the column it captions, and say each code once."""

    expected = VALUE_MAP_TYPES.get(field.type)
    if expected is None or field.computed is not None:
        _add(
            diagnostics,
            "TIDE277",
            "a value map requires a stored integer, string or boolean field",
            document,
            (*field_path, "values"),
        )
        return
    seen: list[Any] = []
    for index, item in enumerate(field.values):
        # `bool` is a subclass of `int`, so an exact type is the only check
        # that keeps `true` out of an integer map.
        if type(item.value) is not expected:
            _add(
                diagnostics,
                "TIDE278",
                f"value {item.value!r} is not {field.type}",
                document,
                (*field_path, "values", index, "value"),
            )
        elif any(existing == item.value for existing in seen):
            _add(
                diagnostics,
                "TIDE279",
                f"value {item.value!r} is captioned more than once",
                document,
                (*field_path, "values", index, "value"),
            )
        else:
            seen.append(item.value)


def _validate_selection_assignments(
    entity: EntitySource,
    reference_name: str,
    reference: FieldSource,
    target: EntitySource,
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    assert reference.on_select is not None
    for destination_name, assignment in reference.on_select.assign.items():
        path = (
            "fields",
            reference_name,
            "on_select",
            "assign",
            destination_name,
        )
        destination = entity.fields.get(destination_name)
        if destination is None:
            _add(
                diagnostics,
                "TIDE219",
                f"selection assignment targets unknown field {destination_name!r}",
                document,
                path,
            )
            continue
        source = target.fields.get(assignment.source)
        if source is None:
            _add(
                diagnostics,
                "TIDE219",
                f"selection assignment reads unknown field "
                f"{reference.target}.{assignment.source}",
                document,
                (*path, "from"),
            )
            continue
        if (
            destination.primary_key
            or destination.readonly
            or destination.write != "normal"
            or destination.computed is not None
        ):
            _add(
                diagnostics,
                "TIDE219",
                f"selection assignment target {destination_name!r} is not writable",
                document,
                path,
            )
        if source.type != destination.type or (
            source.type == "reference" and source.target != destination.target
        ):
            _add(
                diagnostics,
                "TIDE219",
                f"selection assignment cannot copy {source.type} "
                f"to {destination.type} field {destination_name!r}",
                document,
                (*path, "from"),
            )


def _validate_lookup_view(
    lookup_name: str,
    reference: FieldSource,
    views: dict[str, ViewSource],
    entities: dict[str, EntitySource],
    document: SourceDocument,
    path: tuple[str | int, ...],
    diagnostics: list[Diagnostic],
) -> None:
    lookup = views.get(lookup_name)
    if lookup is None:
        _add(
            diagnostics,
            "TIDE239",
            f"unknown lookup view {lookup_name!r}",
            document,
            path,
        )
        return
    if _view_kind(lookup) != "lookup":
        _add(
            diagnostics,
            "TIDE239",
            f"view {lookup_name!r} is not a lookup view",
            document,
            path,
        )
    lookup_entity = lookup.entity or _infer_view_entity(lookup_name, entities)
    if reference.target and lookup_entity != reference.target:
        _add(
            diagnostics,
            "TIDE239",
            f"lookup view {lookup_name!r} targets {lookup_entity!r}, "
            f"not {reference.target!r}",
            document,
            path,
        )


def _validate_views(
    views: dict[str, ViewSource],
    documents: dict[str, SourceDocument],
    entities: dict[str, EntitySource],
    entity_documents: dict[str, SourceDocument],
    presets: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> None:
    for entity_name, entity in entities.items():
        for field_name, field in entity.fields.items():
            if field.lookup_view is None:
                continue
            if field.type != "reference":
                _add(
                    diagnostics,
                    "TIDE239",
                    "only reference fields may declare lookup_view",
                    entity_documents[entity_name],
                    ("fields", field_name, "lookup_view"),
                )
                continue
            _validate_lookup_view(
                field.lookup_view,
                field,
                views,
                entities,
                entity_documents[entity_name],
                ("fields", field_name, "lookup_view"),
                diagnostics,
            )

    for view_name, view in views.items():
        document = documents[view_name]
        entity_name = view.entity or _infer_view_entity(view_name, entities)
        if entity_name is None or entity_name not in entities:
            _add(
                diagnostics,
                "TIDE231",
                "view does not resolve to a known entity; declare entity explicitly",
                document,
                ("entity",) if view.entity else ("view",),
            )
            continue
        entity = entities[entity_name]
        if view.extends and view.extends not in presets:
            _add(
                diagnostics,
                "TIDE232",
                f"unknown presentation preset {view.extends!r}",
                document,
                ("extends",),
            )
        elif view.extends and presets[view.extends].kind != _view_kind(view):
            _add(
                diagnostics,
                "TIDE237",
                f"preset {view.extends!r} has kind {presets[view.extends].kind!r}, not {_view_kind(view)!r}",
                document,
                ("extends",),
            )
        if view.base and not view.base.startswith("generated.") and view.base not in views:
            _add(
                diagnostics,
                "TIDE233",
                f"unknown base view {view.base!r}",
                document,
                ("base",),
            )
        for field_name in (*view.columns, *view.search, *view.fields.keys()):
            _require_field(entity, field_name, document, ("view",), diagnostics)
        _validate_view_summaries(view, entity, document, diagnostics)
        if _view_kind(view) == "browse":
            _validate_browse_edit_mode(
                view.settings, document, ("settings",), diagnostics
            )
        for field_name, configuration in view.fields.items():
            field = entity.fields.get(field_name)
            if field is None:
                continue
            editor = configuration.get("editor")
            if editor is not None and (
                not isinstance(editor, str) or editor not in {"select", "lookup"}
            ):
                _add(
                    diagnostics,
                    "TIDE238",
                    f"unknown reference editor {editor!r}; expected 'select' or 'lookup'",
                    document,
                    ("fields", field_name, "editor"),
                )
            if editor is not None and field.type != "reference":
                _add(
                    diagnostics,
                    "TIDE238",
                    "select and lookup editors require a reference field",
                    document,
                    ("fields", field_name, "editor"),
                )
            lookup_view = configuration.get("lookup_view", field.lookup_view)
            if editor == "lookup" and not lookup_view:
                _add(
                    diagnostics,
                    "TIDE239",
                    "lookup editors require a lookup_view",
                    document,
                    ("fields", field_name, "lookup_view"),
                )
            elif lookup_view is not None:
                if not isinstance(lookup_view, str):
                    _add(
                        diagnostics,
                        "TIDE239",
                        "lookup_view must be a view name",
                        document,
                        ("fields", field_name, "lookup_view"),
                    )
                else:
                    _validate_lookup_view(
                        lookup_view,
                        field,
                        views,
                        entities,
                        document,
                        ("fields", field_name, "lookup_view"),
                        diagnostics,
                    )
            allow_create = configuration.get("allow_create", False)
            create_view = configuration.get("create_view")
            if not isinstance(allow_create, bool):
                _add(
                    diagnostics,
                    "TIDE242",
                    "allow_create must be true or false",
                    document,
                    ("fields", field_name, "allow_create"),
                )
            elif allow_create:
                if field.type != "reference" or field.target not in entities:
                    _add(
                        diagnostics,
                        "TIDE242",
                        "lookup record creation requires a reference field",
                        document,
                        ("fields", field_name, "allow_create"),
                    )
                elif not isinstance(create_view, str):
                    _add(
                        diagnostics,
                        "TIDE242",
                        "allow_create requires a create_view",
                        document,
                        ("fields", field_name, "create_view"),
                    )
                else:
                    target_view = views.get(create_view)
                    target_entity = (
                        target_view.entity
                        or _infer_view_entity(create_view, entities)
                        if target_view is not None
                        else None
                    )
                    if (
                        target_view is None
                        or _view_kind(target_view) != "form"
                        or target_entity != field.target
                    ):
                        _add(
                            diagnostics,
                            "TIDE242",
                            f"create_view {create_view!r} must be a form for "
                            f"{field.target}",
                            document,
                            ("fields", field_name, "create_view"),
                        )
                    target = entities[field.target]
                    if not target.expose.tui or not target.permissions.create:
                        _add(
                            diagnostics,
                            "TIDE242",
                            f"target entity {field.target} must expose TUI creation",
                            document,
                            ("fields", field_name, "allow_create"),
                        )
            elif create_view is not None:
                _add(
                    diagnostics,
                    "TIDE242",
                    "create_view requires allow_create: true",
                    document,
                    ("fields", field_name, "create_view"),
                )
        for filter_name, filter_ in view.filters.items():
            _validate_expression_at(
                filter_.criteria,
                entity,
                entities,
                document,
                ("filters", filter_name, "criteria"),
                diagnostics,
                expected_type="boolean",
            )
        if _view_kind(view) == "lookup":
            for index, field_name in enumerate(view.search):
                field = entity.fields.get(field_name)
                if field is not None and (
                    field.type not in {"string", "choice"} or field.computed
                ):
                    _add(
                        diagnostics,
                        "TIDE239",
                        "lookup search fields must be stored strings or choices",
                        document,
                        ("search", index),
                    )
            for index, field_name in enumerate(view.columns):
                field = entity.fields.get(field_name)
                if field is not None and field.type == "collection":
                    _add(
                        diagnostics,
                        "TIDE239",
                        "lookup columns cannot contain collection fields",
                        document,
                        ("columns", index),
                    )
        if _view_kind(view) == "inline_edit":
            _validate_inline_editor_layout(
                view,
                entity,
                document,
                diagnostics,
            )
        _validate_view_actions(view, entity, document, diagnostics)
        _validate_layout(view, entity, entities, views, document, diagnostics)

    for view_name in views:
        chain: list[str] = []
        current = view_name
        while current in views:
            if current in chain:
                cycle = chain[chain.index(current) :] + [current]
                _add(
                    diagnostics,
                    "TIDE236",
                    "view inheritance cycle: " + " -> ".join(cycle),
                    documents[view_name],
                    ("base",),
                )
                break
            chain.append(current)
            base = views[current].base
            if not base or base.startswith("generated."):
                break
            current = base


def _validate_layout(
    view: ViewSource,
    entity: EntitySource,
    entities: dict[str, EntitySource],
    views: dict[str, ViewSource],
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    for index, node in enumerate(view.layout):
        if not isinstance(node, dict):
            continue
        if "tab" in node:
            _validate_presentation_label(
                node["tab"],
                name="layout tab",
                document=document,
                path=("layout", index, "tab"),
                diagnostics=diagnostics,
            )
        if "rows" in node:
            for field_name in _strings_in(node["rows"]):
                _require_field(entity, field_name, document, ("layout", index, "rows"), diagnostics)
        if "collection" in node:
            collection = node["collection"]
            field = entity.fields.get(collection) if isinstance(collection, str) else None
            if field is None or field.type != "collection":
                _add(
                    diagnostics,
                    "TIDE234",
                    f"layout collection {collection!r} is not a collection field",
                    document,
                    ("layout", index, "collection"),
                )
            referenced_view = node.get("view")
            if referenced_view and referenced_view not in views:
                _add(
                    diagnostics,
                    "TIDE235",
                    f"unknown collection view {referenced_view!r}",
                    document,
                    ("layout", index, "view"),
                )
            elif referenced_view:
                inline_view = views[referenced_view]
                inline_entity = inline_view.entity or _infer_view_entity(
                    referenced_view, entities
                )
                if (
                    _view_kind(inline_view) != "inline_edit"
                    or field is None
                    or inline_entity != field.target
                ):
                    _add(
                        diagnostics,
                        "TIDE244",
                        f"collection view {referenced_view!r} must be an inline_edit "
                        f"view for {field.target if field is not None else 'the collection target'}",
                        document,
                        ("layout", index, "view"),
                    )
            raw_actions = node.get("actions")
            if raw_actions is not None:
                _validate_action_names(
                    raw_actions,
                    allowed={"add", "apply", "remove"},
                    description="collection action bar",
                    document=document,
                    path=("layout", index, "actions"),
                    diagnostics=diagnostics,
                )
        elif "actions" in node:
            _add(
                diagnostics,
                "TIDE244",
                "layout actions are supported only on collection sections",
                document,
                ("layout", index, "actions"),
            )


def _validate_view_actions(
    view: ViewSource,
    entity: EntitySource,
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    if not view.actions:
        return
    if _view_kind(view) != "form":
        _add(
            diagnostics,
            "TIDE244",
            "view action bars are supported only on form views",
            document,
            ("actions",),
        )
    _validate_action_names(
        view.actions,
        allowed={*RESERVED_ACTION_NAMES, *entity.actions},
        description="view action bar",
        document=document,
        path=("actions",),
        diagnostics=diagnostics,
    )


def _validate_action_names(
    value: Any,
    *,
    allowed: set[str],
    description: str,
    document: SourceDocument,
    path: tuple[str | int, ...],
    diagnostics: list[Diagnostic],
) -> None:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        _add(
            diagnostics,
            "TIDE244",
            f"{description} must be a sequence of action names",
            document,
            path,
        )
        return
    names = tuple(value)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        _add(
            diagnostics,
            "TIDE244",
            f"{description} repeats actions: " + ", ".join(duplicates),
            document,
            path,
        )
    unknown = sorted(set(names) - allowed)
    if unknown:
        _add(
            diagnostics,
            "TIDE244",
            f"{description} contains unknown actions: " + ", ".join(unknown),
            document,
            path,
        )


def _validate_presentation_label(
    value: Any,
    *,
    name: str,
    document: SourceDocument,
    path: tuple[str | int, ...],
    diagnostics: list[Diagnostic],
) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 80
        or any(character in value for character in ("\r", "\n", "\x00"))
    ):
        _add(
            diagnostics,
            "TIDE244",
            f"{name} must be a non-empty single-line label of at most 80 characters",
            document,
            path,
        )


def _validate_inline_editor_layout(
    view: ViewSource,
    entity: EntitySource,
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    rows: list[tuple[int, int, tuple[str, ...]]] = []
    for section_index, node in enumerate(view.layout):
        if not isinstance(node, dict) or "rows" not in node:
            continue
        raw_rows = node["rows"]
        if not isinstance(raw_rows, (list, tuple)):
            continue
        for row_index, raw_row in enumerate(raw_rows):
            names = tuple(_strings_in(raw_row))
            rows.append((section_index, row_index, names))
            if len(names) > 2:
                _add(
                    diagnostics,
                    "TIDE241",
                    "inline editor rows support at most two fields",
                    document,
                    ("layout", section_index, "rows", row_index),
                )
    if not rows:
        return

    declared = [name for _section, _row, names in rows for name in names]
    duplicates = sorted({name for name in declared if declared.count(name) > 1})
    if duplicates:
        _add(
            diagnostics,
            "TIDE241",
            "inline editor layout repeats fields: " + ", ".join(duplicates),
            document,
            ("layout",),
        )

    expected = {
        name
        for name in view.columns
        if name in entity.fields
        and not entity.fields[name].readonly
        and entity.fields[name].computed is None
        and not view.fields.get(name, {}).get("hidden", False)
    }
    declared_set = set(declared)
    missing = sorted(expected - declared_set)
    unsupported = sorted(declared_set - expected)
    if missing:
        _add(
            diagnostics,
            "TIDE241",
            "inline editor layout omits editable fields: " + ", ".join(missing),
            document,
            ("layout",),
        )
    if unsupported:
        _add(
            diagnostics,
            "TIDE241",
            "inline editor layout includes non-editor fields: "
            + ", ".join(unsupported),
            document,
            ("layout",),
        )


def _validate_navigation(
    defaults: PresentationDefaultsSource,
    document: SourceDocument | None,
    views: dict[str, ViewSource],
    diagnostics: list[Diagnostic],
) -> None:
    """Validate the shared application-navigation contract."""

    if not defaults.navigation or document is None:
        return

    group_labels: set[str] = set()
    referenced_views: set[str] = set()
    for group_index, group in enumerate(defaults.navigation):
        group_path = ("navigation", group_index)
        label = group.label.strip()
        if not _valid_navigation_label(group.label):
            _add(
                diagnostics,
                "TIDE249",
                "navigation group labels must be non-empty, single-line text "
                "of at most 80 characters",
                document,
                (*group_path, "label"),
            )
        elif label.casefold() in group_labels:
            _add(
                diagnostics,
                "TIDE249",
                f"duplicate navigation group label {label!r}",
                document,
                (*group_path, "label"),
            )
        else:
            group_labels.add(label.casefold())

        if not group.items:
            _add(
                diagnostics,
                "TIDE249",
                "navigation groups must contain at least one item",
                document,
                (*group_path, "items"),
            )

        for item_index, item in enumerate(group.items):
            item_path = (*group_path, "items", item_index)
            view = views.get(item.view)
            if view is None:
                _add(
                    diagnostics,
                    "TIDE249",
                    f"unknown navigation view {item.view!r}",
                    document,
                    (*item_path, "view"),
                )
            elif _view_kind(view) != "browse":
                _add(
                    diagnostics,
                    "TIDE249",
                    f"navigation view {item.view!r} must be a browse view",
                    document,
                    (*item_path, "view"),
                )
            if item.view in referenced_views:
                _add(
                    diagnostics,
                    "TIDE249",
                    f"navigation view {item.view!r} is listed more than once",
                    document,
                    (*item_path, "view"),
                )
            referenced_views.add(item.view)
            if item.label is not None and not _valid_navigation_label(item.label):
                _add(
                    diagnostics,
                    "TIDE249",
                    "navigation item labels must be non-empty, single-line text "
                    "of at most 80 characters",
                    document,
                    (*item_path, "label"),
                )


def _valid_navigation_label(value: str) -> bool:
    label = value.strip()
    return bool(label) and "\n" not in value and "\r" not in value and len(label) <= 80


def _resolve_views(
    views: dict[str, ViewSource],
    view_documents: dict[str, SourceDocument],
    entities: dict[str, EntitySource],
    entity_documents: dict[str, SourceDocument],
    defaults: PresentationDefaultsSource,
    defaults_document: SourceDocument | None,
    presets: dict[str, Any],
    preset_documents: dict[str, SourceDocument],
) -> dict[str, ResolvedView]:
    resolved: dict[str, ResolvedView] = {}

    def resolve(view_name: str) -> ResolvedView:
        if view_name in resolved:
            return resolved[view_name]
        view = views[view_name]
        document = view_documents[view_name]
        entity_name = view.entity or _infer_view_entity(view_name, entities)
        assert entity_name is not None
        entity = entities[entity_name]
        kind = _view_kind(view)
        data: dict[str, Any] = {"view": view_name, "entity": entity_name, "kind": kind}
        origins: dict[str, PropertyOrigin] = {
            "view": PropertyOrigin("view overlay", document.file, ("view",)),
            "entity": PropertyOrigin("view overlay", document.file, ("entity",) if view.entity else ("view",)),
            "kind": PropertyOrigin("view overlay", document.file, ("kind",) if view.kind else ("view",)),
        }

        if view.mode == "overlay" and view.base and not view.base.startswith("generated."):
            base = resolve(view.base)
            data = deep_thaw(base.data)
            data.update(view=view_name, entity=entity_name, kind=kind)
            origins = dict(base.origins)

        data.setdefault("settings", {})
        _merge_layer(
            data["settings"],
            FRAMEWORK_VIEW_DEFAULTS[kind],
            origins,
            output_prefix=("settings",),
            layer="framework defaults",
            file=None,
            source_prefix=(kind,),
        )
        application_defaults = getattr(defaults, kind)
        _merge_layer(
            data["settings"],
            application_defaults,
            origins,
            output_prefix=("settings",),
            layer="application defaults",
            file=defaults_document.file if defaults_document else None,
            source_prefix=(kind,),
        )
        if view.extends:
            preset = presets[view.extends]
            _merge_layer(
                data["settings"],
                preset.settings,
                origins,
                output_prefix=("settings",),
                layer=f"preset:{view.extends}",
                file=preset_documents[view.extends].file,
                source_prefix=("presets", view.extends, "settings"),
            )
        if kind in entity.presentation:
            _merge_layer(
                data["settings"],
                entity.presentation[kind],
                origins,
                output_prefix=("settings",),
                layer=f"entity:{entity_name}",
                file=entity_documents[entity_name].file,
                source_prefix=("presentation", kind),
            )

        overlay = view.model_dump(mode="json", exclude_none=True)
        for property_name in (
            "settings",
            "fields",
            "columns",
            "search",
            "summaries",
            "filters",
            "layout",
            "actions",
            "surfaces",
        ):
            if property_name not in view.model_fields_set:
                continue
            incoming = overlay[property_name]
            if isinstance(incoming, dict):
                target = data.setdefault(property_name, {})
                _merge_layer(
                    target,
                    incoming,
                    origins,
                    output_prefix=(property_name,),
                    layer="view overlay",
                    file=document.file,
                    source_prefix=(property_name,),
                )
            else:
                data[property_name] = incoming
                origins[property_name] = PropertyOrigin(
                    "view overlay", document.file, (property_name,)
                )

        result = ResolvedView(
            name=view_name,
            entity=entity_name,
            kind=kind,
            data=deep_freeze(data),
            origins=immutable_mapping(origins),
        )
        resolved[view_name] = result
        return result

    for name in sorted(views):
        resolve(name)
    return resolved


def _merge_layer(
    target: dict[str, Any],
    incoming: dict[str, Any],
    origins: dict[str, PropertyOrigin],
    *,
    output_prefix: tuple[str | int, ...],
    layer: str,
    file: Path | None,
    source_prefix: tuple[str | int, ...],
) -> None:
    for key, value in incoming.items():
        output_path = (*output_prefix, key)
        source_path = (*source_prefix, key)
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            _merge_layer(
                target[key],
                value,
                origins,
                output_prefix=output_path,
                layer=layer,
                file=file,
                source_prefix=source_path,
            )
        else:
            target[key] = deep_thaw(value)
            origins[_property_path(output_path)] = PropertyOrigin(layer, file, source_path)


def _property_path(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


def _view_kind(view: ViewSource) -> str:
    if view.kind:
        return view.kind
    suffix = view.view.rsplit(".", 1)[-1]
    return {"browse": "browse", "edit": "form", "lookup": "lookup", "inline_edit": "inline_edit"}.get(suffix, "form")


def _validate_browse_edit_mode(
    settings: Mapping[str, Any],
    document: SourceDocument,
    path: tuple[str | int, ...],
    diagnostics: list[Diagnostic],
) -> None:
    mode = settings.get("edit")
    if mode is not None and mode not in BROWSE_EDIT_MODES:
        _add(
            diagnostics,
            "TIDE285",
            f"unknown browse edit mode {mode!r}; expected 'form' or 'inline'",
            document,
            (*path, "edit"),
        )


def _validate_view_summaries(
    view: ViewSource,
    entity: EntitySource,
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    if not view.summaries:
        return
    if _view_kind(view) != "browse":
        _add(
            diagnostics,
            "TIDE283",
            "summaries are a browse-view declaration",
            document,
            ("summaries",),
        )
        return
    if not view.columns:
        # Requiring the columns beside their summaries keeps the check
        # source-level: diagnostics run before view resolution, so a summary
        # against inherited columns would have nothing to look at.
        _add(
            diagnostics,
            "TIDE283",
            "summaries require columns in the same view",
            document,
            ("summaries",),
        )
        return
    for field_name, function in view.summaries.items():
        field = entity.fields.get(field_name)
        if field is None:
            _require_field(
                entity, field_name, document, ("summaries",), diagnostics
            )
            continue
        if field_name not in view.columns:
            _add(
                diagnostics,
                "TIDE283",
                f"summary column {field_name!r} is not among the view's columns",
                document,
                ("summaries", field_name),
            )
            continue
        if field.type == "collection" or (
            field.computed is not None
            and field.computed.materialization == "virtual"
        ):
            _add(
                diagnostics,
                "TIDE284",
                f"column {field_name!r} is not stored and cannot be summarized",
                document,
                ("summaries", field_name),
            )
            continue
        if field.type not in SUMMARIZABLE_FIELD_TYPES[function]:
            requirement = (
                "a numeric column"
                if function in {"sum", "avg"}
                else "an orderable column"
            )
            _add(
                diagnostics,
                "TIDE284",
                f"{function} requires {requirement}; {field_name!r} is {field.type}",
                document,
                ("summaries", field_name),
            )


def _validate_reports(
    reports: dict[str, ReportSource],
    documents: dict[str, SourceDocument],
    entities: dict[str, EntitySource],
    formats: set[str],
    diagnostics: list[Diagnostic],
) -> None:
    for report_name, report in reports.items():
        document = documents[report_name]
        entity = entities.get(report.entity)
        if entity is None:
            _add(
                diagnostics,
                "TIDE251",
                f"unknown report entity {report.entity!r}",
                document,
                ("entity",),
            )
            continue
        if report.permission is None and not report.unrestricted:
            _add(
                diagnostics,
                "TIDE256",
                "reports require a permission or unrestricted: true",
                document,
                ("permission",),
            )
        parameters = {name: parameter.type for name, parameter in report.parameters.items()}
        if report.query.criteria:
            _validate_expression_at(
                report.query.criteria,
                entity,
                entities,
                document,
                ("query", "criteria"),
                diagnostics,
                parameters=parameters,
                expected_type="boolean",
            )
        if report.kind == "summary":
            _validate_summary_report(
                report,
                entity,
                formats,
                document,
                diagnostics,
            )
            continue
        primary_key = next(
            (name for name, field in entity.fields.items() if field.primary_key),
            None,
        )
        parameter_name = (
            _record_report_parameter(report.query.criteria, primary_key)
            if report.query.criteria and primary_key
            else None
        )
        if parameter_name is None or parameter_name not in report.parameters:
            _add(
                diagnostics,
                "TIDE252",
                "record reports require query criteria '<primary_key> == $parameter'",
                document,
                ("query", "criteria"),
            )
        elif not report.parameters[parameter_name].required:
            _add(
                diagnostics,
                "TIDE252",
                f"record identity parameter {parameter_name!r} must be required",
                document,
                ("parameters", parameter_name, "required"),
            )

        assert report.bands is not None
        for band_name in (
            "report_header",
            "record_header",
            "report_footer",
            "page_footer",
        ):
            items = getattr(report.bands, band_name)
            for index, item in enumerate(items):
                item_path = ("bands", band_name, index)
                if item.field is not None:
                    report_field = entity.fields.get(item.field)
                    if report_field is None:
                        _add(
                            diagnostics,
                            "TIDE254",
                            f"unknown report field {item.field!r}",
                            document,
                            (*item_path, "field"),
                        )
                    elif report_field.type == "collection":
                        _add(
                            diagnostics,
                            "TIDE254",
                            "collection fields belong in the report detail band",
                            document,
                            (*item_path, "field"),
                        )
                if item.format is not None and item.format not in formats:
                    _add(
                        diagnostics,
                        "TIDE255",
                        f"unknown report format {item.format!r}",
                        document,
                        (*item_path, "format"),
                    )
                if item.expression is not None:
                    _validate_expression_at(
                        item.expression,
                        entity,
                        entities,
                        document,
                        (*item_path, "expression"),
                        diagnostics,
                        parameters=parameters,
                        globals_=(
                            {"page_number": "integer", "page_count": "integer"}
                            if band_name == "page_footer"
                            else {}
                        ),
                        expected_type=("string" if band_name == "page_footer" else None),
                    )

        detail = report.bands.detail
        source = entity.fields.get(detail.source)
        detail_path = ("bands", "detail")
        if source is None or source.type != "collection" or source.target not in entities:
            _add(
                diagnostics,
                "TIDE253",
                f"report detail source {detail.source!r} must be a collection field",
                document,
                (*detail_path, "source"),
            )
            continue
        target = entities[source.target]
        for index, column in enumerate(detail.columns):
            if column not in target.fields:
                _add(
                    diagnostics,
                    "TIDE254",
                    f"unknown report detail field {column!r}",
                    document,
                    (*detail_path, "columns", index),
                )


def _validate_summary_report(
    report: ReportSource,
    entity: EntitySource,
    formats: set[str],
    document: SourceDocument,
    diagnostics: list[Diagnostic],
) -> None:
    if report.query.criteria and not _summary_criteria_is_queryable(
        report.query.criteria
    ):
        _add(
            diagnostics,
            "TIDE257",
            "summary criteria must use direct field comparisons joined by 'and'",
            document,
            ("query", "criteria"),
        )
    for index, sort_name in enumerate(report.query.sort):
        field_name = sort_name.lstrip("+-")
        field = entity.fields.get(field_name)
        if field is None:
            _add(
                diagnostics,
                "TIDE254",
                f"unknown report sort field {field_name!r}",
                document,
                ("query", "sort", index),
            )
        elif field.type == "collection" or (
            field.computed and field.computed.materialization == "virtual"
        ):
            _add(
                diagnostics,
                "TIDE257",
                f"report sort field {field_name!r} is not queryable",
                document,
                ("query", "sort", index),
            )

    output_names: set[str] = set()
    for index, group in enumerate(report.group_by):
        field = entity.fields.get(group.field)
        path = ("group_by", index)
        if group.field in output_names:
            _add(
                diagnostics,
                "TIDE254",
                f"duplicate summary output {group.field!r}",
                document,
                (*path, "field"),
            )
        output_names.add(group.field)
        if field is None:
            _add(
                diagnostics,
                "TIDE254",
                f"unknown summary group field {group.field!r}",
                document,
                (*path, "field"),
            )
        elif field.type == "collection":
            _add(
                diagnostics,
                "TIDE254",
                "summary groups cannot use collection fields",
                document,
                (*path, "field"),
            )
        if group.format is not None and group.format not in formats:
            _add(
                diagnostics,
                "TIDE255",
                f"unknown report format {group.format!r}",
                document,
                (*path, "format"),
            )

    for index, column in enumerate(report.columns):
        path = ("columns", index)
        column_field = entity.fields.get(column)
        if column_field is None:
            _add(
                diagnostics,
                "TIDE254",
                f"unknown report field {column!r}",
                document,
                path,
            )
        elif column_field.type == "collection":
            _add(
                diagnostics,
                "TIDE255",
                "collection fields cannot be listing columns",
                document,
                path,
            )

    for index, aggregate in enumerate(report.aggregates):
        path = ("aggregates", index)
        if aggregate.name in output_names:
            _add(
                diagnostics,
                "TIDE254",
                f"duplicate summary output {aggregate.name!r}",
                document,
                (*path, "name"),
            )
        output_names.add(aggregate.name)
        if aggregate.function == "sum" and aggregate.field is not None:
            field = entity.fields.get(aggregate.field)
            if field is None:
                _add(
                    diagnostics,
                    "TIDE254",
                    f"unknown summary aggregate field {aggregate.field!r}",
                    document,
                    (*path, "field"),
                )
            elif field.type not in {"integer", "decimal"}:
                _add(
                    diagnostics,
                    "TIDE257",
                    "sum aggregates require an integer or decimal field",
                    document,
                    (*path, "field"),
                )
        if aggregate.format is not None and aggregate.format not in formats:
            _add(
                diagnostics,
                "TIDE255",
                f"unknown report format {aggregate.format!r}",
                document,
                (*path, "format"),
            )


def _summary_criteria_is_queryable(criteria: str) -> bool:
    rewritten = re.sub(
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
        r"__tide_parameter_\1",
        criteria,
    )
    try:
        expression = ast.parse(rewritten, mode="eval").body
    except SyntaxError:
        return False
    clauses = (
        tuple(expression.values)
        if isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.And)
        else (expression,)
    )
    for clause in clauses:
        if (
            not isinstance(clause, ast.Compare)
            or len(clause.ops) != 1
            or len(clause.comparators) != 1
            or not isinstance(clause.left, ast.Name)
            or not isinstance(
                clause.ops[0],
                (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE),
            )
        ):
            return False
        comparator = clause.comparators[0]
        if isinstance(comparator, ast.Name):
            if not (
                comparator.id.startswith("__tide_parameter_")
                or comparator.id in {"true", "false", "null"}
            ):
                return False
        else:
            try:
                ast.literal_eval(comparator)
            except (ValueError, TypeError):
                return False
    return True


def _record_report_parameter(criteria: str, primary_key: str) -> str | None:
    identifier = r"([A-Za-z_][A-Za-z0-9_]*)"
    field_first = re.fullmatch(
        rf"\s*{re.escape(primary_key)}\s*==\s*\${identifier}\s*",
        criteria,
    )
    if field_first is not None:
        return field_first.group(1)
    parameter_first = re.fullmatch(
        rf"\s*\${identifier}\s*==\s*{re.escape(primary_key)}\s*",
        criteria,
    )
    return parameter_first.group(1) if parameter_first is not None else None


def _validate_security(
    items: list[tuple[SecurityDocumentSource, SourceDocument]],
    entities: dict[str, EntitySource],
    entity_documents: dict[str, SourceDocument],
    reports: dict[str, ReportSource],
    report_documents: dict[str, SourceDocument],
    diagnostics: list[Diagnostic],
) -> tuple[
    set[str],
    dict[str, tuple[str, ...]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    permissions: set[str] = set()
    roles: dict[str, tuple[str, ...]] = {}
    role_documents: dict[str, SourceDocument] = {}
    row_policy_ids: set[str] = set()
    normalized_row_policies: list[dict[str, Any]] = []
    normalized_field_policies: list[dict[str, Any]] = []

    for security, document in items:
        for index, permission in enumerate(security.permissions):
            if not IDENTIFIER.fullmatch(permission):
                _add(
                    diagnostics,
                    "TIDE260",
                    "permission identifiers must be qualified dotted names",
                    document,
                    ("permissions", index),
                )
            if (
                permission.startswith(FRAMEWORK_PERMISSION_PREFIX)
                and permission not in FRAMEWORK_PERMISSIONS
            ):
                known = ", ".join(
                    repr(name) for name in sorted(FRAMEWORK_PERMISSIONS)
                )
                _add(
                    diagnostics,
                    "TIDE286",
                    f"unknown framework permission {permission!r}; "
                    f"{FRAMEWORK_PERMISSION_PREFIX!r} is reserved and names "
                    f"{known}",
                    document,
                    ("permissions", index),
                )
            if permission in permissions:
                _add(
                    diagnostics,
                    "TIDE261",
                    f"duplicate permission {permission!r}",
                    document,
                    ("permissions", index),
                )
            permissions.add(permission)

        for role_name, role in security.roles.items():
            if role_name in roles:
                _add(
                    diagnostics,
                    "TIDE262",
                    f"duplicate role {role_name!r}",
                    document,
                    ("roles", role_name),
                )
                continue
            roles[role_name] = tuple(role.grants)
            role_documents[role_name] = document

        for index, policy in enumerate(security.row_policies):
            normalized_row_policies.append(policy.model_dump(mode="json"))
            path = ("row_policies", index)
            if policy.id in row_policy_ids:
                _add(
                    diagnostics,
                    "TIDE263",
                    f"duplicate row policy {policy.id!r}",
                    document,
                    (*path, "id"),
                )
            row_policy_ids.add(policy.id)
            entity = entities.get(policy.entity)
            if entity is None:
                _add(
                    diagnostics,
                    "TIDE264",
                    f"unknown row-policy entity {policy.entity!r}",
                    document,
                    (*path, "entity"),
                )
            else:
                _validate_expression_at(
                    policy.criteria,
                    entity,
                    entities,
                    document,
                    (*path, "criteria"),
                    diagnostics,
                    # A row policy may name the caller; the runtime binds these
                    # as query parameters rather than expanding them into text.
                    parameters=dict(POLICY_PARAMETERS),
                    expected_type="boolean",
                )

        for index, policy in enumerate(security.field_policies):
            normalized_field_policies.append(policy.model_dump(mode="json", exclude_none=True))
            path = ("field_policies", index)
            entity = entities.get(policy.entity)
            if entity is None:
                _add(
                    diagnostics,
                    "TIDE265",
                    f"unknown field-policy entity {policy.entity!r}",
                    document,
                    (*path, "entity"),
                )
            elif policy.field not in entity.fields:
                _add(
                    diagnostics,
                    "TIDE266",
                    f"entity {policy.entity!r} has no field {policy.field!r}",
                    document,
                    (*path, "field"),
                )

    for role_name, grants in roles.items():
        document = role_documents[role_name]
        for index, permission in enumerate(grants):
            if permission not in permissions:
                _add(
                    diagnostics,
                    "TIDE267",
                    f"role grants unknown permission {permission!r}",
                    document,
                    ("roles", role_name, "grants", index),
                )

    for security, document in items:
        for index, policy in enumerate(security.field_policies):
            for property_name in ("read", "write"):
                permission = getattr(policy, property_name)
                if permission and permission not in permissions:
                    _add(
                        diagnostics,
                        "TIDE268",
                        f"field policy references unknown permission {permission!r}",
                        document,
                        ("field_policies", index, property_name),
                    )

    for entity_name, entity in entities.items():
        document = entity_documents[entity_name]
        for operation, permission in entity.permissions.model_dump(by_alias=True).items():
            if permission and permission not in permissions:
                _add(
                    diagnostics,
                    "TIDE269",
                    f"entity operation references unknown permission {permission!r}",
                    document,
                    ("permissions", operation),
                )
        for action_name, action in entity.actions.items():
            if action.permission and action.permission not in permissions:
                _add(
                    diagnostics,
                    "TIDE269",
                    f"action references unknown permission {action.permission!r}",
                    document,
                    ("actions", action_name, "permission"),
                )

    for report_name, report in reports.items():
        if report.permission and report.permission not in permissions:
            _add(
                diagnostics,
                "TIDE269",
                f"report references unknown permission {report.permission!r}",
                report_documents[report_name],
                ("permission",),
            )

    return (
        permissions,
        dict(sorted(roles.items())),
        normalized_row_policies,
        normalized_field_policies,
    )


def _validate_expression_at(
    expression: str,
    entity: EntitySource,
    entities: dict[str, EntitySource],
    document: SourceDocument,
    path: tuple[str | int, ...],
    diagnostics: list[Diagnostic],
    *,
    parameters: dict[str, str] | frozenset[str] = frozenset(),
    globals_: dict[str, str] | frozenset[str] = frozenset(),
    expected_type: str | None = None,
) -> ExpressionResult:
    result = validate_expression(
        expression,
        entity=entity,
        entities=entities,
        parameters=parameters,
        globals_=globals_,
        expected_type=expected_type,
    )
    for issue in result.issues:
        _add(diagnostics, issue.code, issue.message, document, path)
    return result


def _require_field(
    entity: EntitySource,
    field_name: str,
    document: SourceDocument,
    path: tuple[str | int, ...],
    diagnostics: list[Diagnostic],
) -> None:
    if field_name not in entity.fields:
        _add(
            diagnostics,
            "TIDE215",
            f"entity {entity.entity!r} has no field {field_name!r}",
            document,
            path,
        )


def _infer_view_entity(view_name: str, entities: dict[str, EntitySource]) -> str | None:
    matches = [name for name in entities if view_name.startswith(name + ".")]
    return max(matches, key=len) if matches else None


def _strings_in(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings_in(item)


def _find_key(
    value: Any, key: str, path: tuple[str | int, ...]
) -> Iterable[tuple[tuple[str | int, ...], str]]:
    if isinstance(value, dict):
        for child_key, child in value.items():
            child_path = (*path, child_key)
            if child_key == key and isinstance(child, str):
                yield child_path, child
            else:
                yield from _find_key(child, key, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _find_key(child, key, (*path, index))


def _validate_handler_reference(
    reference: str,
    project_root: Path,
    document: SourceDocument,
    path: tuple[str | int, ...],
    diagnostics: list[Diagnostic],
) -> None:
    """Resolve a project handler statically without importing application code."""

    module_name, _, function_name = reference.rpartition(".")
    if not module_name or not function_name:
        return
    module_path = project_root.joinpath(*module_name.split(".")).with_suffix(".py")
    if not module_path.is_file():
        package_path = project_root.joinpath(*module_name.split("."), "__init__.py")
        module_path = package_path if package_path.is_file() else module_path
    if not module_path.is_file():
        _add(
            diagnostics,
            "TIDE223",
            f"handler module {module_name!r} does not exist inside the project",
            document,
            path,
        )
        return
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    except (OSError, SyntaxError) as error:
        _add(
            diagnostics,
            "TIDE224",
            f"handler module cannot be parsed: {error}",
            document,
            path,
        )
        return
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if function_name not in functions:
        _add(
            diagnostics,
            "TIDE225",
            f"handler function {function_name!r} does not exist in {module_name!r}",
            document,
            path,
        )


def _add(
    diagnostics: list[Diagnostic],
    code: str,
    message: str,
    document: SourceDocument,
    path: tuple[str | int, ...],
    *,
    severity: Severity = Severity.ERROR,
) -> None:
    diagnostics.append(
        Diagnostic(
            code=code,
            message=message,
            location=document.location_for(path),
            severity=severity,
            path=path,
        )
    )
