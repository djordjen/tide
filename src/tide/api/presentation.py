"""Secured renderer-neutral presentation projection for remote UI clients."""

from __future__ import annotations

from datetime import date
from string import Formatter
from typing import Mapping

from tide.api.contracts import (
    TideBrowsePresentation,
    TideFilterInput,
    TideFormPresentation,
    TidePresentationColumn,
    TidePresentationFormCollection,
    TidePresentationFormField,
    TidePresentationFormGroup,
    TidePresentationFormat,
    TidePresentationManifest,
    TidePresentationNamedFilter,
    TidePresentationNavigationGroup,
    TidePresentationNavigationItem,
    TidePresentationLookup,
    TidePresentationReference,
    TideSessionInfo,
)
from tide.api.openapi import RestExposure
from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
from tide.presentation import (
    application_navigation,
    browse_columns,
    browse_named_filters,
    browse_search_field,
    browse_sortable_fields,
    field_alignment,
    field_label,
    form_layout_sections,
)


def build_presentation_manifest(
    model: ApplicationModel,
    session: TideSessionInfo,
    exposures: Mapping[str, RestExposure],
    *,
    base_path: str,
) -> TidePresentationManifest:
    """Build the safe presentation subset available to one principal."""

    accessible_columns: dict[str, tuple[str, ...]] = {}
    for view in model.views.values():
        if view.kind != "browse":
            continue
        exposure = exposures.get(view.entity)
        capabilities = session.entities.get(view.entity)
        if (
            exposure is None
            or capabilities is None
            or "list" not in exposure.operations
            or "list" not in capabilities.operations
        ):
            continue
        entity = model.entity(view.entity)
        readable = frozenset(capabilities.readable_fields)
        identity_field = _primary_key(entity)
        columns = tuple(
            name
            for name in browse_columns(view, entity)
            if name in readable
        )
        if identity_field in readable and columns:
            accessible_columns[view.name] = columns

    resolved_navigation = application_navigation(
        model,
        tuple(accessible_columns),
    )
    views: dict[str, TideBrowsePresentation] = {}
    forms: dict[str, TideFormPresentation] = {}
    navigation: list[TidePresentationNavigationGroup] = []
    for group in resolved_navigation:
        items: list[TidePresentationNavigationItem] = []
        for item in group.items:
            view = model.views[item.view]
            entity = model.entity(view.entity)
            exposure = exposures[entity.name]
            root_path = f"{base_path.rstrip('/')}/{exposure.path}"
            capabilities = session.entities[entity.name]
            field_names = accessible_columns[view.name]
            readable = frozenset(capabilities.readable_fields)
            form = _form_contract(
                model,
                entity,
                session,
                exposures,
                view=None,
                base_path=base_path,
            )
            if form is not None:
                forms[form.view] = form
            configured_search = browse_search_field(view, entity)
            search_field = (
                configured_search
                if configured_search in readable
                else None
            )
            filters = {
                name: named_filter
                for name, named_filter in browse_named_filters(view).items()
                if all(
                    condition.field in readable
                    for condition in named_filter.conditions
                )
            }
            views[view.name] = TideBrowsePresentation(
                view=view.name,
                entity=entity.name,
                label=item.label,
                resource_path=root_path,
                query_path=f"{root_path}/_query",
                identity_field=_primary_key(entity),
                columns=tuple(
                    _column_contract(
                        entity,
                        field_name,
                        model,
                        session,
                        exposures,
                        base_path=base_path,
                    )
                    for field_name in field_names
                ),
                search_field=search_field,
                search_label=(
                    field_label(entity.field(search_field))
                    if search_field is not None
                    else None
                ),
                named_filters=tuple(
                    TidePresentationNamedFilter(
                        name=named_filter.name,
                        label=named_filter.label,
                        conditions=tuple(
                            TideFilterInput(
                                field=condition.field,
                                operator=condition.operator,
                                value=condition.value,
                            )
                            for condition in named_filter.conditions
                        ),
                    )
                    for named_filter in filters.values()
                ),
                sortable_fields=browse_sortable_fields(
                    field_names,
                    entity,
                ),
                page_size=int(
                    view.data.get("settings", {}).get("page_size", 50)
                ),
                operations=capabilities.operations,
                detail_view=form.view if form is not None else None,
            )
            items.append(
                TidePresentationNavigationItem(
                    view=view.name,
                    entity=entity.name,
                    label=item.label,
                )
            )
        navigation.append(
            TidePresentationNavigationGroup(
                label=group.label,
                items=tuple(items),
            )
        )

    pending_forms = {
        lookup.create_view
        for form in forms.values()
        for lookup in _form_lookups(form)
        if lookup.create_view is not None
        and lookup.create_view not in forms
    }
    while pending_forms:
        view_name = pending_forms.pop()
        candidate = model.views.get(view_name)
        if candidate is None:
            continue
        form = _form_contract(
            model,
            model.entity(candidate.entity),
            session,
            exposures,
            view=candidate,
            base_path=base_path,
        )
        if form is None:
            continue
        forms[form.view] = form
        pending_forms.update(
            lookup.create_view
            for lookup in _form_lookups(form)
            if lookup.create_view is not None
            and lookup.create_view not in forms
        )
    return TidePresentationManifest(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        principal=session.principal,
        navigation=tuple(navigation),
        views=views,
        forms=forms,
    )


def _form_contract(
    model: ApplicationModel,
    entity: NormalizedEntity,
    session: TideSessionInfo,
    exposures: Mapping[str, RestExposure],
    *,
    view: ResolvedView | None,
    base_path: str,
) -> TideFormPresentation | None:
    capabilities = session.entities.get(entity.name)
    exposure = exposures.get(entity.name)
    if (
        capabilities is None
        or exposure is None
        or "get" not in capabilities.operations
        or "get" not in exposure.operations
    ):
        return None
    view = view or next(
        (
            candidate
            for candidate in model.views.values()
            if candidate.kind == "form" and candidate.entity == entity.name
        ),
        None,
    )
    if (
        view is None
        or view.kind != "form"
        or view.entity != entity.name
    ):
        return None

    readable = frozenset(capabilities.readable_fields)
    fields: dict[str, TidePresentationFormField] = {}
    sections: list[
        TidePresentationFormGroup | TidePresentationFormCollection
    ] = []
    for section in form_layout_sections(view, entity):
        if section.kind == "group":
            rows = tuple(
                tuple(
                    name
                    for name in row
                    if name in readable
                    and entity.field(name).metadata["type"] != "collection"
                )
                for row in section.rows
            )
            rows = tuple(row for row in rows if row)
            if not rows:
                continue
            for name in (name for row in rows for name in row):
                fields[name] = _form_field_contract(
                    entity,
                    name,
                    model,
                    session,
                    exposures,
                    view=view,
                    writable=name in capabilities.writable_fields,
                    base_path=base_path,
                )
            sections.append(
                TidePresentationFormGroup(
                    label=section.label,
                    rows=rows,
                    tab=section.tab,
                )
            )
            continue

        collection_name = section.collection
        if collection_name is None or collection_name not in readable:
            continue
        field = entity.field(collection_name)
        inline = (
            model.views.get(section.inline_view)
            if section.inline_view is not None
            else None
        )
        if (
            field.target_entity is None
            or inline is None
            or inline.kind != "inline_edit"
            or inline.entity != field.target_entity
        ):
            continue
        target = model.entity(field.target_entity)
        target_capabilities = session.entities.get(target.name)
        if target_capabilities is None:
            continue
        target_readable = frozenset(target_capabilities.readable_fields)
        column_names = tuple(
            name
            for name in browse_columns(inline, target)
            if name in target_readable
        )
        if not column_names:
            continue
        inverse = field.metadata.get("inverse")
        target_writable = frozenset(target_capabilities.writable_fields)
        editor_names = frozenset(
            name
            for name in target_readable & target_writable
            if name in target.fields
            and name != inverse
            and not target.field(name).metadata.get("primary_key")
            and not target.field(name).metadata.get("computed")
            and not target.field(name).metadata.get("readonly")
            and target.field(name).metadata.get("write", "normal") == "normal"
            and target.field(name).metadata["type"] != "collection"
        )
        editor_fields: dict[str, TidePresentationFormField] = {}
        editor_groups: list[TidePresentationFormGroup] = []
        for editor_section in form_layout_sections(inline, target):
            if editor_section.kind != "group":
                continue
            rows = tuple(
                tuple(name for name in row if name in editor_names)
                for row in editor_section.rows
            )
            rows = tuple(row for row in rows if row)
            if not rows:
                continue
            for name in (name for row in rows for name in row):
                editor_fields[name] = _form_field_contract(
                    target,
                    name,
                    model,
                    session,
                    exposures,
                    view=inline,
                    writable=True,
                    base_path=base_path,
                )
            editor_groups.append(
                TidePresentationFormGroup(
                    label=editor_section.label,
                    rows=rows,
                    tab=editor_section.tab,
                )
            )
        if not editor_groups:
            fallback = tuple(
                name for name in column_names if name in editor_names
            )
            if fallback:
                for name in fallback:
                    editor_fields[name] = _form_field_contract(
                        target,
                        name,
                        model,
                        session,
                        exposures,
                        view=inline,
                        writable=True,
                        base_path=base_path,
                    )
                editor_groups.append(
                    TidePresentationFormGroup(
                        label=target.label,
                        rows=tuple((name,) for name in fallback),
                    )
                )
        draft_operations = tuple(
            operation
            for operation in target_capabilities.draft_operations
            if operation in field.metadata.get("cascade", ())
        )
        writable = bool(
            collection_name in capabilities.writable_fields
            and _primary_key(target) in target_readable
            and editor_fields
            and draft_operations
        )
        actions = tuple(
            action
            for action in section.actions
            if action in {"add", "apply", "remove"}
        ) or ("add", "apply", "remove")
        sections.append(
            TidePresentationFormCollection(
                name=collection_name,
                label=field_label(field),
                entity=target.name,
                view=inline.name,
                identity_field=(
                    _primary_key(target)
                    if _primary_key(target) in target_readable
                    else None
                ),
                columns=tuple(
                    _column_contract(
                        target,
                        name,
                        model,
                        session,
                        exposures,
                        base_path=base_path,
                    )
                    for name in column_names
                ),
                fields=editor_fields if writable else {},
                groups=tuple(editor_groups) if writable else (),
                actions=actions if writable else (),
                draft_operations=draft_operations if writable else (),
                writable=writable,
                tab=section.tab,
            )
        )

    if not sections or not fields:
        return None
    return TideFormPresentation(
        view=view.name,
        entity=entity.name,
        label=entity.label.removesuffix("s") or entity.label,
        display_template=_safe_display_template(entity, readable),
        fields=fields,
        sections=tuple(sections),
    )


def _form_lookups(
    form: TideFormPresentation,
) -> tuple[TidePresentationLookup, ...]:
    return tuple(
        field.lookup
        for field in (
            *form.fields.values(),
            *(
                field
                for section in form.sections
                if isinstance(section, TidePresentationFormCollection)
                for field in section.fields.values()
            ),
        )
        if field.lookup is not None
    )


def _form_field_contract(
    entity: NormalizedEntity,
    field_name: str,
    model: ApplicationModel,
    session: TideSessionInfo,
    exposures: Mapping[str, RestExposure],
    *,
    view: ResolvedView,
    writable: bool,
    base_path: str,
) -> TidePresentationFormField:
    field = entity.field(field_name)
    metadata = field.metadata
    column = _column_contract(
        entity,
        field_name,
        model,
        session,
        exposures,
        base_path=base_path,
    )
    edit_mask = metadata.get("edit_mask")
    validation = metadata.get("validation")
    validations = (
        (str(validation),)
        if isinstance(validation, str)
        else tuple(str(item) for item in (validation or ()))
    )
    has_default = (
        "default" in metadata or metadata.get("default_factory") == "today"
    )
    default_value = (
        date.today()
        if metadata.get("default_factory") == "today"
        else metadata.get("default")
    )
    return TidePresentationFormField(
        **column.model_dump(),
        writable=writable,
        lookup=_lookup_contract(
            entity,
            field,
            view,
            model,
            session,
            exposures,
            writable=writable,
            base_path=base_path,
        ),
        required=bool(metadata.get("required")),
        help=str(metadata["help"]) if metadata.get("help") else None,
        max_length=(
            int(metadata["length"])
            if metadata.get("length") is not None
            else None
        ),
        choices=tuple(str(item) for item in metadata.get("choices", ())),
        regex=(
            str(edit_mask["regex"])
            if isinstance(edit_mask, Mapping)
            and edit_mask.get("regex") is not None
            else None
        ),
        numeric_mask=edit_mask if isinstance(edit_mask, str) else None,
        precision=(
            int(metadata["precision"])
            if metadata.get("precision") is not None
            else None
        ),
        scale=(
            int(metadata["scale"])
            if metadata.get("scale") is not None
            else None
        ),
        minimum=metadata.get("minimum"),
        maximum=metadata.get("maximum"),
        validations=validations,
        has_default=has_default,
        default_value=default_value if has_default else None,
    )


def _lookup_contract(
    owner: NormalizedEntity,
    field: NormalizedField,
    form_view: ResolvedView,
    model: ApplicationModel,
    session: TideSessionInfo,
    exposures: Mapping[str, RestExposure],
    *,
    writable: bool,
    base_path: str,
) -> TidePresentationLookup | None:
    if (
        not writable
        or field.metadata["type"] != "reference"
        or field.target_entity is None
    ):
        return None
    raw_fields = form_view.data.get("fields", {})
    configuration = (
        raw_fields.get(field.name, {})
        if isinstance(raw_fields, Mapping)
        else {}
    )
    if not isinstance(configuration, Mapping):
        return None
    if configuration.get("editor") != "lookup":
        return None
    lookup_name = configuration.get(
        "lookup_view",
        field.metadata.get("lookup_view"),
    )
    lookup = model.views.get(str(lookup_name)) if lookup_name else None
    if (
        lookup is None
        or lookup.kind != "lookup"
        or lookup.entity != field.target_entity
    ):
        return None

    target = model.entity(field.target_entity)
    capabilities = session.entities.get(target.name)
    exposure = exposures.get(target.name)
    if (
        capabilities is None
        or exposure is None
        or "list" not in capabilities.operations
        or "get" not in capabilities.operations
        or "list" not in exposure.operations
        or "get" not in exposure.operations
        or _primary_key(target) not in capabilities.readable_fields
    ):
        return None
    readable = frozenset(capabilities.readable_fields)
    column_names = tuple(
        name
        for name in browse_columns(lookup, target)
        if name in readable
    )
    configured_search = tuple(lookup.data.get("search", ()))
    search_candidates = (
        configured_search
        or tuple(target.metadata.get("search_fields", ()))
    )
    search_fields = tuple(
        name
        for name in search_candidates
        if isinstance(name, str)
        and name in readable
        and name in target.fields
        and target.field(name).metadata["type"] in {"string", "choice"}
        and not target.field(name).metadata.get("computed")
    )
    if not column_names or not search_fields:
        return None

    root_path = f"{base_path.rstrip('/')}/{exposure.path}"
    operations = tuple(
        operation
        for operation in capabilities.operations
        if operation in exposure.operations
    )
    create_name = configuration.get("create_view")
    create_view = (
        model.views.get(str(create_name))
        if create_name is not None
        else None
    )
    create_form_has_writable_fields = bool(
        create_view is not None
        and create_view.kind == "form"
        and create_view.entity == target.name
        and any(
            name in readable
            and name in capabilities.writable_fields
            and target.field(name).metadata["type"] != "collection"
            for section in form_layout_sections(create_view, target)
            if section.kind == "group"
            for row in section.rows
            for name in row
        )
    )
    create_available = bool(
        configuration.get("allow_create") is True
        and create_view is not None
        and create_view.kind == "form"
        and create_view.entity == target.name
        and "create" in operations
        and create_form_has_writable_fields
    )
    return TidePresentationLookup(
        view=lookup.name,
        title=f"Select {target.label.removesuffix('s') or target.label}",
        owner_entity=owner.name,
        field=field.name,
        target_entity=target.name,
        resource_path=root_path,
        query_path=f"{root_path}/_query",
        selection_path=f"{base_path.rstrip('/')}/_tide/reference-selection",
        identity_field=_primary_key(target),
        columns=tuple(
            _column_contract(
                target,
                name,
                model,
                session,
                exposures,
                base_path=base_path,
            )
            for name in column_names
        ),
        search_fields=search_fields,
        page_size=max(
            1,
            min(
                500,
                int(lookup.data.get("settings", {}).get("page_size", 20)),
            ),
        ),
        operations=operations,
        create_view=create_view.name if create_available else None,
    )


def _column_contract(
    entity: NormalizedEntity,
    field_name: str,
    model: ApplicationModel,
    session: TideSessionInfo,
    exposures: Mapping[str, RestExposure],
    *,
    base_path: str,
) -> TidePresentationColumn:
    field = entity.field(field_name)
    configured_format = field.metadata.get("format")
    format_options = (
        model.formats.get(str(configured_format), {})
        if configured_format is not None
        else {}
    )
    return TidePresentationColumn(
        name=field.name,
        label=field_label(field),
        field_type=str(field.metadata["type"]),
        alignment=field_alignment(field, model.formats),
        format=(
            str(configured_format)
            if configured_format is not None
            else None
        ),
        format_options=(
            TidePresentationFormat(
                decimal_places=format_options.get("decimal_places"),
                thousands_separator=bool(
                    format_options.get("thousands_separator", False)
                ),
                display=(
                    str(format_options["display"])
                    if format_options.get("display") is not None
                    else None
                ),
            )
            if format_options
            else None
        ),
        target_entity=field.target_entity,
        reference=_reference_contract(
            field,
            model,
            session,
            exposures,
            base_path=base_path,
        ),
    )


def _reference_contract(
    field: NormalizedField,
    model: ApplicationModel,
    session: TideSessionInfo,
    exposures: Mapping[str, RestExposure],
    *,
    base_path: str,
) -> TidePresentationReference | None:
    if field.metadata["type"] != "reference" or field.target_entity is None:
        return None
    target = model.entity(field.target_entity)
    exposure = exposures.get(target.name)
    capabilities = session.entities.get(target.name)
    if (
        exposure is None
        or capabilities is None
        or "get" not in exposure.operations
        or "get" not in capabilities.operations
    ):
        return None
    display_template = _safe_display_template(
        target,
        frozenset(capabilities.readable_fields),
    )
    if display_template is None:
        return None
    return TidePresentationReference(
        entity=target.name,
        resource_path=f"{base_path.rstrip('/')}/{exposure.path}",
        identity_field=_primary_key(target),
        display_template=display_template,
    )


def _safe_display_template(
    entity: NormalizedEntity,
    readable_fields: frozenset[str],
) -> str | None:
    if entity.display is None:
        return None
    try:
        display_fields = tuple(
            field_name
            for _, field_name, _, _ in Formatter().parse(entity.display)
            if field_name
        )
    except ValueError:
        return None
    if not display_fields and "{" not in entity.display:
        display_fields = (entity.display,)
    if not display_fields or not all(
        field_name in readable_fields for field_name in display_fields
    ):
        return None
    return entity.display


def _primary_key(entity: NormalizedEntity) -> str:
    return next(
        name
        for name, field in entity.fields.items()
        if field.metadata.get("primary_key")
    )
