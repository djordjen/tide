"""Declared state transitions, and the guards the compiler derives from them.

One business rule used to be written three times in `sales.Invoice`: the state
half of `post`'s `enabled_when`, four copies of `immutable_when: "status !=
'draft'"`, and a hand-rolled status check inside `actions.post_invoice`.
`applications/contacts` then reproduced the whole shape by hand. Nothing could
notice that `cancelled` was a declared state no action could reach, so the
seeded cancelled invoice was a record the application could neither produce
nor leave.

A transition names the machine once. The compiler derives the guards from it
and refuses the states that cannot be reached.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import CompilationFailed, compile_project

ROOT = Path(__file__).parents[1]

HANDLERS = "def run(record, context, payload):\n    return record\n"

BASE_FIELDS = [
    "  id: {type: integer, primary_key: true}",
    "  title: {type: string, length: 40}",
]

STATUS_FIELD = [
    "  status:",
    "    type: choice",
    "    choices: [draft, posted, cancelled]",
    "    default: draft",
    "    readonly: true",
    "    write: action_only",
    "  posted_at: {type: datetime, readonly: true, write: action_only}",
    "  posted_by: {type: string, length: 120, readonly: true, write: action_only}",
]


def _project(tmp_path: Path, name: str, entity_lines: list[str]) -> Path:
    project = tmp_path / name
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                f"application: {{name: {name}, version: 0.1.0}}",
                "model: {paths: [models]}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "handlers.py").write_text(HANDLERS, encoding="utf-8")
    (models / "entity.yaml").write_text("\n".join(entity_lines) + "\n", encoding="utf-8")
    return project


def _entity(
    *,
    fields: list[str],
    actions: list[str],
) -> list[str]:
    return ["entity: demo.Thing", "fields:", *fields, "actions:", *actions]


def _codes(project: Path) -> dict[str, str]:
    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)
    return {
        diagnostic.code: diagnostic.message for diagnostic in caught.value.diagnostics
    }


POST_AND_VOID = [
    "  post:",
    "    label: Post",
    "    unrestricted: true",
    "    execute: handlers.run",
    "    transition:",
    "      field: status",
    "      from: draft",
    "      to: posted",
    "      locks_record: true",
    "      stamp: {posted_at: now, posted_by: principal}",
    "  void:",
    "    label: Void",
    "    unrestricted: true",
    "    execute: handlers.run",
    "    transition:",
    "      field: status",
    "      from: draft",
    "      to: cancelled",
    "      locks_record: true",
]


def test_the_state_guard_is_derived_from_the_transition(tmp_path: Path) -> None:
    """`enabled_when` no longer restates what `from:` already says."""

    project = _project(
        tmp_path,
        "derived-guard",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=POST_AND_VOID),
    )

    model = compile_project(project)
    actions = model.entity("demo.Thing").actions

    assert actions["post"]["enabled_when"] == "status == 'draft'"
    assert actions["void"]["enabled_when"] == "status == 'draft'"


def test_an_authored_condition_is_kept_and_combined_with_the_state_guard(
    tmp_path: Path,
) -> None:
    """The author still writes the business half; only the state half is derived."""

    actions = list(POST_AND_VOID)
    actions.insert(3, '    enabled_when: "title != \'\'"')
    project = _project(
        tmp_path,
        "combined-guard",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    model = compile_project(project)

    assert model.entity("demo.Thing").actions["post"]["enabled_when"] == (
        "status == 'draft' and (title != '')"
    )


def test_a_locking_transition_derives_immutability_for_writable_fields(
    tmp_path: Path,
) -> None:
    """Four hand-written copies of one predicate become one declaration.

    Every state some `locks_record` transition leads to is a locked state, and
    every ordinarily writable field is frozen in those states. Fields the
    workflow already owns -- `action_only`, `system`, computed, read-only --
    are not touched, because their writability was never the question.
    """

    project = _project(
        tmp_path,
        "derived-immutability",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=POST_AND_VOID),
    )

    model = compile_project(project)
    entity = model.entity("demo.Thing")

    assert entity.field("title").metadata["immutable_when"] == (
        "status == 'cancelled' or status == 'posted'"
    )
    assert "immutable_when" not in entity.field("status").metadata
    assert "immutable_when" not in entity.field("posted_at").metadata


def test_a_transition_that_does_not_lock_leaves_fields_writable(
    tmp_path: Path,
) -> None:
    """Contacts archives without freezing; deriving there would change it."""

    actions = [
        "  archive:",
        "    label: Archive",
        "    unrestricted: true",
        "    execute: handlers.run",
        "    transition: {field: status, from: draft, to: posted}",
        "  void:",
        "    label: Void",
        "    unrestricted: true",
        "    execute: handlers.run",
        "    transition: {field: status, from: draft, to: cancelled}",
    ]
    project = _project(
        tmp_path,
        "no-locking",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    model = compile_project(project)

    assert "immutable_when" not in model.entity("demo.Thing").field("title").metadata


def test_a_declared_state_no_transition_reaches_is_refused(tmp_path: Path) -> None:
    """The defect this was built for.

    `sales.Invoice` declared `cancelled`, `status` was `write: action_only`, and
    the only action produced `posted`. The state was unreachable through the
    application while `demo_data` seeded a record already in it: a row that
    could not be posted, because it was not a draft, and could not be edited,
    because it was not a draft either.
    """

    actions = POST_AND_VOID[:10]
    project = _project(
        tmp_path,
        "unreachable-state",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    codes = _codes(project)

    assert "TIDE274" in codes
    assert "cancelled" in codes["TIDE274"]


def test_the_initial_state_counts_as_reached(tmp_path: Path) -> None:
    """`draft` is reachable by being the default, not by any transition."""

    fields = [
        *BASE_FIELDS,
        "  status:",
        "    type: choice",
        "    choices: [draft, posted]",
        "    default: draft",
        "    readonly: true",
        "    write: action_only",
        "  posted_at: {type: datetime, readonly: true, write: action_only}",
        "  posted_by: {type: string, length: 120, readonly: true, write: action_only}",
    ]
    project = _project(
        tmp_path, "initial-state", _entity(fields=fields, actions=POST_AND_VOID[:10])
    )

    model = compile_project(project)

    assert model.entity("demo.Thing").actions["post"]["enabled_when"] == (
        "status == 'draft'"
    )


def test_a_transition_over_something_that_is_not_a_choice_is_refused(
    tmp_path: Path,
) -> None:
    actions = [
        "  post:",
        "    label: Post",
        "    unrestricted: true",
        "    execute: handlers.run",
        "    transition: {field: title, from: draft, to: posted}",
    ]
    project = _project(
        tmp_path,
        "not-a-choice",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    assert "TIDE270" in _codes(project)


def test_a_transition_naming_an_undeclared_state_is_refused(tmp_path: Path) -> None:
    actions = [
        "  post:",
        "    label: Post",
        "    unrestricted: true",
        "    execute: handlers.run",
        "    transition: {field: status, from: draft, to: shipped}",
    ]
    project = _project(
        tmp_path,
        "unknown-state",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    codes = _codes(project)

    assert "TIDE271" in codes
    assert "shipped" in codes["TIDE271"]


def test_a_state_field_an_ordinary_write_could_change_is_refused(
    tmp_path: Path,
) -> None:
    """A machine nobody has to go through is not a machine."""

    fields = [
        *BASE_FIELDS,
        "  status:",
        "    type: choice",
        "    choices: [draft, posted, cancelled]",
        "    default: draft",
        "  posted_at: {type: datetime, readonly: true, write: action_only}",
        "  posted_by: {type: string, length: 120, readonly: true, write: action_only}",
    ]
    project = _project(
        tmp_path, "writable-state", _entity(fields=fields, actions=POST_AND_VOID)
    )

    assert "TIDE272" in _codes(project)


def test_a_state_field_without_an_initial_state_is_refused(tmp_path: Path) -> None:
    fields = [
        *BASE_FIELDS,
        "  status:",
        "    type: choice",
        "    choices: [draft, posted, cancelled]",
        "    write: action_only",
        "  posted_at: {type: datetime, readonly: true, write: action_only}",
        "  posted_by: {type: string, length: 120, readonly: true, write: action_only}",
    ]
    project = _project(
        tmp_path, "no-initial-state", _entity(fields=fields, actions=POST_AND_VOID)
    )

    assert "TIDE272" in _codes(project)


def test_writing_a_guard_the_compiler_derives_is_refused(tmp_path: Path) -> None:
    """The duplication is the defect, so restating it is the thing to refuse.

    Accepting both and checking they agree would leave two places to edit and
    a rule that has to be maintained; refusing the second leaves one.
    """

    actions = list(POST_AND_VOID)
    actions.insert(3, "    enabled_when: \"status == 'draft'\"")
    project = _project(
        tmp_path,
        "restated-guard",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    codes = _codes(project)

    assert "TIDE273" in codes
    assert "status" in codes["TIDE273"]


def test_writing_an_immutability_a_locking_transition_derives_is_refused(
    tmp_path: Path,
) -> None:
    fields = [
        *BASE_FIELDS[:1],
        "  title: {type: string, length: 40, immutable_when: \"status != 'draft'\"}",
        *STATUS_FIELD,
    ]
    project = _project(
        tmp_path, "restated-immutability", _entity(fields=fields, actions=POST_AND_VOID)
    )

    assert "TIDE273" in _codes(project)


def test_a_stamp_naming_a_field_the_entity_does_not_have_is_refused(
    tmp_path: Path,
) -> None:
    actions = list(POST_AND_VOID)
    actions[9] = "      stamp: {closed_at: now}"
    project = _project(
        tmp_path,
        "unknown-stamp",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    codes = _codes(project)

    assert "TIDE275" in codes
    assert "closed_at" in codes["TIDE275"]


def test_a_stamp_of_the_wrong_type_for_its_value_is_refused(tmp_path: Path) -> None:
    """`now` needs a datetime and `principal` a string; a swap is silent otherwise."""

    actions = list(POST_AND_VOID)
    actions[9] = "      stamp: {posted_at: principal, posted_by: now}"
    project = _project(
        tmp_path,
        "mistyped-stamp",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    assert "TIDE275" in _codes(project)


def test_a_stamp_an_ordinary_write_could_forge_is_refused(tmp_path: Path) -> None:
    fields = [
        *BASE_FIELDS,
        "  status:",
        "    type: choice",
        "    choices: [draft, posted, cancelled]",
        "    default: draft",
        "    write: action_only",
        "  posted_at: {type: datetime}",
        "  posted_by: {type: string, length: 120, readonly: true, write: action_only}",
    ]
    project = _project(
        tmp_path, "writable-stamp", _entity(fields=fields, actions=POST_AND_VOID)
    )

    assert "TIDE275" in _codes(project)


def test_an_action_named_after_a_form_button_is_refused(tmp_path: Path) -> None:
    """Found by naming the new invoice action `cancel` and looking.

    A view's `actions:` list mixes domain actions with the action bar's
    built-in `cancel` and `save`, and every renderer resolves those two names
    as the built-ins. A domain action sharing one is filtered out of every
    form while still appearing over REST and MCP -- so it looks implemented
    everywhere except the screen anyone would use.
    """

    actions = [
        "  cancel:",
        "    label: Cancel",
        "    unrestricted: true",
        "    execute: handlers.run",
        "    transition: {field: status, from: draft, to: cancelled}",
    ]
    project = _project(
        tmp_path,
        "reserved-action-name",
        _entity(fields=[*BASE_FIELDS, *STATUS_FIELD], actions=actions),
    )

    codes = _codes(project)

    assert "TIDE276" in codes
    assert "cancel" in codes["TIDE276"]


def test_the_reference_application_declares_its_machine(tmp_path: Path) -> None:
    """Invoicing is the proof, and its `cancelled` state is now reachable."""

    del tmp_path
    model = compile_project(ROOT / "applications" / "invoicing")
    invoice = model.entity("sales.Invoice")

    assert invoice.actions["post"]["transition"]["to"] == "posted"
    assert invoice.actions["void"]["transition"]["to"] == "cancelled"
    assert invoice.actions["post"]["enabled_when"] == (
        "status == 'draft' and (count(lines) > 0)"
    )
    locked = "status == 'cancelled' or status == 'posted'"
    for field_name in ("invoice_date", "currency", "customer", "lines"):
        assert invoice.field(field_name).metadata["immutable_when"] == locked
