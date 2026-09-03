"""An `appearance:` rule says what a record means, on sight.

The reference application this framework is measured against paints an expired
warranty yellow: a record that passes every validation rule and still has to
say something about itself in a list of four hundred. Nothing here authorizes
or refuses anything -- it decides what a renderer emphasizes.

Every layer is covered here rather than beside its own module, because the
point of the feature is that one declaration reaches all of them, and the way
that breaks is one layer quietly not reading it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from textual.widgets import DataTable

from tide import CompilationFailed, compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository, QuerySpec
from tide.presentation import record_appearance
from tide.runtime import (
    Channel,
    ImmutableFieldError,
    Principal,
    RequestContext,
)
from tide.services import ActionService, RecordsService
from tide.tui import TideApp, seed_demo_data

TOKEN = "tide-development-token-that-is-long-enough"
INVOICING = Path(__file__).parents[1] / "applications" / "invoicing"

MANIFEST = (
    'schema_version: "0.1"\n'
    "application: {name: Appearance, version: 0.1.0}\n"
    "database: {mode: managed}\n"
    "model: {paths: [models]}\n"
    "views: {paths: [views]}\n"
    "security: {paths: [security]}\n"
)

POLICIES = (
    "permissions:\n- demo.all\nroles:\n  operator:\n    grants:\n    - demo.all\n"
)

BROWSE = (
    "view: demo.Item.browse\nentity: demo.Item\nkind: browse\n"
    "columns:\n- name\n- status\n- days_left\n"
)

EDIT = (
    "view: demo.Item.edit\nentity: demo.Item\nkind: form\n"
    "layout:\n- group: Item\n  rows:\n  - - name\n    - status\n"
    "  - - days_left\n    - note\n"
)

RULES = (
    "appearance:\n"
    "- {name: retired, when: \"status == 'retired'\", emphasis: muted,"
    " enabled: false}\n"
    "- {name: expired, when: 'days_left < 0', emphasis: danger, fields: [days_left]}\n"
    "- {name: quiet, when: 'days_left < 0', fields: [note], visible: false}\n"
)


def _project(tmp_path: Path, rules: str = RULES, name: str = "appearance") -> Path:
    project = tmp_path / name
    for relative, text in (
        ("tide.yaml", MANIFEST),
        ("security/policies.yaml", POLICIES),
        ("views/item-browse.yaml", BROWSE),
        ("views/item-edit.yaml", EDIT),
        (
            "models/item.yaml",
            "entity: demo.Item\n"
            "display: name\n"
            "expose:\n"
            "  tui: true\n"
            "  rest:\n"
            "    path: items\n"
            "    operations: [list, get, create, update]\n"
            "permissions: {list: demo.all, read: demo.all, create: demo.all,"
            " update: demo.all, delete: demo.all}\n"
            "fields:\n"
            "  id: {type: integer, primary_key: true}\n"
            "  name: {type: string, length: 40, required: true}\n"
            "  status: {type: choice, choices: [active, retired], default: active}\n"
            "  days_left: {type: integer}\n"
            "  note: {type: string, length: 60}\n"
            f"{rules}",
        ),
    ):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return project


def test_the_rules_compile_in_the_order_they_were_written(tmp_path: Path) -> None:
    """Order is the precedence, so the model has to keep it."""

    model = compile_project(_project(tmp_path))

    rules = model.entity("demo.Item").metadata["appearance"]
    assert [rule["name"] for rule in rules] == ["retired", "expired", "quiet"]
    assert rules[0]["emphasis"] == "muted"
    assert rules[0]["when"] == "status == 'retired'"
    assert tuple(rules[0].get("fields") or ()) == ()
    assert tuple(rules[1]["fields"]) == ("days_left",)


@pytest.mark.parametrize(
    ("rules", "code"),
    [
        # A rule that paints a field the entity does not have paints nothing,
        # silently, which is the failure mode this whole file exists to avoid.
        (
            "appearance:\n- {name: a, when: 'days_left < 0', emphasis: danger,"
            " fields: [missing]}\n",
            # The catalogue's existing "no such field", not a private one: it
            # is the same mistake here as in a layout or a validation.
            "TIDE215",
        ),
        (
            "appearance:\n- {name: a, when: 'days_left < 0', emphasis: danger}\n"
            "- {name: a, when: 'days_left > 9', emphasis: warning}\n",
            "TIDE280",
        ),
        # `when` is a question, not a value: `name` is a string, and a rule
        # keyed on truthiness would fire for every record with a name.
        (
            "appearance:\n- {name: a, when: 'name', emphasis: danger}\n",
            "TIDE307",
        ),
        # Rules subtract and never grant: granting would have to overrule the
        # workflow lock and the permission that withheld the thing.
        (
            "appearance:\n- {name: a, when: 'days_left < 0', enabled: true}\n",
            "TIDE281",
        ),
        # Hiding a whole record is filtering, which named filters and row
        # policies already do -- and which paging and counts depend on.
        (
            "appearance:\n- {name: a, when: 'days_left < 0', visible: false}\n",
            "TIDE282",
        ),
    ],
)
def test_a_rule_that_cannot_work_is_refused(
    tmp_path: Path, rules: str, code: str
) -> None:
    with pytest.raises(CompilationFailed) as caught:
        compile_project(_project(tmp_path, rules, name=code.lower()))

    assert code in {item.code for item in caught.value.diagnostics}
    assert all(item.location.line >= 1 for item in caught.value.diagnostics)


def test_an_emphasis_outside_the_closed_set_is_refused(tmp_path: Path) -> None:
    """The set is closed so a renderer can promise to render all of it.

    An author who writes a colour has authored something that works in one of
    the two themes and not in the terminal at all.
    """

    with pytest.raises(CompilationFailed):
        compile_project(
            _project(
                tmp_path,
                "appearance:\n- {name: a, when: 'days_left < 0', emphasis: '#ff0'}\n",
                name="closed",
            )
        )


def test_the_shipped_example_demonstrates_the_rules_it_declares() -> None:
    """A demo that never fires its own rule teaches nothing.

    Invoicing's `nothing_to_post` marks a draft that totals zero, and for its
    first weeks nothing in the seed matched it -- the feature was declared,
    documented and invisible until somebody made an empty draft by hand. The
    seed now carries one, and this is why: remove it and the browser's first
    screen stops showing a rule the application still declares.
    """

    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    seed_demo_data(model, repository)
    records = RecordsService(model, repository)
    context = RequestContext(
        principal=Principal("demo", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )
    rules = model.entity("sales.Invoice").metadata["appearance"]

    verdicts = [
        record_appearance(rules, row)
        for row in records.query_page(
            "sales.Invoice", QuerySpec(limit=50), context
        ).records
    ]

    assert any(verdict.record == "muted" for verdict in verdicts)
    assert any(
        verdict.fields.get("total") == "warning" for verdict in verdicts
    )


def _app(project: Path) -> Any:
    model = compile_project(project)
    repository = InMemoryRepository()
    repository.seed(
        "demo.Item",
        (
            {"id": 1, "name": "Bridge PC", "status": "active", "days_left": 30},
            {"id": 2, "name": "Old radar", "status": "retired", "days_left": -5},
        ),
    )
    records = RecordsService(model, repository)
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"operator"})),
        ),
        actions=ActionService(model, records),
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_the_record_envelope_carries_the_verdict(tmp_path: Path) -> None:
    app = _app(_project(tmp_path))

    async def exercise() -> None:
        async with _client(app) as client:
            painted = await client.get("/api/v1/items/2", headers=_headers())
            plain = await client.get("/api/v1/items/1", headers=_headers())

        assert painted.json()["_tide"]["appearance"] == {
            "record": "muted",
            "fields": {"days_left": "danger"},
            "hidden": ["note"],
        }
        # A record no rule matched says nothing at all, so an application
        # without rules pays no bytes for the feature.
        assert "appearance" not in (plain.json().get("_tide") or {})

    asyncio.run(exercise())


def test_a_query_page_carries_the_verdict_for_every_row(tmp_path: Path) -> None:
    """The list is the case the feature exists for.

    Query rows carried no `_tide` at all before this: an expired warranty that
    only shows its colour once the record is open is not a warning, it is a
    reward for having already looked.
    """

    app = _app(_project(tmp_path))

    async def exercise() -> None:
        async with _client(app) as client:
            page = await client.post(
                "/api/v1/items/_query",
                headers=_headers(),
                json={"limit": 10},
            )

        rows = {row["id"]: row for row in page.json()["records"]}
        assert rows[2]["_tide"]["appearance"]["record"] == "muted"
        assert rows[2]["_tide"]["appearance"]["fields"] == {"days_left": "danger"}
        assert "appearance" not in (rows[1].get("_tide") or {})

    asyncio.run(exercise())


def test_a_rule_may_lock_a_record_and_hide_a_field(tmp_path: Path) -> None:
    """The two effects that are not colour, resolved in the same pass."""

    model = compile_project(_project(tmp_path))
    rules = model.entity("demo.Item").metadata["appearance"]

    retired = record_appearance(rules, {"status": "retired", "days_left": 30})
    assert retired.record == "muted"
    assert retired.locks_record is True
    assert retired.hidden == frozenset()

    expired = record_appearance(rules, {"status": "active", "days_left": -5})
    assert expired.locks_record is False
    assert expired.hidden == frozenset({"note"})


def test_a_locked_record_offers_no_writable_fields(tmp_path: Path) -> None:
    """`enabled: false` feeds the answer the renderers already read.

    Not a second list beside `writable_fields`: a renderer that learned to
    honour one and not the other is exactly the drift this avoids.
    """

    app = _app(_project(tmp_path))

    async def exercise() -> None:
        async with _client(app) as client:
            locked = await client.get("/api/v1/items/2", headers=_headers())
            open_ = await client.get("/api/v1/items/1", headers=_headers())

        assert "writable_fields" not in (locked.json().get("_tide") or {})
        assert "name" in open_.json()["_tide"]["writable_fields"]

    asyncio.run(exercise())


def test_the_service_refuses_a_write_a_rule_disabled(tmp_path: Path) -> None:
    """Enforced where `immutable_when` is enforced, so REST and MCP honour it.

    A rule the browser respects and the API does not is decoration; the
    renderer never offering the edit and the service refusing it are the same
    claim made twice on purpose.
    """

    model = compile_project(_project(tmp_path))
    repository = InMemoryRepository()
    repository.seed(
        "demo.Item",
        (
            {"id": 1, "name": "Bridge PC", "status": "active", "days_left": 30},
            {"id": 2, "name": "Old radar", "status": "retired", "days_left": -5},
        ),
    )
    records = RecordsService(model, repository)
    context = RequestContext(
        principal=Principal("p", roles=frozenset({"operator"})),
        channel=Channel.TUI,
    )

    open_session = records.begin_edit("demo.Item", 1, context)
    open_session.set("name", "Renamed")
    records.commit(open_session, context)

    locked = records.begin_edit("demo.Item", 2, context)
    locked.set("name", "Renamed")
    with pytest.raises(ImmutableFieldError):
        records.commit(locked, context)


def test_the_terminal_paints_the_row_the_same_rules_judged(
    tmp_path: Path,
) -> None:
    """The other surface a person browses four hundred records in.

    A terminal has no wash to give a row, so the verdict lands on the cells as
    a colour -- which is what a terminal has, and what the same rule means
    there.
    """

    model = compile_project(_project(tmp_path))
    repository = InMemoryRepository()
    repository.seed(
        "demo.Item",
        (
            {"id": 1, "name": "Bridge PC", "status": "active", "days_left": 30},
            {"id": 2, "name": "Old radar", "status": "retired", "days_left": -5},
        ),
    )
    application = TideApp(
        model,
        RecordsService(model, repository),
        RequestContext(
            principal=Principal("p", roles=frozenset({"operator"})),
            channel=Channel.TUI,
        ),
    )

    async def drive() -> list[list[Any]]:
        async with application.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            table = application.query_one(DataTable)
            return [
                list(table.get_row(key)) for key in list(table.rows)
            ]

    plain, painted = asyncio.run(drive())

    # The record-level rule reaches every cell of its row -- the mark gutter
    # at index 0 included; the field-level one overrides the cell it names,
    # one to the right of where it sat before the gutter existed.
    assert all(_style_of(cell) == "dim" for cell in plain) is False
    assert _style_of(painted[0]) == "dim"
    assert _style_of(painted[1]) == "dim"
    assert _style_of(painted[3]) == "red"


def test_the_terminal_leaves_out_a_field_the_rules_hid(tmp_path: Path) -> None:
    """The other surface with a record form, reading the same verdict.

    Asserted on both records rather than one: a form that never composed the
    field would pass the hidden half on its own.
    """

    model = compile_project(_project(tmp_path))
    repository = InMemoryRepository()
    repository.seed(
        "demo.Item",
        (
            {
                "id": 1,
                "name": "Bridge PC",
                "status": "active",
                "days_left": 30,
                "note": "in service",
            },
            {
                "id": 2,
                "name": "Old radar",
                "status": "active",
                "days_left": -5,
                "note": "past its date",
            },
        ),
    )
    application = TideApp(
        model,
        RecordsService(model, repository),
        RequestContext(
            principal=Principal("p", roles=frozenset({"operator"})),
            channel=Channel.TUI,
        ),
    )

    async def drive() -> tuple[int, int]:
        async with application.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            application.open_record(1)
            for _ in range(5):
                await pilot.pause()
            shown = len(application.screen.query("#field-note"))
            application.screen.dismiss(None)
            for _ in range(5):
                await pilot.pause()
            application.open_record(2)
            for _ in range(5):
                await pilot.pause()
            return shown, len(application.screen.query("#field-note"))

    shown, hidden = asyncio.run(drive())

    assert shown == 1
    assert hidden == 0


def _style_of(cell: Any) -> str | None:
    spans = getattr(cell, "spans", ())
    return str(spans[0].style) if spans else None


def test_an_application_declaring_no_rules_carries_nothing(tmp_path: Path) -> None:
    app = _app(_project(tmp_path, rules="", name="quiet"))

    async def exercise() -> None:
        async with _client(app) as client:
            record = await client.get("/api/v1/items/2", headers=_headers())
            page = await client.post(
                "/api/v1/items/_query",
                headers=_headers(),
                json={"limit": 10},
            )

        assert "appearance" not in (record.json().get("_tide") or {})
        assert all(
            "appearance" not in (row.get("_tide") or {})
            for row in page.json()["records"]
        )

    asyncio.run(exercise())


def test_the_service_reads_a_record_the_rules_cannot_judge(tmp_path: Path) -> None:
    """A null in a compared column is the ordinary case, not a broken one.

    TIDE reads rows it did not write: `days_left` may simply be empty, and a
    comparison against it cannot answer. The record comes back unpainted
    rather than the request failing.
    """

    model = compile_project(_project(tmp_path))
    repository = InMemoryRepository()
    repository.seed(
        "demo.Item",
        ({"id": 3, "name": "Unknown", "status": "active", "days_left": None},),
    )
    records = RecordsService(model, repository)
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"operator"})),
        ),
        actions=ActionService(model, records),
    )

    async def exercise() -> None:
        async with _client(app) as client:
            record = await client.get("/api/v1/items/3", headers=_headers())

        assert record.status_code == 200
        assert "appearance" not in (record.json().get("_tide") or {})

    asyncio.run(exercise())


def test_the_service_reads_a_record_whose_rule_divides_by_zero(
    tmp_path: Path,
) -> None:
    """The arithmetic sibling of the null above, and the same promise.

    Division is in the expression subset, so `100 / days_left` is a rule an
    author can write -- and a zero in the divisor column is a value TIDE did
    not write and must still read. The record comes back unpainted rather
    than the arithmetic error surfacing as a server fault.
    """

    model = compile_project(
        _project(
            tmp_path,
            "appearance:\n"
            "- {name: ratio, when: '100 / days_left > 3', emphasis: danger}\n",
            name="dividing",
        )
    )
    repository = InMemoryRepository()
    repository.seed(
        "demo.Item",
        ({"id": 4, "name": "Idle", "status": "active", "days_left": 0},),
    )
    records = RecordsService(model, repository)
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"operator"})),
        ),
        actions=ActionService(model, records),
    )

    async def exercise() -> None:
        async with _client(app) as client:
            record = await client.get("/api/v1/items/4", headers=_headers())

        assert record.status_code == 200
        assert "appearance" not in (record.json().get("_tide") or {})

    asyncio.run(exercise())
