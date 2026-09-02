"""How a loaded page names the records it points at.

A browse grid shows `customer` as "ACME - ACME Ltd", not as `1`, and every
renderer used to buy that one referenced record at a time. These are the
contract tests for resolving a whole page at once, with the same authority
the per-record fetch had: entity capability, row policy, field protection.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import event

from tide import compile_project
from tide.data import InMemoryRepository, QuerySpec, SQLAlchemyRepository
from tide.data.repository import BATCH_IDENTITY_LIMIT
from tide.runtime import Channel, Principal, RequestContext
from tide.security import SecurityEngine
from tide.services import RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"

CUSTOMERS = [
    {"id": 1, "code": "ACME", "name": "ACME Ltd", "email": None, "active": True, "invoices": []},
    {"id": 2, "code": "BETA", "name": "Beta GmbH", "email": None, "active": True, "invoices": []},
    {"id": 3, "code": "OLD", "name": "Inactive Co", "email": None, "active": False, "invoices": []},
]
PRODUCTS = [
    {"id": 1, "code": "CONS", "name": "Consulting", "unit_price": Decimal("4.20"), "active": True},
    {"id": 2, "code": "GONE", "name": "Retired", "unit_price": Decimal("1.00"), "active": False},
]
LINES = [
    {
        "id": 1,
        "line_number": 1,
        "description": "Consulting",
        "quantity": Decimal("1.000"),
        "unit_price": Decimal("4.20"),
        "product": 1,
        "total": Decimal("4.20"),
    },
    {
        "id": 2,
        "line_number": 2,
        "description": "Retired thing",
        "quantity": Decimal("1.000"),
        "unit_price": Decimal("1.00"),
        "product": 2,
        "total": Decimal("1.00"),
    },
]
# Four invoices over three customers: two name the same one, and the fourth
# names a customer the read policy hides. A resolver that asks per row and a
# resolver that asks per page disagree about how many queries that is.
INVOICES = [
    {
        "id": index,
        "number": f"INV-2026-{index:04d}",
        "invoice_date": date(2026, 7, 14),
        "currency": "EUR",
        "status": "draft",
        "posted_at": None,
        "posted_by": None,
        "version": 1,
        "customer": customer,
        "total": Decimal("5.20") if index == 1 else Decimal("0.00"),
        "lines": LINES if index == 1 else [],
    }
    for index, customer in enumerate((1, 2, 1, 3), start=1)
]


def context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal("user:clerk", roles=frozenset(roles or ("sales_clerk",))),
        channel=Channel.TUI,
    )


class CountingRepository:
    """Pass every call through, and remember how many of each there were.

    The point of this change is the *number* of loads, which no assertion
    about the returned text can see.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            return attribute(*args, **kwargs)

        return counted


@pytest.fixture(params=("memory", "sql"))
def seeded(request: pytest.FixtureRequest) -> Iterator[tuple[RecordsService, Any, Any]]:
    model = compile_project(INVOICING)
    repository: InMemoryRepository | SQLAlchemyRepository
    if request.param == "memory":
        repository = InMemoryRepository()
    else:
        repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
        repository.create_schema()
    repository.seed("crm.Customer", CUSTOMERS)
    repository.seed("catalog.Product", PRODUCTS)
    repository.seed("sales.Invoice", INVOICES)
    counting = CountingRepository(repository)
    records = RecordsService(model, counting, SecurityEngine(model))
    yield records, counting, repository
    if isinstance(repository, SQLAlchemyRepository):
        repository.dispose()


def test_a_batch_load_answers_only_for_the_rows_its_criteria_admit(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    _, _, repository = seeded

    rows = repository.get_many(
        "crm.Customer",
        [1, 3, 99, 1],
        row_criteria=("active == true",),
    )

    # 3 is refused by the criteria and 99 does not exist. Both are simply
    # absent, and the repeat of 1 is asked about once.
    assert sorted(rows) == [1]
    assert rows[1]["code"] == "ACME"


def test_a_batch_load_leaves_child_collections_out(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    _, _, repository = seeded

    rows = repository.get_many("sales.Invoice", [1])

    # Invoice 1 has lines, and this is the one load in the stack that applies
    # no child policy at all -- so it must not be the one that returns them.
    assert rows[1]["number"] == "INV-2026-0001"
    assert "lines" not in rows[1]


def test_a_batch_load_of_nothing_asks_nothing(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    _, _, repository = seeded

    assert repository.get_many("crm.Customer", []) == {}


def test_a_batch_load_splits_at_the_adapter_parameter_cap(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    _, _, repository = seeded
    if not isinstance(repository, SQLAlchemyRepository):
        pytest.skip("bound-parameter caps are a SQL driver concern")
    statements: list[str] = []

    @event.listens_for(repository.engine, "before_cursor_execute")
    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    rows = repository.get_many(
        "crm.Customer",
        [*range(1, BATCH_IDENTITY_LIMIT * 2 + 3)],
        row_criteria=("active == true",),
    )

    # Every driver caps bound parameters -- SQLite at 999 in the builds that
    # still use the old default -- so a page wider than the cap has to split.
    # Three statements for 1,002 identities, and the answer is unaffected.
    assert len(statements) == 3
    assert sorted(rows) == [1, 2]


def test_a_page_names_each_referenced_record_once_however_many_rows_point_at_it(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    records, counting, _ = seeded
    page = records.query_page("sales.Invoice", QuerySpec(limit=10), context())
    counting.calls.clear()

    displays = records.reference_displays("sales.Invoice", page.records, context())

    assert displays.display("crm.Customer", 1) == "ACME - ACME Ltd"
    assert displays.display("crm.Customer", 2) == "BETA - Beta GmbH"
    # Six reference cells -- four customers on the invoices, two products on
    # invoice 1's lines -- naming five distinct records across two entities.
    # One load each. A resolver that asks per row leaves `get` at five.
    assert counting.calls["get_many"] == 2
    assert counting.calls["get"] == 0


def test_a_row_policy_hides_the_display_without_hiding_the_row(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    records, _, _ = seeded
    page = records.query_page("sales.Invoice", QuerySpec(limit=10), context())

    displays = records.reference_displays("sales.Invoice", page.records, context())

    assert [record["customer"] for record in page.records] == [1, 2, 1, 3]
    # Customer 3 is inactive, and `active == true` is the read policy. The
    # invoice naming it is still listed; only the name it would show is gone.
    assert displays.display("crm.Customer", 3) is None


def test_a_reference_the_principal_cannot_read_resolves_to_nothing(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    records, counting, _ = seeded
    viewer = context("summary_viewer")
    page = records.query_page("sales.Invoice", QuerySpec(limit=10), viewer)
    counting.calls.clear()

    displays = records.reference_displays("sales.Invoice", page.records, viewer)

    # `summary_viewer` holds sales.invoice.read and nothing else, so customers
    # are not merely policy-filtered -- they are not readable at all, and the
    # resolver must not go looking.
    assert displays.display("crm.Customer", 1) is None
    assert counting.calls["get_many"] == 0


def test_a_collection_child_resolves_its_own_references(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    records, _, _ = seeded
    invoice = records.get("sales.Invoice", 1, context())

    displays = records.reference_displays("sales.Invoice", [invoice], context())

    assert [line["product"] for line in invoice["lines"]] == [1, 2]
    assert displays.display("catalog.Product", 1) == "CONS - Consulting"
    # Product 2 is inactive and READABLE: retirement is a lookup_filter on
    # the referencing edge now, not a read policy, so the historical line
    # names its product instead of showing a withheld display. The
    # policy-hidden case stays covered by the customer assertions above.
    assert displays.display("catalog.Product", 2) == "GONE - Retired"


def test_resolving_a_page_costs_one_select_per_target_entity(
    seeded: tuple[RecordsService, Any, Any],
) -> None:
    records, _, repository = seeded
    if not isinstance(repository, SQLAlchemyRepository):
        pytest.skip("statement capture applies only to the SQL adapter")
    page = records.query_page("sales.Invoice", QuerySpec(limit=10), context())
    statements: list[str] = []

    @event.listens_for(repository.engine, "before_cursor_execute")
    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.upper())

    records.reference_displays("sales.Invoice", page.records, context())

    # One `IN` per target entity, and nothing else: no per-row SELECT, and no
    # statement for `sales.Invoice`, which nothing on this page points at.
    assert [statement.split(" \nFROM ")[1].split()[0] for statement in statements] == [
        "CRM_CUSTOMER",
        "CATALOG_PRODUCT",
    ]
    assert all(" IN (" in statement for statement in statements)


def _guarded_project(root: Path) -> Path:
    """A two-entity application whose display field is a guarded one.

    Nothing in the sample applications displays a record through a field a
    reader may be refused, and that is exactly the case worth pinning: the
    batch load must not become the way a protected value reaches a grid.
    """

    project = root / "guarded"
    models = project / "models"
    security = project / "security"
    models.mkdir(parents=True)
    security.mkdir()
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Guarded, version: 0.1.0}",
                "model: {paths: [models]}",
                "security: {paths: [security]}",
            ]
        ),
        encoding="utf-8",
    )
    (models / "owner.yaml").write_text(
        "\n".join(
            [
                "entity: secret.Owner",
                "display: code_name",
                "permissions: {list: secret.read, read: secret.read}",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  code_name: {type: string, length: 40, required: true}",
            ]
        ),
        encoding="utf-8",
    )
    (models / "ticket.yaml").write_text(
        "\n".join(
            [
                "entity: secret.Ticket",
                "display: subject",
                "permissions: {list: secret.read, read: secret.read}",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  subject: {type: string, length: 40, required: true}",
                "  owner:",
                "    type: reference",
                "    target: secret.Owner",
                "    storage: owner_id",
                "    required: true",
            ]
        ),
        encoding="utf-8",
    )
    (security / "policies.yaml").write_text(
        "\n".join(
            [
                "permissions: [secret.read, secret.reveal]",
                "roles:",
                "  clerk: {grants: [secret.read]}",
                "  handler: {grants: [secret.read, secret.reveal]}",
                "field_policies:",
                "  - {entity: secret.Owner, field: code_name, read: secret.reveal}",
            ]
        ),
        encoding="utf-8",
    )
    return project


@pytest.fixture
def guarded(tmp_path: Path) -> tuple[RecordsService, CountingRepository]:
    model = compile_project(_guarded_project(tmp_path))
    repository = InMemoryRepository()
    repository.seed("secret.Owner", [{"id": 1, "code_name": "BLUEBIRD"}])
    repository.seed("secret.Ticket", [{"id": 1, "subject": "Lost key", "owner": 1}])
    counting = CountingRepository(repository)
    return RecordsService(model, counting, SecurityEngine(model)), counting


def test_a_display_over_a_field_the_reader_may_not_see_is_never_loaded(
    guarded: tuple[RecordsService, CountingRepository],
) -> None:
    records, counting = guarded
    clerk = RequestContext(
        principal=Principal("user:clerk", roles=frozenset({"clerk"})),
        channel=Channel.TUI,
    )
    tickets = [records.get("secret.Ticket", 1, clerk)]
    counting.calls.clear()

    displays = records.reference_displays("secret.Ticket", tickets, clerk)

    assert displays.display("secret.Owner", 1) is None
    # Not merely withheld on the way out: the batch load applies no field
    # policy of its own, so the guarded value must not be read at all.
    assert counting.calls["get_many"] == 0


def test_a_reader_who_may_see_the_display_field_gets_the_display(
    guarded: tuple[RecordsService, CountingRepository],
) -> None:
    records, _ = guarded
    handler = RequestContext(
        principal=Principal("user:handler", roles=frozenset({"handler"})),
        channel=Channel.TUI,
    )
    tickets = [records.get("secret.Ticket", 1, handler)]

    displays = records.reference_displays("secret.Ticket", tickets, handler)

    assert displays.display("secret.Owner", 1) == "BLUEBIRD"


class LyingRepository(CountingRepository):
    """Return every row asked for, whatever the criteria say.

    An adapter whose SQL translation drifts from the expression evaluator
    would look exactly like this, and the row policy is the thing that must
    not depend on which adapter is underneath.
    """

    def get_many(
        self,
        entity: str,
        identities: Any,
        *,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Any = None,
    ) -> dict[Any, dict[str, Any]]:
        del row_criteria, criteria_parameters
        return self._inner.get_many(entity, identities)


def test_the_service_refuses_a_row_its_adapter_should_not_have_returned() -> None:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    repository.seed("crm.Customer", CUSTOMERS)
    repository.seed("sales.Invoice", INVOICES)
    records = RecordsService(model, LyingRepository(repository), SecurityEngine(model))
    page = records.query_page("sales.Invoice", QuerySpec(limit=10), context())

    displays = records.reference_displays("sales.Invoice", page.records, context())

    assert displays.display("crm.Customer", 1) == "ACME - ACME Ltd"
    # Customer 3 is inactive. The adapter handed it over; the service is the
    # second place that says no, and one of the two has to hold.
    assert displays.display("crm.Customer", 3) is None
