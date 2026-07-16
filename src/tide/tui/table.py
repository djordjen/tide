"""Shared semantic alignment for Textual data tables."""

from __future__ import annotations

from rich.text import Text

from tide.compiler.normalized import NormalizedField

_NUMERIC_TYPES = frozenset({"integer", "decimal"})


def table_cell(field: NormalizedField, value: str) -> str | Text:
    """Return a cell aligned according to its model field type."""
    if field.metadata["type"] in _NUMERIC_TYPES:
        return Text(value, justify="right", no_wrap=True)
    return value


def table_label(field: NormalizedField, value: str) -> Text:
    """Return a column label aligned with the values beneath it."""
    justify = "right" if field.metadata["type"] in _NUMERIC_TYPES else "left"
    return Text(value, justify=justify, no_wrap=True)
