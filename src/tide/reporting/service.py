"""Security-aware construction of renderer-neutral report documents."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

from tide.compiler.expressions import evaluate_expression
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data import FilterCondition, QuerySpec, SortField
from tide.runtime import Channel, RequestContext, TideRuntimeError
from tide.runtime.errors import AuthorizationError, ValidationFailed, ValidationIssue
from tide.security import PROTECTED
from tide.services.records import RecordsService

from .document import ReportCell, ReportColumn, ReportDocument, ReportTable, ReportValue


class ReportService:
    """Build reports only from secured application-service projections."""

    def __init__(self, model: ApplicationModel, records: RecordsService) -> None:
        self.model = model
        self.records = records

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
        bands = report["bands"]
        header_text, header_values = self._content_values(
            entity,
            record,
            bands.get("report_header", ()),
            parameter_values,
            report_context,
        )
        record_text, record_values = self._content_values(
            entity,
            record,
            bands.get("record_header", ()),
            parameter_values,
            report_context,
        )
        footer_text, footer_values = self._content_values(
            entity,
            record,
            bands.get("report_footer", ()),
            parameter_values,
            report_context,
        )
        detail = self._detail(
            entity,
            record,
            bands["detail"],
            report_context,
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
        filename_value = record.get("number", record.get(primary_key))
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
            suggested_filename=f"invoice-{_safe_filename(str(filename_value))}",
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
        page = self.records.query_page(
            entity.name,
            QuerySpec(
                filters=_summary_filters(str(query.get("criteria") or ""), parameters),
                sort=tuple(_summary_sort(str(name)) for name in query.get("sort", ())),
                limit=row_limit,
            ),
            report_context,
        )
        if page.next_cursor is not None:
            raise ValueError(
                f"summary report {report_name!r} exceeds its row limit of "
                f"{row_limit}; narrow the report criteria"
            )

        group_definitions = tuple(report.get("group_by", ()))
        aggregate_definitions = tuple(report["aggregates"])
        groups: dict[tuple[Any, ...], list[int | Decimal]] = {}
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
            for index, aggregate in enumerate(aggregate_definitions):
                if aggregate["function"] == "count":
                    values[index] = int(values[index]) + 1
                    continue
                field_name = str(aggregate["field"])
                raw = _read_report_value(entity.name, field_name, record)
                if raw is not None:
                    values[index] = Decimal(values[index]) + Decimal(raw)

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
            ReportColumn(
                str(aggregate["name"]),
                str(aggregate.get("label") or _humanize(str(aggregate["name"]))),
                "right",
            )
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
                    ),
                    _alignment(
                        entity.field(str(group["field"])),
                        self.model.formats,
                        group.get("format"),
                    ),
                )
                for group, value in zip(group_definitions, key)
            )
            aggregate_cells: list[ReportCell] = []
            for aggregate, value in zip(aggregate_definitions, aggregate_values):
                if aggregate["function"] == "count":
                    text = self._format_scalar(value, aggregate.get("format"))
                else:
                    text = self._format_field(
                        entity.field(str(aggregate["field"])),
                        value,
                        report_context,
                        format_name=aggregate.get("format"),
                    )
                aggregate_cells.append(ReportCell(text, "right"))
            rows.append(group_cells + tuple(aggregate_cells))

        now = generated_at or datetime.now(timezone.utc)
        return ReportDocument(
            report=report_name,
            title=str(report["title"]),
            application=self.model.name,
            generated_at=now,
            header_text=(),
            record_values=(),
            detail=ReportTable(columns, tuple(rows)),
            footer_values=(
                ReportValue("Source records", str(len(page.records)), "right"),
            ),
            page_footer_template="Page {page_number} of {page_count}",
            suggested_filename=(
                f"{_safe_filename(report_name.replace('.', '-'))}-"
                f"{now.astimezone(timezone.utc).date().isoformat()}"
            ),
        )

    def _content_values(
        self,
        entity: NormalizedEntity,
        record: Mapping[str, Any],
        items: tuple[Mapping[str, Any], ...],
        parameters: Mapping[str, Any],
        context: RequestContext,
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

    def _format_field(
        self,
        field: NormalizedField,
        value: Any,
        context: RequestContext,
        *,
        format_name: Any = None,
    ) -> str:
        if value is None:
            return ""
        if field.metadata["type"] == "reference" and field.target_entity:
            try:
                related = self.records.get(field.target_entity, value, context)
            except TideRuntimeError:
                return str(value)
            return _display_record(self.model.entity(field.target_entity), related)
        if field.metadata["type"] == "choice":
            return str(value).replace("_", " ").title()
        return self._format_scalar(value, format_name or field.metadata.get("format"))

    def _format_scalar(self, value: Any, format_name: Any = None) -> str:
        if value is None:
            return ""
        configuration = self.model.formats.get(str(format_name), {})
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


def _initial_aggregates(
    aggregates: tuple[Mapping[str, Any], ...],
) -> list[int | Decimal]:
    return [
        0 if aggregate["function"] == "count" else Decimal(0)
        for aggregate in aggregates
    ]


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
    return next(
        name for name, field in entity.fields.items() if field.metadata.get("primary_key")
    )


def _field_label(field: NormalizedField) -> str:
    return str(field.metadata.get("label") or _humanize(field.name))


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _alignment(
    field: NormalizedField,
    formats: Mapping[str, Mapping[str, Any]],
    format_name: Any,
) -> str:
    configured = _format_alignment(formats, format_name or field.metadata.get("format"))
    if configured != "left":
        return configured
    return "right" if field.metadata["type"] in {"integer", "decimal"} else "left"


def _format_alignment(
    formats: Mapping[str, Mapping[str, Any]],
    format_name: Any,
) -> str:
    value = formats.get(str(format_name), {}).get("align", "left")
    return str(value) if value in {"left", "center", "right"} else "left"


def _display_record(entity: NormalizedEntity, values: Mapping[str, Any]) -> str:
    if entity.display:
        try:
            return entity.display.format_map(
                {name: "" if value is None or value is PROTECTED else value for name, value in values.items()}
            )
        except (KeyError, ValueError):
            pass
    primary_key = _primary_key(entity)
    return str(values.get(primary_key, ""))


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned or "report"
