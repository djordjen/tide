from __future__ import annotations

from pathlib import Path
import re
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
FIRST_APPLICATION = ROOT / "docs" / "examples" / "first-application"
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


def test_first_application_tutorial_example_compiles() -> None:
    model = compile_project(FIRST_APPLICATION)

    assert model.name == "TIDE Contacts"
    assert model.version == "0.1.0"
    assert set(model.entities) == {"crm.Contact"}
    assert set(model.views) == {"crm.Contact.browse", "crm.Contact.edit"}
    assert set(model.roles) == {"contact_manager", "contact_viewer"}
    assert model.diagnostics == ()


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
    assert job["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "windows-latest"]
    }
    assert job["name"] == "Python 3.11 / ${{ matrix.os }}"
    setup = next(
        step for step in job["steps"] if step.get("uses") == "actions/setup-python@v6"
    )
    assert setup["with"]["python-version"] == "3.11"
    build = next(
        step for step in job["steps"] if step.get("run") == "python -m build"
    )
    assert build["if"] == "matrix.os == 'ubuntu-latest'"
