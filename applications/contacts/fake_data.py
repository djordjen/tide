"""Deterministic Faker profile for a persistent Contacts development database."""

from __future__ import annotations

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
    """Create companies first, then contacts that reference them."""

    company_count = counts.get("companies", 25)
    contact_count = counts.get("contacts", 100)
    if contact_count and not company_count:
        raise ValueError("fake contacts require at least one company")

    company_ids: list[int] = []
    for index in range(1, company_count + 1):
        session = records.create(
            "crm.Company",
            context,
            {
                "code": f"C{index:05d}",
                "name": faker.unique.company()[:120],
                "website": faker.url()[:200],
                "active": True,
            },
        )
        company_ids.append(int(records.commit(session, context)["id"]))

    archived = 0
    for index in range(1, contact_count + 1):
        session = records.create(
            "crm.Contact",
            context,
            {
                "name": faker.name()[:120],
                "email": faker.unique.email()[:254],
                "phone": faker.phone_number()[:40],
                "company": faker.random_element(company_ids),
            },
        )
        contact = records.commit(session, context)
        if faker.random_int(min=1, max=100) <= 15:
            actions.execute(
                "crm.Contact",
                "archive",
                contact["id"],
                {},
                context,
                idempotency_key=f"fake:{random_seed}:contact:{index}",
            )
            archived += 1

    return {
        "companies": company_count,
        "contacts": contact_count,
        "archived_contacts": archived,
    }
