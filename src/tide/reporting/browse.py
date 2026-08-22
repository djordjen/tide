"""Taking a browse query away as a file.

The reader has already filtered, sorted and totalled a grid. This walks the
same query, under the same security, and writes down what it walked -- the
conditions, the sort, and where it stopped -- because a file that outlives the
session has to be able to say what it is.

Bounded on purpose. A caller holding `list` can already page every row, so the
cap is not a confidentiality boundary; it is what stops one request becoming
an unbounded scan on a shared server. Past it the file still arrives and says
it is partial, the way `_distinct` returns 200 values and reports that it
truncated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from typing import Any

from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    ResolvedView,
)
from tide.data import FilterCondition, QuerySpec, SortField, SummaryRequest
from tide.labels import humanize
from tide.presentation import browse_columns, field_alignment, field_label
from tide.runtime import RequestContext
from tide.runtime.errors import AuthorizationError, TideRuntimeError
from tide.services.records import RecordsService

from .document import (
    ReportCell,
    ReportColumn,
    ReportDocument,
    ReportTable,
    ReportValue,
)
from .fields import FieldFormatter

EXPORT = "tide.records.export"
"""The framework capability that separates reading a grid from carrying it."""

MAX_EXPORT_ROWS = 10_000
"""How many rows one export may carry."""

PAGE_SIZE = 500
"""How many it fetches at a time -- the ceiling `query_page` accepts."""

_TYPED_FIELD_TYPES = frozenset(
    {"integer", "decimal", "date", "datetime", "boolean"}
)
"""Column types a spreadsheet should hold as values rather than as text.

Everything else is already text by the time it is worth reading: a reference
names a record, a choice is captioned rather than coded, and a string is a
string. Sending their stored values instead would put an identity where a
customer's name belongs.
"""

_OPERATOR_PHRASES = {
    "eq": "is",
    "ne": "is not",
    "gt": "is after",
    "gte": "is at least",
    "lt": "is before",
    "lte": "is at most",
    "contains": "contains",
    "icontains": "contains",
    "startswith": "starts with",
    "endswith": "ends with",
    "in": "is one of",
}


class UnknownBrowseView(TideRuntimeError):
    """The named view is missing, or is not a browse."""


class ExportNotPermitted(AuthorizationError):
    """The principal may read the grid but not carry it off."""


@dataclass(frozen=True, slots=True)
class BrowseExport:
    """One browse query, rendered once as text and once as typed values.

    `document` is what every reader sees; `typed_values` is the same table for
    a format that can hold a number as a number. An entry is `None` wherever
    the text is already the whole truth -- references and choices, whose value
    *is* their display string.
    """

    document: ReportDocument
    typed_values: tuple[tuple[Any, ...], ...]
    rows: int
    total: int

    @property
    def truncated(self) -> bool:
        return self.rows < self.total


class BrowseExportService:
    """Build one export from the same secured query the grid ran."""

    def __init__(self, model: ApplicationModel, records: RecordsService) -> None:
        self.model = model
        self.records = records
        self.formatter = FieldFormatter(model, records)

    def can_export(self, context: RequestContext) -> bool:
        return EXPORT in self.records.security.effective_permissions(
            context.principal
        )

    def build(
        self,
        view_name: str,
        filters: tuple[FilterCondition, ...],
        sort: tuple[SortField, ...],
        context: RequestContext,
    ) -> BrowseExport:
        """Walk one browse query to the cap and describe what was walked."""

        view = self.model.views.get(view_name)
        if view is None or view.kind != "browse":
            raise UnknownBrowseView(f"unknown browse view {view_name!r}")
        if not self.can_export(context):
            raise ExportNotPermitted("this principal may not export records")

        entity = self.model.entity(view.entity)
        columns = tuple(
            name
            for name in browse_columns(view, entity)
            if self.records.security.can_read_field(entity.name, name, context)
        )
        if not columns:
            raise ExportNotPermitted("no exportable column is readable")

        counting = SummaryRequest(field=entity.primary_key.name, function="count")
        declared = tuple(
            SummaryRequest(field=name, function=str(function))
            for name, function in (view.data.get("summaries") or {}).items()
            if name in columns
        )
        asked = declared if counting in declared else declared + (counting,)

        page = self.records.query_page(
            entity.name,
            QuerySpec(
                filters=filters,
                sort=sort,
                limit=PAGE_SIZE,
                summaries=asked,
            ),
            context,
        )
        # Answered over the whole filtered set rather than this page, so the
        # first ask is the only one worth making.
        summaries = page.summaries

        rows: list[tuple[ReportCell, ...]] = []
        typed: list[tuple[Any, ...]] = []
        while True:
            for record in page.records:
                if len(rows) >= MAX_EXPORT_ROWS:
                    break
                cells: list[ReportCell] = []
                values: list[Any] = []
                for name in columns:
                    field = entity.field(name)
                    raw = record.get(name)
                    cells.append(
                        ReportCell(
                            self.formatter.field(
                                field,
                                raw,
                                context,
                                references=page.references,
                            ),
                            field_alignment(field, self.model.formats, None),
                        )
                    )
                    values.append(_typed(field, raw))
                rows.append(tuple(cells))
                typed.append(tuple(values))
            if page.next_cursor is None or len(rows) >= MAX_EXPORT_ROWS:
                break
            # The cursor's shape carries the filters, sort, limit and
            # principal but not the summaries, so dropping them here is safe
            # -- and necessary, because each ask recomputes the same
            # aggregates over the same whole set.
            page = self.records.query_page(
                entity.name,
                QuerySpec(
                    filters=filters,
                    sort=sort,
                    limit=PAGE_SIZE,
                    cursor=page.next_cursor,
                ),
                context,
            )

        total = _summary_value(summaries, counting, len(rows))
        title = _view_title(view, entity)
        generated = datetime.now(timezone.utc)
        return BrowseExport(
            document=ReportDocument(
                report=view_name,
                title=title,
                application=self.model.name,
                generated_at=generated,
                header_text=self._provenance(
                    entity,
                    title,
                    filters,
                    sort,
                    context,
                    generated=generated,
                    rows=len(rows),
                    total=total,
                ),
                record_values=(),
                detail=ReportTable(
                    columns=tuple(
                        ReportColumn(
                            name=name,
                            label=field_label(entity.field(name)),
                            alignment=field_alignment(
                                entity.field(name),
                                self.model.formats,
                                None,
                            ),
                        )
                        for name in columns
                    ),
                    rows=tuple(rows),
                ),
                footer_values=tuple(
                    ReportValue(
                        label=(
                            f"{request.function.upper()} "
                            f"{field_label(entity.field(request.field))}"
                        ),
                        text=self.formatter.scalar(
                            value,
                            entity.field(request.field).metadata.get("format"),
                        ),
                        alignment="right",
                    )
                    for request, value in summaries
                    if request != counting
                ),
                page_footer_template="",
                suggested_filename=_filename(
                    title,
                    generated,
                    partial=len(rows) < total,
                ),
            ),
            typed_values=tuple(typed),
            rows=len(rows),
            total=total,
        )

    def _provenance(
        self,
        entity: NormalizedEntity,
        title: str,
        filters: tuple[FilterCondition, ...],
        sort: tuple[SortField, ...],
        context: RequestContext,
        *,
        generated: datetime,
        rows: int,
        total: int,
    ) -> tuple[str, ...]:
        """Say what this file is, in the words the reader saw on screen."""

        lines = [
            f"{self.model.name} - {title}",
            f"Exported {generated.strftime('%Y-%m-%d %H:%M')} UTC",
        ]
        for condition in filters:
            # A condition on a column this principal cannot read cannot reach
            # here: `_require_queryable_field` refuses it before the query
            # runs.
            field = entity.field(condition.field)
            phrase = _OPERATOR_PHRASES.get(condition.operator, condition.operator)
            values = (
                condition.value
                if isinstance(condition.value, (list, tuple))
                else [condition.value]
            )
            rendered = ", ".join(
                self.formatter.field(field, value, context) or "(blank)"
                for value in values
            )
            lines.append(f"{field_label(field)} {phrase} {rendered}")
        if sort:
            lines.append(
                "Sorted by "
                + ", ".join(
                    field_label(entity.field(item.field))
                    + (" descending" if item.descending else "")
                    for item in sort
                )
            )
        lines.append(
            f"{rows:,} of {total:,} rows" if rows < total else f"{rows:,} rows"
        )
        return tuple(lines)


def _typed(field: NormalizedField, value: Any) -> Any:
    """The value a spreadsheet should hold, or None to use the text.

    Decided by the column rather than by the value, because a reference stores
    an integer identity whose text is a customer's name -- and a workbook
    showing `1` where the grid showed `ACME Ltd` would be a worse file, not a
    better one.
    """

    if value is None or str(field.metadata["type"]) not in _TYPED_FIELD_TYPES:
        return None
    if isinstance(value, (bool, int, Decimal, datetime, date)):
        return value
    return None


def _summary_value(
    summaries: tuple[tuple[SummaryRequest, Any], ...],
    request: SummaryRequest,
    fallback: int,
) -> int:
    for asked, value in summaries:
        if asked == request:
            try:
                return int(value)
            except (TypeError, ValueError):
                return fallback
    return fallback


def _view_title(view: ResolvedView, entity: NormalizedEntity) -> str:
    declared = view.data.get("label")
    if declared:
        return str(declared)
    return str(
        entity.metadata.get("label") or humanize(entity.name.split(".")[-1])
    )


def _filename(title: str, generated: datetime, *, partial: bool) -> str:
    """Name the download after what the reader was looking at.

    A partial file says so here, because CSV is the table and nothing else --
    a preamble row would break the one thing a CSV export is for.
    """

    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip(".-").lower() or "export"
    qualifier = generated.strftime("%Y-%m-%d")
    return f"{stem}-{qualifier}-partial" if partial else f"{stem}-{qualifier}"
