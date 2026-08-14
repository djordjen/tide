"""Display labels derived from model identifiers.

Deliberately dependency-free: the compiler names entities and fields while
building the model, and every renderer names them again afterwards, so this
cannot sit under either of them.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def humanize(value: str) -> str:
    """Turn an identifier into the label a person reads.

    Models mix all three naming styles -- `unit_price`, `posted-at`, and
    `customerName` -- so one transform has to handle each. Copies of this used
    to disagree about camel case and hyphens, which meant the same field could
    be labelled two ways on two screens of the same application.
    """

    spaced = _CAMEL_BOUNDARY.sub(" ", value)
    return spaced.replace("_", " ").replace("-", " ").strip().title()


def humanize_qualified(name: str) -> str:
    """Label a dotted model name, ignoring its namespace."""

    return humanize(name.rsplit(".", 1)[-1])


def value_label(value: object) -> str:
    """Label a value an application author wrote, such as a choice literal.

    Kept separate from `humanize` deliberately. `humanize` names identifiers
    TIDE exposes, where splitting `customerName` into "Customer Name" is the
    whole point; a stored `in_progress` is the application's own data, and how
    it reads should not depend on which renderer is asking. Eight copies of
    this expression lived across Qt, Textual and reporting and agreed entirely
    by coincidence -- exactly as the field-label transform beside them did,
    right up until one of them met a camelCase field name.

    The rule is narrow: underscores become spaces and `title()` capitalises.
    It does not split camel case, so a literal written `inProgress` reads
    "Inprogress". That is carried over from the copies rather than endorsed --
    changing it changes what deployed applications display, which is a
    decision for a model author and not a side effect of removing duplication.
    """

    return str(value).replace("_", " ").title()


def declared_values(metadata: Mapping[str, Any]) -> tuple[tuple[Any, str], ...]:
    """The `(code, caption)` pairs a field declares, or none at all.

    One reader for the whole framework. The boundary refuses an uncaptioned
    code through it, every renderer shows a caption through it, and the
    dropdowns are built from it -- which is the arrangement the choice-value
    transform above arrived at the hard way, after eight copies agreed only by
    coincidence.
    """

    return tuple(
        (item["value"], str(item["label"])) for item in metadata.get("values", ())
    )


def value_caption(metadata: Mapping[str, Any], value: Any) -> str | None:
    """What a stored code stands for, or None when nothing claims it.

    An uncaptioned code is shown as itself rather than blanked: a legacy
    column will hold values nobody wrote down, and hiding one loses the only
    evidence that it is there.
    """

    for code, caption in declared_values(metadata):
        if code == value and type(code) is type(value):
            return caption
    return None
