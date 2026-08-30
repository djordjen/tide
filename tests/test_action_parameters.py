"""Declared action parameters: the compiled shape, the gate, the service rule.

An action's `parameters:` block reuses the report parameter declaration --
the same scalar types, `required`, `default` -- and lands in the compiled
action metadata under the names it was written with. The compiler's only
own rule is that a name must be a plain identifier, because each one
becomes a field on the generated MCP tool arguments model.

The service owns the payload for every door: it types the values before
the handler or the idempotency fingerprint sees them, refuses everything
at once under `action_parameter`, and holds an undeclared action to the
empty payload the transports used to enforce one by one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tide import CompilationFailed, compile_project
from tide.data import InMemoryRepository
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.errors import IdempotencyConflict, ValidationFailed
from tide.services import ActionService, RecordsService

HANDLERS = "def run(record, context, payload):\n    return record\n"

BASE_LINES = [
    "entity: demo.Thing",
    "fields:",
    "  id: {type: integer, primary_key: true}",
    "  title: {type: string, length: 40}",
    "actions:",
    "  archive:",
    "    label: Archive",
    "    unrestricted: true",
    "    execute: handlers.run",
]


def _project(tmp_path: Path, name: str, entity_lines: list[str]) -> Path:
    project = tmp_path / name
    models = project / "models"
    security = project / "security"
    models.mkdir(parents=True)
    security.mkdir()
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                f"application: {{name: {name}, version: 0.1.0}}",
                "model: {paths: [models]}",
                "security: {paths: [security]}",
            ]
        ),
        encoding="utf-8",
    )
    (security / "policies.yaml").write_text(
        "permissions:\n- demo.all\nroles:\n  operator:\n    grants:\n    - demo.all\n",
        encoding="utf-8",
    )
    (project / "handlers.py").write_text(HANDLERS, encoding="utf-8")
    (models / "entity.yaml").write_text(
        "\n".join(entity_lines) + "\n", encoding="utf-8"
    )
    return project


def test_declared_parameters_reach_the_compiled_action(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        "declared-parameters",
        [
            *BASE_LINES,
            "    parameters:",
            "      reason: {type: string, required: true}",
            "      occurred_at: {type: datetime}",
        ],
    )

    model = compile_project(project)
    action = model.entity("demo.Thing").actions["archive"]

    assert action["parameters"] == {
        "reason": {"type": "string", "required": True},
        "occurred_at": {"type": "datetime", "required": False},
    }


def test_an_action_without_the_block_compiles_to_no_parameters(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "no-parameters", BASE_LINES)

    model = compile_project(project)
    action = model.entity("demo.Thing").actions["archive"]

    assert action.get("parameters", {}) == {}


PARAMETRIZED_ENTITY = [
    "entity: demo.Thing",
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all}",
    "fields:",
    "  id: {type: integer, primary_key: true}",
    "  title: {type: string, length: 40}",
    "actions:",
    "  archive:",
    "    label: Archive",
    "    unrestricted: true",
    "    execute: handlers.run",
    "    idempotent: true",
    "    parameters:",
    "      reason: {type: string, required: true}",
    "      occurred_at: {type: datetime}",
    "      mood: {type: string, default: calm}",
    "  touch:",
    "    label: Touch",
    "    unrestricted: true",
    "    execute: handlers.run",
]


class _Recorder:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def __call__(
        self, record: dict[str, Any], context: RequestContext, payload: Any
    ) -> None:
        self.payloads.append(dict(payload))


def _runtime(tmp_path: Path) -> tuple[ActionService, _Recorder, Any, RequestContext]:
    project = _project(tmp_path, "service-parameters", PARAMETRIZED_ENTITY)
    model = compile_project(project)
    records = RecordsService(model, InMemoryRepository())
    context = RequestContext(
        principal=Principal("user:clerk", roles=frozenset({"operator"})),
        channel=Channel.TUI,
        correlation_id="parameters",
    )
    session = records.create("demo.Thing", context, {"title": "one"})
    stored = records.commit(session, context)
    actions = ActionService(model, records)
    recorder = _Recorder()
    actions.register("handlers.run", recorder)
    return actions, recorder, stored["id"], context


def test_the_service_types_the_payload_before_the_handler_sees_it(
    tmp_path: Path,
) -> None:
    actions, recorder, identity, context = _runtime(tmp_path)

    actions.execute(
        "demo.Thing",
        "archive",
        identity,
        {"reason": "damaged", "occurred_at": "2026-03-01T12:00:00+00:00"},
        context,
    )

    assert recorder.payloads == [
        {
            "reason": "damaged",
            "occurred_at": datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
            "mood": "calm",
        }
    ]


def test_a_refused_payload_names_every_reason_at_once(tmp_path: Path) -> None:
    actions, recorder, identity, context = _runtime(tmp_path)

    with pytest.raises(ValidationFailed) as refusal:
        actions.execute(
            "demo.Thing",
            "archive",
            identity,
            {"extra": 1, "occurred_at": "not a moment"},
            context,
        )

    issues = refusal.value.issues
    assert {issue.rule for issue in issues} == {"action_parameter"}
    messages = "\n".join(issue.message for issue in issues)
    assert "unknown action parameter 'extra'" in messages
    assert "action parameter 'reason' is required" in messages
    assert "action parameter 'occurred_at' must be datetime" in messages
    assert recorder.payloads == []


def test_an_undeclared_action_accepts_only_an_empty_payload(
    tmp_path: Path,
) -> None:
    actions, recorder, identity, context = _runtime(tmp_path)

    with pytest.raises(ValidationFailed) as refusal:
        actions.execute(
            "demo.Thing", "touch", identity, {"stray": True}, context
        )
    assert "unknown action parameter 'stray'" in str(refusal.value)

    actions.execute("demo.Thing", "touch", identity, {}, context)
    assert recorder.payloads == [{}]


def test_the_string_form_and_the_typed_form_replay_as_one_request(
    tmp_path: Path,
) -> None:
    """Coercion happens before the fingerprint, so spellings of one request
    replay instead of conflicting."""

    actions, recorder, identity, context = _runtime(tmp_path)
    moment = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

    actions.execute(
        "demo.Thing",
        "archive",
        identity,
        {"reason": "damaged", "occurred_at": "2026-03-01T12:00:00+00:00"},
        context,
        idempotency_key="one-request",
    )
    actions.execute(
        "demo.Thing",
        "archive",
        identity,
        {"reason": "damaged", "occurred_at": moment},
        context,
        idempotency_key="one-request",
    )

    assert len(recorder.payloads) == 1


def test_a_reused_key_for_different_parameters_still_conflicts(
    tmp_path: Path,
) -> None:
    actions, recorder, identity, context = _runtime(tmp_path)

    actions.execute(
        "demo.Thing",
        "archive",
        identity,
        {"reason": "damaged"},
        context,
        idempotency_key="one-key",
    )
    with pytest.raises(IdempotencyConflict, match="different request"):
        actions.execute(
            "demo.Thing",
            "archive",
            identity,
            {"reason": "expired"},
            context,
            idempotency_key="one-key",
        )
    assert len(recorder.payloads) == 1


def test_a_parameter_name_must_be_a_plain_identifier(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        "bad-parameter-name",
        [
            *BASE_LINES,
            "    parameters:",
            "      bad-name: {type: string}",
        ],
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    codes = {
        diagnostic.code: diagnostic.message
        for diagnostic in caught.value.diagnostics
    }
    assert "TIDE292" in codes
    assert "'bad-name'" in codes["TIDE292"]
