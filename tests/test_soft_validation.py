"""A declared rule can warn or inform instead of refusing.

`severity: warning | info` has been accepted by the schema since the initial
commit and documented with an example, while the service evaluated both and
kept only the errors -- a rule an author declares, the framework runs, and
nobody sees. This suite makes the documented sentence true: errors refuse,
warnings refuse *until acknowledged by rule id*, info rides the successful
result as notices. Every layer is covered here rather than beside its own
module, because the point of the feature is that one declaration reaches all
of them, and the way that breaks is one layer quietly not reading it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

import httpx
import pytest

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.mcp.runtime import RuntimeMcpService
from tide.mcp.server import build_runtime_mcp_server, mount_runtime_mcp
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.errors import ValidationFailed
from tide.services import ActionService, AuditHistoryService, RecordsService

TOKEN = "tide-development-token-that-is-long-enough"

MANIFEST = (
    'schema_version: "0.1"\n'
    "application: {name: SoftValidation, version: 0.1.0}\n"
    "database: {mode: managed}\n"
    "model: {paths: [models]}\n"
    "views: {paths: [views]}\n"
    "security: {paths: [security]}\n"
)

POLICIES = (
    "permissions:\n- demo.all\nroles:\n  operator:\n    grants:\n    - demo.all\n"
)

BROWSE = (
    "view: demo.Shipment.browse\nentity: demo.Shipment\nkind: browse\n"
    "columns:\n- reference\n- status\n- weight\n"
)

EDIT = (
    "view: demo.Shipment.edit\nentity: demo.Shipment\nkind: form\n"
    "layout:\n- group: Shipment\n  rows:\n  - - reference\n    - status\n"
    "  - - weight\n    - note\n"
)

SHIPMENT = (
    "entity: demo.Shipment\n"
    "display: reference\n"
    "expose:\n"
    "  tui: true\n"
    "  rest:\n"
    "    path: shipments\n"
    "    operations: [list, get, create, update]\n"
    "  mcp:\n"
    "    resources: [schema, record]\n"
    "    tools: [search, create, update]\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all, delete: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  reference: {type: string, length: 40, required: true}\n"
    "  status:\n"
    "    type: choice\n"
    "    choices: [draft, dispatched]\n"
    "    default: draft\n"
    "    readonly: true\n"
    "    write: action_only\n"
    "  weight: {type: integer}\n"
    "  note: {type: string, length: 60}\n"
    "  parcels:\n"
    "    type: collection\n"
    "    target: demo.Parcel\n"
    "    inverse: shipment\n"
    "    cascade: [create, update]\n"
    "    orphan_delete: true\n"
    "validations:\n"
    "- id: dispatched_needs_weight\n"
    "  when: \"status == 'dispatched'\"\n"
    "  assert: 'weight != null'\n"
    "  message: A dispatched shipment must state its weight.\n"
    "  fields: [weight]\n"
    "- id: unshippable_reference\n"
    "  assert: \"reference != 'X'\"\n"
    "  message: X is not a shipment reference.\n"
    "  fields: [reference]\n"
    "- id: heavy_shipment\n"
    "  when: 'weight != null'\n"
    "  assert: 'weight <= 100'\n"
    "  severity: warning\n"
    "  message: The shipment weight is unusually high.\n"
    "  fields: [weight]\n"
    "- id: missing_note\n"
    "  assert: 'note != null'\n"
    "  severity: info\n"
    "  message: A note helps the courier.\n"
    "  fields: [note]\n"
    "actions:\n"
    "  dispatch:\n"
    "    label: Dispatch\n"
    "    permission: demo.all\n"
    "    execute: actions.dispatch\n"
    "    expose: {rest: true, mcp: true}\n"
    "    idempotent: true\n"
    "    transition:\n"
    "      field: status\n"
    "      from: draft\n"
    "      to: dispatched\n"
)

PARCEL = (
    "entity: demo.Parcel\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  item: {type: string, length: 60, required: true}\n"
    "  count: {type: integer}\n"
    "  shipment:\n"
    "    type: reference\n"
    "    target: demo.Shipment\n"
    "    storage: shipment_id\n"
    "    inverse: parcels\n"
    "    required: true\n"
    "    on_delete: cascade\n"
    "validations:\n"
    "- id: bulky_parcel\n"
    "  when: 'count != null'\n"
    "  assert: 'count <= 10'\n"
    "  severity: warning\n"
    "  message: The parcel count is unusually high.\n"
    "  fields: [count]\n"
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "soft-validation"
    for relative, text in (
        ("tide.yaml", MANIFEST),
        ("security/policies.yaml", POLICIES),
        ("views/shipment-browse.yaml", BROWSE),
        ("views/shipment-edit.yaml", EDIT),
        ("models/shipment.yaml", SHIPMENT),
        ("models/parcel.yaml", PARCEL),
        (
            "actions.py",
            "def dispatch(values, context, payload):\n"
            "    values['status'] = 'dispatched'\n",
        ),
    ):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return project


def _context() -> RequestContext:
    return RequestContext(
        Principal("tests:operator", roles=frozenset({"operator"})),
        channel=Channel.REST,
        correlation_id="soft-validation",
    )


def _dispatch(
    values: dict[str, object], context: object, payload: Mapping[str, object]
) -> None:
    values["status"] = "dispatched"


def _services(tmp_path: Path) -> tuple[RecordsService, ActionService, RequestContext]:
    model = compile_project(_project(tmp_path))
    records = RecordsService(model, InMemoryRepository())
    actions = ActionService(model, records)
    actions.register("actions.dispatch", _dispatch)
    return records, actions, _context()


def _shipment(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"reference": "S-1", "note": "fragile"}
    values.update(overrides)
    return values


# --- the gate, at the service ------------------------------------------------


def test_a_warning_rule_refuses_the_commit_until_acknowledged(
    tmp_path: Path,
) -> None:
    """This used to commit silently: the warning was evaluated and dropped."""

    records, _actions, context = _services(tmp_path)
    session = records.create("demo.Shipment", context, _shipment(weight=500))

    with pytest.raises(ValidationFailed) as failure:
        records.commit(session, context)

    issues = failure.value.issues
    assert [issue.rule for issue in issues] == ["heavy_shipment"]
    assert issues[0].severity == "warning"
    assert issues[0].fields == ("weight",)


def test_an_acknowledged_warning_commits_and_is_echoed_as_a_notice(
    tmp_path: Path,
) -> None:
    records, _actions, context = _services(tmp_path)
    session = records.create("demo.Shipment", context, _shipment(weight=500))

    stored = records.commit(
        session,
        context,
        acknowledged_warnings=frozenset({"heavy_shipment"}),
    )

    assert stored["weight"] == 500
    assert [(issue.rule, issue.severity) for issue in session.notices] == [
        ("heavy_shipment", "warning")
    ]


def test_an_info_rule_never_gates_and_rides_the_result_as_a_notice(
    tmp_path: Path,
) -> None:
    records, _actions, context = _services(tmp_path)
    session = records.create(
        "demo.Shipment", context, {"reference": "S-2", "weight": 50}
    )

    stored = records.commit(session, context)

    assert stored["reference"] == "S-2"
    assert [(issue.rule, issue.severity) for issue in session.notices] == [
        ("missing_note", "info")
    ]


def test_a_refusal_reports_errors_and_unacknowledged_warnings_together(
    tmp_path: Path,
) -> None:
    """One round trip shows everything: the person fixes and weighs at once."""

    records, _actions, context = _services(tmp_path)
    session = records.create("demo.Shipment", context, _shipment(reference="X", weight=500))

    with pytest.raises(ValidationFailed) as failure:
        records.commit(session, context)

    issues = {issue.rule: issue.severity for issue in failure.value.issues}
    assert issues == {
        "unshippable_reference": "error",
        "heavy_shipment": "warning",
    }


def test_an_acknowledged_warning_does_not_soften_a_standing_error(
    tmp_path: Path,
) -> None:
    records, _actions, context = _services(tmp_path)
    session = records.create("demo.Shipment", context, _shipment(reference="X", weight=500))

    with pytest.raises(ValidationFailed) as failure:
        records.commit(
            session,
            context,
            acknowledged_warnings=frozenset({"heavy_shipment"}),
        )

    assert [issue.rule for issue in failure.value.issues] == [
        "unshippable_reference"
    ]


def test_a_child_row_warning_gates_the_parent_commit_by_its_own_rule_id(
    tmp_path: Path,
) -> None:
    records, _actions, context = _services(tmp_path)
    session = records.create(
        "demo.Shipment",
        context,
        _shipment(parcels=[{"item": "crate", "count": 99}]),
    )

    with pytest.raises(ValidationFailed) as failure:
        records.commit(session, context)

    assert [(issue.rule, issue.severity) for issue in failure.value.issues] == [
        ("bulky_parcel", "warning")
    ]

    retry = records.create(
        "demo.Shipment",
        context,
        _shipment(parcels=[{"item": "crate", "count": 99}]),
    )
    stored = records.commit(
        retry,
        context,
        acknowledged_warnings=frozenset({"bulky_parcel"}),
    )

    assert stored["parcels"][0]["count"] == 99
    assert [issue.rule for issue in retry.notices] == ["bulky_parcel"]


def test_acknowledging_a_rule_that_did_not_fire_changes_nothing(
    tmp_path: Path,
) -> None:
    """A client may echo back a previous refusal's ids wholesale."""

    records, _actions, context = _services(tmp_path)
    session = records.create("demo.Shipment", context, _shipment(weight=50))

    stored = records.commit(
        session,
        context,
        acknowledged_warnings=frozenset({"heavy_shipment", "never_declared"}),
    )

    assert stored["weight"] == 50
    assert session.notices == ()


# --- the gate, through the action door ---------------------------------------


def _heavy_dispatchable(
    records: RecordsService, context: RequestContext
) -> object:
    session = records.create("demo.Shipment", context, _shipment(weight=500))
    stored = records.commit(
        session,
        context,
        acknowledged_warnings=frozenset({"heavy_shipment"}),
    )
    return stored["id"]


def test_a_warning_gates_the_action_commit_until_acknowledged(
    tmp_path: Path,
) -> None:
    records, actions, context = _services(tmp_path)
    identity = _heavy_dispatchable(records, context)

    with pytest.raises(ValidationFailed) as failure:
        actions.execute(
            "demo.Shipment",
            "dispatch",
            identity,
            {},
            context,
            idempotency_key="dispatch-1",
        )

    assert [(issue.rule, issue.severity) for issue in failure.value.issues] == [
        ("heavy_shipment", "warning")
    ]

    outcome = actions.execute(
        "demo.Shipment",
        "dispatch",
        identity,
        {},
        context,
        idempotency_key="dispatch-2",
        acknowledged_warnings=frozenset({"heavy_shipment"}),
    )

    assert outcome.record["status"] == "dispatched"
    assert [issue.rule for issue in outcome.notices] == ["heavy_shipment"]


def test_a_gated_attempt_burns_its_idempotency_key_like_any_refusal(
    tmp_path: Path,
) -> None:
    """The acknowledged resubmit is a new request and uses a fresh key.

    This is the pre-existing FAILED-requires-reconciliation contract, pinned
    here with a warning so the documented fresh-key rule cannot drift.
    """

    from tide.runtime.errors import IdempotencyConflict

    records, actions, context = _services(tmp_path)
    identity = _heavy_dispatchable(records, context)

    with pytest.raises(ValidationFailed):
        actions.execute(
            "demo.Shipment",
            "dispatch",
            identity,
            {},
            context,
            idempotency_key="burned",
        )

    with pytest.raises(IdempotencyConflict):
        actions.execute(
            "demo.Shipment",
            "dispatch",
            identity,
            {},
            context,
            idempotency_key="burned",
            acknowledged_warnings=frozenset({"heavy_shipment"}),
        )


# --- the gate, over REST -----------------------------------------------------


def _api_app(tmp_path: Path) -> Any:
    model = compile_project(_project(tmp_path))
    records = RecordsService(model, InMemoryRepository())
    actions = ActionService(model, records)
    actions.register("actions.dispatch", _dispatch)
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"operator"})),
        ),
        actions=actions,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _headers(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", **extra}


def test_rest_create_gates_and_acknowledges_through_the_query_parameter(
    tmp_path: Path,
) -> None:
    app = _api_app(tmp_path)

    async def exercise() -> None:
        async with _client(app) as client:
            refused = await client.post(
                "/api/v1/shipments",
                headers=_headers(),
                json={"reference": "S-9", "weight": 500, "note": "fragile"},
            )
            assert refused.status_code == 422
            body = refused.json()
            assert body["code"] == "validation_failed"
            assert [
                (issue["rule"], issue["severity"]) for issue in body["issues"]
            ] == [("heavy_shipment", "warning")]

            accepted = await client.post(
                "/api/v1/shipments",
                headers=_headers(),
                params=[("acknowledge_warnings", "heavy_shipment")],
                json={"reference": "S-9", "weight": 500, "note": "fragile"},
            )
            assert accepted.status_code == 201
            envelope = accepted.json()
            assert envelope["_tide"]["notices"] == [
                {
                    "rule": "heavy_shipment",
                    "message": "The shipment weight is unusually high.",
                    "fields": ["weight"],
                    "severity": "warning",
                }
            ]

            quiet = await client.post(
                "/api/v1/shipments",
                headers=_headers(),
                json={"reference": "S-10", "weight": 50, "note": "calm"},
            )
            assert quiet.status_code == 201
            assert "notices" not in (quiet.json().get("_tide") or {})

    asyncio.run(exercise())


def test_rest_update_and_action_doors_take_the_same_parameter(
    tmp_path: Path,
) -> None:
    app = _api_app(tmp_path)

    async def exercise() -> None:
        async with _client(app) as client:
            created = await client.post(
                "/api/v1/shipments",
                headers=_headers(),
                json={"reference": "S-11", "weight": 50, "note": "calm"},
            )
            assert created.status_code == 201
            identity = created.json()["id"]

            heavier = await client.patch(
                f"/api/v1/shipments/{identity}",
                headers=_headers(),
                json={"weight": 700},
            )
            assert heavier.status_code == 422

            acknowledged = await client.patch(
                f"/api/v1/shipments/{identity}",
                headers=_headers(),
                params=[("acknowledge_warnings", "heavy_shipment")],
                json={"weight": 700},
            )
            assert acknowledged.status_code == 200
            assert [
                issue["rule"]
                for issue in acknowledged.json()["_tide"]["notices"]
            ] == ["heavy_shipment"]

            gated = await client.post(
                f"/api/v1/shipments/{identity}/actions/dispatch",
                headers=_headers(**{"Idempotency-Key": "rest-dispatch-1"}),
                json={},
            )
            assert gated.status_code == 422

            dispatched = await client.post(
                f"/api/v1/shipments/{identity}/actions/dispatch",
                headers=_headers(**{"Idempotency-Key": "rest-dispatch-2"}),
                params=[("acknowledge_warnings", "heavy_shipment")],
                json={},
            )
            assert dispatched.status_code == 200
            envelope = dispatched.json()
            assert envelope["status"] == "dispatched"
            assert [
                issue["rule"] for issue in envelope["_tide"]["notices"]
            ] == ["heavy_shipment"]

    asyncio.run(exercise())


def _mcp(tmp_path: Path) -> tuple[RuntimeMcpService, RequestContext]:
    model = compile_project(_project(tmp_path))
    records = RecordsService(model, InMemoryRepository())
    actions = ActionService(model, records)
    actions.register("actions.dispatch", _dispatch)
    audits = AuditHistoryService(
        model,
        actions.execution_store,
        records,
        records.security,
    )
    service = RuntimeMcpService(model, records, actions=actions, audits=audits)
    return service, _context()


def test_mcp_create_gates_and_reports_notices_on_the_result(
    tmp_path: Path,
) -> None:
    service, context = _mcp(tmp_path)

    with pytest.raises(ValidationFailed):
        service.create(
            "demo.Shipment",
            {"reference": "S-20", "weight": 500, "note": "fragile"},
            context,
        )

    result = service.create(
        "demo.Shipment",
        {"reference": "S-20", "weight": 500, "note": "fragile"},
        context,
        acknowledged_warnings=frozenset({"heavy_shipment"}),
    )
    dumped = result.model_dump(mode="json")
    assert dumped["notices"] == [
        {
            "rule": "heavy_shipment",
            "message": "The shipment weight is unusually high.",
            "fields": ["weight"],
            "severity": "warning",
        }
    ]

    quiet = service.update(
        "demo.Shipment",
        result.identity,
        {"weight": 60},
        context,
    )
    assert "notices" not in quiet.model_dump(mode="json")


def test_mcp_action_gate_takes_the_same_argument(tmp_path: Path) -> None:
    service, context = _mcp(tmp_path)
    created = service.create(
        "demo.Shipment",
        {"reference": "S-21", "weight": 500, "note": "fragile"},
        context,
        acknowledged_warnings=frozenset({"heavy_shipment"}),
    )

    with pytest.raises(ValidationFailed):
        service.execute_action(
            "demo.Shipment",
            "dispatch",
            created.identity,
            {},
            context,
            idempotency_key="mcp-dispatch-1",
        )

    dispatched = service.execute_action(
        "demo.Shipment",
        "dispatch",
        created.identity,
        {},
        context,
        idempotency_key="mcp-dispatch-2",
        acknowledged_warnings=frozenset({"heavy_shipment"}),
    )
    assert dispatched.record is not None
    assert dispatched.record["status"] == "dispatched"
    assert [issue.rule for issue in dispatched.notices or ()] == [
        "heavy_shipment"
    ]


def test_mcp_tools_expose_the_acknowledgement_argument(tmp_path: Path) -> None:
    """An agent's flow end to end: refused, told which rule, acknowledged."""

    import json

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    model = compile_project(_project(tmp_path))
    records = RecordsService(model, InMemoryRepository())
    actions = ActionService(model, records)
    actions.register("actions.dispatch", _dispatch)
    audits = AuditHistoryService(
        model,
        actions.execution_store,
        records,
        records.security,
    )
    authenticator = DevelopmentTokenAuthenticator(
        TOKEN,
        Principal("mcp:test", roles=frozenset({"operator"})),
    )
    app = build_fastapi_app(
        model,
        records,
        authenticator,
        actions=actions,
        audits=audits,
    )
    mount_runtime_mcp(
        app,
        build_runtime_mcp_server(
            RuntimeMcpService(model, records, actions=actions, audits=audits),
            authenticator,
            issuer_url="http://127.0.0.1:8000",
            resource_url="http://127.0.0.1:8000/mcp",
        ),
    )

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as http:
                async with streamable_http_client(
                    "http://127.0.0.1:8000/mcp",
                    http_client=http,
                ) as (read_stream, write_stream, _session_id):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        shipment = {
                            "reference": "S-30",
                            "weight": 500,
                            "note": "fragile",
                        }
                        refused = await session.call_tool(
                            "create_demo_shipment",
                            {"values": shipment},
                        )
                        assert refused.isError
                        assert "unusually high" in refused.content[0].text

                        accepted = await session.call_tool(
                            "create_demo_shipment",
                            {
                                "values": shipment,
                                "acknowledge_warnings": ["heavy_shipment"],
                            },
                        )
                        assert not accepted.isError
                        payload = json.loads(accepted.content[0].text)
                        assert [
                            notice["rule"] for notice in payload["notices"]
                        ] == ["heavy_shipment"]
                        identity = payload["identity"]

                        gated = await session.call_tool(
                            "dispatch_demo_shipment",
                            {
                                "identity": identity,
                                "idempotency_key": "mcp-tool-dispatch-1",
                            },
                        )
                        assert gated.isError

                        dispatched = await session.call_tool(
                            "dispatch_demo_shipment",
                            {
                                "identity": identity,
                                "idempotency_key": "mcp-tool-dispatch-2",
                                "acknowledge_warnings": ["heavy_shipment"],
                            },
                        )
                        assert not dispatched.isError
                        outcome = json.loads(dispatched.content[0].text)
                        assert outcome["record"]["status"] == "dispatched"
                        assert [
                            notice["rule"] for notice in outcome["notices"]
                        ] == ["heavy_shipment"]

    asyncio.run(exercise())


# --- the gate, in the terminal ----------------------------------------------


def _tui_app(tmp_path: Path) -> tuple[Any, RecordsService, RequestContext]:
    from tide.tui import TideApp

    model = compile_project(_project(tmp_path))
    records = RecordsService(model, InMemoryRepository())
    actions = ActionService(model, records)
    actions.register("actions.dispatch", _dispatch)
    context = RequestContext(
        Principal("tui:test", roles=frozenset({"operator"})),
        channel=Channel.TUI,
        correlation_id="soft-validation-tui",
    )
    seeded = records.create(
        "demo.Shipment", context, {"reference": "S-40", "weight": 50}
    )
    records.commit(seeded, context)
    return TideApp(model, records, context, actions=actions), records, context


async def _wait(pilot: Any, condition: Any) -> None:
    for _ in range(80):
        if condition():
            return
        await pilot.pause()
    raise AssertionError("condition was not reached")


def _weight_editable(app: Any) -> bool:
    """The content claim, not the screen class: the form body mounts after
    a refresh, so an empty RecordEditScreen is `not yet`, never `ready`."""

    from tide.tui.form import RecordEditScreen

    screen = app.screen
    return isinstance(screen, RecordEditScreen) and bool(
        screen.query("#field-weight")
    )


def test_tui_save_anyway_confirms_the_warning_and_speaks_the_info(
    tmp_path: Path,
) -> None:
    from textual.widgets import Input

    from tide.tui.form import RecordEditScreen
    from tide.tui.warnings import WarningsScreen

    app, records, _context = _tui_app(tmp_path)
    spoken: list[tuple[str, str]] = []
    original_notify = app.notify

    def capture(message: Any, **options: Any) -> None:
        spoken.append((str(message), str(options.get("severity", "information"))))
        original_notify(message, **options)

    app.notify = capture  # type: ignore[method-assign]

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(1)
            await _wait(pilot, lambda: _weight_editable(app))
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            screen.query_one("#field-weight", Input).value = "500"
            await pilot.click("#save-form")
            await _wait(pilot, lambda: isinstance(app.screen, WarningsScreen))
            dialog = app.screen
            assert isinstance(dialog, WarningsScreen)
            assert any(
                "unusually high" in message for message in dialog.messages
            )
            await pilot.click("#confirm-warnings")
            # The claim, not a proxy: while the dialog is up the screen is
            # already not a RecordEditScreen, so a screen-class wait passes
            # instantly and a slow runner shuts the app down mid-retry.
            # The info notification is the last observable of the retry.
            await _wait(
                pilot,
                lambda: ("A note helps the courier.", "information") in spoken,
            )

    asyncio.run(exercise())

    stored = records.repository.get("demo.Shipment", 1)
    assert stored["weight"] == 500
    assert ("A note helps the courier.", "information") in spoken


def test_tui_cancel_keeps_the_form_and_writes_nothing(tmp_path: Path) -> None:
    from textual.widgets import Input

    from tide.tui.form import RecordEditScreen
    from tide.tui.warnings import WarningsScreen

    app, records, _context = _tui_app(tmp_path)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(1)
            await _wait(pilot, lambda: _weight_editable(app))
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            screen.query_one("#field-weight", Input).value = "500"
            await pilot.click("#save-form")
            await _wait(pilot, lambda: isinstance(app.screen, WarningsScreen))
            await pilot.click("#cancel-warnings")
            await _wait(pilot, lambda: isinstance(app.screen, RecordEditScreen))

    asyncio.run(exercise())

    assert records.repository.get("demo.Shipment", 1)["weight"] == 50


def test_rest_info_notices_ride_success_without_ever_gating(
    tmp_path: Path,
) -> None:
    app = _api_app(tmp_path)

    async def exercise() -> None:
        async with _client(app) as client:
            created = await client.post(
                "/api/v1/shipments",
                headers=_headers(),
                json={"reference": "S-12", "weight": 50},
            )
            assert created.status_code == 201
            assert created.json()["_tide"]["notices"] == [
                {
                    "rule": "missing_note",
                    "message": "A note helps the courier.",
                    "fields": ["note"],
                    "severity": "info",
                }
            ]

    asyncio.run(exercise())
