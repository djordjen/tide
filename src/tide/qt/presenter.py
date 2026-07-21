"""Qt-neutral browse presentation over the secured HTTP client contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any, Literal, Mapping, Protocol

from tide.api.contracts import TideSessionInfo
from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
from tide.runtime import TideRuntimeError
from tide.security import PROTECTED

Alignment = Literal["left", "center", "right"]


class BrowseApiClient(Protocol):
    """Small typed-client surface consumed by the initial Qt presenter."""

    def list_records(
        self,
        entity_name: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Any: ...

    def get_record(self, entity_name: str, identity: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class QtBrowseColumn:
    name: str
    label: str
    alignment: Alignment = "left"


@dataclass(frozen=True, slots=True)
class QtBrowsePage:
    columns: tuple[QtBrowseColumn, ...]
    rows: tuple[tuple[str, ...], ...]
    page_number: int
    previous_available: bool
    next_available: bool


class QtBrowseController:
    """Build a metadata-driven read-only browse without importing PySide6."""

    def __init__(
        self,
        model: ApplicationModel,
        client: BrowseApiClient,
        session: TideSessionInfo,
        *,
        view_name: str | None = None,
        page_size: int | None = None,
    ) -> None:
        self.model = model
        self.client = client
        self.session = session
        self.view = _select_browse_view(model, session, view_name)
        self.entity = model.entity(self.view.entity)
        self.field_names = _browse_columns(self.view, self.entity)
        configured_page_size = int(
            self.view.data.get("settings", {}).get("page_size", 25)
        )
        self.page_size = configured_page_size if page_size is None else page_size
        if self.page_size < 1 or self.page_size > 500:
            raise ValueError("Qt browse page size must be between 1 and 500")
        self.columns = tuple(
            QtBrowseColumn(
                field.name,
                _field_label(field),
                _field_alignment(field, model.formats),
            )
            for field in (self.entity.field(name) for name in self.field_names)
        )
        self._page_cursors: list[str | None] = [None]
        self._page_index = 0
        self._current: QtBrowsePage | None = None
        self._next_cursor: str | None = None
        self._reference_cache: dict[tuple[str, Any], str] = {}

    @property
    def title(self) -> str:
        return self.entity.label

    @property
    def context_text(self) -> str:
        roles = ", ".join(sorted(self.session.roles)) or "no role"
        return f"{self.view.name}  ·  {self.session.principal}  ·  {roles}"

    def refresh(self) -> QtBrowsePage:
        self._reference_cache.clear()
        return self._load(self._page_cursors[self._page_index], self._page_index)

    def next_page(self) -> QtBrowsePage:
        if self._current is None:
            return self.refresh()
        if self._next_cursor is None:
            return self._current
        target_index = self._page_index + 1
        target_cursor = self._next_cursor
        page = self._load(target_cursor, target_index, update_state=False)
        self._page_cursors = self._page_cursors[:target_index]
        self._page_cursors.append(target_cursor)
        self._page_index = target_index
        self._current = page
        return page

    def previous_page(self) -> QtBrowsePage:
        if self._current is None:
            return self.refresh()
        if self._page_index == 0:
            return self._current
        target_index = self._page_index - 1
        page = self._load(
            self._page_cursors[target_index],
            target_index,
            update_state=False,
        )
        self._page_index = target_index
        self._current = page
        return page

    def _load(
        self,
        cursor: str | None,
        page_index: int,
        *,
        update_state: bool = True,
    ) -> QtBrowsePage:
        remote = self.client.list_records(
            self.entity.name,
            limit=self.page_size,
            cursor=cursor,
        )
        rows = tuple(
            tuple(
                self._format_value(self.entity.field(name), record.get(name))
                for name in self.field_names
            )
            for record in remote.records
        )
        page = QtBrowsePage(
            columns=self.columns,
            rows=rows,
            page_number=page_index + 1,
            previous_available=page_index > 0,
            next_available=remote.next_cursor is not None,
        )
        self._next_cursor = remote.next_cursor
        if update_state:
            self._page_index = page_index
            self._current = page
        return page

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
        if key in self._reference_cache:
            return self._reference_cache[key]
        try:
            record = self.client.get_record(entity_name, identity).values
            result = _display_record(self.model.entity(entity_name), record)
        except TideRuntimeError:
            result = "Protected"
        self._reference_cache[key] = result
        return result


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


class _EmptyCapabilities:
    operations: tuple[str, ...] = ()


_EMPTY_CAPABILITIES = _EmptyCapabilities()


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
    primary_key = next(
        name for name, field in entity.fields.items() if field.metadata.get("primary_key")
    )
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


def _safe_display_value(value: Any) -> str:
    if value is PROTECTED:
        return "Protected"
    return "" if value is None else str(value)


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").title()
