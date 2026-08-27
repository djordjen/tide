"""Keep pytest's scratch directories out of the Windows user profile.

`pytest` creates a `<testname>current` symlink beside every numbered temp
directory it makes. On Windows those are NTFS reparse points, and the User
Profile Service resolves every one in the profile at logon. On this project's
development machine 43,224 accumulated under `%LOCALAPPDATA%\\Temp` and logon
went from under a second to about 105.

They accumulated because runs passed `--basetemp`. That option is not a
tidier default -- it is a different branch of `TempPathFactory.getbasetemp`
with no retention at all. The default branch calls
`make_numbered_dir_with_cleanup`, which keeps the last
`tmp_path_retention_count` runs and deletes the rest. The `--basetemp` branch
does `rm_rf(basetemp); basetemp.mkdir()` and nothing else: it clears only the
one directory it was handed, at the start of a run that uses that exact path,
so a per-label directory is never touched by a run using a different label and
an interrupted or killed run clears nothing at all.

So: nothing here passes `--basetemp`, and this file makes sure the default
lands somewhere the profile service will not walk.

* `PYTEST_DEBUG_TEMPROOT` wins whenever it is set. It is the operator's
  setting and this file does not second-guess it.
* Otherwise, on Windows, the root is `pytest-scratch` at the root of the drive
  the checkout is on -- off the profile, and deliberately **not** inside the
  checkout. Retention applies, because this is the default branch.
* On anything else nothing is set at all. `/tmp` was never the problem, and a
  Windows fix has no business reaching further than Windows.
* An explicit `--basetemp` under the profile is refused by name, because a
  warning in a `-q` run is invisible to exactly the caller who needs it.

**Not inside the repository**, and that is measured rather than tidiness. The
first version of this put scratch in a git-ignored `.pytest-scratch/` beside
the checkout, which is portable and needs no machine setup -- and made
`test_designer_save.py::test_replacement_failure_rolls_back_all_yaml_sources`
fail intermittently: 6 failures in 45 runs with scratch inside the tree, 0 in
45 with it outside. The save service reads and locks project sources, and
temporary projects sitting inside the real one are not neutral scenery.

The environment variable is read lazily inside `getbasetemp()`, at the first
`tmp_path` request, so setting it from `pytest_configure` is early enough. It
must name a directory that already exists: pytest's own `mkdir` there is not
recursive, and a missing root errors every test in setup rather than being
created.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

SCRATCH_DIRNAME = "pytest-scratch"

_REFUSAL = """\
--basetemp {given} is inside the user profile ({profile}).

pytest writes a `<testname>current` reparse point per test into a basetemp,
and the --basetemp branch has no retention: it clears only that exact
directory, only at the start of a run that names it, and not at all if the run
is interrupted. They accumulate, and the Windows User Profile Service resolves
every one of them at logon -- 43,224 of them once cost this project's machine
about 105 seconds per logon.

Do one of these instead:

  * drop --basetemp entirely and let this repository choose the root; or
  * set PYTEST_DEBUG_TEMPROOT to somewhere off the profile, which wins over
    the repository's own choice; or
  * pass --basetemp somewhere off the profile if you really need one run
    pinned to a known directory.
"""


def under_user_profile(
    path: Path,
    *,
    profile: str | None,
    windows: bool,
) -> bool:
    """Whether `path` is somewhere the profile service will walk at logon.

    Platform and profile are parameters rather than lookups so tests can
    exercise this without monkeypatching the environment. Pure in its
    arguments, but not portable in them: `pathlib.Path` parses a Windows
    literal as one opaque relative component on POSIX, so the tests that
    feed it Windows paths run only on Windows -- the one place the answer
    matters.
    """

    if not windows or not profile:
        return False
    try:
        path.resolve().relative_to(Path(profile).resolve())
    except (ValueError, OSError):
        return False
    return True


def check_given_basetemp(
    given: Path,
    *,
    profile: str | None,
    windows: bool,
) -> None:
    """Refuse an explicit basetemp that would accumulate in the profile."""

    if under_user_profile(given, profile=profile, windows=windows):
        raise pytest.UsageError(
            _REFUSAL.format(given=given, profile=profile)
        )


def default_scratch_root(rootpath: Path, *, windows: bool) -> Path | None:
    """Where scratch goes when nothing else has chosen.

    `None` means "leave pytest's own default alone", which is the right answer
    everywhere except Windows -- and on Windows the answer is the drive root
    the checkout sits on, which is off the profile without being inside the
    tree under test.
    """

    if not windows:
        return None
    anchor = Path(rootpath).anchor
    if not anchor:
        return None
    return Path(anchor) / SCRATCH_DIRNAME


def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):
        # An xdist worker. The controller hands each one an explicit
        # `--basetemp` under its own root, which has already been checked --
        # so checking again here would refuse every parallel run.
        return

    windows = sys.platform == "win32"
    profile = os.environ.get("USERPROFILE")

    given = config.getoption("basetemp", None)
    if given is not None:
        check_given_basetemp(Path(given), profile=profile, windows=windows)
        return

    if not os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        chosen = default_scratch_root(config.rootpath, windows=windows)
        if chosen is not None:
            try:
                # pytest will not create this itself, and a missing root
                # errors every test in setup.
                chosen.mkdir(parents=True, exist_ok=True)
            except OSError:
                chosen = None  # read-only drive root, a share, a sandbox
            else:
                os.environ["PYTEST_DEBUG_TEMPROOT"] = str(chosen)

    configured = os.environ.get("PYTEST_DEBUG_TEMPROOT")
    root = Path(configured) if configured else Path(tempfile.gettempdir())
    if under_user_profile(root, profile=profile, windows=windows):
        # Not fatal: the run is still correct, and a checkout inside the
        # profile is a reasonable thing to have. It just cannot be left
        # unsaid.
        config.issue_config_time_warning(
            pytest.PytestConfigWarning(
                f"pytest scratch root {root} is inside the user profile "
                f"({profile}); its reparse points slow Windows logon. Set "
                "PYTEST_DEBUG_TEMPROOT to a location on another drive."
            ),
            stacklevel=2,
        )
