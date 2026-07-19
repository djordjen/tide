from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote

import pytest


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
