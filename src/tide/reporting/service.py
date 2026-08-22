"""Security-aware construction of renderer-neutral report documents."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

from tide.compiler.expressions import evaluate_expression
from tide.labels import humanize as _humanize
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data import FilterCondition, QuerySpec, SortField
from tide.presentation import field_alignment, field_label, record_display
from tide.services import NO_REFERENCE_DISPLAYS, ReferenceDisplays
from tide.runtime import Channel, RequestContext
from tide.runtime.errors import AuthorizationError, ValidationFailed, ValidationIssue
from tide.security import PROTECTED
from tide.services.records import RecordsService

from .fields import FieldFormatter, display_record as _display_record_shared
from .document import (
    ReportCell,
    ReportColumn,
    ReportDocument,
    ReportGroup,
    ReportTable,
    ReportValue,
)


class ReportService:
    """Build reports only from secured application-service projections."""

    def __init__(self, model: ApplicationModel, records: RecordsService) -> None:
        self.model = model
        self.records = records
        self.formatter = FieldFormatter(model, records)

    def can_generate(self, report_name: str, context: RequestContext) -> bool:
        report = self.model.reports.get(report_name)
        if report is None:
            return False
        return self.records.security.can_access_report(report, context)

    def build(
        self,
        report_name: str,
        parameters: Mapping[str, Any],
        context: RequestContext,
        *,
        generated_at: datetime | None = None,
    ) -> ReportDocument:
        report = self.model.reports.get(report_name)
        if report is None:
            raise ValueError(f"unknown report {report_name!r}")
        self.records.security.authorize_report(report_name, report, context)
        parameter_values = _coerce_parameters(report, parameters)
        if report.get("kind", "record") == "summary":
            return self._build_summary(
                report_name,
                report,
                parameter_values,
                context,
                generated_at=generated_at,
            )
        entity = self.model.entity(str(report["entity"]))
        primary_key = _primary_key(entity)
        parameter_name = _record_parameter(str(report["query"]["criteria"]), primary_key)
        if parameter_name is None:  # Compiler validation makes this defensive only.
            raise ValueError("record report query is not executable")
        report_context = replace(context, channel=Channel.REPORT)
        record = self.records.get(
            entity.name,
            parameter_values[parameter_name],
            report_context,
        )
        references = self.records.reference_displays(
            entity.name,
            (record,),
            report_context,
        )
        bands = report["bands"]
        header_text, header_values = self._content_values(
            entity,
            record,
            bands.get("report_header", ()),
            parameter_values,
            report_context,
            references,
        )
        record_text, record_values = self._content_values(
            entity,
            record,
            bands.get("record_header", ()),
            parameter_values,
            report_context,
            references,
        )
        footer_text, footer_values = self._content_values(
            entity,
            record,
            bands.get("report_footer", ()),
            parameter_values,
            report_context,
            references,
        )
        detail = self._detail(
            entity,
            record,
            bands["detail"],
            report_context,
            references,
        )
        page_footer = self._page_footer(
            record,
            bands.get("page_footer", ()),
            parameter_values,
        )
        title = header_text[0] if header_text else str(report["title"])
        extra_header = tuple(header_text[1:]) + tuple(record_text)
        extra_footer = tuple(
            ReportValue("", text) for text in footer_text if text
        )
        now = generated_at or datetime.now(timezone.utc)
        return ReportDocument(
            report=report_name,
            title=title,
            application=self.model.name,
            generated_at=now,
            header_text=extra_header,
            record_values=record_values,
            detail=detail,
            footer_values=footer_values + extra_footer,
            page_footer_template=page_footer,
            suggested_filename=_report_filename(
                report, record_display(entity, record)
            ),
        )

    def build_for_record(
        self,
        report_name: str,
        identity: Any,
        context: RequestContext,
        *,
        generated_at: datetime | None = None,
    ) -> ReportDocument:
        """Build a compiler-validated record report for one identity."""

        report = self.model.reports.get(report_name)
        if report is None:
            raise ValueError(f"unknown report {report_name!r}")
        if report.get("kind", "record") != "record":
            raise ValueError(f"report {report_name!r} is not a record report")
        entity = self.model.entity(str(report["entity"]))
        parameter = _record_parameter(
            str(report["query"]["criteria"]),
            _primary_key(entity),
        )
        if parameter is None:
            raise ValueError("record report query is not executable")
        return self.build(
            report_name,
            {parameter: identity},
            context,
            generated_at=generated_at,
        )

    def _build_summary(
        self,
        report_name: str,
        report: Mapping[str, Any],
        parameters: Mapping[str, Any],
        context: RequestContext,
        *,
        generated_at: datetime | None,
    ) -> ReportDocument:
        entity = self.model.entity(str(report["entity"]))
        report_context = replace(context, channel=Channel.REPORT)
        row_limit = int(report.get("row_limit", 500))
        query = report.get("query", {})
        group_definitions = tuple(report.get("group_by", ()))
        column_names = tuple(str(name) for name in report.get("columns", ()))
        declared_sort = tuple(
            _summary_sort(str(name)) for name in query.get("sort", ())
        )
        if column_names and group_definitions:
            # A group is a contiguous run of equal keys, so the rows must
            # arrive grouped whatever the author sorted by. Prepending the
            # group fields keeps their declared sort as the order inside
            # each group rather than an instruction the listing ignores.
            group_fields = tuple(
                str(group["field"]) for group in group_definitions
            )
            declared_sort = tuple(SortField(name) for name in group_fields) + tuple(
                item for item in declared_sort if item.field not in group_fields
            )
        page = self.records.query_page(
            entity.name,
            QuerySpec(
                filters=_summary_filters(str(query.get("criteria") or ""), parameters),
                sort=declared_sort,
                limit=row_limit,
            ),
            report_context,
        )
        if page.next_cursor is not None:
            raise ValueError(
                f"summary report {report_name!r} exceeds its row limit of "
                f"{row_limit}; narrow the report criteria"
            )

        aggregate_definitions = tuple(report["aggregates"])
        if column_names:
            return self._build_listing(
                report_name,
                report,
                entity,
                page,
                group_definitions,
                column_names,
                aggregate_definitions,
                report_context,
                generated_at=generated_at,
            )
        groups: dict[tuple[Any, ...], list[int | Decimal]] = {}
        totals = _initial_aggregates(aggregate_definitions)
        if not group_definitions:
            groups[()] = _initial_aggregates(aggregate_definitions)
        for record in page.records:
            key = tuple(
                _read_report_value(entity.name, str(group["field"]), record)
                for group in group_definitions
            )
            values = groups.setdefault(
                key,
                _initial_aggregates(aggregate_definitions),
            )
            _accumulate(entity.name, aggregate_definitions, values, record)
            _accumulate(entity.name, aggregate_definitions, totals, record)

        columns = tuple(
            ReportColumn(
                str(group["field"]),
                str(
                    group.get("label")
                    or _field_label(entity.field(str(group["field"])))
                ),
                _alignment(
                    entity.field(str(group["field"])),
                    self.model.formats,
                    group.get("format"),
                ),
            )
            for group in group_definitions
        ) + tuple(
            ReportColumn(str(aggregate["name"]), _aggregate_label(aggregate), "right")
            for aggregate in aggregate_definitions
        )

        rows: list[tuple[ReportCell, ...]] = []
        for key, aggregate_values in sorted(
            groups.items(),
            key=lambda item: tuple("" if value is None else str(value) for value in item[0]),
        ):
            group_cells = tuple(
                ReportCell(
                    self._format_field(
                        entity.field(str(group["field"])),
                        value,
                        report_context,
                        format_name=group.get("format"),
                        references=page.references,
                    ),
                    _alignment(
                        entity.field(str(group["field"])),
                        self.model.formats,
                        group.get("format"),
                    ),
                )
                for group, value in zip(group_definitions, key)
            )
            aggregate_cells = tuple(
                ReportCell(
                    self._aggregate_text(entity, aggregate, value, report_context),
                    "right",
                )
                for aggregate, value in zip(aggregate_definitions, aggregate_values)
            )
            rows.append(group_cells + aggregate_cells)

        now = generated_at or datetime.now(timezone.utc)
        return ReportDocument(
            report=report_name,
            title=str(report["title"]),
            application=self.model.name,
            generated_at=now,
            header_text=(),
            record_values=(),
            detail=ReportTable(columns, tuple(rows)),
            footer_values=tuple(
                ReportValue(
                    _aggregate_label(aggregate),
                    self._aggregate_text(entity, aggregate, value, report_context),
                    "right",
                )
                for aggregate, value in zip(aggregate_definitions, totals)
            )
            + (ReportValue("Source records", str(len(page.records)), "right"),),
            page_footer_template="Page {page_number} of {page_count}",
            suggested_filename=_report_filename(
                report, now.astimezone(timezone.utc).date().isoformat()
            ),
        )

    def _build_listing(
        self,
        report_name: str,
        report: Mapping[str, Any],
        entity: NormalizedEntity,
        page: Any,
        group_definitions: tuple[Mapping[str, Any], ...],
        column_names: tuple[str, ...],
        aggregate_definitions: tuple[Mapping[str, Any], ...],
        context: RequestContext,
        *,
        generated_at: datetime | None,
    ) -> ReportDocument:
        """List the matching records themselves, sliced into subtotaled groups.

        The detail table holds every row exactly once; a group names its
        contiguous slice, heads it with the group values and closes it with
        the aggregates. The same aggregates run once more over everything for
        the report footer, so a group total and the grand total can only
        disagree if the arithmetic itself does.
        """

        column_fields = tuple(entity.field(name) for name in column_names)
        columns = tuple(
            ReportColumn(
                field.name,
                _field_label(field),
                _alignment(field, self.model.formats, None),
            )
            for field in column_fields
        )
        rows: list[tuple[ReportCell, ...]] = []
        groups: list[ReportGroup] = []
        totals = _initial_aggregates(aggregate_definitions)
        run_key: tuple[Any, ...] | None = None
        run_start = 0
        run_values = _initial_aggregates(aggregate_definitions)

        def close_run() -> None:
            if not group_definitions or run_key is None:
                return
            groups.append(
                ReportGroup(
                    tuple(
                        ReportValue(
                            str(
                                group.get("label")
                                or _field_label(entity.field(str(group["field"])))
                            ),
                            self._format_field(
                                entity.field(str(group["field"])),
                                value,
                                context,
                                format_name=group.get("format"),
                                references=page.references,
                            ),
                        )
                        for group, value in zip(group_definitions, run_key)
                    ),
                    run_start,
                    len(rows) - run_start,
                    tuple(
                        ReportValue(
                            _aggregate_label(aggregate),
                            self._aggregate_text(entity, aggregate, value, context),
                            "right",
                        )
                        for aggregate, value in zip(aggregate_definitions, run_values)
                    ),
                )
            )

        for record in page.records:
            key = tuple(
                _read_report_value(entity.name, str(group["field"]), record)
                for group in group_definitions
            )
            if group_definitions and key != run_key:
                close_run()
                run_key = key
                run_start = len(rows)
                run_values = _initial_aggregates(aggregate_definitions)
            _accumulate(entity.name, aggregate_definitions, run_values, record)
            _accumulate(entity.name, aggregate_definitions, totals, record)
            rows.append(
                tuple(
                    ReportCell(
                        self._format_field(
                            field,
                            _read_report_value(entity.name, field.name, record),
                            context,
                            references=page.references,
                        ),
                        _alignment(field, self.model.formats, None),
                    )
                    for field in column_fields
                )
            )
        close_run()

        now = generated_at or datetime.now(timezone.utc)
        return ReportDocument(
            report=report_name,
            title=str(report["title"]),
            application=self.model.name,
            generated_at=now,
            header_text=(),
            record_values=(),
            detail=ReportTable(columns, tuple(rows)),
            footer_values=tuple(
                ReportValue(
                    _aggregate_label(aggregate),
                    self._aggregate_text(entity, aggregate, value, context),
                    "right",
                )
                for aggregate, value in zip(aggregate_definitions, totals)
            )
            + (ReportValue("Source records", str(len(page.records)), "right"),),
            page_footer_template="Page {page_number} of {page_count}",
            suggested_filename=_report_filename(
                report, now.astimezone(timezone.utc).date().isoformat()
            ),
            groups=tuple(groups),
        )

    def _content_values(
        self,
        entity: NormalizedEntity,
        record: Mapping[str, Any],
        items: tuple[Mapping[str, Any], ...],
        parameters: Mapping[str, Any],
        context: RequestContext,
        references: ReferenceDisplays = NO_REFERENCE_DISPLAYS,
    ) -> tuple[tuple[str, ...], tuple[ReportValue, ...]]:
        texts: list[str] = []
        values: list[ReportValue] = []
        for item in items:
            if "text" in item:
                texts.append(str(item["text"]))
                continue
            if "field" in item:
                field = entity.field(str(item["field"]))
                raw = _read_report_value(entity.name, field.name, record)
                text = self._format_field(
                    field,
                    raw,
                    context,
                    format_name=item.get("format"),
                    references=references,
                )
                values.append(
                    ReportValue(
                        str(item.get("label") or _field_label(field)),
                        text,
                        _alignment(field, self.model.formats, item.get("format")),
                    )
                )
                continue
            expression = str(item["expression"])
            raw = evaluate_expression(expression, record, parameters=parameters)
            text = self._format_scalar(raw, item.get("format"))
            values.append(
                ReportValue(
                    str(item.get("label") or ""),
                    text,
                    _format_alignment(self.model.formats, item.get("format")),
                )
            )
        return tuple(texts), tuple(values)

    def _detail(
        self,
        entity: NormalizedEntity,
        record: Mapping[str, Any],
        detail: Mapping[str, Any],
        context: RequestContext,
        references: ReferenceDisplays = NO_REFERENCE_DISPLAYS,
    ) -> ReportTable:
        source_name = str(detail["source"])
        source = entity.field(source_name)
        raw_rows = _read_report_value(entity.name, source_name, record)
        if not isinstance(raw_rows, (list, tuple)):
            raise ValueError(f"report detail {source_name!r} is not a collection")
        assert source.target_entity is not None
        target = self.model.entity(source.target_entity)
        fields = tuple(target.field(str(name)) for name in detail["columns"])
        columns = tuple(
            ReportColumn(
                field.name,
                _field_label(field),
                _alignment(field, self.model.formats, None),
            )
            for field in fields
        )
        rows: list[tuple[ReportCell, ...]] = []
        for raw_row in raw_rows:
            rows.append(
                tuple(
                    ReportCell(
                        self._format_field(
                            field,
                            _read_report_value(target.name, field.name, raw_row),
                            context,
                            references=references,
                        ),
                        _alignment(field, self.model.formats, None),
                    )
                    for field in fields
                )
            )
        return ReportTable(columns, tuple(rows))

    def _page_footer(
        self,
        record: Mapping[str, Any],
        items: tuple[Mapping[str, Any], ...],
        parameters: Mapping[str, Any],
    ) -> str:
        parts: list[str] = []
        for item in items:
            if "text" in item:
                parts.append(str(item["text"]))
            elif "field" in item:
                value = _read_report_value("report", str(item["field"]), record)
                parts.append(str(value))
            else:
                parts.append(
                    str(
                        evaluate_expression(
                            str(item["expression"]),
                            record,
                            parameters=parameters,
                            globals_={
                                "page_number": "{page_number}",
                                "page_count": "{page_count}",
                            },
                        )
                    )
                )
        return "  |  ".join(parts) or "Page {page_number} of {page_count}"

    def _aggregate_text(
        self,
        entity: NormalizedEntity,
        aggregate: Mapping[str, Any],
        value: Any,
        context: RequestContext,
    ) -> str:
        """Format one aggregate the same way wherever it appears.

        A subtotal, a grand total and a summary cell are the same number at
        different scopes; formatting them in one place is what keeps a group
        footer from disagreeing with the column above it.
        """

        if aggregate["function"] == "count":
            return self._format_scalar(value, aggregate.get("format"))
        return self._format_field(
            entity.field(str(aggregate["field"])),
            value,
            context,
            format_name=aggregate.get("format"),
        )

    def _format_field(
        self,
        field: NormalizedField,
        value: Any,
        context: RequestContext,
        *,
        format_name: Any = None,
        references: ReferenceDisplays = NO_REFERENCE_DISPLAYS,
    ) -> str:
        return self.formatter.field(
            field,
            value,
            context,
            format_name=format_name,
            references=references,
        )

    def _format_scalar(self, value: Any, format_name: Any = None) -> str:
        return self.formatter.scalar(value, format_name)


def _coerce_parameters(
    report: Mapping[str, Any],
    supplied: Mapping[str, Any],
) -> dict[str, Any]:
    definitions = report.get("parameters", {})
    unknown = sorted(set(supplied) - set(definitions))
    issues: list[ValidationIssue] = []
    if unknown:
        issues.append(
            ValidationIssue(
                "report_parameter",
                f"unknown report parameter {unknown[0]!r}",
                (unknown[0],),
            )
        )
    result: dict[str, Any] = {}
    for name, definition in definitions.items():
        value = supplied.get(name, definition.get("default"))
        if value is None:
            if definition.get("required"):
                issues.append(
                    ValidationIssue(
                        "report_parameter",
                        f"report parameter {name!r} is required",
                        (name,),
                    )
                )
            result[name] = None
            continue
        try:
            result[name] = _coerce_parameter(str(definition["type"]), value)
        except (TypeError, ValueError, InvalidOperation):
            issues.append(
                ValidationIssue(
                    "report_parameter",
                    f"report parameter {name!r} must be {definition['type']}",
                    (name,),
                )
            )
    if issues:
        raise ValidationFailed(issues)
    return result


def _summary_filters(
    criteria: str,
    parameters: Mapping[str, Any],
) -> tuple[FilterCondition, ...]:
    if not criteria:
        return ()
    rewritten = re.sub(
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
        r"__tide_parameter_\1",
        criteria,
    )
    expression = ast.parse(rewritten, mode="eval").body
    clauses = (
        tuple(expression.values)
        if isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.And)
        else (expression,)
    )
    operators = {
        ast.Eq: "eq",
        ast.NotEq: "ne",
        ast.Lt: "lt",
        ast.LtE: "lte",
        ast.Gt: "gt",
        ast.GtE: "gte",
    }
    result: list[FilterCondition] = []
    for clause in clauses:
        if not isinstance(clause, ast.Compare) or not isinstance(clause.left, ast.Name):
            raise ValueError("summary report criteria is not queryable")
        comparator = clause.comparators[0]
        if isinstance(comparator, ast.Name) and comparator.id.startswith(
            "__tide_parameter_"
        ):
            value = parameters[comparator.id.removeprefix("__tide_parameter_")]
            if value is None:
                # A declared optional parameter that was not supplied. The
                # clause asks nothing, so it is dropped rather than sent to
                # the database as a comparison with nothing -- which is what
                # lets one report answer both "everything" and "this period".
                # A required parameter cannot be None here (coercion refused
                # it), and a literal `null` comparison takes the branch below.
                continue
        elif isinstance(comparator, ast.Name):
            value = {"true": True, "false": False, "null": None}[comparator.id]
        else:
            value = ast.literal_eval(comparator)
        result.append(
            FilterCondition(
                clause.left.id,
                operators[type(clause.ops[0])],
                value,
            )
        )
    return tuple(result)


def _summary_sort(value: str) -> SortField:
    return SortField(value.lstrip("+-"), descending=value.startswith("-"))


def _aggregate_label(aggregate: Mapping[str, Any]) -> str:
    return str(aggregate.get("label") or _humanize(str(aggregate["name"])))


def _initial_aggregates(
    aggregates: tuple[Mapping[str, Any], ...],
) -> list[int | Decimal]:
    return [
        0 if aggregate["function"] == "count" else Decimal(0)
        for aggregate in aggregates
    ]


def _accumulate(
    entity_name: str,
    aggregates: tuple[Mapping[str, Any], ...],
    values: list[int | Decimal],
    record: Mapping[str, Any],
) -> None:
    """Fold one record into a running aggregate row, in place.

    A group subtotal, the grand total and the flat summary's cells all walk
    through here, which is what entitles the listing to claim they agree.
    """

    for index, aggregate in enumerate(aggregates):
        if aggregate["function"] == "count":
            values[index] = int(values[index]) + 1
            continue
        raw = _read_report_value(entity_name, str(aggregate["field"]), record)
        if raw is not None:
            values[index] = Decimal(values[index]) + Decimal(raw)


def _coerce_parameter(field_type: str, value: Any) -> Any:
    if field_type == "string":
        if not isinstance(value, str):
            raise TypeError
        return value
    if field_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            return int(value)
        raise TypeError
    if field_type == "decimal":
        if isinstance(value, bool):
            raise TypeError
        return Decimal(str(value))
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise TypeError
        return value
    if field_type == "date":
        if isinstance(value, datetime):
            raise TypeError
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if field_type == "datetime":
        if isinstance(value, date) and not isinstance(value, datetime):
            raise TypeError
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    raise ValueError(field_type)


def _read_report_value(
    entity_name: str,
    field_name: str,
    values: Mapping[str, Any],
) -> Any:
    value = values.get(field_name)
    if value is PROTECTED:
        raise AuthorizationError(
            f"field {entity_name}.{field_name} is protected in this report"
        )
    return value


def _record_parameter(criteria: str, primary_key: str) -> str | None:
    identifier = r"([A-Za-z_][A-Za-z0-9_]*)"
    first = re.fullmatch(
        rf"\s*{re.escape(primary_key)}\s*==\s*\${identifier}\s*",
        criteria,
    )
    if first is not None:
        return first.group(1)
    second = re.fullmatch(
        rf"\s*\${identifier}\s*==\s*{re.escape(primary_key)}\s*",
        criteria,
    )
    return second.group(1) if second is not None else None


def _primary_key(entity: NormalizedEntity) -> str:
    return entity.primary_key.name


_field_label = field_label
_alignment = field_alignment


def _format_alignment(
    formats: Mapping[str, Mapping[str, Any]],
    format_name: Any,
) -> str:
    value = formats.get(str(format_name), {}).get("align", "left")
    return str(value) if value in {"left", "center", "right"} else "left"


_display_record = _display_record_shared


def _report_filename(report: Mapping[str, Any], qualifier: str) -> str:
    """Name a downloaded report after what the reader was looking at.

    The record report used to be `f"invoice-{record.get('number', ...)}"` --
    one application's report and one application's field name, in framework
    code, which named every other application's records after an invoice and
    fell back to the primary key the moment an entity had no `number`. Both
    halves are declared metadata: the report's title is what the reader saw on
    screen, and the entity's `display` is how the application says a record
    names itself.

    Lower-cased because it is a filename, and the title is prose.
    """

    title = _safe_filename(str(report.get("title") or "report")).lower()
    return f"{title}-{_safe_filename(qualifier)}"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned or "report"
