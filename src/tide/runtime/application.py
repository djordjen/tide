"""Explicit loading of application-owned runtime registrations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from tide.compiler.normalized import ApplicationModel

if TYPE_CHECKING:
    from tide.services import ActionService, RecordsService


class ApplicationRuntimeError(ValueError):
    """The application's optional runtime registration is invalid."""


def configure_application_runtime(
    model: ApplicationModel,
    records: RecordsService,
    actions: ActionService,
) -> bool:
    """Run an application's optional ``runtime.py`` registration hook."""

    runtime_file = model.project_root / "runtime.py"
    if not runtime_file.is_file():
        verify_runtime_registrations(model, records, actions)
        return False
    module = _load_runtime(runtime_file)
    configure = getattr(module, "configure_runtime", None)
    if not callable(configure):
        raise ApplicationRuntimeError(
            "runtime.py must define configure_runtime(records, actions)"
        )
    try:
        configure(records, actions)
    except Exception as error:
        raise ApplicationRuntimeError(
            f"application runtime registration failed: {error}"
        ) from error
    verify_runtime_registrations(model, records, actions)
    return True


def verify_runtime_registrations(
    model: ApplicationModel,
    records: RecordsService,
    actions: ActionService,
) -> None:
    """Fail at startup for a handler the model names and nothing registered.

    The compiler already proves the referenced function exists in the project
    source. What nothing checked is whether the runtime hook registered it under
    the name the metadata uses, and the two are independent: `execute:` is a
    string key looked up in a dictionary. A missing or mistyped registration
    therefore surfaced as a `RuntimeError` the first time somebody ran the
    action, which is a deployment that starts cleanly and breaks on use.

    A registration nobody references is reported alongside a missing one, since
    a typo produces both and naming the near miss points at the line to fix. On
    its own it does not stop startup: one hook may serve several model variants,
    and a handler nobody calls costs nothing at runtime.
    """

    required = {
        str(action["execute"])
        for entity in model.entities.values()
        for action in entity.actions.values()
        if action.get("execute")
    }
    generated = {
        str(field.metadata["generated_by"])
        for entity in model.entities.values()
        for field in entity.fields.values()
        if field.metadata.get("generated_by")
    }
    missing = sorted(
        (required - actions.registered_handlers)
        | (generated - records.registered_generators)
    )
    if not missing:
        return
    unused = sorted(
        (actions.registered_handlers - required)
        | (records.registered_generators - generated)
    )
    detail = (
        f"; registered but unreferenced: {', '.join(unused)}" if unused else ""
    )
    raise ApplicationRuntimeError(
        "application metadata references handlers that nothing registered: "
        + ", ".join(missing)
        + detail
    )


def _load_runtime(runtime_file: Path) -> ModuleType:
    module_name = f"tide_application_runtime_{abs(hash(runtime_file.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, runtime_file)
    if spec is None or spec.loader is None:
        raise ApplicationRuntimeError(
            f"could not load application runtime from {runtime_file.as_posix()}"
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise ApplicationRuntimeError(
            f"application runtime failed to load: {error}"
        ) from error
    return module
