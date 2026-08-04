"""Display labels derived from model identifiers.

Deliberately dependency-free: the compiler names entities and fields while
building the model, and every renderer names them again afterwards, so this
cannot sit under either of them.
"""

from __future__ import annotations

import re

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
