from __future__ import annotations

from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

import pytest
import yaml

from tide import compile_project


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
    assert [entry["os"] for entry in job["strategy"]["matrix"]["include"]] == [
        "ubuntu-latest",
        "windows-latest",
    ]
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
