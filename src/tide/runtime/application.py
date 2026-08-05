"""Explicit loading of application-owned runtime registrations."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import TYPE_CHECKING

from tide.compiler.normalized import ApplicationModel

if TYPE_CHECKING:
    from tide.services import ActionService, RecordsService


class ApplicationRuntimeError(ValueError):
    """The application's optional runtime registration is invalid."""


class ApplicationModuleError(ImportError):
    """A Python file an application owns could not be imported."""


def load_application_module(path: Path) -> ModuleType:
    """Import a Python file an application owns, once per path.

    Five places wrote this out: the runtime hook, the demo-data provider, the
    fake-data provider, the invoicing application's own action loader, and the
    template that generates that loader into every generated application. Each
    named the module `f"..._{abs(hash(path.resolve()))}"` and left it out of
    `sys.modules`, and both halves are wrong in ways that stay quiet:

    * `hash` on a string is salted per process, so the same file is a
      differently-named module on every run. Anything that reads `__name__` --
      a traceback, a log line, a pickle -- reads a number that means nothing
      and never repeats.
    * A module absent from `sys.modules` cannot be found by its own name, so
      `typing.get_type_hints` on anything it defines fails. Under
      `from __future__ import annotations`, which every file in this tree
      uses, that is every annotation the runtime tries to resolve.
    * Loading twice executed twice, and the two runs produce different class
      objects for the same `class` statement, so `isinstance` says no between
      values that came from the same source file.

    The name is derived from the path, so it is stable across processes and
    readable in a traceback. The digest keeps two `runtime.py` files in
    different applications apart.
    """

    resolved = path.resolve()
    name = _application_module_name(resolved)
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:
        raise ApplicationModuleError(f"could not load {resolved.as_posix()}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution, so a module that inspects its own
    # annotations while importing can find itself, and removed again if that
    # execution fails -- a half-run module left behind would be returned to
    # the next caller as though it had loaded.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _application_module_name(resolved: Path) -> str:
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    readable = re.sub(r"[^0-9A-Za-z]+", "_", f"{resolved.parent.name}_{resolved.stem}")
    return f"tide_application_{readable.strip('_') or 'module'}_{digest}"


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
    try:
        return load_application_module(runtime_file)
    except ApplicationModuleError as error:
        raise ApplicationRuntimeError(
            f"could not load application runtime from {runtime_file.as_posix()}"
        ) from error
    except Exception as error:
        raise ApplicationRuntimeError(
            f"application runtime failed to load: {error}"
        ) from error
