"""Deterministic Faker profile for a persistent invoicing development database."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from tide.runtime import RequestContext
from tide.services import ActionService, RecordsService


def seed_fake_data(
    *,
    faker: Any,
    records: RecordsService,
    actions: ActionService,
    context: RequestContext,
    counts: dict[str, int],
    random_seed: int,
) -> dict[str, int]:
    customer_count = counts.get("customers", 0)
    product_count = counts.get("products", 0)
    invoice_count = counts.get("invoices", 0)
    if invoice_count and (not customer_count or not product_count):
        raise ValueError("fake invoices require at least one customer and product")

    customer_ids: list[int] = []
    for index in range(1, customer_count + 1):
        session = records.create(
            "crm.Customer",
            context,
            {
                "code": f"C{index:05d}",
                "name": faker.unique.company(),
                "email": faker.unique.company_email(),
                "active": True,
            },
        )
        customer_ids.append(int(records.commit(session, context)["id"]))

    products: list[dict[str, Any]] = []
    for index in range(1, product_count + 1):
        price = Decimal(faker.pydecimal(2, 4, positive=True)).quantize(
            Decimal("0.01")
        )
        session = records.create(
            "catalog.Product",
            context,
            {
                "code": f"P{index:05d}",
                "name": faker.unique.catch_phrase()[:120],
                "unit_price": price,
                "active": True,
            },
        )
        products.append(records.commit(session, context))

    posted = 0
    today = date.today()
    for index in range(1, invoice_count + 1):
        line_count = faker.random_int(min=1, max=min(4, product_count))
        selected_products = faker.random_elements(
            elements=products,
            length=line_count,
            unique=True,
        )
        lines = [
            {
                "line_number": line_number,
                "product": product["id"],
                "description": product["name"],
                "quantity": Decimal(faker.random_int(min=1, max=20)),
                "unit_price": product["unit_price"],
            }
            for line_number, product in enumerate(selected_products, start=1)
        ]
        session = records.create(
            "sales.Invoice",
            context,
            {
                "invoice_date": today
                - timedelta(days=faker.random_int(min=0, max=180)),
                "customer": faker.random_element(customer_ids),
                "currency": "EUR",
                "lines": lines,
            },
        )
        invoice = records.commit(session, context)
        if faker.random_int(min=1, max=100) <= 35:
            occurred_at = datetime.combine(
                invoice["invoice_date"],
                time(hour=12),
                tzinfo=timezone.utc,
            )
            actions.execute(
                "sales.Invoice",
                "post",
                invoice["id"],
                {"occurred_at": occurred_at},
                context,
                idempotency_key=f"fake:{random_seed}:invoice:{index}",
            )
            posted += 1

    return {
        "customers": customer_count,
        "products": product_count,
        "invoices": invoice_count,
        "posted_invoices": posted,
    }
