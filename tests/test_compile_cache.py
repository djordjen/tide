"""The session compile cache, which is test infrastructure and so untrusted.

Sharing one compiled model across the suite is only safe while the cache can
never report a green run decided by a model that no longer matches the source.
Because the fingerprint is checked once at the end rather than on every call --
checking it per call cost more than the compiles it saved -- these are the tests
that the end check actually catches an edit.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from conftest import (
    APPLICATIONS,
    UNCACHED,
    cached_compile,
    contents,
    stale_applications,
)

import tide

ROOT = Path(__file__).parents[1]
CONTACTS = ROOT / "applications" / "contacts"


class Counting:
    """The real compiler, wrapped so the cache's effect is observable."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, project: Any) -> Any:
        self.calls += 1
        # The real compiler, not the patched name, or this would recurse.
        return UNCACHED(project)


def copied(tmp_path: Path) -> Path:
    project = tmp_path / "applications" / "contacts"
    shutil.copytree(CONTACTS, project, ignore=shutil.ignore_patterns("__pycache__"))
    return project


def rename_an_entity(project: Path) -> None:
    model = project / "models" / "crm" / "contact.yaml"
    model.write_text(
        model.read_text(encoding="utf-8").replace(
            "label: Contacts", "label: Renamed Contacts"
        ),
        encoding="utf-8",
    )


def test_a_second_request_for_the_same_project_is_not_compiled_again(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    compiler, cache, seen = Counting(), {}, {}
    root = tmp_path / "applications"

    first = cached_compile(
        project, root=root, cache=cache, seen=seen, compile_project=compiler
    )
    second = cached_compile(
        project, root=root, cache=cache, seen=seen, compile_project=compiler
    )

    assert compiler.calls == 1
    assert first is second


def test_an_application_edited_after_it_was_cached_is_reported_stale(
    tmp_path: Path,
) -> None:
    """The assertion the whole cache rests on.

    A path-keyed cache serves the old model after an edit -- that is the point
    of it. What must not happen is the run finishing green without saying so.
    """

    project = copied(tmp_path)
    compiler, cache, seen = Counting(), {}, {}
    root = tmp_path / "applications"
    before = cached_compile(
        project, root=root, cache=cache, seen=seen, compile_project=compiler
    )

    assert stale_applications(seen) == []

    rename_an_entity(project)
    served = cached_compile(
        project, root=root, cache=cache, seen=seen, compile_project=compiler
    )

    assert served is before
    assert served.entity("crm.Contact").label == "Contacts"
    assert stale_applications(seen) == [project.resolve()]


def test_a_project_outside_the_applications_tree_is_never_cached(
    tmp_path: Path,
) -> None:
    """Projects built for one test gain nothing and must not be tracked."""

    project = copied(tmp_path)
    compiler, cache, seen = Counting(), {}, {}

    for _ in range(2):
        cached_compile(
            project,
            root=tmp_path / "elsewhere",
            cache=cache,
            seen=seen,
            compile_project=compiler,
        )

    assert compiler.calls == 2
    assert cache == {}
    assert seen == {}


def test_the_fingerprint_ignores_compiled_python_but_not_sources(
    tmp_path: Path,
) -> None:
    """Importing an application's handlers writes `__pycache__` beside them."""

    project = copied(tmp_path)
    original = contents(project)

    bytecode = project / "__pycache__"
    bytecode.mkdir()
    (bytecode / "actions.cpython-311.pyc").write_bytes(b"not really bytecode")

    assert contents(project) == original

    (project / "demo_data.py").write_text("# changed\n", encoding="utf-8")

    assert contents(project) != original


def test_the_live_cache_is_wired_to_the_checked_in_applications() -> None:
    """Nothing above proves the patch is in place for the suite that ran."""

    assert tide.compile_project is cached_compile
    assert APPLICATIONS == (ROOT / "applications").resolve()
    assert tide.compile_project(CONTACTS) is tide.compile_project(CONTACTS)
    # If this run had edited a checked-in application, `pytest_sessionfinish`
    # would already be committed to failing the session.
    assert stale_applications() == []
