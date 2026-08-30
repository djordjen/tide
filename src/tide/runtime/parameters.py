"""One coercion rule for declared scalar parameters.

Reports and actions declare parameters with the same shape -- a scalar
``type``, ``required``, ``default`` -- and both must accept the string
forms human dialogs collect alongside the typed values programs send.
This module is where that acceptance is defined once. Callers differ only
in the issue ``rule`` they refuse under; the human noun in each message is
derived from it (``report_parameter`` -> "report parameter"), which is
what lets both services share the wording style without sharing a name.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

from tide.runtime.errors import ValidationFailed, ValidationIssue

PARAMETER_TYPES = ("string", "integer", "decimal", "boolean", "date", "datetime")


def coerce_parameters(
    definitions: Mapping[str, Mapping[str, Any]],
    supplied: Mapping[str, Any],
    *,
    rule: str,
) -> dict[str, Any]:
    """Coerce ``supplied`` against ``definitions``, refusing everything at once.

    Unknown names, missing required values, and uncoercible values are
    gathered into one :class:`ValidationFailed` whose issues carry ``rule``
    and the parameter name. Defaults fill absent values and are coerced the
    same way, so a caller always receives every declared name, typed --
    which is also what lets an idempotency fingerprint treat the string
    form and the typed form of one request as the same request.
    """

    noun = rule.replace("_", " ")
    unknown = sorted(set(supplied) - set(definitions))
    issues: list[ValidationIssue] = []
    if unknown:
        issues.append(
            ValidationIssue(
                rule,
                f"unknown {noun} {unknown[0]!r}",
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
                        rule,
                        f"{noun} {name!r} is required",
                        (name,),
                    )
                )
            result[name] = None
            continue
        try:
            result[name] = coerce_parameter(str(definition["type"]), value)
        except (TypeError, ValueError, InvalidOperation):
            issues.append(
                ValidationIssue(
                    rule,
                    f"{noun} {name!r} must be {definition['type']}",
                    (name,),
                )
            )
    if issues:
        raise ValidationFailed(issues)
    return result


def coerce_parameter(field_type: str, value: Any) -> Any:
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
        result = Decimal(str(value))
        # Decimal parses "NaN" and "Infinity", and no criteria can honestly
        # answer them: NaN takes SQLite's float hop and binds as NULL (a
        # silently empty report) and SQL Server refuses the bind outright.
        if not result.is_finite():
            raise TypeError
        return result
    if field_type == "boolean":
        if isinstance(value, bool):
            return value
        # The TUI dialog collects raw text and prompts "true or false";
        # every other type accepts its own string form.
        if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
            return value.strip().casefold() == "true"
        raise TypeError
    if field_type == "date":
        if isinstance(value, datetime):
            raise TypeError
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if field_type == "datetime":
        if isinstance(value, date) and not isinstance(value, datetime):
            raise TypeError
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    raise ValueError(field_type)
