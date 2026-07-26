"""Secured renderer-neutral presentation projection for remote UI clients."""

from __future__ import annotations

from string import Formatter
from typing import Mapping

from tide.api.contracts import (
    TideBrowsePresentation,
    TideFilterInput,
    TidePresentationColumn,
    TidePresentationFormat,
    TidePresentationManifest,
    TidePresentationNamedFilter,
    TidePresentationNavigationGroup,
    TidePresentationNavigationItem,
    TidePresentationReference,
    TideSessionInfo,
)
from tide.api.openapi import RestExposure
from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
)
from tide.presentation import (
    application_navigation,
    browse_columns,
    browse_named_filters,
    browse_search_field,
    browse_sortable_fields,
    field_alignment,
    field_label,
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
    return TidePresentationManifest(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        principal=session.principal,
        navigation=tuple(navigation),
        views=views,
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
