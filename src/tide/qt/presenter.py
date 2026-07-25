"""Qt-neutral browse/detail presentation over the secured HTTP client."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
from threading import Lock
from typing import Any, Literal, Mapping, Protocol

from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
from tide.compiler.expressions import evaluate_expression
from tide.data import FilterCondition, QuerySpec, SortField
from tide.presentation import (
    BrowseNamedFilter,
    browse_named_filters,
    browse_search_field,
    browse_sortable_fields,
)
from tide.runtime import TideRuntimeError
from tide.security import PROTECTED

Alignment = Literal["left", "center", "right"]


class BrowseApiClient(Protocol):
    """Small typed-client surface consumed by the initial Qt presenter."""

    def query_records(
        self,
        entity_name: str,
        query: QuerySpec,
    ) -> Any: ...

    def get_record(self, entity_name: str, identity: Any) -> Any: ...

    def create_record(
        self,
        entity_name: str,
        values: Mapping[str, Any],
    ) -> Any: ...

    def update_record(
        self,
        entity_name: str,
        identity: Any,
        values: Mapping[str, Any],
        *,
        if_match: str | int | None = None,
    ) -> Any: ...

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: Mapping[str, Any],
        identity: Any,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class QtBrowseColumn:
    name: str
    label: str
    alignment: Alignment = "left"


@dataclass(frozen=True, slots=True)
class QtBrowseBatch:
    columns: tuple[QtBrowseColumn, ...]
    rows: tuple[tuple[str, ...], ...]
    identities: tuple[Any, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class QtBrowseQuery:
    """Renderer query state that starts one server-owned cursor sequence."""

    search_text: str = ""
    filter_name: str | None = None
    sort_field: str | None = None
    sort_descending: bool = False


@dataclass(frozen=True, slots=True)
class QtEditField:
    """One form field and the metadata needed by a Qt editor."""

    name: str
    label: str
    field_type: str
    value: Any
    editable: bool
    required: bool = False
    max_length: int | None = None
    choices: tuple[Any, ...] = ()
    regex: str | None = None
    numeric_mask: str | None = None
    precision: int | None = None
    scale: int | None = None
    minimum: int | Decimal | None = None
    maximum: int | Decimal | None = None
    target_entity: str | None = None
    reference_display: str = ""
    lookup_view: str | None = None


@dataclass(frozen=True, slots=True)
class QtEditGroup:
    label: str
    rows: tuple[tuple[QtEditField, ...], ...]


@dataclass(frozen=True, slots=True)
class QtEditForm:
    """One create/update draft opened through an authenticated API capability."""

    entity: str
    title: str
    operation: Literal["create", "update"]
    identity: Any
    etag: str | None
    original: Mapping[str, Any]
    groups: tuple[QtEditGroup, ...]
    omitted_collections: tuple[str, ...] = ()

    @property
    def fields(self) -> tuple[QtEditField, ...]:
        return tuple(
            field
            for group in self.groups
            for row in group.rows
            for field in row
        )


@dataclass(frozen=True, slots=True)
class QtLookupSpec:
    """Resolved secured lookup metadata for one reference editor."""

    owner_entity: str
    field_name: str
    title: str
    target_entity: str
    columns: tuple[QtBrowseColumn, ...]
    search_fields: tuple[str, ...]
    limit: int
    create_view: str | None = None

    @property
    def create_available(self) -> bool:
        return self.create_view is not None


@dataclass(frozen=True, slots=True)
class QtLookupRecord:
    identity: Any
    display: str
    cells: tuple[str, ...]
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QtLookupSelection:
    """Server-applied reference choice and any declarative draft assignments."""

    field_name: str
    identity: Any
    display: str
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QtDetailField:
    name: str
    label: str
    value: str
    alignment: Alignment = "left"


@dataclass(frozen=True, slots=True)
class QtDetailGroup:
    label: str
    rows: tuple[tuple[QtDetailField, ...], ...]


@dataclass(frozen=True, slots=True)
class QtDetailCollection:
    name: str
    label: str
    columns: tuple[QtBrowseColumn, ...]
    rows: tuple[tuple[str, ...], ...]
    protected: bool = False


QtDetailSection = QtDetailGroup | QtDetailCollection


@dataclass(frozen=True, slots=True)
class QtDetailRecord:
    identity: Any
    title: str
    sections: tuple[QtDetailSection, ...]


class QtBrowseController:
    """Build metadata-driven Qt contracts without importing PySide6."""

    def __init__(
        self,
        model: ApplicationModel,
        client: BrowseApiClient,
        session: TideSessionInfo,
        *,
        view_name: str | None = None,
        form_view_name: str | None = None,
        page_size: int | None = None,
    ) -> None:
        self.model = model
        self.client = client
        self.session = session
        self.view = _select_browse_view(model, session, view_name)
        self.entity = model.entity(self.view.entity)
        self.detail_view = _select_form_view(
            model,
            session,
            self.entity.name,
            form_view_name,
        )
        self.field_names = _browse_columns(self.view, self.entity)
        self.search_field = browse_search_field(self.view, self.entity)
        self.named_filters: dict[str, BrowseNamedFilter] = browse_named_filters(
            self.view
        )
        self.sortable_fields = browse_sortable_fields(
            self.field_names,
            self.entity,
        )
        configured_page_size = int(
            self.view.data.get("settings", {}).get("page_size", 25)
        )
        self.batch_size = configured_page_size if page_size is None else page_size
        if self.batch_size < 1 or self.batch_size > 500:
            raise ValueError("Qt browse batch size must be between 1 and 500")
        self.columns = tuple(
            QtBrowseColumn(
                field.name,
                _field_label(field),
                _field_alignment(field, model.formats),
            )
            for field in (self.entity.field(name) for name in self.field_names)
        )
        self._reference_cache: dict[tuple[str, Any], str] = {}
        self._reference_cache_lock = Lock()

    @property
    def title(self) -> str:
        return self.entity.label

    @property
    def context_text(self) -> str:
        roles = ", ".join(sorted(self.session.roles)) or "no role"
        return f"{self.view.name}  ·  {self.session.principal}  ·  {roles}"

    @property
    def detail_available(self) -> bool:
        return bool(
            self.detail_view is not None
            and "get" in self._entity_capabilities.operations
        )

    @property
    def create_available(self) -> bool:
        return bool(
            self._supported_form_available
            and "create" in self._entity_capabilities.operations
            and self._has_writable_form_field
        )

    @property
    def update_available(self) -> bool:
        return bool(
            self._supported_form_available
            and "get" in self._entity_capabilities.operations
            and "update" in self._entity_capabilities.operations
            and self._has_writable_form_field
        )

    @property
    def search_label(self) -> str | None:
        if self.search_field is None:
            return None
        return _field_label(self.entity.field(self.search_field))

    @property
    def _entity_capabilities(self) -> TideEntityCapabilities | _EmptyCapabilities:
        return self.session.entities.get(self.entity.name, _EMPTY_CAPABILITIES)

    @property
    def _supported_form_available(self) -> bool:
        if self.detail_view is None:
            return False
        field_names = _form_field_names(self.detail_view, self.entity)
        if not field_names:
            return False
        scalar_types = {
            "boolean",
            "choice",
            "date",
            "datetime",
            "decimal",
            "integer",
            "string",
        }
        for name in field_names:
            field_type = self.entity.field(name).metadata["type"]
            if field_type in scalar_types:
                continue
            if field_type != "reference":
                return False
            try:
                self.lookup_spec(name)
            except ValueError:
                return False
        return True

    @property
    def _has_writable_form_field(self) -> bool:
        if self.detail_view is None:
            return False
        return any(
            name in self._entity_capabilities.writable_fields
            for name in _form_field_names(self.detail_view, self.entity)
        )

    def reset_browse(self) -> None:
        """Discard browse-only display caches before a fresh server query."""

        with self._reference_cache_lock:
            self._reference_cache.clear()

    def query_spec(
        self,
        query: QtBrowseQuery,
        cursor: str | None = None,
    ) -> QuerySpec:
        """Validate renderer state and build one structured server query."""

        filters: list[FilterCondition] = []
        if query.search_text:
            if self.search_field is None:
                raise ValueError("Qt browse search is not configured")
            filters.append(
                FilterCondition(self.search_field, "contains", query.search_text)
            )
        if query.filter_name is not None:
            named_filter = self.named_filters.get(query.filter_name)
            if named_filter is None:
                raise ValueError(
                    f"Qt browse filter {query.filter_name!r} is not configured"
                )
            filters.extend(named_filter.conditions)
        if (
            query.sort_field is not None
            and query.sort_field not in self.sortable_fields
        ):
            raise ValueError(
                f"Qt browse field {query.sort_field!r} is not sortable"
            )
        return QuerySpec(
            filters=tuple(filters),
            sort=(
                (
                    SortField(
                        query.sort_field,
                        descending=query.sort_descending,
                    ),
                )
                if query.sort_field is not None
                else ()
            ),
            limit=self.batch_size,
            cursor=cursor,
        )

    def query_summary(self, query: QtBrowseQuery) -> str:
        """Return a compact human-readable summary for the browse status."""

        parts: list[str] = []
        if query.search_text:
            parts.append(f"search {query.search_text!r}")
        if query.filter_name is not None:
            named_filter = self.named_filters.get(query.filter_name)
            if named_filter is not None:
                parts.append(named_filter.label)
        if query.sort_field is not None:
            direction = "descending" if query.sort_descending else "ascending"
            parts.append(
                f"{_field_label(self.entity.field(query.sort_field))} {direction}"
            )
        return "  ·  ".join(parts)

    def fetch_batch(
        self,
        cursor: str | None = None,
        *,
        query: QtBrowseQuery | None = None,
    ) -> QtBrowseBatch:
        """Fetch and format one opaque server-owned continuation batch."""

        remote = self.client.query_records(
            self.entity.name,
            self.query_spec(query or QtBrowseQuery(), cursor),
        )
        rows = tuple(
            tuple(
                self._format_value(self.entity.field(name), record.get(name))
                for name in self.field_names
            )
            for record in remote.records
        )
        return QtBrowseBatch(
            columns=self.columns,
            rows=rows,
            identities=tuple(
                record.get(_primary_key_name(self.entity))
                for record in remote.records
            ),
            next_cursor=remote.next_cursor,
        )

    def load_detail(self, identity: Any) -> QtDetailRecord:
        if not self.detail_available:
            raise ValueError(
                f"{self.entity.name} does not define an accessible form view"
            )
        assert self.detail_view is not None
        if identity is None or identity is PROTECTED:
            raise ValueError("Qt detail record identity is unavailable")
        values = self.client.get_record(self.entity.name, identity).values
        display = _display_record(self.entity, values)
        return QtDetailRecord(
            identity=identity,
            title=f"{self.entity.label} — {display}",
            sections=self._detail_sections(values),
        )

    def new_form(self) -> QtEditForm:
        """Open a metadata-defaulted create form without touching storage."""

        if not self.create_available:
            raise ValueError(
                f"{self.entity.name} does not define an accessible create form"
            )
        defaults: dict[str, Any] = {}
        for field_name, field in self.entity.fields.items():
            metadata = field.metadata
            if metadata.get("default_factory") == "today":
                defaults[field_name] = date.today()
            elif "default" in metadata:
                defaults[field_name] = deepcopy(metadata["default"])
        return self._edit_form(
            operation="create",
            identity=None,
            etag=None,
            values=defaults,
        )

    def edit_form(self, identity: Any) -> QtEditForm:
        """Load one editable record through the authenticated API."""

        if not self.update_available:
            raise ValueError(
                f"{self.entity.name} does not define an accessible update form"
            )
        if identity is None or identity is PROTECTED:
            raise ValueError("Qt edit record identity is unavailable")
        remote = self.client.get_record(self.entity.name, identity)
        return self._edit_form(
            operation="update",
            identity=identity,
            etag=remote.etag,
            values=remote.values,
        )

    def save_form(
        self,
        form: QtEditForm,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Commit one supported draft through typed create/update API methods."""

        if form.entity != self.entity.name:
            raise ValueError("Qt edit form belongs to a different entity")
        available = (
            self.create_available
            if form.operation == "create"
            else self.update_available
        )
        if not available:
            raise ValueError(
                f"{self.entity.name} {form.operation} is not available"
            )
        editable = {field.name for field in form.fields if field.editable}
        unknown = set(values) - editable
        if unknown:
            raise ValueError(
                "Qt edit form contains non-writable field(s): "
                + ", ".join(sorted(unknown))
            )
        draft = {
            field.name: deepcopy(values.get(field.name, field.value))
            for field in form.fields
            if field.editable and field.value is not PROTECTED
        }
        if form.operation == "create":
            return self.client.create_record(self.entity.name, draft).values
        changes = {
            name: value
            for name, value in draft.items()
            if form.original.get(name) != value
        }
        if not changes:
            return deepcopy(dict(form.original))
        return self.client.update_record(
            self.entity.name,
            form.identity,
            changes,
            if_match=form.etag,
        ).values

    def lookup_spec(self, field_name: str) -> QtLookupSpec:
        """Resolve one reference lookup without granting any server access."""

        if self.detail_view is None or field_name not in self.entity.fields:
            raise ValueError(f"Qt lookup field {field_name!r} is not available")
        field = self.entity.field(field_name)
        if field.metadata["type"] != "reference" or not field.target_entity:
            raise ValueError(f"field {field_name!r} is not a reference")
        configuration = _view_field_configuration(self.detail_view, field_name)
        if configuration.get("editor") != "lookup":
            raise ValueError(f"field {field_name!r} does not use a lookup editor")
        lookup_name = configuration.get(
            "lookup_view",
            field.metadata.get("lookup_view"),
        )
        lookup = self.model.views.get(str(lookup_name)) if lookup_name else None
        if (
            lookup is None
            or lookup.kind != "lookup"
            or lookup.entity != field.target_entity
        ):
            raise ValueError(f"field {field_name!r} has no valid lookup view")
        target = self.model.entity(field.target_entity)
        capabilities = self.session.entities.get(
            target.name,
            _EMPTY_CAPABILITIES,
        )
        if "list" not in capabilities.operations:
            raise ValueError(
                f"{target.name} lookup is not accessible to this principal"
            )
        readable = set(capabilities.readable_fields)
        field_names = tuple(
            name
            for name in _lookup_columns(lookup, target)
            if name in readable
        )
        if not field_names:
            raise ValueError(f"lookup view {lookup.name!r} has no readable columns")
        search_fields = tuple(
            name
            for name in _lookup_search_fields(lookup, target)
            if name in readable
        )
        if not search_fields:
            raise ValueError(
                f"lookup view {lookup.name!r} has no readable search fields"
            )
        create_name = configuration.get("create_view")
        create_view = (
            self.model.views.get(str(create_name)) if create_name else None
        )
        create_available = bool(
            configuration.get("allow_create") is True
            and create_view is not None
            and create_view.kind == "form"
            and create_view.entity == target.name
            and "create" in capabilities.operations
            and capabilities.writable_fields
        )
        return QtLookupSpec(
            owner_entity=self.entity.name,
            field_name=field_name,
            title=f"Select {target.label.removesuffix('s') or target.label}",
            target_entity=target.name,
            columns=tuple(
                QtBrowseColumn(
                    name,
                    _field_label(target.field(name)),
                    _field_alignment(target.field(name), self.model.formats),
                )
                for name in field_names
            ),
            search_fields=search_fields,
            limit=max(
                1,
                min(
                    500,
                    int(lookup.data.get("settings", {}).get("page_size", 20)),
                ),
            ),
            create_view=create_view.name if create_available else None,
        )

    def search_lookup(
        self,
        spec: QtLookupSpec,
        search_text: str,
    ) -> tuple[QtLookupRecord, ...]:
        """Return bounded secured matches using the ordinary query API."""

        if spec.owner_entity != self.entity.name:
            raise ValueError("Qt lookup belongs to a different entity")
        target = self.model.entity(spec.target_entity)
        key_name = _primary_key_name(target)
        candidate = search_text.strip()
        sort = (SortField(spec.search_fields[0]),)
        records: dict[Any, Mapping[str, Any]] = {}
        queries = (
            (
                QuerySpec(sort=sort, limit=spec.limit),
            )
            if not candidate
            else tuple(
                QuerySpec(
                    filters=(
                        FilterCondition(field_name, "icontains", candidate),
                    ),
                    sort=sort,
                    limit=spec.limit,
                )
                for field_name in spec.search_fields
            )
        )
        for query in queries:
            page = self.client.query_records(target.name, query)
            for record in page.records:
                identity = record.get(key_name)
                if identity is None or identity is PROTECTED:
                    continue
                records.setdefault(identity, record)
                if len(records) >= spec.limit:
                    break
            if len(records) >= spec.limit:
                break
        return tuple(
            self.lookup_record(spec, record)
            for record in records.values()
        )

    def lookup_record(
        self,
        spec: QtLookupSpec,
        values: Mapping[str, Any],
    ) -> QtLookupRecord:
        """Format one selected or newly created target record consistently."""

        if spec.owner_entity != self.entity.name:
            raise ValueError("Qt lookup belongs to a different entity")
        target = self.model.entity(spec.target_entity)
        identity = values.get(_primary_key_name(target))
        if identity is None or identity is PROTECTED:
            raise ValueError("Qt lookup record identity is unavailable")
        return QtLookupRecord(
            identity=identity,
            display=_display_record(target, values),
            cells=tuple(
                self._format_value(target.field(column.name), values.get(column.name))
                for column in spec.columns
            ),
            values=deepcopy(dict(values)),
        )

    def apply_lookup_selection(
        self,
        form: QtEditForm,
        field_name: str,
        values: Mapping[str, Any],
        record: QtLookupRecord,
    ) -> QtLookupSelection:
        """Apply a selected identity through the server-owned draft operation."""

        if form.entity != self.entity.name:
            raise ValueError("Qt edit form belongs to a different entity")
        spec = self.lookup_spec(field_name)
        if spec.target_entity != self.entity.field(field_name).target_entity:
            raise ValueError("Qt lookup target does not match the reference")
        updated = self.client.apply_reference_selection(
            self.entity.name,
            field_name,
            values,
            record.identity,
        )
        return QtLookupSelection(
            field_name=field_name,
            identity=record.identity,
            display=record.display,
            values=deepcopy(dict(updated)),
        )

    def related_create_controller(
        self,
        spec: QtLookupSpec,
    ) -> QtBrowseController:
        """Create a target controller pinned to the compiler-approved create form."""

        if not spec.create_available:
            raise ValueError("Qt lookup does not permit related record creation")
        browse = next(
            (
                view
                for view in self.model.views.values()
                if view.kind == "browse"
                and view.entity == spec.target_entity
                and "list"
                in self.session.entities.get(
                    view.entity,
                    _EMPTY_CAPABILITIES,
                ).operations
            ),
            None,
        )
        if browse is None:
            raise ValueError(
                f"{spec.target_entity} has no accessible browse view"
            )
        return QtBrowseController(
            self.model,
            self.client,
            self.session,
            view_name=browse.name,
            form_view_name=spec.create_view,
        )

    def _edit_form(
        self,
        *,
        operation: Literal["create", "update"],
        identity: Any,
        etag: str | None,
        values: Mapping[str, Any],
    ) -> QtEditForm:
        assert self.detail_view is not None
        groups: list[QtEditGroup] = []
        omitted_collections: list[str] = []
        for configuration in self.detail_view.data.get("layout", ()):
            collection_name = configuration.get("collection")
            if collection_name:
                omitted_collections.append(str(collection_name))
                continue
            label = configuration.get("group")
            if not label:
                continue
            rows = tuple(
                tuple(
                    self._edit_field(
                        self.entity.field(str(name)),
                        values,
                        _view_field_configuration(
                            self.detail_view,
                            str(name),
                        ),
                    )
                    for name in row
                    if str(name) in self.entity.fields
                    and self.entity.field(str(name)).metadata["type"]
                    != "collection"
                    and not _field_is_hidden(self.detail_view, str(name))
                )
                for row in configuration.get("rows", ())
            )
            visible_rows = tuple(row for row in rows if row)
            if visible_rows:
                groups.append(QtEditGroup(str(label), visible_rows))
        if not groups:
            rows = tuple(
                (self._edit_field(self.entity.field(name), values),)
                for name in _form_field_names(self.detail_view, self.entity)
            )
            groups.append(QtEditGroup(self.entity.label, rows))
        singular = self.entity.label.removesuffix("s") or self.entity.label
        return QtEditForm(
            entity=self.entity.name,
            title=(
                f"New {singular}"
                if operation == "create"
                else f"Edit {singular} — {_display_record(self.entity, values)}"
            ),
            operation=operation,
            identity=identity,
            etag=etag,
            original=deepcopy(dict(values)),
            groups=tuple(groups),
            omitted_collections=tuple(omitted_collections),
        )

    def _edit_field(
        self,
        field: NormalizedField,
        values: Mapping[str, Any],
        configuration: Mapping[str, Any] | None = None,
    ) -> QtEditField:
        metadata = field.metadata
        configuration = configuration or {}
        edit_mask = metadata.get("edit_mask")
        value = values.get(field.name)
        immutable_when = metadata.get("immutable_when")
        editable = bool(
            field.name in self._entity_capabilities.writable_fields
            and value is not PROTECTED
            and not metadata.get("primary_key")
            and not metadata.get("computed")
            and not metadata.get("readonly")
            and metadata.get("write", "normal") == "normal"
            and not (
                immutable_when
                and bool(evaluate_expression(str(immutable_when), values))
            )
        )
        return QtEditField(
            name=field.name,
            label=_field_label(field),
            field_type=str(metadata["type"]),
            value=value,
            editable=editable,
            required=bool(metadata.get("required")),
            max_length=(
                int(metadata["length"]) if metadata.get("length") else None
            ),
            choices=tuple(metadata.get("choices", ())),
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
            target_entity=field.target_entity,
            reference_display=(
                self._reference_display(field.target_entity, value)
                if metadata["type"] == "reference"
                and field.target_entity
                and value is not None
                and value is not PROTECTED
                else ""
            ),
            lookup_view=(
                str(
                    configuration.get(
                        "lookup_view",
                        metadata.get("lookup_view"),
                    )
                )
                if metadata["type"] == "reference"
                and configuration.get("editor") == "lookup"
                and configuration.get(
                    "lookup_view",
                    metadata.get("lookup_view"),
                )
                else None
            ),
        )

    def _detail_sections(
        self,
        values: Mapping[str, Any],
    ) -> tuple[QtDetailSection, ...]:
        assert self.detail_view is not None
        sections: list[QtDetailSection] = []
        for configuration in self.detail_view.data.get("layout", ()):
            if configuration.get("group"):
                rows = tuple(
                    tuple(
                        self._detail_field(self.entity.field(str(name)), values)
                        for name in row
                        if str(name) in self.entity.fields
                        and self.entity.field(str(name)).metadata["type"]
                        != "collection"
                        and not _field_is_hidden(self.detail_view, str(name))
                    )
                    for row in configuration.get("rows", ())
                )
                visible_rows = tuple(row for row in rows if row)
                if visible_rows:
                    sections.append(
                        QtDetailGroup(
                            label=str(configuration["group"]),
                            rows=visible_rows,
                        )
                    )
                continue
            if configuration.get("collection"):
                collection = self._detail_collection(configuration, values)
                if collection is not None:
                    sections.append(collection)
        if sections:
            return tuple(sections)
        rows = tuple(
            (self._detail_field(field, values),)
            for field in self.entity.fields.values()
            if field.metadata["type"] != "collection"
            and not _field_is_hidden(self.detail_view, field.name)
        )
        return (QtDetailGroup(label=self.entity.label, rows=rows),)

    def _detail_field(
        self,
        field: NormalizedField,
        values: Mapping[str, Any],
    ) -> QtDetailField:
        return QtDetailField(
            name=field.name,
            label=_field_label(field),
            value=self._format_value(field, values.get(field.name)),
            alignment=_field_alignment(field, self.model.formats),
        )

    def _detail_collection(
        self,
        configuration: Mapping[str, Any],
        values: Mapping[str, Any],
    ) -> QtDetailCollection | None:
        assert self.detail_view is not None
        name = str(configuration["collection"])
        if name not in self.entity.fields or _field_is_hidden(self.detail_view, name):
            return None
        field = self.entity.field(name)
        inline_name = configuration.get("view")
        if field.target_entity is None or not inline_name:
            return None
        inline = self.model.views.get(str(inline_name))
        if inline is None or inline.kind != "inline_edit":
            return None
        target = self.model.entity(field.target_entity)
        field_names = _browse_columns(inline, target)
        columns = tuple(
            QtBrowseColumn(
                item.name,
                _field_label(item),
                _field_alignment(item, self.model.formats),
            )
            for item in (target.field(field_name) for field_name in field_names)
        )
        raw_rows = values.get(name)
        protected = raw_rows is PROTECTED
        records = raw_rows if isinstance(raw_rows, list) else ()
        rows = tuple(
            tuple(
                self._format_value(target.field(field_name), record.get(field_name))
                for field_name in field_names
            )
            for record in records
        )
        return QtDetailCollection(
            name=name,
            label=_field_label(field),
            columns=columns,
            rows=rows,
            protected=protected,
        )

    def _format_value(self, field: NormalizedField, value: Any) -> str:
        if value is PROTECTED:
            return "Protected"
        if value is None:
            return ""
        if field.metadata["type"] == "reference" and field.target_entity:
            return self._reference_display(field.target_entity, value)
        if field.metadata["type"] == "choice":
            return str(value).replace("_", " ").title()
        configuration = self.model.formats.get(
            str(field.metadata.get("format")),
            {},
        )
        if isinstance(value, datetime):
            pattern = str(configuration.get("display", "%d.%m.%Y %H:%M"))
            return value.strftime(pattern)
        if isinstance(value, date):
            pattern = str(configuration.get("display", "%Y-%m-%d"))
            return value.strftime(pattern)
        if isinstance(value, Decimal):
            places = configuration.get("decimal_places")
            if places is None:
                return str(value)
            grouping = "," if configuration.get("thousands_separator") else ""
            return format(value, f"{grouping}.{int(places)}f")
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)

    def _reference_display(self, entity_name: str, identity: Any) -> str:
        key = entity_name, identity
        with self._reference_cache_lock:
            cached = self._reference_cache.get(key, _CACHE_MISS)
        if cached is not _CACHE_MISS:
            return cached
        try:
            record = self.client.get_record(entity_name, identity).values
            result = _display_record(self.model.entity(entity_name), record)
        except TideRuntimeError:
            result = "Protected"
        with self._reference_cache_lock:
            return self._reference_cache.setdefault(key, result)


def _select_browse_view(
    model: ApplicationModel,
    session: TideSessionInfo,
    view_name: str | None,
) -> ResolvedView:
    browse_views = tuple(
        view
        for view in model.views.values()
        if view.kind == "browse"
        and "list" in session.entities.get(view.entity, _EMPTY_CAPABILITIES).operations
    )
    if view_name is not None:
        selected = next((view for view in browse_views if view.name == view_name), None)
        if selected is None:
            raise ValueError(f"Qt browse view {view_name!r} is not accessible")
        return selected
    selected = next(
        (
            view
            for view in browse_views
            if view.data.get("settings", {}).get("default") is True
        ),
        browse_views[0] if browse_views else None,
    )
    if selected is None:
        raise ValueError("application does not define an accessible browse view")
    return selected


def _select_form_view(
    model: ApplicationModel,
    session: TideSessionInfo,
    entity_name: str,
    view_name: str | None = None,
) -> ResolvedView | None:
    capabilities = session.entities.get(entity_name, _EMPTY_CAPABILITIES)
    if not (
        {"get", "create", "update"} & set(capabilities.operations)
        or capabilities.draft_operations
    ):
        return None
    selected = next(
        (
            view
            for view in model.views.values()
            if view.kind == "form"
            and view.entity == entity_name
            and (view_name is None or view.name == view_name)
        ),
        None,
    )
    if view_name is not None and selected is None:
        raise ValueError(
            f"Qt form view {view_name!r} is not accessible for {entity_name}"
        )
    return selected


class _EmptyCapabilities:
    operations: tuple[str, ...] = ()
    draft_operations: tuple[str, ...] = ()
    writable_fields: tuple[str, ...] = ()


_EMPTY_CAPABILITIES = _EmptyCapabilities()
_CACHE_MISS = object()


def _browse_columns(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    configured = tuple(str(name) for name in view.data.get("columns", ()))
    columns = configured or tuple(
        name
        for name, field in entity.fields.items()
        if field.metadata["type"] != "collection"
    )
    field_configuration = view.data.get("fields", {})
    return tuple(
        name
        for name in columns
        if not (
            isinstance(field_configuration, Mapping)
            and isinstance(field_configuration.get(name), Mapping)
            and field_configuration[name].get("hidden", False)
        )
    )


def _field_is_hidden(view: ResolvedView, field_name: str) -> bool:
    field_configuration = view.data.get("fields", {})
    return bool(
        isinstance(field_configuration, Mapping)
        and isinstance(field_configuration.get(field_name), Mapping)
        and field_configuration[field_name].get("hidden", False)
    )


def _form_field_names(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    result: list[str] = []
    for section in view.data.get("layout", ()):
        for row in section.get("rows", ()):
            for raw_name in row:
                name = str(raw_name)
                if (
                    name in entity.fields
                    and entity.field(name).metadata["type"] != "collection"
                    and not _field_is_hidden(view, name)
                    and name not in result
                ):
                    result.append(name)
    if result:
        return tuple(result)
    return tuple(
        name
        for name, field in entity.fields.items()
        if field.metadata["type"] != "collection"
        and not _field_is_hidden(view, name)
    )


def _view_field_configuration(
    view: ResolvedView,
    field_name: str,
) -> Mapping[str, Any]:
    fields = view.data.get("fields", {})
    if not isinstance(fields, Mapping):
        return {}
    configuration = fields.get(field_name, {})
    return configuration if isinstance(configuration, Mapping) else {}


def _lookup_columns(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    configured = tuple(str(name) for name in view.data.get("columns", ()))
    return configured or tuple(
        name
        for name, field in entity.fields.items()
        if field.metadata["type"] != "collection"
    )


def _lookup_search_fields(
    view: ResolvedView,
    entity: NormalizedEntity,
) -> tuple[str, ...]:
    configured = tuple(str(name) for name in view.data.get("search", ()))
    candidates = configured or tuple(entity.metadata.get("search_fields", ()))
    return tuple(
        name
        for name in candidates
        if name in entity.fields
        and entity.field(name).metadata["type"] in {"string", "choice"}
        and not entity.field(name).metadata.get("computed")
    )


def _field_label(field: NormalizedField) -> str:
    return str(field.metadata.get("label") or _humanize(field.name))


def _field_alignment(
    field: NormalizedField,
    formats: Mapping[str, Mapping[str, Any]],
) -> Alignment:
    configured = formats.get(str(field.metadata.get("format")), {}).get("align")
    if configured in {"left", "center", "right"}:
        return configured
    return "right" if field.metadata["type"] in {"integer", "decimal"} else "left"


def _display_record(entity: NormalizedEntity, values: Mapping[str, Any]) -> str:
    primary_key = _primary_key_name(entity)
    if not entity.display:
        return str(values.get(primary_key, ""))
    if "{" not in entity.display:
        return _safe_display_value(values.get(entity.display))
    try:
        return entity.display.format_map(
            {name: _safe_display_value(value) for name, value in values.items()}
        )
    except (KeyError, ValueError):
        return str(values.get(primary_key, ""))


def _primary_key_name(entity: NormalizedEntity) -> str:
    return next(
        name for name, field in entity.fields.items() if field.metadata.get("primary_key")
    )


def _safe_display_value(value: Any) -> str:
    if value is PROTECTED:
        return "Protected"
    return "" if value is None else str(value)


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").title()
