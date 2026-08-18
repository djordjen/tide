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


EMPHASIS_STYLES: Mapping[str, str] = {
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "danger": "red",
    "muted": "dim",
}
"""What an `appearance:` emphasis is, in the only palette a terminal has.

The browser draws the same verdict as a left edge and a wash. Neither is the
author's to choose, which is the point of naming the meaning rather than the
colour: a hex value that suits a light theme has nothing to say here.
"""


def emphasized(cell: str | Text, emphasis: str | None) -> str | Text:
    """Return the cell wearing the style an appearance rule asked for.

    An emphasis this terminal has no style for is left alone rather than
    rendered as nothing, the same way the browser drops one it cannot draw.
    """

    style = EMPHASIS_STYLES.get(emphasis or "")
    if not style:
        return cell
    text = cell.copy() if isinstance(cell, Text) else Text(cell, no_wrap=True)
    text.stylize(style)
    return text
