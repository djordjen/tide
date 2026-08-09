"""Typed, application-owned records for ``tide run --demo``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def load_demo_data() -> dict[str, tuple[dict[str, Any], ...]]:
    """Return a small, deterministic Contacts workspace."""

    return {
        "crm.Company": (
            _company(1, "ADRIA", "Adria Consulting", "https://adria.example"),
            _company(2, "MORA", "Mora Trade", "https://mora.example"),
            _company(3, "LOV", "Lovcen Studio", None),
            _company(4, "BOKA", "Boka Systems", "https://boka.example"),
        ),
        "crm.Contact": (
            _contact(1, "Ana Petrovic", "ana@adria.example", "+382 20 555 101", 1),
            _contact(2, "Marko Vukovic", "marko@adria.example", "+382 20 555 102", 1),
            _contact(3, "Jelena Maric", "jelena@mora.example", "+382 32 555 201", 2),
            _contact(4, "Nikola Ivanovic", "nikola@mora.example", None, 2),
            _contact(5, "Sara Radovic", "sara@lovcen.example", "+382 41 555 301", 3),
            _contact(6, "Luka Jovanovic", "luka@boka.example", "+382 31 555 401", 4),
            _contact(
                7,
                "Mina Popovic",
                "mina@boka.example",
                "+382 31 555 402",
                4,
                archived=True,
            ),
        ),
    }


def _company(
    identity: int,
    code: str,
    name: str,
    website: str | None,
) -> dict[str, Any]:
    return {
        "id": identity,
        "code": code,
        "name": name,
        "website": website,
        "active": True,
    }


def _contact(
    identity: int,
    name: str,
    email: str,
    phone: str | None,
    company: int,
    *,
    archived: bool = False,
) -> dict[str, Any]:
    return {
        "id": identity,
        "name": name,
        "email": email,
        "phone": phone,
        "company": company,
        "status": "archived" if archived else "active",
        "archived_at": (
            datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
            if archived
            else None
        ),
        "archived_by": "demo:editor" if archived else None,
    }
