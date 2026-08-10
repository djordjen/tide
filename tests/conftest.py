"""Shared pytest configuration for the TIDE contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import tide

APPLICATIONS = (Path(__file__).parents[1] / "applications").resolve()

Fingerprint = tuple[tuple[str, int, int], ...]


def contents(project: Path) -> Fingerprint:
    """Name, size and modification time of everything the compiler will read.

    `__pycache__` is skipped: importing an application's handlers rewrites it,
    and that is not a change to the application.
    """

    found = []
    for path in sorted(project.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        stat = path.stat()
        found.append(
            (path.relative_to(project).as_posix(), stat.st_mtime_ns, stat.st_size)
        )
    return tuple(found)


def cached_compile(
    project: str | Path = ".",
    *,
    root: Path = APPLICATIONS,
    cache: dict[Path, Any] | None = None,
    seen: dict[Path, Fingerprint] | None = None,
    compile_project: Callable[..., Any] | None = None,
) -> Any:
    """Compile a checked-in application once per session instead of per test.

    The suite asks for `applications/invoicing` from 176 call sites, most inside
    per-test setup, and each compile costs about 76ms. The result is an
    immutable `ApplicationModel`, so handing the same one to every caller
    changes nothing a test can observe -- except the clock.

    Only the applications directory is cached. A project built in `tmp_path` is
    compiled once by the test that built it, so caching would buy nothing.

    The first version of this fingerprinted the directory on *every* call so a
    mid-session edit would recompile. That was correct and 28 seconds slower
    than no cache at all: on Windows an `rglob` and `stat` sweep costs more than
    the compile it was protecting, 408 times over. The fingerprint is now taken
    once per application and re-checked once at the end of the session, so the
    cost is paid twice rather than 408 times -- and an edited application fails
    the run loudly instead of silently serving a stale model.
    """

    cache = COMPILED if cache is None else cache
    seen = FINGERPRINTS if seen is None else seen
    compile_project = UNCACHED if compile_project is None else compile_project

    resolved = Path(project).resolve()
    if root not in resolved.parents:
        return compile_project(project)

    if resolved not in cache:
        seen[resolved] = contents(resolved)
        cache[resolved] = compile_project(project)
    return cache[resolved]


def stale_applications(seen: dict[Path, Fingerprint] | None = None) -> list[Path]:
    """Applications that changed after their model was cached."""

    seen = FINGERPRINTS if seen is None else seen
    return [path for path, before in seen.items() if contents(path) != before]


COMPILED: dict[Path, Any] = {}
FINGERPRINTS: dict[Path, Fingerprint] = {}
UNCACHED = tide.compile_project

# Test modules bind the name at their own import time, which happens after this
# file. Patching here rather than adding a fixture leaves all 176 call sites
# alone; the alternative was editing every one of them to accept a model.
tide.compile_project = cached_compile


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Refuse to report a green run that a cached model may have decided.

    Nothing in the suite writes to `applications/`, which is why caching by path
    is safe. This is what makes that a checked claim rather than an assumption:
    if it stops being true, the run fails here instead of passing quietly.
    """

    del exitstatus
    stale = stale_applications()
    if stale:
        session.exitstatus = 1
        names = ", ".join(path.name for path in stale)
        print(
            f"\nERROR: {names} changed during the session, so tests after the "
            "first compile ran against a stale model. Results are not "
            "trustworthy; see the cache note in tests/conftest.py."
        )
