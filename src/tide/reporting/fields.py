"""Formatting one stored value the way every document shows it.

Reports and browse exports both turn a record into text, and if they did it
twice they would eventually do it differently -- a decimal rounded one way
here and another there, a reference named by its identity in one file and by
its display in the other. They ask this instead.

One deliberate difference from the reporting fork this was extracted from: a
protected value answers as blank rather than falling through to `str(value)`.
Reports never met the sentinel, because they format declared report columns; a
browse view can name a field a field policy protects, and the fallback would
have written `ProtectedValue` into a cell.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
)
from tide.labels import value_label
from tide.runtime import RequestContext, TideRuntimeError
from tide.security import PROTECTED
from tide.services import NO_REFERENCE_DISPLAYS, ReferenceDisplays
from tide.services.records import RecordsService


class FieldFormatter:
    """Turn one secured value into the text every renderer shows."""

    def __init__(self, model: ApplicationModel, records: RecordsService) -> None:
        self.model = model
        self.records = records

    def field(
        self,
        field: NormalizedField,
        value: Any,
        context: RequestContext,
        *,
        format_name: Any = None,
        references: ReferenceDisplays = NO_REFERENCE_DISPLAYS,
    ) -> str:
        if value is None or value is PROTECTED:
            return ""
        if field.metadata["type"] == "reference" and field.target_entity:
            resolved = references.display(field.target_entity, value)
            if resolved is not None:
                return resolved
            try:
                related = self.records.get(field.target_entity, value, context)
            except TideRuntimeError:
                return str(value)
            return display_record(self.model.entity(field.target_entity), related)
        if field.metadata["type"] == "choice":
            return value_label(value)
        return self.scalar(value, format_name or field.metadata.get("format"))

    def scalar(self, value: Any, format_name: Any = None) -> str:
        if value is None or value is PROTECTED:
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


def display_record(
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> str:
    """Name one record the way its entity says it names itself."""

    if entity.display:
        try:
            return entity.display.format_map(
                {
                    name: "" if value is None or value is PROTECTED else value
                    for name, value in values.items()
                }
            )
        except (KeyError, ValueError):
            pass
    return str(values.get(entity.primary_key.name, ""))


TYPED_FIELD_TYPES = frozenset(
    {"integer", "decimal", "date", "datetime", "boolean"}
)
"""Column types a typed format should hold as values rather than as text.

Everything else is already text by the time it is worth reading: a reference
names a record, a choice is captioned rather than coded, and a string is a
string. Sending their stored values instead would put an identity where a
customer's name belongs.
"""


def typed_cell(field: NormalizedField, value: Any) -> Any:
    """The value a typed format should hold, or None to use the text.

    Decided by the column rather than by the value, which is the whole point:
    a reference stores an integer identity, and typing it by value would put
    `1` in the cell where every renderer shows `ACME - ACME Ltd`.
    """

    if value is None or str(field.metadata["type"]) not in TYPED_FIELD_TYPES:
        return None
    if isinstance(value, (bool, int, Decimal, datetime, date)):
        return value
    return None


def typed_number(value: Any) -> Any:
    """The value an aggregate should hold, decided by the value itself.

    Safe here where `typed_cell` would not be: `_initial_aggregates` seeds
    every report aggregate as `0` or `Decimal(0)`, so an aggregate is always a
    number and can never be a reference or a choice.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        return value
    return None
