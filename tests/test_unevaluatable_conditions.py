"""The service is the authority a failed condition falls back to.

`field_is_immutable` and `action_state` withhold what they cannot judge, and
their docstrings promise the service decides. Deciding means refusing in the
contract's vocabulary: a condition the service cannot evaluate at commit or
execution is a refusal shaped like every other refusal, not an evaluator
exception escaping as a server fault. Division supplies the case, because it
is in the expression subset and a zero divisor is data TIDE did not write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.runtime import (
    ActionDisabled,
    Channel,
    ImmutableFieldError,
    Principal,
    RequestContext,
)
from tide.services import ActionService, RecordsService

MANIFEST = (
    'schema_version: "0.1"\n'
    "application: {name: Gauges, version: 0.1.0}\n"
    "database: {mode: managed}\n"
    "model: {paths: [models]}\n"
    "security: {paths: [security]}\n"
)

POLICIES = (
    "permissions:\n- demo.all\nroles:\n  operator:\n    grants:\n    - demo.all\n"
)

MODEL = (
    "entity: demo.Gauge\n"
    "display: name\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all, delete: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  name: {type: string, length: 40, required: true,"
    ' immutable_when: "100 / factor > 1"}\n'
    "  factor: {type: integer}\n"
    "actions:\n"
    "  calibrate:\n"
    "    label: Calibrate\n"
    "    unrestricted: true\n"
    "    execute: handlers.run\n"
    '    enabled_when: "100 / factor > 1"\n'
)

HANDLERS = "def run(record, context, payload):\n    return record\n"


def _services(
    tmp_path: Path,
) -> tuple[RecordsService, ActionService, RequestContext]:
    project = tmp_path / "gauges"
    for relative, text in (
        ("tide.yaml", MANIFEST),
        ("security/policies.yaml", POLICIES),
        ("models/gauge.yaml", MODEL),
        ("handlers.py", HANDLERS),
    ):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    model = compile_project(project)
    repository = InMemoryRepository()
    repository.seed(
        "demo.Gauge",
        (
            {"id": 1, "name": "Alpha", "factor": 0},
            {"id": 2, "name": "Beta", "factor": 200},
        ),
    )
    records = RecordsService(model, repository)
    context = RequestContext(
        principal=Principal("p", roles=frozenset({"operator"})),
        channel=Channel.TUI,
    )
    return records, ActionService(model, records), context


def test_commit_refuses_a_lock_condition_it_cannot_evaluate(
    tmp_path: Path,
) -> None:
    """Presentation predicted "locked" for this record; commit must agree.

    `field_is_immutable` already withholds the edit when the condition
    cannot answer, so the renderer never offers it. A raw API caller who
    tries anyway gets the same refusal the renderer predicted, not a 500.
    """

    records, _, context = _services(tmp_path)

    session = records.begin_edit("demo.Gauge", 1, context)
    session.set("name", "Renamed")

    with pytest.raises(ImmutableFieldError, match="could not be evaluated"):
        records.commit(session, context)


def test_commit_honours_a_lock_condition_that_answers_no(
    tmp_path: Path,
) -> None:
    records, _, context = _services(tmp_path)

    session = records.begin_edit("demo.Gauge", 2, context)
    session.set("name", "Renamed")
    records.commit(session, context)

    assert records.begin_edit("demo.Gauge", 2, context).original["name"] == (
        "Renamed"
    )


def test_an_action_condition_that_cannot_be_evaluated_disables_it(
    tmp_path: Path,
) -> None:
    records, actions, context = _services(tmp_path)

    with pytest.raises(ActionDisabled, match="could not be evaluated"):
        actions.execute("demo.Gauge", "calibrate", 1, {}, context)


def test_an_action_condition_that_answers_no_disables_it_plainly(
    tmp_path: Path,
) -> None:
    records, actions, context = _services(tmp_path)

    with pytest.raises(ActionDisabled) as caught:
        actions.execute("demo.Gauge", "calibrate", 2, {}, context)

    assert "could not be evaluated" not in str(caught.value)
