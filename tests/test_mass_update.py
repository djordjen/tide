"""One change applied to a selection, answered row by row.

Mass update is the single-record update run N times by the service under
the existing ``update`` permission: every per-record rule -- row policies,
field policies, ``immutable_when``, validation, the warning acknowledgement
gate, optimistic versions, audit -- meets each row exactly as a hand-made
edit would, and the answers come back as per-row outcomes rather than one
error. The whole slice sits in this one suite because the feature is one
loop reaching every layer; the way it breaks is a layer quietly looping for
itself.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pytest

from tide import compile_project
from tide.data import InMemoryRepository, SQLAlchemyRepository
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.errors import NULL_VERSION, MassAssignmentError
from tide.services import RecordsService
from tide.services.records import MassUpdateTarget

MANIFEST = (
    'schema_version: "0.1"\n'
    "application: {name: MassUpdate, version: 0.1.0}\n"
    "database: {mode: managed}\n"
    "model: {paths: [models]}\n"
    "views: {paths: [views]}\n"
    "security: {paths: [security]}\n"
)

# ``reviewer`` is writable only with demo.review, which the operator is not
# granted; jobs in the frozen region may be read but not updated.
POLICIES = (
    "permissions:\n"
    "- demo.all\n"
    "- demo.review\n"
    "- demo.update\n"
    "roles:\n"
    "  operator:\n"
    "    grants:\n"
    "    - demo.all\n"
    "    - demo.update\n"
    "  reviewer:\n"
    "    grants:\n"
    "    - demo.all\n"
    "    - demo.update\n"
    "    - demo.review\n"
    "  watcher:\n"
    "    grants:\n"
    "    - demo.all\n"
    "row_policies:\n"
    "- id: updatable_regions\n"
    "  entity: demo.Job\n"
    "  operations: [update]\n"
    "  criteria: \"region != 'frozen'\"\n"
    "field_policies:\n"
    "- entity: demo.Job\n"
    "  field: reviewer\n"
    "  write: demo.review\n"
)

WORKER = (
    "entity: demo.Worker\n"
    "display: name\n"
    "expose:\n"
    "  rest:\n"
    "    path: workers\n"
    "    operations: [list, get]\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  name: {type: string, length: 60, required: true}\n"
    "  active: {type: boolean, default: true}\n"
)

JOB = (
    "entity: demo.Job\n"
    "display: title\n"
    "expose:\n"
    "  tui: true\n"
    "  rest:\n"
    "    path: jobs\n"
    "    operations: [list, get, create, update]\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.update}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  title: {type: string, length: 60, required: true}\n"
    "  status: {type: choice, choices: [open, closed], default: open}\n"
    "  priority:\n"
    "    type: string\n"
    "    length: 20\n"
    "    immutable_when: \"status == 'closed'\"\n"
    "  hours: {type: decimal, precision: 8, scale: 2}\n"
    "  region: {type: string, length: 20}\n"
    "  reviewer: {type: string, length: 60}\n"
    "  slug: {type: string, length: 60, readonly: true, write: system}\n"
    "  worker:\n"
    "    type: reference\n"
    "    target: demo.Worker\n"
    "    storage: worker_id\n"
    "    lookup_filter: 'active == true'\n"
    "  version:\n"
    "    type: integer\n"
    "    required: true\n"
    "    default: 1\n"
    "    readonly: true\n"
    "    write: system\n"
    "    concurrency_token: true\n"
    "validations:\n"
    "- id: heavy_hours\n"
    "  when: 'hours != null'\n"
    "  assert: 'hours <= 40'\n"
    "  severity: warning\n"
    "  message: Hours are unusually high.\n"
    "  fields: [hours]\n"
    "  run: [before_commit]\n"
)

TAG = (
    "entity: demo.Tag\n"
    "display: label\n"
    "expose:\n"
    "  tui: true\n"
    "  rest:\n"
    "    path: tags\n"
    "    operations: [list, get, update]\n"
    "permissions: {list: demo.all, read: demo.all, update: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  label: {type: string, length: 40, required: true}\n"
    "  color: {type: string, length: 20}\n"
)

JOB_BROWSE = (
    "view: demo.Job.browse\n"
    "entity: demo.Job\n"
    "kind: browse\n"
    "columns: [title, status, priority]\n"
)

JOB_EDIT = (
    "view: demo.Job.edit\n"
    "entity: demo.Job\n"
    "kind: form\n"
    "layout:\n"
    "- group: Job\n"
    "  rows:\n"
    "  - - title\n"
    "    - priority\n"
    "  - - hours\n"
    "    - reviewer\n"
    "  - - worker\n"
)

TAG_BROWSE = (
    "view: demo.Tag.browse\n"
    "entity: demo.Tag\n"
    "kind: browse\n"
    "columns: [label, color]\n"
)

WORKERS = [
    {"id": 1, "name": "Ada", "active": True},
    {"id": 2, "name": "Ben", "active": False},
]

JOBS = [
    {
        "id": 1,
        "title": "Alpha",
        "status": "open",
        "priority": "low",
        "hours": Decimal("5.00"),
        "region": "north",
        "worker": 1,
        "version": 1,
    },
    {
        "id": 2,
        "title": "Beta",
        "status": "closed",
        "priority": "low",
        "hours": Decimal("5.00"),
        "region": "north",
        "worker": 1,
        "version": 1,
    },
    {
        "id": 3,
        "title": "Gamma",
        "status": "open",
        "priority": "low",
        "hours": Decimal("5.00"),
        "region": "frozen",
        "worker": 1,
        "version": 1,
    },
    {
        "id": 4,
        "title": "Delta",
        "status": "open",
        "priority": "low",
        "hours": Decimal("5.00"),
        "region": "south",
        "worker": 1,
        "version": 1,
    },
]

TAGS = [
    {"id": 1, "label": "urgent", "color": "red"},
    {"id": 2, "label": "later", "color": "gray"},
]


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "mass-update"
    for relative, text in (
        ("tide.yaml", MANIFEST),
        ("security/policies.yaml", POLICIES),
        ("models/worker.yaml", WORKER),
        ("models/job.yaml", JOB),
        ("models/tag.yaml", TAG),
        ("views/job-browse.yaml", JOB_BROWSE),
        ("views/job-edit.yaml", JOB_EDIT),
        ("views/tag-browse.yaml", TAG_BROWSE),
    ):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return project


def _context(role: str = "operator") -> RequestContext:
    return RequestContext(
        Principal(f"tests:{role}", roles=frozenset({role})),
        channel=Channel.REST,
        correlation_id="mass-update",
    )


@pytest.fixture(params=("memory", "sql"))
def runtime(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[RecordsService, RequestContext]]:
    model = compile_project(_project(tmp_path))
    if request.param == "memory":
        repository: InMemoryRepository | SQLAlchemyRepository = InMemoryRepository()
    else:
        repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
        repository.create_schema()
    repository.seed("demo.Worker", WORKERS)
    repository.seed("demo.Job", JOBS)
    repository.seed("demo.Tag", TAGS)
    yield RecordsService(model, repository), _context()
    if isinstance(repository, SQLAlchemyRepository):
        repository.dispose()


# --- the loop, row by row -----------------------------------------------------


def test_mass_update_updates_each_selected_row(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime

    result = records.mass_update(
        "demo.Job",
        {"priority": "high"},
        [MassUpdateTarget(1, 1), MassUpdateTarget(4, 1)],
        context,
    )

    assert result.updated == 2
    assert result.refused == 0
    assert [outcome.identity for outcome in result.outcomes] == [1, 4]
    assert all(outcome.status == "updated" for outcome in result.outcomes)
    assert all(outcome.version == 2 for outcome in result.outcomes)
    for identity in (1, 4):
        assert records.get("demo.Job", identity, context)["priority"] == "high"


def test_each_row_answers_for_itself(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    """One call, five targets, five different answers -- in request order,
    with the healthy row genuinely written despite its refused siblings."""

    records, context = runtime

    result = records.mass_update(
        "demo.Job",
        {"priority": "high"},
        [
            MassUpdateTarget(1, 1),
            MassUpdateTarget(2, 1),  # closed: priority is immutable
            MassUpdateTarget(3, 1),  # frozen region: update row policy refuses
            MassUpdateTarget(99, 1),  # gone
            MassUpdateTarget(4, 7),  # stale assertion
        ],
        context,
    )

    assert [outcome.identity for outcome in result.outcomes] == [1, 2, 3, 99, 4]
    assert [outcome.status for outcome in result.outcomes] == [
        "updated",
        "refused",
        "refused",
        "refused",
        "refused",
    ]
    assert [outcome.code for outcome in result.outcomes] == [
        None,
        "immutable_field",
        "forbidden",
        "not_found",
        "stale_version",
    ]
    assert result.updated == 1
    assert result.refused == 4
    assert records.get("demo.Job", 1, context)["priority"] == "high"
    assert records.get("demo.Job", 4, context)["priority"] == "low"


def test_versioned_targets_require_an_assertion(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime

    result = records.mass_update(
        "demo.Job",
        {"priority": "high"},
        [MassUpdateTarget(1)],
        context,
    )

    assert result.outcomes[0].status == "refused"
    assert result.outcomes[0].code == "version_precondition_required"
    assert records.get("demo.Job", 1, context)["priority"] == "low"


def test_a_null_version_assertion_is_a_version_like_any_other(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    """Asserting "the token was never written" against a written row is
    staleness, not a request error -- the spelling travels and compares."""

    records, context = runtime

    result = records.mass_update(
        "demo.Job",
        {"priority": "high"},
        [MassUpdateTarget(1, NULL_VERSION)],
        context,
    )

    assert result.outcomes[0].code == "stale_version"


def test_unversioned_entities_travel_without_assertions(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime

    result = records.mass_update(
        "demo.Tag",
        {"color": "blue"},
        [MassUpdateTarget(1), MassUpdateTarget(2)],
        context,
    )

    assert result.updated == 2
    assert all(outcome.version is None for outcome in result.outcomes)
    for identity in (1, 2):
        assert records.get("demo.Tag", identity, context)["color"] == "blue"


def test_duplicate_targets_meet_staleness_honestly(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime

    result = records.mass_update(
        "demo.Job",
        {"priority": "high"},
        [MassUpdateTarget(1, 1), MassUpdateTarget(1, 1)],
        context,
    )

    assert [outcome.status for outcome in result.outcomes] == ["updated", "refused"]
    assert result.outcomes[1].code == "stale_version"


# --- validation across the set ------------------------------------------------


def test_warnings_refuse_until_acknowledged_by_rule_id(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime
    targets = [MassUpdateTarget(1, 1), MassUpdateTarget(4, 1)]

    first = records.mass_update(
        "demo.Job", {"hours": Decimal("99.00")}, targets, context
    )
    assert [outcome.code for outcome in first.outcomes] == [
        "validation_failed",
        "validation_failed",
    ]
    warnings = {
        issue.rule
        for outcome in first.outcomes
        for issue in outcome.issues
        if issue.severity == "warning"
    }
    assert warnings == {"heavy_hours"}

    # Nothing was written, so the same assertions still hold; one
    # acknowledgement by rule id covers every row it fired on.
    second = records.mass_update(
        "demo.Job",
        {"hours": Decimal("99.00")},
        targets,
        context,
        acknowledged_warnings=frozenset({"heavy_hours"}),
    )
    assert second.updated == 2
    for outcome in second.outcomes:
        assert "heavy_hours" in {issue.rule for issue in outcome.notices}
    assert records.get("demo.Job", 1, context)["hours"] == Decimal("99.00")


def test_error_validation_refuses_the_row(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime

    result = records.mass_update(
        "demo.Job", {"title": ""}, [MassUpdateTarget(1, 1)], context
    )

    outcome = result.outcomes[0]
    assert outcome.code == "validation_failed"
    assert any(issue.rule == "required" for issue in outcome.issues)


def test_a_mass_assigned_reference_meets_its_lookup_filter(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime

    refused = records.mass_update(
        "demo.Job", {"worker": 2}, [MassUpdateTarget(1, 1)], context
    )
    assert refused.outcomes[0].code == "validation_failed"
    assert any(
        issue.rule == "lookup_filter" for issue in refused.outcomes[0].issues
    )

    accepted = records.mass_update(
        "demo.Job", {"worker": 1}, [MassUpdateTarget(4, 1)], context
    )
    assert accepted.updated == 1


def test_a_field_policy_refuses_the_field_for_this_principal(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime

    refused = records.mass_update(
        "demo.Job", {"reviewer": "Rita"}, [MassUpdateTarget(1, 1)], context
    )
    assert refused.outcomes[0].code == "forbidden"

    granted = records.mass_update(
        "demo.Job",
        {"reviewer": "Rita"},
        [MassUpdateTarget(1, 1)],
        _context("reviewer"),
    )
    assert granted.updated == 1
    assert records.get("demo.Job", 1, context)["reviewer"] == "Rita"


# --- the declaration gate -----------------------------------------------------


def test_the_declaration_gate_refuses_before_touching_any_row(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime
    target = [MassUpdateTarget(1, 1)]

    for changes, named in (
        ({"missing": 1}, "missing"),
        ({"id": 9}, "id"),
        ({"version": 9}, "version"),
        ({"slug": "x"}, "slug"),
        ({"priority": "high", "version": 9}, "version"),
    ):
        with pytest.raises(MassAssignmentError) as error:
            records.mass_update("demo.Job", changes, target, context)
        assert named in str(error.value)
    assert records.get("demo.Job", 1, context)["priority"] == "low"

    with pytest.raises(MassAssignmentError):
        records.mass_update("demo.Job", {}, target, context)
    with pytest.raises(MassAssignmentError):
        records.mass_update("demo.Job", {"priority": "high"}, [], context)
    with pytest.raises(MassAssignmentError):
        records.mass_update(
            "demo.Job",
            {"priority": "high"},
            [MassUpdateTarget(index, 1) for index in range(1_001)],
            context,
        )


# --- audit --------------------------------------------------------------------


def test_each_update_writes_its_own_audit_event(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, context = runtime

    records.mass_update(
        "demo.Job",
        {"priority": "high"},
        [MassUpdateTarget(1, 1), MassUpdateTarget(2, 1), MassUpdateTarget(4, 1)],
        context,
    )

    events = records.audit_store.record_audit_events(entity="demo.Job")
    assert sorted(event.identity for event in events) == [1, 4]
    for event in events:
        assert any(change.field == "priority" for change in event.changes)


# --- the REST door ------------------------------------------------------------


TOKEN = "tide-development-token-that-is-long-enough"


def _api_app(tmp_path: Path, role: str = "operator") -> object:
    from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app

    model = compile_project(_project(tmp_path))
    repository = InMemoryRepository()
    repository.seed("demo.Worker", WORKERS)
    repository.seed("demo.Job", JOBS)
    repository.seed("demo.Tag", TAGS)
    records = RecordsService(model, repository)
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({role})),
        ),
    )


def _rest(app: object, method: str, path: str, **kwargs: object) -> object:
    import asyncio

    import httpx

    async def exercise() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://testserver",
        ) as client:
            return await client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {TOKEN}"},
                **kwargs,  # type: ignore[arg-type]
            )

    return asyncio.run(exercise())


def test_rest_mass_update_answers_row_by_row(tmp_path: Path) -> None:
    app = _api_app(tmp_path)

    response = _rest(
        app,
        "POST",
        "/api/v1/jobs/_mass-update",
        json={
            "changes": {"priority": "high"},
            "targets": [
                {"identity": 1, "version": 1},
                {"identity": 2, "version": 1},
                {"identity": 99, "version": 1},
                {"identity": 4, "version": 7},
            ],
        },
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    payload = response.json()  # type: ignore[attr-defined]
    assert payload["updated"] == 1
    assert payload["refused"] == 3
    assert [outcome["identity"] for outcome in payload["outcomes"]] == [1, 2, 99, 4]
    assert [outcome["code"] for outcome in payload["outcomes"]] == [
        None,
        "immutable_field",
        "not_found",
        "stale_version",
    ]
    assert payload["outcomes"][0]["status"] == "updated"
    assert payload["outcomes"][0]["version"] == 2


def test_rest_mass_update_spells_null_versions_like_if_match(
    tmp_path: Path,
) -> None:
    app = _api_app(tmp_path)

    response = _rest(
        app,
        "POST",
        "/api/v1/jobs/_mass-update",
        json={
            "changes": {"priority": "high"},
            "targets": [{"identity": 1, "version": "null"}],
        },
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    outcome = response.json()["outcomes"][0]  # type: ignore[attr-defined]
    assert outcome["status"] == "refused"
    assert outcome["code"] == "stale_version"


def test_rest_mass_update_answers_each_identity_for_itself(
    tmp_path: Path,
) -> None:
    app = _api_app(tmp_path)

    response = _rest(
        app,
        "POST",
        "/api/v1/jobs/_mass-update",
        json={
            "changes": {"priority": "high"},
            "targets": [
                {"identity": "abc", "version": 1},
                {"identity": 4, "version": 1},
            ],
        },
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    payload = response.json()  # type: ignore[attr-defined]
    assert [outcome["code"] for outcome in payload["outcomes"]] == [
        "invalid_identity",
        None,
    ]
    assert payload["outcomes"][1]["status"] == "updated"
    assert payload["updated"] == 1


def test_rest_mass_update_refuses_declaration_problems(tmp_path: Path) -> None:
    app = _api_app(tmp_path)

    # A field the update contract does not admit is refused by the typed
    # payload itself -- the same model the PATCH door validates with.
    unknown = _rest(
        app,
        "POST",
        "/api/v1/jobs/_mass-update",
        json={"changes": {"slug": "x"}, "targets": [{"identity": 1, "version": 1}]},
    )
    assert unknown.status_code == 422  # type: ignore[attr-defined]

    empty_changes = _rest(
        app,
        "POST",
        "/api/v1/jobs/_mass-update",
        json={"changes": {}, "targets": [{"identity": 1, "version": 1}]},
    )
    assert empty_changes.status_code == 400  # type: ignore[attr-defined]

    empty_targets = _rest(
        app,
        "POST",
        "/api/v1/jobs/_mass-update",
        json={"changes": {"priority": "high"}, "targets": []},
    )
    assert empty_targets.status_code == 422  # type: ignore[attr-defined]

    oversized = _rest(
        app,
        "POST",
        "/api/v1/jobs/_mass-update",
        json={
            "changes": {"priority": "high"},
            "targets": [
                {"identity": index, "version": 1} for index in range(1_001)
            ],
        },
    )
    assert oversized.status_code == 422  # type: ignore[attr-defined]

    # The door exists only where update is exposed: without it the path
    # falls through to the single-record GET shape, where POST is not a
    # method at all.
    missing = _rest(
        app,
        "POST",
        "/api/v1/workers/_mass-update",
        json={"changes": {"name": "x"}, "targets": [{"identity": 1}]},
    )
    assert missing.status_code == 405  # type: ignore[attr-defined]


def test_rest_mass_update_acknowledges_warnings_by_rule_id(
    tmp_path: Path,
) -> None:
    app = _api_app(tmp_path)
    body = {
        "changes": {"hours": "99.00"},
        "targets": [
            {"identity": 1, "version": 1},
            {"identity": 4, "version": 1},
        ],
    }

    first = _rest(app, "POST", "/api/v1/jobs/_mass-update", json=body)
    assert first.status_code == 200  # type: ignore[attr-defined]
    outcomes = first.json()["outcomes"]  # type: ignore[attr-defined]
    assert [outcome["code"] for outcome in outcomes] == [
        "validation_failed",
        "validation_failed",
    ]
    assert {
        issue["rule"]
        for outcome in outcomes
        for issue in outcome["issues"]
        if issue["severity"] == "warning"
    } == {"heavy_hours"}

    second = _rest(
        app,
        "POST",
        "/api/v1/jobs/_mass-update?acknowledge_warnings=heavy_hours",
        json=body,
    )
    assert second.status_code == 200  # type: ignore[attr-defined]
    payload = second.json()  # type: ignore[attr-defined]
    assert payload["updated"] == 2
    for outcome in payload["outcomes"]:
        assert "heavy_hours" in {issue["rule"] for issue in outcome["notices"]}


# --- the manifest names the door ----------------------------------------------


def _browse_presentations(app: object) -> dict[str, dict[str, object]]:
    response = _rest(app, "GET", "/api/v1/_tide/presentation")
    assert response.status_code == 200  # type: ignore[attr-defined]
    browses: dict[str, dict[str, object]] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "view" in node and "resource_path" in node:
                browses[str(node["view"])] = node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(response.json())  # type: ignore[attr-defined]
    return browses


def test_the_manifest_names_the_door_only_where_it_may_be_used(
    tmp_path: Path,
) -> None:
    """Presence carries the path, the version field's name and the bound;
    absence covers the old server and the no-permission principal with one
    signal, so a client that sees nothing offers nothing."""

    browses = _browse_presentations(_api_app(tmp_path))
    assert browses["demo.Job.browse"]["mass_update"] == {
        "path": "/api/v1/jobs/_mass-update",
        "version_field": "version",
        "limit": 1_000,
    }
    # An entity without a version field says so explicitly: null means
    # "send targets without assertions", not "old server".
    assert browses["demo.Tag.browse"]["mass_update"] == {
        "path": "/api/v1/tags/_mass-update",
        "version_field": None,
        "limit": 1_000,
    }

    watcher = _browse_presentations(_api_app(tmp_path, role="watcher"))
    assert watcher["demo.Job.browse"]["mass_update"] is None
    assert watcher["demo.Tag.browse"]["mass_update"] is not None


# --- the doors: the typed client and remote mode ------------------------------


def _remote_stack(tmp_path: Path) -> tuple[object, object, list[dict[str, object]], list[list[str]]]:
    """The remote TUI's whole stack over a captured wire.

    Returns the compiled model, the ASGI app, the captured `_mass-update`
    bodies, and the captured `acknowledge_warnings` query values.
    """

    import asyncio
    import json
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    model = compile_project(_project(tmp_path))
    from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app

    repository = InMemoryRepository()
    repository.seed("demo.Worker", WORKERS)
    repository.seed("demo.Job", JOBS)
    repository.seed("demo.Tag", TAGS)
    app = build_fastapi_app(
        model,
        RecordsService(model, repository),
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"operator"})),
        ),
    )
    bodies: list[dict[str, object]] = []
    acknowledged: list[list[str]] = []

    def dispatch(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_mass-update"):
            bodies.append(json.loads(request.content.decode("utf-8")))
            acknowledged.append(request.url.params.get_list("acknowledge_warnings"))

        async def send() -> httpx.Response:
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1",
                transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
            ) as forwarded:
                response = await forwarded.request(
                    request.method,
                    str(request.url),
                    headers=request.headers,
                    content=request.content,
                )
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=await response.aread(),
                    request=request,
                )

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, send()).result()

    transport = httpx.Client(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(dispatch)
    )
    return model, transport, bodies, acknowledged


def test_remote_mass_update_serializes_values_and_translates_outcomes(
    tmp_path: Path,
) -> None:
    """The twin sends values and identities, never sessions, and hands back
    the same outcome dataclasses the local service answers with."""

    from tide.api.client import TideApiClient
    from tide.api.remote import RemoteRecordsService
    from tide.services import MassUpdateResult

    model, transport, bodies, acknowledged = _remote_stack(tmp_path)
    with transport:  # type: ignore[attr-defined]
        client = TideApiClient(
            model, "http://127.0.0.1", TOKEN, http_client=transport
        )
        session = client.connect()
        context = RequestContext(
            Principal(session.principal, roles=frozenset(session.roles)),
            channel=Channel.TUI,
        )
        remote = RemoteRecordsService(model, client, session)

        result = remote.mass_update(
            "demo.Job",
            {"priority": "high"},
            [
                MassUpdateTarget(1, 1),
                MassUpdateTarget(2, 1),
                MassUpdateTarget(4, 7),
            ],
            context,
        )
        assert isinstance(result, MassUpdateResult)
        assert [outcome.status for outcome in result.outcomes] == [
            "updated",
            "refused",
            "refused",
        ]
        assert [outcome.code for outcome in result.outcomes] == [
            None,
            "immutable_field",
            "stale_version",
        ]
        assert result.outcomes[0].version == 2
        assert result.updated == 1

        warned = remote.mass_update(
            "demo.Job",
            {"hours": Decimal("99.00")},
            [MassUpdateTarget(4, 1)],
            context,
        )
        issue_rules = {
            (issue.rule, issue.severity)
            for issue in warned.outcomes[0].issues
        }
        assert ("heavy_hours", "warning") in issue_rules

        acked = remote.mass_update(
            "demo.Job",
            {"hours": Decimal("99.00")},
            [MassUpdateTarget(4, 1)],
            context,
            acknowledged_warnings=frozenset({"heavy_hours"}),
        )
        assert acked.updated == 1
        assert "heavy_hours" in {
            issue.rule for issue in acked.outcomes[0].notices
        }

    assert bodies[0] == {
        "changes": {"priority": "high"},
        "targets": [
            {"identity": 1, "version": 1},
            {"identity": 2, "version": 1},
            {"identity": 4, "version": 7},
        ],
    }
    # Exact decimals travel as strings, the way every mutation encodes them.
    assert bodies[1]["changes"] == {"hours": "99.00"}
    assert acknowledged[:3] == [[], [], ["heavy_hours"]]


def test_remote_mass_update_spells_null_and_absent_versions(
    tmp_path: Path,
) -> None:
    from tide.api.client import TideApiClient
    from tide.api.remote import RemoteRecordsService
    from tide.runtime.errors import MassAssignmentError as GateError

    model, transport, bodies, _acknowledged = _remote_stack(tmp_path)
    with transport:  # type: ignore[attr-defined]
        client = TideApiClient(
            model, "http://127.0.0.1", TOKEN, http_client=transport
        )
        session = client.connect()
        context = RequestContext(
            Principal(session.principal, roles=frozenset(session.roles)),
            channel=Channel.TUI,
        )
        remote = RemoteRecordsService(model, client, session)

        nulled = remote.mass_update(
            "demo.Job",
            {"priority": "high"},
            [MassUpdateTarget(1, NULL_VERSION)],
            context,
        )
        assert nulled.outcomes[0].code == "stale_version"

        unversioned = remote.mass_update(
            "demo.Tag",
            {"color": "blue"},
            [MassUpdateTarget(1), MassUpdateTarget(2)],
            context,
        )
        assert unversioned.updated == 2

        # The twin judges a bad request locally, exactly as the service
        # would, so both TUI modes refuse with the same message shape --
        # and nothing unassignable ever reaches the wire.
        with pytest.raises(GateError):
            remote.mass_update(
                "demo.Job", {"slug": "x"}, [MassUpdateTarget(1, 1)], context
            )

    assert bodies[0]["targets"] == [{"identity": 1, "version": "null"}]
    assert bodies[1]["targets"] == [{"identity": 1}, {"identity": 2}]
    assert len(bodies) == 2


def test_rest_mass_update_on_an_unversioned_entity(tmp_path: Path) -> None:
    app = _api_app(tmp_path)

    response = _rest(
        app,
        "POST",
        "/api/v1/tags/_mass-update",
        json={
            "changes": {"color": "blue"},
            "targets": [{"identity": 1}, {"identity": 2}],
        },
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    payload = response.json()  # type: ignore[attr-defined]
    assert payload["updated"] == 2
    assert all(outcome["version"] is None for outcome in payload["outcomes"])
