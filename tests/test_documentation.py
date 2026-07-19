from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote

import pytest

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
