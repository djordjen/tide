"""One answer to "may this be edited, and may this action be offered".

Every renderer asks the same two questions of the same compiled metadata, and
every renderer used to answer them itself. The answers had drifted: only the
REST projection survived a condition it could not evaluate, and only the
Textual adapter would call an action enabled while hiding it.

None of this authorizes anything. The service re-checks `enabled_when` and
`immutable_when` at commit; these decide what a renderer offers.
"""

from __future__ import annotations

import pytest

from tide.compiler.normalized import NormalizedField, immutable_mapping
from tide.presentation import (
    action_label,
    action_state,
    field_is_immutable,
    record_appearance,
)


def _field(name: str, **metadata: object) -> NormalizedField:
    return NormalizedField(name=name, metadata=immutable_mapping(dict(metadata)))


def test_an_action_is_enabled_only_while_it_is_visible() -> None:
    """The Textual adapter evaluated the two conditions independently.

    Offering an action that is hidden is a contradiction, and the two
    conditions routinely disagree: `visible_when: status == 'draft'` with
    `enabled_when: total > 0` enables a posted invoice's hidden Post button.
    """

    action = {"visible_when": "status == 'draft'", "enabled_when": "total > 0"}
    posted = {"status": "posted", "total": 10}

    state = action_state(action, posted)

    assert state.visible is False
    assert state.enabled is False


def test_an_action_condition_that_cannot_be_evaluated_hides_it() -> None:
    """Guessing "yes" offers a button the commit will refuse."""

    action = {"enabled_when": "total > 0"}

    state = action_state(action, {})

    assert state.visible is False
    assert state.enabled is False


def test_an_unconditional_action_is_offered() -> None:
    state = action_state({}, {})

    assert state.visible is True
    assert state.enabled is True


def test_a_field_condition_that_cannot_be_evaluated_locks_the_field() -> None:
    """Withholding an allowed edit beats offering one the service rejects."""

    field = _field("number", type="string", immutable_when="status != 'draft'")

    assert field_is_immutable(field, {}) is True


def test_a_field_without_a_condition_is_never_locked_by_one() -> None:
    assert field_is_immutable(_field("number", type="string"), {}) is False


def test_a_field_is_locked_only_while_its_condition_holds() -> None:
    field = _field("number", type="string", immutable_when="status != 'draft'")

    assert field_is_immutable(field, {"status": "draft"}) is False
    assert field_is_immutable(field, {"status": "posted"}) is True


def test_the_first_matching_rule_owns_a_target() -> None:
    """Declaration order is the precedence, so it is read off the page.

    Two rules routinely match one record -- `cancelled` and `overdue` both
    describe an invoice nobody is going to pay -- and a renderer can only
    show one. Numbered priorities would put the answer somewhere other than
    where the rules are written.
    """

    rules = (
        {"name": "cancelled", "when": "status == 'cancelled'", "emphasis": "muted"},
        {"name": "large", "when": "total > 100", "emphasis": "warning"},
    )

    appearance = record_appearance(rules, {"status": "cancelled", "total": 900})

    assert appearance.record == "muted"


def test_a_rule_that_names_fields_marks_those_and_not_the_record() -> None:
    rules = (
        {
            "name": "large",
            "when": "total > 100",
            "emphasis": "warning",
            "fields": ("total",),
        },
    )

    appearance = record_appearance(rules, {"total": 900})

    assert appearance.record is None
    assert appearance.fields == {"total": "warning"}


def test_a_condition_that_cannot_be_evaluated_paints_nothing() -> None:
    """The opposite of `immutable_when`, and both fail safe for what they are.

    Withholding an edit is caution. Painting a record a colour that means
    something it is not is a lie about the data, so an unreadable condition
    leaves the record as it was.
    """

    rules = ({"name": "large", "when": "total > 100", "emphasis": "warning"},)

    appearance = record_appearance(rules, {})

    assert appearance.record is None
    assert bool(appearance) is False


def test_an_entity_declaring_no_rules_carries_no_appearance() -> None:
    assert bool(record_appearance((), {"status": "draft"})) is False


def test_an_action_label_humanizes_like_every_other_name() -> None:
    """Qt and the Textual form each ran `str.title()` over the raw name."""

    assert action_label("postInvoice", {}) == "Post Invoice"
    assert action_label("post_invoice", {}) == "Post Invoice"
    assert action_label("post_invoice", {"label": "Post it"}) == "Post it"


@pytest.mark.parametrize(
    "module_path, attribute",
    [
        ("tide.api.server", "_action_state"),
        ("tide.tui.form", "_action_state"),
    ],
)
def test_every_renderer_gates_an_action_through_one_implementation(
    module_path: str,
    attribute: str,
) -> None:
    from importlib import import_module

    assert getattr(import_module(module_path), attribute) is action_state


@pytest.mark.parametrize(
    "module_path",
    ["tide.api.server", "tide.tui.form"],
)
def test_every_renderer_locks_a_field_through_one_implementation(
    module_path: str,
) -> None:
    from importlib import import_module

    assert getattr(import_module(module_path), "_field_is_immutable") is (
        field_is_immutable
    )
