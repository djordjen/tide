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
    "roles:\n"
    "  operator:\n"
    "    grants:\n"
    "    - demo.all\n"
    "  reviewer:\n"
    "    grants:\n"
    "    - demo.all\n"
    "    - demo.review\n"
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
    " update: demo.all}\n"
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
