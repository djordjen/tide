from __future__ import annotations

import asyncio
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
import yaml

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.cli.main import _create_parser, main
from tide.data import InMemoryRepository
from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService


ROOT = Path(__file__).parents[1]
DOCUMENTS = tuple(
    sorted(
        {
            *ROOT.glob("*.md"),
            *(ROOT / "docs").rglob("*.md"),
            *(ROOT / "applications").rglob("README.md"),
        }
    )
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]+]\(([^)]+)\)")
# `text` fences are this repository's convention for expected output and for
# commands that do not exist yet; `tests/test_launcher_contracts.py` reads the
# runnable fences, and this module reads these.
ILLUSTRATION = re.compile(r"```text\n(.*?)```", re.S)
INVOICING = ROOT / "applications" / "invoicing"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.mark.parametrize(
    "document",
    DOCUMENTS,
    ids=lambda path: path.relative_to(ROOT).as_posix(),
)
def test_documentation_local_links_resolve(document: Path) -> None:
    missing: list[str] = []
    for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
        target = raw_target.strip().strip("<>").split("#", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        if not (document.parent / unquote(target)).exists():
            missing.append(raw_target)

    assert missing == []


def _documented_validate_output() -> list[tuple[str, str]]:
    """Every `Model is valid:` line a document promises, as (document, line)."""

    found: list[tuple[str, str]] = []
    for document in DOCUMENTS:
        for block in ILLUSTRATION.findall(document.read_text(encoding="utf-8")):
            for raw in block.splitlines():
                line = raw.strip()
                if line.startswith("Model is valid:"):
                    found.append((document.relative_to(ROOT).as_posix(), line))
    return found


def test_documented_validate_output_is_what_the_compiler_prints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The counts a reader is told to expect, checked against the compiler.

    `tests/test_launcher_contracts.py` reads the commands; nothing read what
    the documents said those commands would print. Entity, view and report
    counts are exactly the sort of number that is right when written and wrong
    two applications later, and a first-run guide that miscounts is worse than
    one that stays quiet, because it teaches a newcomer to distrust the page.
    """

    printed = set()
    for application in sorted((ROOT / "applications").iterdir()):
        if not (application / "tide.yaml").is_file():
            continue
        assert main(["model", "validate", str(application)]) == 0
        printed.add(capsys.readouterr().out.strip())

    documented = _documented_validate_output()
    # Both applications appear in GETTING-STARTED and Invoicing again in the
    # README; a scan that silently found nothing would pass every assertion
    # below it.
    assert len(documented) >= 3, "no documented validate output was found to check"
    for document, line in documented:
        assert line in printed, (
            f"{document} promises output the compiler does not print:\n"
            f"  documented: {line}\n"
            f"  actual:     {sorted(printed)}"
        )


def test_serve_offers_neither_the_web_ui_nor_mcp_until_asked() -> None:
    """`serve` alone is REST and the description, which is what the docs say.

    The README claimed for months that this command put "the Web UI and MCP
    available from the same process"; both answered 404, because each needs
    asking for. The claim was prose, so no test could disagree with it.
    """

    parsed = _create_parser().parse_args(["serve", "applications/invoicing", "--demo"])

    assert parsed.web_root is None
    assert parsed.mcp is False


def test_the_web_ui_is_served_only_when_a_build_is_named(tmp_path: Path) -> None:
    """The other half: the default really does serve nothing at `/`."""

    marker = "<!doctype html><title>a build</title>"
    (tmp_path / "index.html").write_text(marker, encoding="utf-8")

    async def exercise() -> None:
        async with _client(_api()) as client:
            assert (await client.get("/")).status_code == 404
        async with _client(_api(web_root=tmp_path)) as client:
            served = await client.get("/")
            assert served.status_code == 200
            assert served.text == marker

    asyncio.run(exercise())


def _api(*, web_root: Path | None = None) -> Any:
    model = compile_project(INVOICING)
    records = RecordsService(model, InMemoryRepository())
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            "tide-development-token-that-is-long-enough",
            Principal("api:test", roles=frozenset({"sales_clerk"})),
        ),
        actions=actions,
        web_root=web_root,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def test_what_the_ci_test_command_leaves_behind_is_ignored() -> None:
    """Making CI parallel changed the artifacts it produces.

    `pytest -n 4 --cov` writes one coverage data file per worker, named
    `.coverage.<host>.<pid>.<random>`. A run that finishes combines and removes
    them; one that is killed does not, and the ignore rule was the exact name
    `.coverage`, which matches none of them. The same per-file-list shape as
    the identity stores, arriving the moment the workers did.
    """

    fragments = (
        ".coverage",
        ".coverage.a-machine.pid14432.XdMXKjtx.HNQIp837rWOh",
    )
    checked = subprocess.run(
        ["git", "check-ignore", *fragments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert set(checked.stdout.split()) == set(fragments)

    # A `.coverage*` glob would cover the above and quietly hide a config file
    # somebody adds later, so the pattern has to stay narrower than that.
    configuration = subprocess.run(
        ["git", "check-ignore", ".coveragerc"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert configuration.returncode == 1, configuration.stdout


def test_invoicing_walkthrough_references_current_contract() -> None:
    model = compile_project(INVOICING)

    assert {"crm.Customer", "catalog.Product", "sales.Invoice"}.issubset(
        model.entities
    )
    assert {
        "sales.Invoice.browse",
        "sales.Invoice.edit",
        "catalog.Product.lookup",
    }.issubset(model.views)
    assert "sales.invoice" in model.reports
    assert {"sales_clerk", "auditor"}.issubset(model.roles)
    assert model.entity("sales.Invoice").actions["post"]["permission"] == (
        "sales.invoice.post"
    )


def test_ci_uses_the_certified_python_baseline_without_duplicate_branch_runs() -> None:
    workflow = yaml.load(
        CI_WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    assert workflow["on"] == {
        "push": {"branches": ["main"]},
        "pull_request": {"branches": ["main"]},
    }
    job = workflow["jobs"]["test"]
    # A plain os list, not `include` with per-entry extras. The two jobs used
    # to install different environments so the Qt suite could run on Windows
    # only; with that renderer gone they run the same tests, and two jobs
    # reporting two different counts is what made a green run hard to read.
    assert job["strategy"]["matrix"] == {"os": ["ubuntu-latest", "windows-latest"]}
    assert job["name"] == "Python 3.11 / ${{ matrix.os }}"
    setup = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    )
    assert setup["with"]["python-version"] == "3.11"
    # Installing from the lockfile is what keeps the tool versions CI runs the
    # same as the ones committed; resolving them fresh once turned an unrelated
    # ruff release into a failure on unchanged code.
    install = next(
        step for step in job["steps"] if str(step.get("run", "")).startswith("uv sync")
    )
    assert "--locked" in install["run"]
    build = next(
        step for step in job["steps"] if step.get("run") == "uv run python -m build"
    )
    assert build["if"] == "matrix.os == 'ubuntu-latest'"
