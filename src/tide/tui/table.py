"""Shared semantic alignment for Textual data tables."""

from __future__ import annotations

from typing import Any, Mapping

from rich.text import Text

from tide.compiler.normalized import NormalizedField
from tide.presentation import field_alignment


def table_cell(
    field: NormalizedField,
    value: str,
    formats: Mapping[str, Mapping[str, Any]],
) -> str | Text:
    """Return a cell aligned the way every other renderer aligns it.

    ``formats`` is required rather than defaulted. This module used to justify
    on the field type alone and ignore a format's explicit ``align``, and a
    default would leave that same mistake one forgotten argument away.
    """

    alignment = field_alignment(field, formats)
    if alignment == "left":
        return value
    return Text(value, justify=alignment, no_wrap=True)


def table_label(
    field: NormalizedField,
    value: str,
    formats: Mapping[str, Mapping[str, Any]],
) -> Text:
    """Return a column label aligned with the values beneath it."""

    return Text(value, justify=field_alignment(field, formats), no_wrap=True)
