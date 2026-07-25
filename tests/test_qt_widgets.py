from __future__ import annotations

from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QHeaderView, QTableView

from tide import compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.qt import QtBrowseController, TideQtWindow


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


class _WidgetClient:
    def __init__(self) -> None:
        self.list_cursors: list[str | None] = []
        self.list_threads: list[int] = []

    def list_records(
        self,
        entity_name: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Any:
        assert entity_name == "sales.Invoice"
        assert limit == 5
        self.list_cursors.append(cursor)
        self.list_threads.append(threading.get_ident())
        if cursor is None:
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
                next_cursor="qt-batch-2",
            )
        assert cursor == "qt-batch-2"
        return SimpleNamespace(
            records=(
                {
                    "id": 2,
                    "number": "INV-QT-002",
                    "invoice_date": date(2026, 7, 22),
                    "customer": 1,
                    "status": "posted",
                    "total": Decimal("25.00"),
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


def test_qt_widget_adapter_incrementally_loads_the_server_list() -> None:
    gui_thread = threading.get_ident()
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
    client = _WidgetClient()
    controller = QtBrowseController(model, client, session, page_size=5)

    window = TideQtWindow(controller, source_label="off-screen test")
    window.show()
    _wait_until(application, lambda: window.table_model.rowCount() >= 1)
    window.table.scrollToBottom()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 2
        and not window.table_model.loading,
    )

    assert window.windowTitle() == "TIDE Invoicing — Invoices"
    assert isinstance(window.table, QTableView)
    assert window.table_model.rowCount() == 2
    assert window.table_model.columnCount() == 5
    assert window.table_model.index(0, 2).data() == "ADRIA - Adria Consulting"
    assert window.table_model.index(0, 4).data() == "1,250.00"
    assert window.table_model.index(1, 0).data() == "INV-QT-002"
    assert client.list_cursors[:2] == [None, "qt-batch-2"]
    assert all(thread != gui_thread for thread in client.list_threads)
    assert "All available records loaded" in window.status.text()
    header = window.table.horizontalHeader()
    assert header.stretchLastSection() is False
    assert all(
        header.sectionResizeMode(index) == QHeaderView.ResizeMode.Interactive
        for index in range(window.table_model.columnCount())
    )
    assert window.table.columnWidth(2) > window.table.columnWidth(4)

    window.table.setColumnWidth(0, 222)
    window.refresh.click()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 2
        and not window.table_model.loading
        and len(client.list_cursors) >= 4,
    )
    assert window.table.columnWidth(0) == 222

    window.table.selectRow(0)
    application.processEvents()
    assert window.view.isEnabled() is True
    window.table.activated.emit(window.table_model.index(0, 0))
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
    assert window.table_model.wait_for_done(1000)


def _wait_until(
    application: QApplication,
    predicate: Any,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for Qt background work")
        time.sleep(0.005)
    application.processEvents()
