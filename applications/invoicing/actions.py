"""Business actions for the executable invoicing application.

The runtime will eventually supply typed records. Keeping this handler against a
mutable mapping makes its transaction-independent business behavior testable now.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, MutableMapping


class PostingError(ValueError):
    """The invoice cannot make the requested state transition."""


def allocate_invoice_number(sequence: int, invoice_date: Any) -> str:
    """Format a number allocated atomically by the persistence adapter."""

    year = getattr(invoice_date, "year", str(invoice_date)[:4])
    return f"INV-{year}-{sequence:06d}"


def post_invoice(
    invoice: MutableMapping[str, Any],
    *,
    principal: str,
    occurred_at: datetime | None = None,
) -> MutableMapping[str, Any]:
    """Post a draft exactly once and stamp its audit/concurrency fields."""

    status = invoice.get("status")
    if status == "posted":
        return invoice
    if status != "draft":
        raise PostingError(f"only draft invoices can be posted, not {status!r}")

    lines = invoice.get("lines") or []
    if not lines:
        raise PostingError("an invoice must contain at least one line")

    invoice["status"] = "posted"
    invoice["posted_at"] = occurred_at or datetime.now(timezone.utc)
    invoice["posted_by"] = principal
    return invoice
