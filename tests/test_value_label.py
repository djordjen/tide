"""One transform names a stored value, and every renderer reaches for that one.

`str(value).replace("_", " ").title()` was written out twelve times across Qt,
Textual, reporting and the studios. They agreed, which is what made it easy to
leave alone -- and is exactly how the field-label transform looked before one
copy met a camelCase field name and started disagreeing with the other six.
Two of these twelve had already drifted the same way.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tide.labels import humanize, value_label

ROOT = Path(__file__).parents[1]

def _consumers() -> tuple[str, ...]:
    """Every module that displays a stored choice value, found not listed.

    A hand-kept list goes stale the moment a renderer is split into more
    modules: `tide.qt.app` stayed here after the code that called this moved
    to `tide.qt.form`, so the guard was asking a module about a function it no
    longer contained. Deriving it means the guard follows the code.
    """

    modules = []
    for path in sorted((ROOT / "src" / "tide").rglob("*.py")):
        if path.name == "labels.py":
            continue
        if "value_label" not in path.read_text(encoding="utf-8"):
            continue
        parts = path.relative_to(ROOT / "src").with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.append(".".join(parts))
    return tuple(modules)


CONSUMERS = _consumers()


def test_the_guard_still_has_something_to_guard() -> None:
    """A derived list that quietly emptied would pass everything below."""

    families = {name.split(".")[1] for name in CONSUMERS}
    assert {"qt", "reporting", "tui"} <= families, CONSUMERS


@pytest.mark.parametrize("module_name", CONSUMERS)
def test_every_renderer_names_the_one_implementation(module_name: str) -> None:
    """Identity, not equality.

    Two modules producing the same string prove nothing -- twelve copies did
    that for months. What has to hold is that there is one function.
    """

    if module_name.startswith("tide.qt"):
        pytest.importorskip("PySide6")
    module = importlib.import_module(module_name)

    assert module.value_label is value_label


def test_no_module_carries_its_own_copy() -> None:
    """The duplication itself is the defect, so the guard is against its shape."""

    expression = 'replace("_", " ").title()'
    offenders = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "src" / "tide").rglob("*.py")
        if expression in path.read_text(encoding="utf-8")
        and path.name != "labels.py"
    )

    assert offenders == []


def test_a_stored_value_reads_as_words() -> None:
    assert value_label("in_progress") == "In Progress"
    assert value_label("draft") == "Draft"


def test_a_stored_value_is_not_split_on_camel_case() -> None:
    """The one place this deliberately differs from `humanize`.

    `humanize` names identifiers TIDE exposes, where splitting is the point.
    This names the application's own data. The result for a camelCase literal
    is admittedly poor -- "Inprogress" -- but it is what every one of the
    copies produced, and changing what deployed applications display is a
    model author's call, not a side effect of removing duplication.
    """

    assert value_label("inProgress") == "Inprogress"
    assert humanize("inProgress") == "In Progress"


def test_a_non_string_value_is_still_labelled() -> None:
    """Choices are not always strings; `str()` is inside, not at each caller."""

    assert value_label(3) == "3"
    assert value_label(None) == "None"
