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
