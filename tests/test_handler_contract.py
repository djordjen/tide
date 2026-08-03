"""A handler the model names must exist before the application serves anyone.

The compiler validates that a referenced function exists in the project source.
Nothing then checked that the runtime hook actually registered it under the name
the metadata uses, so a missing or mistyped registration surfaced the first time
somebody pressed the button.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.runtime.application import (
    ApplicationRuntimeError,
    configure_application_runtime,
)
from tide.services import ActionService, RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _services(project: Path):
    model = compile_project(project)
    records = RecordsService(model, InMemoryRepository())
    return model, records, ActionService(model, records)


def _copy(tmp_path: Path) -> Path:
    return Path(shutil.copytree(INVOICING, tmp_path / "invoicing"))


def test_the_bundled_application_registers_every_handler_it_names() -> None:
    model, records, actions = _services(INVOICING)

    assert configure_application_runtime(model, records, actions) is True


def test_startup_refuses_a_handler_the_model_names_and_nothing_registers(
    tmp_path: Path,
) -> None:
    """This used to surface when a user pressed Post, not when the app started."""

    project = _copy(tmp_path)
    (project / "runtime.py").write_text(
        "def configure_runtime(records, actions):\n    return None\n",
        encoding="utf-8",
    )
    model, records, actions = _services(project)

    with pytest.raises(ApplicationRuntimeError) as error:
        configure_application_runtime(model, records, actions)

    message = str(error.value)
    assert "actions.post_invoice" in message
    assert "actions.allocate_invoice_number" in message


def test_startup_refuses_a_project_with_no_runtime_hook_at_all(
    tmp_path: Path,
) -> None:
    """No `runtime.py` is the same gap one step earlier, and used to pass."""

    project = _copy(tmp_path)
    (project / "runtime.py").unlink()
    model, records, actions = _services(project)

    with pytest.raises(ApplicationRuntimeError, match="actions.post_invoice"):
        configure_application_runtime(model, records, actions)


def test_a_near_miss_registration_is_named_in_the_failure(tmp_path: Path) -> None:
    """A typo leaves a reference unregistered and a registration unused.

    Reporting both turns "nothing registered for actions.post_invoice" into
    something that points at the line to fix.
    """

    project = _copy(tmp_path)
    runtime = project / "runtime.py"
    runtime.write_text(
        runtime.read_text(encoding="utf-8").replace(
            '"actions.post_invoice"', '"actions.post_invoiced"'
        ),
        encoding="utf-8",
    )
    model, records, actions = _services(project)

    with pytest.raises(ApplicationRuntimeError) as error:
        configure_application_runtime(model, records, actions)

    message = str(error.value)
    assert "actions.post_invoice" in message
    assert "actions.post_invoiced" in message


def test_an_unused_registration_alone_does_not_stop_startup(
    tmp_path: Path,
) -> None:
    """Dead registration is not a broken deployment.

    One `runtime.py` may serve several model variants, and refusing to start
    over a handler nobody calls would punish that for no runtime benefit. A typo
    still fails, because it leaves a reference missing as well.
    """

    project = _copy(tmp_path)
    runtime = project / "runtime.py"
    runtime.write_text(
        runtime.read_text(encoding="utf-8").replace(
            "def _load_actions()",
            '    actions.register("actions.unused", lambda *_: None)\n\n\n'
            "def _load_actions()",
        ),
        encoding="utf-8",
    )
    model, records, actions = _services(project)

    assert configure_application_runtime(model, records, actions) is True
