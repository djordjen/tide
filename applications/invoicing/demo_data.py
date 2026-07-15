"""Typed, application-owned records for ``tide run --demo``."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any


def load_demo_data() -> dict[str, tuple[dict[str, Any], ...]]:
    customers = (
        _customer(1, "ADRIA", "Adria Consulting", "office@adria.example"),
        _customer(2, "MORA", "Mora Trade", "finance@mora.example"),
        _customer(3, "LOV", "Lovćen Studio", None),
    )
    products = (
        _product(1, "CONS", "Consulting hour", "85.00"),
        _product(2, "SUP", "Priority support", "240.00"),
        _product(3, "LIC", "Annual license", "1200.00"),
    )
    invoices = (
        _invoice(1, "INV-2026-0001", date(2026, 7, 1), 1, "posted", 1, "10", "85.00"),
        _invoice(2, "INV-2026-0002", date(2026, 7, 3), 2, "draft", 2, "2", "240.00"),
        _invoice(3, "INV-2026-0003", date(2026, 7, 5), 3, "draft", 3, "1", "1200.00"),
        _invoice(4, "INV-2026-0004", date(2026, 7, 7), 1, "posted", 1, "16", "85.00"),
        _invoice(
            5, "INV-2026-0005", date(2026, 7, 9), 2, "cancelled", 2, "1", "240.00"
        ),
        _invoice(6, "INV-2026-0006", date(2026, 7, 11), 3, "draft", 1, "6.5", "85.00"),
        _invoice(7, "INV-2026-0007", date(2026, 7, 13), 1, "posted", 3, "2", "1200.00"),
        _invoice(8, "INV-2026-0008", date(2026, 7, 15), 2, "draft", 2, "3", "240.00"),
    )
    return {
        "crm.Customer": customers,
        "catalog.Product": products,
        "sales.Invoice": invoices,
    }


def _customer(
    identity: int,
    code: str,
    name: str,
    email: str | None,
) -> dict[str, Any]:
    return {
        "id": identity,
        "code": code,
        "name": name,
        "email": email,
        "active": True,
        "invoices": [],
    }


def _product(identity: int, code: str, name: str, price: str) -> dict[str, Any]:
    return {
        "id": identity,
        "code": code,
        "name": name,
        "unit_price": Decimal(price),
        "active": True,
    }


def _invoice(
    identity: int,
    number: str,
    invoice_date: date,
    customer: int,
    status: str,
    product: int,
    quantity: str,
    unit_price: str,
) -> dict[str, Any]:
    quantity_value = Decimal(quantity)
    price_value = Decimal(unit_price)
    total = (quantity_value * price_value).quantize(Decimal("0.01"))
    posted = status == "posted"
    return {
        "id": identity,
        "number": number,
        "invoice_date": invoice_date,
        "currency": "EUR",
        "status": status,
        "posted_at": (
            datetime(2026, 7, invoice_date.day, 12, 0, tzinfo=timezone.utc)
            if posted
            else None
        ),
        "posted_by": "demo:clerk" if posted else None,
        "version": 2 if posted else 1,
        "customer": customer,
        "lines": [
            {
                "id": identity,
                "line_number": 1,
                "description": "Demo invoice line",
                "quantity": quantity_value,
                "unit_price": price_value,
                "invoice": identity,
                "product": product,
                "total": total,
            }
        ],
        "total": total,
    }
