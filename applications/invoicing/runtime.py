"""Register invoicing business behavior with the TIDE runtime."""

from __future__ import annotations

from pathlib import Path

from tide.runtime import load_application_module
from tide.services import ActionService, RecordsService


def configure_runtime(records: RecordsService, actions: ActionService) -> None:
    invoicing_actions = load_application_module(
        Path(__file__).with_name("actions.py")
    )
    records.register_generator(
        "actions.allocate_invoice_number",
        lambda values, _context, repository: invoicing_actions.allocate_invoice_number(
            repository.next_sequence_value("sales.Invoice.number"),
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
    actions.register(
        "actions.void_invoice",
        lambda record, context, payload: invoicing_actions.void_invoice(
            record,
            principal=context.principal.identifier,
            occurred_at=payload.get("occurred_at"),
        ),
    )
