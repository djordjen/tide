"""How one record names itself.

Deliberately low in the stack, and for the same reason `labels` is: services
resolve a page of references and every renderer draws them, so this cannot
sit under either. Importing it must not reach `tide.data`, whose package
import runs the adapters and comes back through `tide.services`.
"""

from __future__ import annotations

from string import Formatter
from typing import Any, Mapping

from tide.compiler.normalized import NormalizedEntity
from tide.security.engine import PROTECTED


def record_display(
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> str:
    """Render how one record of ``entity`` names itself.

    The entity's ``display`` is the application's own answer -- a field name,
    or a template over several -- and the primary key is the fallback, because
    every record has one and no reader is helped by a blank.

    This is what a renderer shows for a reference, a window title, or a
    downloaded file. `record_label` is the other question: that names the
    *type* ("Invoice"), this names the *record* ("INV-2026-0001").
    """

    primary_key = entity.primary_key.name
    if not entity.display:
        return str(values.get(primary_key, ""))
    if "{" not in entity.display:
        return display_value(values.get(entity.display))
    try:
        return entity.display.format_map(
            {name: display_value(value) for name, value in values.items()}
        )
    except (KeyError, ValueError):
        # A template naming a field this principal cannot read, or malformed
        # in a way the compiler did not catch. The key still identifies the
        # record, which is the point of showing anything at all.
        return str(values.get(primary_key, ""))


def display_fields(entity: NormalizedEntity) -> tuple[str, ...]:
    """Return the fields an entity's ``display`` reads to name a record.

    A bare field name is a template of one. An empty result means there is
    nothing to render -- no display at all, or one malformed in a way the
    compiler let through -- and every caller treats those the same way,
    by falling back to the primary key.
    """

    if not entity.display:
        return ()
    if "{" not in entity.display:
        return (entity.display,)
    try:
        return tuple(
            name for _, name, _, _ in Formatter().parse(entity.display) if name
        )
    except ValueError:
        return ()


def display_value(value: Any) -> str:
    """Render one stored value for display, without leaking a withheld one."""

    if value is PROTECTED:
        return "Protected"
    return "" if value is None else str(value)
