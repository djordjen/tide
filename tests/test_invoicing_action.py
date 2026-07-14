from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
ACTIONS_FILE = ROOT / "applications" / "invoicing" / "actions.py"
SPEC = importlib.util.spec_from_file_location("invoicing_actions", ACTIONS_FILE)
assert SPEC and SPEC.loader
actions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(actions)


def draft_invoice() -> dict:
    return {
        "status": "draft",
        "version": 1,
        "lines": [{"quantity": "2.5", "unit_price": "4.20"}],
    }


def test_post_invoice_is_transaction_friendly_and_idempotent() -> None:
    invoice = draft_invoice()
    occurred_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    result = actions.post_invoice(invoice, principal="user:42", occurred_at=occurred_at)

    assert result is invoice
    assert invoice["status"] == "posted"
    assert invoice["posted_at"] == occurred_at
    assert invoice["posted_by"] == "user:42"
    assert invoice["version"] == 1

    actions.post_invoice(invoice, principal="user:42", occurred_at=occurred_at)
    assert invoice["version"] == 1


@pytest.mark.parametrize(
    "invoice",
    [
        {"status": "cancelled", "lines": [{}]},
        {"status": "draft", "lines": []},
    ],
)
def test_post_invoice_rejects_invalid_transitions(invoice: dict) -> None:
    with pytest.raises(actions.PostingError):
        actions.post_invoice(invoice, principal="user:42")
