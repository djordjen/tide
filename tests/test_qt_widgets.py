from __future__ import annotations

from datetime import date
from decimal import Decimal
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QHeaderView

from tide import compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.qt import QtBrowseController, TideQtWindow


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


class _WidgetClient:
    def list_records(
        self,
        entity_name: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Any:
        assert entity_name == "sales.Invoice"
        assert cursor is None
        return SimpleNamespace(
            records=(
                {
                    "id": 1,
                    "number": "INV-QT-001",
                    "invoice_date": date(2026, 7, 21),
                    "customer": 1,
                    "status": "draft",
                    "total": Decimal("1250.00"),
                },
            ),
            next_cursor=None,
        )

    def get_record(self, entity_name: str, identity: Any) -> Any:
        if entity_name == "sales.Invoice":
            assert identity == 1
            return SimpleNamespace(
                values={
                    "id": 1,
                    "number": "INV-QT-001",
                    "invoice_date": date(2026, 7, 21),
                    "currency": "EUR",
                    "status": "draft",
                    "posted_at": None,
                    "posted_by": None,
                    "version": 1,
                    "customer": 1,
                    "lines": [
                        {
                            "line_number": 1,
                            "product": 10,
                            "description": "Consulting day",
                            "quantity": Decimal("2"),
                            "unit_price": Decimal("625.00"),
                            "total": Decimal("1250.00"),
                        }
                    ],
                    "total": Decimal("1250.00"),
                }
            )
        if entity_name == "catalog.Product":
            assert identity == 10
            return SimpleNamespace(
                values={"id": 10, "code": "CONSULT", "name": "Consulting"}
            )
        assert entity_name == "crm.Customer"
        assert identity == 1
        return SimpleNamespace(
            values={"id": 1, "code": "ADRIA", "name": "Adria Consulting"}
        )


def test_qt_widget_adapter_renders_the_presented_page() -> None:
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    session = TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="development",
        principal="qt:tester",
        roles=("sales_clerk",),
        entities={
            name: TideEntityCapabilities(
                operations=("list", "get"),
                readable_fields=tuple(entity.fields),
            )
            for name, entity in model.entities.items()
        },
    )
    controller = QtBrowseController(model, _WidgetClient(), session, page_size=5)

    window = TideQtWindow(controller, source_label="off-screen test")
    window.show()
    application.processEvents()

    assert window.windowTitle() == "TIDE Invoicing — Invoices"
    assert window.table.rowCount() == 1
    assert window.table.columnCount() == 5
    assert window.table.item(0, 2).text() == "ADRIA - Adria Consulting"
    assert window.table.item(0, 4).text() == "1,250.00"
    assert window.previous.isEnabled() is False
    assert window.next.isEnabled() is False
    header = window.table.horizontalHeader()
    assert header.stretchLastSection() is False
    assert all(
        header.sectionResizeMode(index) == QHeaderView.ResizeMode.Interactive
        for index in range(window.table.columnCount())
    )
    assert window.table.columnWidth(2) > window.table.columnWidth(4)

    window.table.setColumnWidth(0, 222)
    window.refresh.click()
    application.processEvents()
    assert window.table.columnWidth(0) == 222

    window.table.selectRow(0)
    application.processEvents()
    assert window.view.isEnabled() is True
    window.table.itemActivated.emit(window.table.item(0, 0))
    application.processEvents()
    assert len(window._detail_dialogs) == 1
    detail = next(iter(window._detail_dialogs))
    assert detail.field_editors["number"].text() == "INV-QT-001"
    assert detail.field_editors["customer"].text() == "ADRIA - Adria Consulting"
    lines = detail.collection_tables["lines"]
    assert lines.rowCount() == 1
    assert lines.item(0, 1).text() == "CONSULT - Consulting"
    assert lines.item(0, 5).text() == "1,250.00"
    detail.close()
    window.close()
