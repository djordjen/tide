"""Register invoicing business behavior with the TIDE runtime."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from tide.services import ActionService, RecordsService


def configure_runtime(records: RecordsService, actions: ActionService) -> None:
    invoicing_actions = _load_actions()
    records.register_generator(
        "actions.allocate_invoice_number",
        lambda values, _context, repository: invoicing_actions.allocate_invoice_number(
            repository.peek_next_identity("sales.Invoice"),
            values["invoice_date"],
        ),
    )
    actions.register(
        "actions.post_invoice",
        lambda record, context, payload: invoicing_actions.post_invoice(
            record,
            principal=context.principal.identifier,
            occurred_at=payload.get("occurred_at"),
        ),
    )


def _load_actions() -> ModuleType:
    actions_file = Path(__file__).with_name("actions.py")
    spec = importlib.util.spec_from_file_location(
        "tide_invoicing_actions",
        actions_file,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {actions_file.as_posix()}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
