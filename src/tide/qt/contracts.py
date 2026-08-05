"""What the Qt renderer is handed: one screen's worth of compiled
metadata at a time, with no PySide6 and no controller behind it."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Mapping, Protocol

from tide.data import QuerySpec
from tide.reporting import ReportDocument
from tide.runtime import TideRuntimeError
from tide.sessions import (
    RecordConflict,
)

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

    def execute_action(
        self,
        entity_name: str,
        action_name: str,
        identity: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        if_match: str | int | None = None,
        idempotency_key: str | None = None,
    ) -> Any: ...

    def build_report_for_record(
        self,
        report_name: str,
        identity: Any,
    ) -> ReportDocument: ...

    def build_report(
        self,
        report_name: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ReportDocument: ...


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
class QtRecordReport:
    """One REST-exposed record report authorized for the current session."""

    name: str
    title: str


@dataclass(frozen=True, slots=True)
class QtSummaryReport:
    """One parameterless REST summary report authorized for the session."""

    name: str
    title: str


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
class QtEditCollection:
    """One compiler-resolved inline collection editor."""

    name: str
    label: str
    entity: str
    columns: tuple[QtBrowseColumn, ...]
    groups: tuple[QtEditGroup, ...]
    actions: tuple[str, ...]
    records: tuple[Mapping[str, Any], ...]
    defaults: Mapping[str, Any]
    editable: bool

    @property
    def fields(self) -> tuple[QtEditField, ...]:
        return tuple(
            field
            for group in self.groups
            for row in group.rows
            for field in row
        )


@dataclass(frozen=True, slots=True)
class QtEditAction:
    """One metadata-ordered, capability-gated form action."""

    name: str
    label: str
    enabled: bool
    visible: bool = True


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
    collections: tuple[QtEditCollection, ...] = ()
    actions: tuple[QtEditAction, ...] = ()
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
class QtEditConflict:
    """A stale Qt draft compared with the latest secured server record."""

    current_form: QtEditForm
    comparison: RecordConflict
    draft: Mapping[str, Any]
    locked_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QtEditRebase:
    """A fresh form carrying only the explicitly resolved draft values."""

    form: QtEditForm
    retained_fields: tuple[str, ...]
    dropped_fields: tuple[str, ...]


class QtEditActionError(TideRuntimeError):
    """An action failed after Qt may already have saved its draft."""

    def __init__(
        self,
        action: QtEditAction,
        cause: Exception,
        *,
        form: QtEditForm,
        draft: Mapping[str, Any],
        saved_before_action: bool,
    ) -> None:
        self.action = action
        self.cause = cause
        self.form = form
        self.draft = deepcopy(dict(draft))
        self.saved_before_action = saved_before_action
        self.code = str(getattr(cause, "code", "runtime_error"))
        super().__init__(str(cause))


@dataclass(frozen=True, slots=True)
class QtLookupSpec:
    """Resolved secured lookup metadata for one reference editor."""

    owner_entity: str
    field_name: str
    title: str
    target_entity: str
    collection_name: str | None
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
