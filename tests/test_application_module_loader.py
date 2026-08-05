"""One loader for the Python files an application owns.

Five places wrote this out -- the runtime hook, the demo-data provider, the
fake-data provider, the invoicing application's action loader, and the template
that generates that loader into every generated application -- and all five
named the module after `abs(hash(path))` and never registered it. The issue
listed three; there were five, which is the usual shape: agreement is what
makes a copy easy to leave, right up until one of them drifts.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import get_type_hints

import pytest

from tide.runtime import ApplicationModuleError, load_application_module

ROOT = Path(__file__).parents[1]


def test_the_module_name_is_the_same_in_the_next_process() -> None:
    """`abs(hash(str))` is salted per process and never repeats.

    Anything that reads `__name__` -- a traceback, a log line, a pickle --
    was reading a number that meant nothing and differed on every run.
    """

    names = {_module_name_in_a_fresh_process() for _ in range(2)}

    assert len(names) == 1, f"the loaded module was named {names}"
    assert "runtime" in names.pop()


def test_a_loaded_module_can_resolve_its_own_annotations(tmp_path: Path) -> None:
    """A module missing from `sys.modules` cannot be found by its own name.

    Every file in this tree carries `from __future__ import annotations`, so
    every annotation is a string until something resolves it -- and resolving
    one needs the defining module to be importable by name.
    """

    provider = tmp_path / "provider.py"
    provider.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class Row:\n"
        "    label: str\n",
        encoding="utf-8",
    )

    module = load_application_module(provider)

    assert get_type_hints(module.Row) == {"label": str}


def test_the_same_file_is_loaded_once(tmp_path: Path) -> None:
    """Two loads used to run the file twice and define two of every class.

    `isinstance` then says no between values built from the same source.
    """

    provider = tmp_path / "counted.py"
    provider.write_text(
        "from __future__ import annotations\n"
        "class Marker:\n"
        "    pass\n",
        encoding="utf-8",
    )

    first = load_application_module(provider)
    second = load_application_module(provider)

    assert first is second
    assert isinstance(first.Marker(), second.Marker)


def test_two_applications_keep_their_own_runtime(tmp_path: Path) -> None:
    """Every application names its hook `runtime.py`; they are not the same."""

    first_file = tmp_path / "first" / "runtime.py"
    second_file = tmp_path / "second" / "runtime.py"
    for path, value in ((first_file, "one"), (second_file, "two")):
        path.parent.mkdir()
        path.write_text(f"VALUE = {value!r}\n", encoding="utf-8")

    assert load_application_module(first_file).VALUE == "one"
    assert load_application_module(second_file).VALUE == "two"


def test_a_module_that_fails_to_load_is_not_kept(tmp_path: Path) -> None:
    """The module is registered before it runs, so a failure has to undo it.

    Left behind, a half-executed module is handed to the next caller as
    though it had loaded -- which is worse than the error it swallowed.
    """

    provider = tmp_path / "broken.py"
    provider.write_text(
        "VALUE = 'set before the failure'\n"
        "raise RuntimeError('provider is broken')\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="provider is broken"):
        load_application_module(provider)

    provider.write_text("VALUE = 'repaired'\n", encoding="utf-8")
    assert load_application_module(provider).VALUE == "repaired"


def test_a_file_that_is_not_there_is_reported_as_such(tmp_path: Path) -> None:
    with pytest.raises((ApplicationModuleError, FileNotFoundError)):
        load_application_module(tmp_path / "absent.py")


def _module_name_in_a_fresh_process() -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path\n"
            "from tide.runtime import load_application_module\n"
            "module = load_application_module("
            "Path('applications/invoicing/runtime.py'))\n"
            "print(module.__name__)\n",
        ],
        capture_output=True,
        check=True,
        cwd=ROOT,
        text=True,
    )
    return result.stdout.strip()
