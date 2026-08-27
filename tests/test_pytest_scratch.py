"""Where the suite is allowed to put its scratch directories.

`pytest` creates a `<testname>current` symlink beside every numbered temp
directory it makes. On Windows those are NTFS reparse points, and the User
Profile Service walks the whole profile at logon and resolves every one. On
this project's development machine 43,224 of them accumulated under
`%LOCALAPPDATA%\\Temp` and logon went from under a second to about 105.

They accumulated because runs passed `--basetemp`. That option is not a
tidier version of the default -- it is a different code path with no
retention at all:

* the default path calls `make_numbered_dir_with_cleanup`, which keeps the
  last `tmp_path_retention_count` runs (3) and deletes the rest;
* the `--basetemp` path does `rm_rf(basetemp); basetemp.mkdir()` at the start
  of a run and nothing else -- so it clears only the one exact directory it
  was handed, a per-label directory is never touched by a run using a
  different label, and a run that is interrupted or killed clears nothing.

So the rule this file enforces is: nothing in this repository passes
`--basetemp`, and the scratch root is somewhere outside the user profile.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]


def _conftest() -> Any:
    """Load the repository-root conftest by path.

    By name it would be ambiguous: `tests/conftest.py` is also importable as
    `conftest`, and which one wins depends on sys.path ordering.
    """

    spec = importlib.util.spec_from_file_location(
        "tide_root_conftest", ROOT / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The fence languages this repository treats as runnable commands, which is
# also the set CONTRIBUTING.md documents.
_COMMAND_FENCES = {"bash", "powershell", "sh", "shell", "console", "pwsh"}


def _command_blocks(markdown: str) -> str:
    """Only the fenced blocks a reader would actually run."""

    blocks: list[str] = []
    language: str | None = None
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            token = stripped[3:].strip().lower()
            language = token if language is None else None
            continue
        if language in _COMMAND_FENCES:
            blocks.append(line)
    return "\n".join(blocks)


def test_the_repository_asks_for_no_basetemp_anywhere() -> None:
    """Derived rather than listed, because a list of call sites drifts.

    The accumulation did not come from a runner script -- there has never
    been one. It came from ad-hoc commands repeating a convention. A checked
    tree is the only part of that this repository can speak for, so it speaks
    for it.
    """

    offenders = []
    for path in sorted(ROOT.rglob("*")):
        parts = set(path.parts)
        if not path.is_file() or parts & {
            ".git",
            ".venv",
            ".pytest-scratch",  # pytest's own scratch, which lives here now
            "node_modules",
            "__pycache__",
            "web",  # a design-sync junction recurses until Windows refuses
        }:
            continue
        if path.suffix not in {".py", ".toml", ".ini", ".cfg", ".yml", ".yaml",
                               ".bat", ".ps1", ".sh", ".mjs", ".json", ".md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # The guard and this test are the two places allowed to name it:
        # one enforces the rule and one checks the enforcement. Exempted by
        # role rather than by a list that would grow.
        if path.name == "test_pytest_scratch.py" or path == ROOT / "conftest.py":
            continue
        # Prose explaining the rule is not a caller breaking it. In markdown
        # only fenced command blocks count -- which is the same thing that
        # makes a documented command executable in this repository.
        haystack = _command_blocks(text) if path.suffix == ".md" else text
        if "--basetemp" in haystack:
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == [], (
        f"{offenders} pass --basetemp; that path has no retention and "
        "accumulates reparse points in the user profile"
    )


def test_a_scratch_root_inside_the_user_profile_is_recognised() -> None:
    conftest = _conftest()
    profile = r"C:\Users\Someone"

    assert conftest.under_user_profile(
        Path(r"C:\Users\Someone\AppData\Local\Temp\tide-basetemp"),
        profile=profile,
        windows=True,
    )
    assert not conftest.under_user_profile(
        Path(r"E:\pytest-scratch"), profile=profile, windows=True
    )
    # The reparse-point cost is the profile service walking the profile, so
    # it is a Windows concern and nothing else.
    assert not conftest.under_user_profile(
        Path(r"C:\Users\Someone\AppData\Local\Temp"),
        profile=profile,
        windows=False,
    )
    # No profile to be inside of.
    assert not conftest.under_user_profile(
        Path(r"C:\Users\Someone\x"), profile=None, windows=True
    )


def test_an_explicit_basetemp_in_the_profile_is_refused_by_name() -> None:
    """The guard has to stop a caller that has never heard of this.

    A warning would not: the suite runs `-q`, and an agent session reading a
    green summary would carry the habit forward.
    """

    conftest = _conftest()
    profile = r"C:\Users\Someone"

    with pytest.raises(pytest.UsageError) as caught:
        conftest.check_given_basetemp(
            Path(r"C:\Users\Someone\AppData\Local\Temp\tide-basetemp\label-x"),
            profile=profile,
            windows=True,
        )
    message = str(caught.value)
    assert "--basetemp" in message
    assert "PYTEST_DEBUG_TEMPROOT" in message
    # It has to say why, or the next person deletes the guard.
    assert "reparse" in message.lower() or "logon" in message.lower()

    # Outside the profile it is a legitimate choice and stays allowed.
    conftest.check_given_basetemp(
        Path(r"E:\pytest-scratch\label-x"), profile=profile, windows=True
    )


def test_the_default_scratch_root_is_off_the_profile_on_windows() -> None:
    conftest = _conftest()

    assert conftest.default_scratch_root(
        Path(r"E:\projects\tide"), windows=True
    ) == Path(r"E:\pytest-scratch")


def test_nothing_is_imposed_where_the_default_was_never_a_problem() -> None:
    """`/tmp` was fine. A Windows fix should not reach past Windows."""

    conftest = _conftest()

    assert (
        conftest.default_scratch_root(Path("/home/someone/tide"), windows=False)
        is None
    )


def test_the_scratch_root_is_never_inside_the_checkout() -> None:
    """Measured, not tidiness.

    The first version of this put scratch in a git-ignored `.pytest-scratch/`
    beside the checkout -- portable, needing no machine setup, and it made
    `test_designer_save.py::test_replacement_failure_rolls_back_all_yaml_sources`
    fail intermittently: 6 failures in 45 runs with scratch inside the tree,
    0 in 45 with it outside. The save service reads and locks project
    sources, and temporary projects sitting inside the real one are not
    neutral scenery.
    """

    conftest = _conftest()
    checkout = Path(r"E:\projects\tide")

    chosen = conftest.default_scratch_root(checkout, windows=True)
    assert chosen is not None
    assert checkout not in chosen.parents
    assert chosen != checkout
