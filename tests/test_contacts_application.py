"""Portability proof for the second checked-in TIDE application."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tide import compile_project
from tide.api.openapi import REST_OPERATIONS, rest_exposures
from tide.cli import main
from tide.data import (
    InMemoryRepository,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
)
from tide.development import (
    ApplicationApplyApproval,
    ApplicationApplyService,
    ApplicationGenerationPlan,
    ApplicationMaterializationService,
    seed_fake_data,
)
from tide.runtime import (
    AuthorizationError,
    Channel,
    Principal,
    RequestContext,
    configure_application_runtime,
)
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

from test_renderer_acceptance import (
    CONTRACT_COVERAGE,
    _RESOLVERS,
    _check_contract,
)


ROOT = Path(__file__).parents[1]
CONTACTS = ROOT / "applications" / "contacts"
PLAN_PATH = ROOT / "examples" / "ai_generation" / "contacts_plan.json"

CONTACTS_CONTRACT: dict[str, Any] = {
    "role": "contact_editor",
    "navigation": [
        {
            "label": "Application",
            "views": ["crm.Company.browse", "crm.Contact.browse"],
        }
    ],
    "browse": {
        "crm.Company.browse": {
            "columns": ["code", "name", "website", "active"],
            "alignments": {
                "code": "left",
                "name": "left",
                "website": "left",
                "active": "left",
            },
        },
        "crm.Contact.browse": {
            "columns": [
                "name",
                "email",
                "phone",
                "company",
                "status",
                "archived_at",
                "archived_by",
            ],
            "alignments": {
                "name": "left",
                "email": "left",
                "phone": "left",
                "company": "left",
                "status": "left",
                "archived_at": "left",
                "archived_by": "left",
            },
        },
    },
    "forms": {
        "crm.Company.edit": {
            "sections": [
                {
                    "kind": "group",
                    "label": "Companies",
                    "rows": [["code", "website"], ["name", "active"]],
                }
            ]
        },
        "crm.Contact.edit": {
            "sections": [
                {
                    "kind": "group",
                    "label": "Contacts",
                    "rows": [
                        ["name", "status"],
                        ["email", "archived_at"],
                        ["phone", "archived_by"],
                        ["company"],
                    ],
                }
            ]
        },
    },
    "reports": [],
}


def test_contacts_plan_is_the_exact_generated_application_baseline() -> None:
    preview = ApplicationMaterializationService().preview(_plan())

    assert preview.valid is True
    assert preview.workspace_writes_performed is False
    assert preview.candidate_persisted is False
    assert preview.application_database_accessed is False
    assert preview.external_commands_executed is False
    assert preview.temporary_candidate_deleted is True
    assert len(preview.artifacts) == 12
    assert not [check for check in preview.checks if check.status == "failed"]

    for artifact in preview.artifacts:
        checked_in = CONTACTS / artifact.path
        assert checked_in.is_file(), f"missing generated artifact: {artifact.path}"
        assert checked_in.read_text(encoding="utf-8") == artifact.content


def test_contacts_plan_crosses_the_real_approval_boundary_in_isolation(
    tmp_path: Path,
) -> None:
    service = ApplicationApplyService(tmp_path)
    preparation = service.prepare(_plan())

    assert preparation.ready is True
    assert preparation.writes_performed is False
    assert not (tmp_path / "applications").exists()

    result = service.apply(
        _plan(),
        ApplicationApplyApproval.from_preparation(preparation),
    )
    generated = compile_project(tmp_path / "applications" / "contacts")

    assert result.applied is True
    assert set(generated.entities) == {"crm.Company", "crm.Contact"}
    assert set(generated.roles) == {"contact_editor", "contact_viewer"}
    assert generated.entity("crm.Contact").actions["archive"]["idempotent"] is True


def test_contacts_demo_exercises_shared_security_and_action_services() -> None:
    model = compile_project(CONTACTS)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 11
    records, actions = _services(model, repository)
    editor = _context("contact_editor", "contacts:editor")
    viewer = _context("contact_viewer", "contacts:viewer")

    assert records.get("crm.Company", 1, viewer)["code"] == "ADRIA"
    archived = actions.execute(
        "crm.Contact",
        "archive",
        1,
        {},
        editor,
        idempotency_key="contacts-demo-archive-1",
    )
    replayed = actions.execute(
        "crm.Contact",
        "archive",
        1,
        {},
        editor,
        idempotency_key="contacts-demo-archive-1",
    )

    assert archived["status"] == replayed["status"] == "archived"
    assert archived["archived_by"] == "contacts:editor"
    assert archived["archived_at"].tzinfo is not None
    with pytest.raises(AuthorizationError):
        records.create("crm.Company", viewer, {"code": "NO", "name": "Denied"})
    with pytest.raises(AuthorizationError):
        actions.execute("crm.Contact", "archive", 2, {}, viewer)


def test_contacts_workflow_runs_on_managed_sqlalchemy_storage() -> None:
    model = compile_project(CONTACTS)
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
    try:
        repository.create_schema()
        records, actions = _services(model, repository)
        editor = _context("contact_editor", "contacts:sql-editor")

        company = records.commit(
            records.create(
                "crm.Company",
                editor,
                {
                    "code": "SQL",
                    "name": "SQL-backed Company",
                    "website": "https://sql.example",
                },
            ),
            editor,
        )
        contact = records.commit(
            records.create(
                "crm.Contact",
                editor,
                {
                    "name": "SQL Contact",
                    "email": "contact@sql.example",
                    "company": company["id"],
                },
            ),
            editor,
        )
        archived = actions.execute(
            "crm.Contact",
            "archive",
            contact["id"],
            {},
            editor,
            idempotency_key="contacts-sql-archive-1",
        )

        repository.check_readiness()
        assert archived["status"] == "archived"
        assert archived["company"] == company["id"]
    finally:
        repository.dispose()


def test_contacts_application_owns_deterministic_faker_seeding() -> None:
    model = compile_project(CONTACTS)
    repository = InMemoryRepository()
    records, actions = _services(model, repository)

    seeded = seed_fake_data(
        model,
        records,
        actions,
        _context("contact_editor", "contacts:seed"),
        counts={"companies": 3, "contacts": 12},
        random_seed=20260806,
        locale="en_US",
    )

    assert seeded["companies"] == 3
    assert seeded["contacts"] == 12
    assert len(repository.all("crm.Company")) == 3
    contacts = repository.all("crm.Contact")
    assert len(contacts) == 12
    assert seeded["archived_contacts"] == sum(
        contact["status"] == "archived" for contact in contacts
    )


def test_contacts_cli_persists_application_owned_fake_data(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    model = compile_project(CONTACTS)
    url = f"sqlite+pysqlite:///{(tmp_path / 'contacts.db').as_posix()}"
    repository = SQLAlchemyRepository(model, url)
    repository.create_schema()
    SQLAlchemyCursorStore(repository.engine, mode="managed").create_schema()
    SQLAlchemyActionExecutionStore(
        repository.engine,
        mode="managed",
    ).create_schema()
    repository.dispose()
    monkeypatch.setenv("CONTACTS_DATABASE_URL", url)

    result = main(
        [
            "db",
            "seed",
            str(CONTACTS),
            "--database-env",
            "CONTACTS_DATABASE_URL",
            "--role",
            "contact_editor",
            "--count",
            "companies=4",
            "--count",
            "contacts=15",
            "--random-seed",
            "20260806",
        ]
    )

    assert result == 0
    assert "companies=4, contacts=15" in capsys.readouterr().out
    persisted = SQLAlchemyRepository(model, url)
    try:
        assert len(persisted.all("crm.Company")) == 4
        assert len(persisted.all("crm.Contact")) == 15
    finally:
        persisted.dispose()


@pytest.mark.parametrize("renderer", tuple(CONTRACT_COVERAGE))
def test_contacts_metadata_resolves_consistently_in_every_renderer(
    renderer: str,
) -> None:
    model = compile_project(CONTACTS)

    resolved = _RESOLVERS[renderer](model, CONTACTS_CONTRACT)

    _check_contract(resolved, CONTACTS_CONTRACT, renderer)


def test_contacts_rest_and_mcp_exposure_remain_explicit() -> None:
    model = compile_project(CONTACTS)
    exposures = rest_exposures(model, allowed_operations=REST_OPERATIONS)

    assert exposures["crm.Company"].operations == REST_OPERATIONS
    assert exposures["crm.Contact"].operations == REST_OPERATIONS
    contact_exposure = model.entity("crm.Contact").metadata["expose"]
    assert contact_exposure["mcp"]["resources"] == ("schema", "record", "audit")
    assert contact_exposure["mcp"]["tools"] == (
        "search",
        "create",
        "update",
        "delete",
    )
    assert model.entity("crm.Contact").actions["archive"]["expose"] == {
        "rest": True,
        "mcp": True,
    }


def _plan() -> ApplicationGenerationPlan:
    return ApplicationGenerationPlan.model_validate_json(
        PLAN_PATH.read_text(encoding="utf-8")
    )


def _context(role: str, identifier: str) -> RequestContext:
    return RequestContext(
        principal=Principal(identifier, roles=frozenset({role})),
        channel=Channel.SYSTEM,
    )


def _services(
    model: Any,
    repository: Any,
) -> tuple[RecordsService, ActionService]:
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions) is True
    return records, actions
