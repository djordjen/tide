"""A reference field's declared criterion narrows pickers and gates writes.

``lookup_filter`` is one boolean expression over the *target* entity,
declared on the reference edge. Pickers apply it the way row policies are
applied -- inside the repository query, whoever is asking -- and the commit
refuses a newly chosen row the criterion excludes, while never re-firing on
values that were already stored. Every layer sits in this one suite because
the feature is one declaration reaching all of them; the way it breaks is
one layer quietly not reading it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from tide import compile_project
from tide.data import (
    FilterCondition,
    InMemoryRepository,
    QuerySpec,
    SQLAlchemyRepository,
    SortField,
)
from tide.runtime import (
    AuthorizationError,
    Channel,
    InvalidQueryCursor,
    Principal,
    RequestContext,
)
from tide.runtime.errors import ValidationFailed
from tide.services import RecordsService

MANIFEST = (
    'schema_version: "0.1"\n'
    "application: {name: FilteredLookups, version: 0.1.0}\n"
    "database: {mode: managed}\n"
    "model: {paths: [models]}\n"
    "views: {paths: [views]}\n"
    "security: {paths: [security]}\n"
)

# ``tier`` is readable only with demo.detail, which the operator is not
# granted: the criterion referencing it must keep working anyway, because
# the rule belongs to the model, not to the requester.
POLICIES = (
    "permissions:\n"
    "- demo.all\n"
    "- demo.detail\n"
    "roles:\n"
    "  operator:\n"
    "    grants:\n"
    "    - demo.all\n"
    "field_policies:\n"
    "- entity: demo.Carrier\n"
    "  field: tier\n"
    "  read: demo.detail\n"
)

CARRIER = (
    "entity: demo.Carrier\n"
    "display: name\n"
    "search_fields: [name]\n"
    "expose:\n"
    "  rest:\n"
    "    path: carriers\n"
    "    operations: [list, get]\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  name: {type: string, length: 60, required: true}\n"
    "  active: {type: boolean, default: true}\n"
    "  tier: {type: string, length: 20}\n"
)

PART = (
    "entity: demo.Part\n"
    "display: name\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  name: {type: string, length: 60, required: true}\n"
    "  stocked: {type: boolean, default: true}\n"
)

ORDER = (
    "entity: demo.Order\n"
    "display: code\n"
    "expose:\n"
    "  tui: true\n"
    "  rest:\n"
    "    path: orders\n"
    "    operations: [list, get, create, update]\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  code: {type: string, length: 40, required: true}\n"
    "  carrier:\n"
    "    type: reference\n"
    "    target: demo.Carrier\n"
    "    storage: carrier_id\n"
    "    lookup_view: demo.Carrier.lookup\n"
    "    lookup_filter: 'active == true'\n"
    "  premium_carrier:\n"
    "    type: reference\n"
    "    target: demo.Carrier\n"
    "    storage: premium_carrier_id\n"
    "    lookup_filter: \"active == true and tier == 'gold'\"\n"
    "  fallback_carrier:\n"
    "    type: reference\n"
    "    target: demo.Carrier\n"
    "    storage: fallback_carrier_id\n"
    "    lookup_view: demo.Carrier.lookup\n"
    "  items:\n"
    "    type: collection\n"
    "    target: demo.Item\n"
    "    inverse: order\n"
    "    cascade: [create, update]\n"
    "    orphan_delete: true\n"
)

ITEM = (
    "entity: demo.Item\n"
    "permissions: {list: demo.all, read: demo.all, create: demo.all,"
    " update: demo.all}\n"
    "fields:\n"
    "  id: {type: integer, primary_key: true}\n"
    "  label: {type: string, length: 60, required: true}\n"
    "  order:\n"
    "    type: reference\n"
    "    target: demo.Order\n"
    "    storage: order_id\n"
    "    inverse: items\n"
    "    required: true\n"
    "    on_delete: cascade\n"
    "  part:\n"
    "    type: reference\n"
    "    target: demo.Part\n"
    "    storage: part_id\n"
    "    lookup_filter: 'stocked == true'\n"
)

CARRIER_LOOKUP = (
    "view: demo.Carrier.lookup\n"
    "entity: demo.Carrier\n"
    "kind: lookup\n"
    "columns: [name]\n"
    "search: [name]\n"
)

ORDER_BROWSE = (
    "view: demo.Order.browse\n"
    "entity: demo.Order\n"
    "kind: browse\n"
    "columns: [code]\n"
)

ORDER_EDIT = (
    "view: demo.Order.edit\n"
    "entity: demo.Order\n"
    "kind: form\n"
    "layout:\n"
    "- group: Order\n"
    "  rows:\n"
    "  - - code\n"
    "    - carrier\n"
    "  - - fallback_carrier\n"
    "fields:\n"
    "  carrier: {editor: lookup}\n"
    "  fallback_carrier: {editor: lookup}\n"
)

CARRIERS = [
    {"id": 1, "name": "Anchor", "active": True, "tier": "gold"},
    {"id": 2, "name": "Baltic", "active": True, "tier": "silver"},
    {"id": 3, "name": "Coastal", "active": False, "tier": "gold"},
    {"id": 4, "name": "Duna", "active": True, "tier": "gold"},
]

PARTS = [
    {"id": 1, "name": "Bolt", "stocked": True},
    {"id": 2, "name": "Washer", "stocked": False},
]


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "filtered-lookups"
    for relative, text in (
        ("tide.yaml", MANIFEST),
        ("security/policies.yaml", POLICIES),
        ("models/carrier.yaml", CARRIER),
        ("models/part.yaml", PART),
        ("models/order.yaml", ORDER),
        ("models/item.yaml", ITEM),
        ("views/carrier-lookup.yaml", CARRIER_LOOKUP),
        ("views/order-browse.yaml", ORDER_BROWSE),
        ("views/order-edit.yaml", ORDER_EDIT),
    ):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return project


def _context() -> RequestContext:
    return RequestContext(
        Principal("tests:operator", roles=frozenset({"operator"})),
        channel=Channel.REST,
        correlation_id="filtered-lookups",
    )


@pytest.fixture(params=("memory", "sql"))
def runtime(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[RecordsService, InMemoryRepository | SQLAlchemyRepository, RequestContext]]:
    model = compile_project(_project(tmp_path))
    if request.param == "memory":
        repository: InMemoryRepository | SQLAlchemyRepository = InMemoryRepository()
    else:
        repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
        repository.create_schema()
    repository.seed("demo.Carrier", CARRIERS)
    repository.seed("demo.Part", PARTS)
    yield RecordsService(model, repository), repository, _context()
    if isinstance(repository, SQLAlchemyRepository):
        repository.dispose()


# --- resolving the edge -------------------------------------------------------


def test_lookup_criteria_resolves_the_declared_edge(tmp_path: Path) -> None:
    model = compile_project(_project(tmp_path))
    records = RecordsService(model, InMemoryRepository())

    assert records.lookup_criteria("demo.Order", "carrier") == ("active == true",)
    assert records.lookup_criteria("demo.Order", "premium_carrier") == (
        "active == true and tier == 'gold'",
    )
    # An undeclared edge is silence, not an error: callers may ask for any
    # reference and apply whatever comes back.
    assert records.lookup_criteria("demo.Item", "order") == ()


def test_lookup_criteria_refuses_a_non_reference_edge(tmp_path: Path) -> None:
    model = compile_project(_project(tmp_path))
    records = RecordsService(model, InMemoryRepository())

    with pytest.raises(ValueError):
        records.lookup_criteria("demo.Order", "missing")
    with pytest.raises(ValueError):
        records.lookup_criteria("demo.Order", "code")


# --- pickers narrow (both repositories) ---------------------------------------


def test_query_criteria_narrow_both_repositories(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, _repository, context = runtime

    page = records.query_page(
        "demo.Carrier",
        QuerySpec(
            lookup_source=("demo.Order", "carrier"),
            sort=(SortField("name"),),
        ),
        context,
    )

    assert [record["id"] for record in page.records] == [1, 2, 4]


def test_criteria_compose_with_ordinary_filters(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, _repository, context = runtime

    page = records.query_page(
        "demo.Carrier",
        QuerySpec(
            filters=(FilterCondition("name", "icontains", "a"),),
            lookup_source=("demo.Order", "carrier"),
            sort=(SortField("name"),),
        ),
        context,
    )

    assert [record["id"] for record in page.records] == [1, 2, 4]


def test_criteria_may_name_fields_the_requester_cannot_read(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    """The criterion is the model's rule, so field policies do not veto it --
    while the same field in a caller-authored filter is refused as ever."""

    records, _repository, context = runtime

    page = records.query_page(
        "demo.Carrier",
        QuerySpec(
            lookup_source=("demo.Order", "premium_carrier"),
            sort=(SortField("name"),),
        ),
        context,
    )
    assert [record["id"] for record in page.records] == [1, 4]

    with pytest.raises(AuthorizationError):
        records.query_page(
            "demo.Carrier",
            QuerySpec(filters=(FilterCondition("tier", "eq", "gold"),)),
            context,
        )


def test_cursor_pages_keep_the_criteria(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, _repository, context = runtime
    source = ("demo.Order", "carrier")

    first = records.query_page(
        "demo.Carrier",
        QuerySpec(lookup_source=source, sort=(SortField("name"),), limit=2),
        context,
    )
    assert [record["id"] for record in first.records] == [1, 2]
    assert first.next_cursor is not None

    second = records.query_page(
        "demo.Carrier",
        QuerySpec(
            lookup_source=source,
            sort=(SortField("name"),),
            limit=2,
            cursor=first.next_cursor,
        ),
        context,
    )
    assert [record["id"] for record in second.records] == [4]

    # A cursor minted under the criteria must not open an unfiltered page:
    # the criteria are part of the query's shape.
    with pytest.raises(InvalidQueryCursor):
        records.query_page(
            "demo.Carrier",
            QuerySpec(sort=(SortField("name"),), limit=2, cursor=first.next_cursor),
            context,
        )


def test_lookup_records_threads_criteria(
    runtime: tuple[RecordsService, RequestContext],
) -> None:
    records, _repository, context = runtime
    source = ("demo.Order", "carrier")

    browsing = records.lookup_records(
        "demo.Carrier", ("name",), "", context, source=source
    )
    assert [record["id"] for record in browsing] == [1, 2, 4]

    searching = records.lookup_records(
        "demo.Carrier", ("name",), "Coastal", context, source=source
    )
    assert searching == ()


def test_a_lookup_source_must_target_the_queried_entity(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    from tide.runtime.errors import QueryFieldError

    records, _repository, context = runtime

    with pytest.raises(QueryFieldError):
        records.query_page(
            "demo.Carrier",
            QuerySpec(lookup_source=("demo.Item", "order")),
            context,
        )
    with pytest.raises(QueryFieldError):
        records.query_page(
            "demo.Carrier",
            QuerySpec(lookup_source=("demo.Order", "missing")),
            context,
        )


# --- writes refuse newly chosen ineligible rows -------------------------------


def _order(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"code": "O-1"}
    values.update(overrides)
    return values


def test_create_refuses_an_ineligible_reference(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    records, _repository, context = runtime
    session = records.create("demo.Order", context, _order(carrier=3))

    with pytest.raises(ValidationFailed) as failure:
        records.commit(session, context)

    issues = failure.value.issues
    assert [(issue.rule, issue.severity, issue.fields) for issue in issues] == [
        ("lookup_filter", "error", ("carrier",))
    ]


def test_create_accepts_an_eligible_reference(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    records, _repository, context = runtime
    session = records.create("demo.Order", context, _order(carrier=1))

    stored = records.commit(session, context)

    assert stored["carrier"] == 1


def test_each_edge_applies_its_own_criterion(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    """Baltic is active (fine for carrier) but silver: only the premium edge
    refuses it."""

    records, _repository, context = runtime
    session = records.create(
        "demo.Order", context, _order(carrier=2, premium_carrier=2)
    )

    with pytest.raises(ValidationFailed) as failure:
        records.commit(session, context)

    assert [(issue.rule, issue.fields) for issue in failure.value.issues] == [
        ("lookup_filter", ("premium_carrier",))
    ]


def test_an_unchanged_stored_reference_never_refires(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    """TIDE tolerates rows it did not write: an order already pointing at a
    retired carrier stays editable as long as the pointer is not the edit."""

    records, repository, context = runtime
    repository.seed(  # type: ignore[union-attr]
        "demo.Order",
        [{"id": 1, "code": "O-9", "carrier": 3, "premium_carrier": None, "items": []}],
    )

    edit = records.begin_edit("demo.Order", 1, context)
    edit.set("code", "O-9-renamed")
    stored = records.commit(edit, context)

    assert stored["code"] == "O-9-renamed"
    assert stored["carrier"] == 3


def test_changing_to_an_ineligible_reference_refuses(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    records, repository, context = runtime
    repository.seed(  # type: ignore[union-attr]
        "demo.Order",
        [{"id": 1, "code": "O-9", "carrier": 1, "premium_carrier": None, "items": []}],
    )

    edit = records.begin_edit("demo.Order", 1, context)
    edit.set("carrier", 3)

    with pytest.raises(ValidationFailed) as failure:
        records.commit(edit, context)

    assert [(issue.rule, issue.fields) for issue in failure.value.issues] == [
        ("lookup_filter", ("carrier",))
    ]


def test_child_rows_are_gated_per_row(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    records, _repository, context = runtime
    session = records.create(
        "demo.Order",
        context,
        _order(carrier=1, items=[{"label": "Fastening", "part": 2}]),
    )

    with pytest.raises(ValidationFailed) as failure:
        records.commit(session, context)

    assert [(issue.rule, issue.fields) for issue in failure.value.issues] == [
        ("lookup_filter", ("part",))
    ]


def test_an_unchanged_child_reference_never_refires(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    records, repository, context = runtime
    repository.seed(  # type: ignore[union-attr]
        "demo.Order",
        [
            {
                "id": 1,
                "code": "O-9",
                "carrier": 1,
                "premium_carrier": None,
                "items": [{"id": 1, "label": "Fastening", "part": 2}],
            }
        ],
    )

    edit = records.begin_edit("demo.Order", 1, context)
    edit.set("code", "O-9-renamed")
    stored = records.commit(edit, context)

    assert [item["part"] for item in stored["items"]] == [2]


def test_changing_a_child_reference_to_an_ineligible_row_refuses(
    runtime: tuple[RecordsService, object, RequestContext],
) -> None:
    from copy import deepcopy

    records, repository, context = runtime
    repository.seed(  # type: ignore[union-attr]
        "demo.Order",
        [
            {
                "id": 1,
                "code": "O-9",
                "carrier": 1,
                "premium_carrier": None,
                "items": [{"id": 1, "label": "Fastening", "part": 1}],
            }
        ],
    )

    edit = records.begin_edit("demo.Order", 1, context)
    items = deepcopy(list(edit.values["items"]))
    items[0]["part"] = 2
    edit.set("items", items)

    with pytest.raises(ValidationFailed) as failure:
        records.commit(edit, context)

    assert [(issue.rule, issue.fields) for issue in failure.value.issues] == [
        ("lookup_filter", ("part",))
    ]


# --- the doors: REST ----------------------------------------------------------


TOKEN = "tide-development-token-that-is-long-enough"


def _api_app(tmp_path: Path) -> tuple[object, object]:
    import asyncio as _asyncio  # noqa: F401  (httpx drives the ASGI app)

    from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app

    model = compile_project(_project(tmp_path))
    repository = InMemoryRepository()
    repository.seed("demo.Carrier", CARRIERS)
    repository.seed("demo.Part", PARTS)
    records = RecordsService(model, repository)
    return model, build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"operator"})),
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


def test_rest_query_narrows_through_lookup_source(tmp_path: Path) -> None:
    _model, app = _api_app(tmp_path)

    filtered = _rest(
        app,
        "POST",
        "/api/v1/carriers/_query",
        json={
            "lookup_source": {"entity": "demo.Order", "field": "carrier"},
            "sort": [{"field": "name"}],
        },
    )
    assert filtered.status_code == 200  # type: ignore[attr-defined]
    payload = filtered.json()  # type: ignore[attr-defined]
    assert [record["id"] for record in payload["records"]] == [1, 2, 4]

    # Without the edge the query is what it always was -- an old client
    # keeps working and simply sees an unfiltered picker.
    unfiltered = _rest(
        app,
        "POST",
        "/api/v1/carriers/_query",
        json={"sort": [{"field": "name"}]},
    )
    assert unfiltered.status_code == 200  # type: ignore[attr-defined]
    assert len(unfiltered.json()["records"]) == 4  # type: ignore[attr-defined]


def test_rest_query_refuses_a_nonsense_lookup_source(tmp_path: Path) -> None:
    _model, app = _api_app(tmp_path)

    for source in (
        {"entity": "demo.Missing", "field": "carrier"},
        {"entity": "demo.Order", "field": "missing"},
        {"entity": "demo.Order", "field": "code"},
        {"entity": "demo.Item", "field": "order"},
    ):
        response = _rest(
            app,
            "POST",
            "/api/v1/carriers/_query",
            json={"lookup_source": source},
        )
        assert response.status_code == 400, source  # type: ignore[attr-defined]


def test_rest_query_ignores_a_source_with_no_declared_filter(
    tmp_path: Path,
) -> None:
    _model, app = _api_app(tmp_path)

    response = _rest(
        app,
        "POST",
        "/api/v1/orders/_query",
        json={"lookup_source": {"entity": "demo.Item", "field": "order"}},
    )

    assert response.status_code == 200  # type: ignore[attr-defined]


# --- the doors: the typed client and remote mode ------------------------------


def test_remote_lookup_forwards_the_edge_and_omits_it_when_absent(
    tmp_path: Path,
) -> None:
    """The edge rides the wire; expressions never do. And a query naming no
    edge must stay byte-identical to what the client always sent, so an old
    server keeps answering it."""

    import asyncio
    import json
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    from tide.api.client import TideApiClient
    from tide.api.remote import RemoteRecordsService

    model, app = _api_app(tmp_path)
    base_url = "http://127.0.0.1"
    query_bodies: list[dict[str, object]] = []

    def dispatch(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_query"):
            query_bodies.append(json.loads(request.content.decode("utf-8")))

        async def send() -> httpx.Response:
            async with httpx.AsyncClient(
                base_url=base_url,
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

    with httpx.Client(
        base_url=base_url, transport=httpx.MockTransport(dispatch)
    ) as transport:
        client = TideApiClient(model, base_url, TOKEN, http_client=transport)
        session = client.connect()
        context = RequestContext(
            Principal(session.principal, roles=frozenset(session.roles)),
            channel=Channel.TUI,
        )
        records = RemoteRecordsService(model, client, session)

        filtered = records.lookup_records(
            "demo.Carrier",
            ("name",),
            "",
            context,
            source=("demo.Order", "carrier"),
        )
        assert [record["id"] for record in filtered] == [1, 2, 4]

        unfiltered = records.lookup_records("demo.Carrier", ("name",), "", context)
        assert len(unfiltered) == 4

    assert query_bodies[0]["lookup_source"] == {
        "entity": "demo.Order",
        "field": "carrier",
    }
    assert "lookup_source" not in query_bodies[1]


def test_the_manifest_names_the_edge_only_where_a_filter_is_declared(
    tmp_path: Path,
) -> None:
    """The lookup contract's ``source`` is the dialog's marching order: echo
    it on every query. Its absence is equally load-bearing -- it is how a
    client knows not to send a key an older server would refuse."""

    _model, app = _api_app(tmp_path)

    response = _rest(app, "GET", "/api/v1/_tide/presentation")
    assert response.status_code == 200  # type: ignore[attr-defined]

    lookups: dict[tuple[str, str], object] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "owner_entity" in node and "query_path" in node:
                lookups[(node["owner_entity"], node["field"])] = node.get("source")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(response.json())  # type: ignore[attr-defined]

    assert lookups[("demo.Order", "carrier")] == {
        "entity": "demo.Order",
        "field": "carrier",
    }
    assert lookups[("demo.Order", "fallback_carrier")] is None
