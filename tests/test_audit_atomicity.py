"""A change and the record of it land together or not at all.

The CRUD audit event was written after the data transaction had already
committed, on its own connection. A crash in the gap left a change nobody could
account for -- which is precisely what an audit trail exists to make impossible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tide import compile_project
from tide.data import InMemoryRepository, QuerySpec, SQLAlchemyRepository
from tide.runtime import Channel, Principal, RequestContext
from tide.services import RecordsService
from tide.services.action_store import (
    ActionStoreError,
    InMemoryActionExecutionStore,
    RecordAuditEvent,
)

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _context() -> RequestContext:
    return RequestContext(
        Principal("tests:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.REST,
        correlation_id="audit-atomicity",
    )


class _FailingAuditStore(InMemoryActionExecutionStore):
    """Refuses to record, the way a crash between the two writes would."""

    def record_audit(self, event: RecordAuditEvent, **kwargs: Any) -> None:
        raise ActionStoreError("audit storage is unavailable")


def _write_customer(records: RecordsService, code: str = "AUDIT") -> Any:
    session = records.create(
        "crm.Customer", _context(), {"code": code, "name": "Audited"}
    )
    return records.commit(session, _context())


def _stored(records: RecordsService) -> list[dict[str, Any]]:
    return records.query("crm.Customer", QuerySpec(), _context())


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_change_that_cannot_be_audited_is_not_kept(kind: str, tmp_path: Path) -> None:
    """The record used to survive its own missing audit entry."""

    records = _services(kind, tmp_path, audit_store=_FailingAuditStore())

    with pytest.raises(ActionStoreError):
        _write_customer(records)

    assert _stored(records) == []


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_an_audited_change_is_readable_afterwards(kind: str, tmp_path: Path) -> None:
    records = _services(kind, tmp_path)

    _write_customer(records)

    assert [row["code"] for row in _stored(records)] == ["AUDIT"]
    assert len(records.audit_store.record_audit_events(entity="crm.Customer")) == 1


def test_the_audit_row_is_written_on_the_record_s_own_connection(
    tmp_path: Path,
) -> None:
    """Rolling back is not proof of enlisting.

    A store that opened its own transaction and failed would also take the
    record down, because the error still escapes the write. What distinguishes
    the two is which connection the audit row is written on.
    """

    from tide.data.sqlalchemy_actions import SQLAlchemyActionExecutionStore

    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(
        model, f"sqlite+pysqlite:///{(tmp_path / 'enlist.db').as_posix()}"
    )
    repository.create_schema()
    store = SQLAlchemyActionExecutionStore(repository.engine, mode="managed")
    store.create_schema()
    seen: list[Any] = []
    original = store.record_audit

    def watching(event: RecordAuditEvent, *, connection: Any = None) -> None:
        seen.append(connection)
        original(event, connection=connection)

    store.record_audit = watching  # type: ignore[method-assign]
    records = RecordsService(model, repository, audit_store=store)

    _write_customer(records)

    assert len(seen) == 1
    assert seen[0] is not None, "the audit opened its own transaction"
    assert seen[0].engine is repository.engine


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_rejected_write_records_nothing(kind: str, tmp_path: Path) -> None:
    """The other direction: no audit entry for a change that never happened."""

    records = _services(kind, tmp_path)
    _write_customer(records)

    with pytest.raises(Exception):
        _write_customer(records)  # duplicate code

    assert len(records.audit_store.record_audit_events(entity="crm.Customer")) == 1


def _services(
    kind: str, tmp_path: Path, audit_store: Any | None = None
) -> RecordsService:
    model = compile_project(INVOICING)
    if kind == "memory":
        repository: Any = InMemoryRepository()
    else:
        repository = SQLAlchemyRepository(
            model, f"sqlite+pysqlite:///{(tmp_path / 'audit.db').as_posix()}"
        )
        repository.create_schema()
    return RecordsService(model, repository, audit_store=audit_store)
