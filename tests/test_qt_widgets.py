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

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHeaderView,
    QLineEdit,
    QTableView,
)

from tide import compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.data import FilterCondition, QuerySpec, SortField
from tide.qt import QtBrowseController, TideQtWindow


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


class _WidgetClient:
    def __init__(self) -> None:
        self.queries: list[QuerySpec] = []
        self.query_threads: list[int] = []

    def query_records(
        self,
        entity_name: str,
        query: QuerySpec,
    ) -> Any:
        assert entity_name == "sales.Invoice"
        assert query.limit == 5
        self.queries.append(query)
        self.query_threads.append(threading.get_ident())
        if query.cursor is None:
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
        assert query.cursor == "qt-batch-2"
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


class _ProductWidgetClient:
    def __init__(self) -> None:
        self.records = [
            {
                "id": 10,
                "code": "CONSULT",
                "name": "Consulting",
                "unit_price": Decimal("500.00"),
                "active": True,
            }
        ]
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[Any, dict[str, Any], Any]] = []
        self.mutation_threads: list[int] = []

    def query_records(self, entity_name: str, query: QuerySpec) -> Any:
        assert entity_name == "catalog.Product"
        return SimpleNamespace(records=tuple(self.records), next_cursor=None)

    def get_record(self, entity_name: str, identity: Any) -> Any:
        assert entity_name == "catalog.Product"
        record = next(item for item in self.records if item["id"] == identity)
        return SimpleNamespace(values=dict(record), etag='"3"')

    def create_record(
        self,
        entity_name: str,
        values: dict[str, Any],
    ) -> Any:
        assert entity_name == "catalog.Product"
        self.mutation_threads.append(threading.get_ident())
        self.created.append(dict(values))
        stored = {"id": 11, **values}
        self.records.append(stored)
        return SimpleNamespace(values=dict(stored), etag=None)

    def update_record(
        self,
        entity_name: str,
        identity: Any,
        values: dict[str, Any],
        *,
        if_match: str | int | None = None,
    ) -> Any:
        assert entity_name == "catalog.Product"
        self.mutation_threads.append(threading.get_ident())
        self.updated.append((identity, dict(values), if_match))
        record = next(item for item in self.records if item["id"] == identity)
        record.update(values)
        return SimpleNamespace(values=dict(record), etag='"4"')


def test_qt_widget_adapter_incrementally_loads_the_server_list(
    tmp_path: Path,
) -> None:
    gui_thread = threading.get_ident()
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    session = _session(model)
    client = _WidgetClient()
    controller = QtBrowseController(model, client, session, page_size=5)
    layout_settings = _layout_settings(tmp_path / "browse-layout.ini")

    window = TideQtWindow(
        controller,
        source_label="off-screen test",
        layout_settings=layout_settings,
    )
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
    assert [query.cursor for query in client.queries[:2]] == [
        None,
        "qt-batch-2",
    ]
    assert all(thread != gui_thread for thread in client.query_threads)
    assert "All available records loaded" in window.status.text()
    header = window.table.horizontalHeader()
    assert header.stretchLastSection() is False
    assert header.sectionsMovable() is True
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
        and len(client.queries) >= 4,
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

    window.search.setText("QT-002")
    _wait_until(
        application,
        lambda: any(
            query.filters
            == (FilterCondition("number", "contains", "QT-002"),)
            for query in client.queries
        )
        and not window.table_model.loading,
    )
    assert "search 'QT-002'" in window.status.text()

    drafts_index = window.named_filter.findData("drafts")
    assert drafts_index > 0
    window.named_filter.setCurrentIndex(drafts_index)
    _wait_until(
        application,
        lambda: any(
            query.filters
            == (
                FilterCondition("number", "contains", "QT-002"),
                FilterCondition("status", "eq", "draft"),
            )
            for query in client.queries
        )
        and not window.table_model.loading,
    )
    assert "Draft invoices" in window.status.text()

    header.sectionClicked.emit(4)
    _wait_until(
        application,
        lambda: any(
            query.sort == (SortField("total"),)
            for query in client.queries
        )
        and not window.table_model.loading,
    )
    assert "Total ascending" in window.status.text()

    header.sectionClicked.emit(4)
    _wait_until(
        application,
        lambda: any(
            query.sort == (SortField("total", descending=True),)
            for query in client.queries
        )
        and not window.table_model.loading,
    )
    assert "Total descending" in window.status.text()

    window.clear_query.click()
    _wait_until(
        application,
        lambda: client.queries[-1].filters == ()
        and client.queries[-1].sort == ()
        and not window.table_model.loading,
    )
    assert window.search.text() == ""
    assert window.named_filter.currentData() is None
    assert header.isSortIndicatorShown() is False

    window.close()
    assert window.table_model.wait_for_done(1000)


def test_qt_column_layout_is_personal_and_resettable(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    session = _session(model)
    settings_path = tmp_path / "personal-layout.ini"
    settings = _layout_settings(settings_path)
    first = TideQtWindow(
        QtBrowseController(
            model,
            _WidgetClient(),
            session,
            page_size=5,
        ),
        source_label="layout test",
        layout_settings=settings,
    )
    first.show()
    _wait_until(application, lambda: first.table_model.rowCount() >= 1)

    first_header = first.table.horizontalHeader()
    first_header.moveSection(first_header.visualIndex(4), 0)
    first.table.setColumnWidth(0, 231)
    application.processEvents()
    first.close()
    assert first.table_model.wait_for_done(1000)

    other_principal = TideQtWindow(
        QtBrowseController(
            model,
            _WidgetClient(),
            _session(model, principal="qt:other"),
            page_size=5,
        ),
        source_label="layout test",
        layout_settings=_layout_settings(settings_path),
    )
    other_principal.show()
    _wait_until(
        application,
        lambda: other_principal.table_model.rowCount() >= 1,
    )
    assert other_principal.table.horizontalHeader().logicalIndex(0) == 0
    other_principal.close()
    assert other_principal.table_model.wait_for_done(1000)

    restored_settings = _layout_settings(settings_path)
    second = TideQtWindow(
        QtBrowseController(
            model,
            _WidgetClient(),
            session,
            page_size=5,
        ),
        source_label="layout test",
        layout_settings=restored_settings,
    )
    second.show()
    _wait_until(application, lambda: second.table_model.rowCount() >= 1)

    second_header = second.table.horizontalHeader()
    assert second_header.logicalIndex(0) == 4
    assert second.table.columnWidth(0) == 231

    second.table.setColumnWidth(4, 900)
    second.best_fit.click()
    application.processEvents()
    assert 72 <= second.table.columnWidth(4) <= 360
    assert restored_settings.contains(second._column_layout_key)

    second.reset_layout.click()
    application.processEvents()
    assert tuple(
        second_header.logicalIndex(visual_index)
        for visual_index in range(second_header.count())
    ) == (0, 1, 2, 3, 4)
    assert not restored_settings.contains(second._column_layout_key)

    second.close()
    assert second.table_model.wait_for_done(1000)


def test_qt_flat_product_form_creates_and_updates_through_api(
    tmp_path: Path,
) -> None:
    gui_thread = threading.get_ident()
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _ProductWidgetClient()
    window = TideQtWindow(
        QtBrowseController(
            model,
            client,
            _product_session(model),
            view_name="catalog.Product.browse",
            page_size=5,
        ),
        source_label="flat edit test",
        layout_settings=_layout_settings(tmp_path / "product-layout.ini"),
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 1
        and not window.table_model.loading,
    )

    assert window.new.isEnabled() is True
    assert window.edit.isEnabled() is False
    window.new.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    create_dialog = next(iter(window._edit_dialogs))
    code = create_dialog.editors["code"]
    name = create_dialog.editors["name"]
    unit_price = create_dialog.editors["unit_price"]
    active = create_dialog.editors["active"]
    assert isinstance(code, QLineEdit)
    assert isinstance(name, QLineEdit)
    assert isinstance(unit_price, QLineEdit)
    assert isinstance(active, QCheckBox)
    assert active.isChecked() is True

    code.setText("lowercase")
    name.setText("Support")
    unit_price.setText("12.50")
    create_dialog.save_button.click()
    application.processEvents()
    assert "invalid format" in create_dialog.message.text()
    assert client.created == []

    code.setText("SUPPORT")
    create_dialog.save_button.click()
    _wait_until(
        application,
        lambda: len(client.created) == 1
        and not window._edit_dialogs
        and window.table_model.rowCount() == 2
        and not window.table_model.loading,
    )
    assert client.created[0] == {
        "code": "SUPPORT",
        "unit_price": Decimal("12.50"),
        "name": "Support",
        "active": True,
    }

    window.table.selectRow(0)
    application.processEvents()
    assert window.edit.isEnabled() is True
    window.edit.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    edit_dialog = next(iter(window._edit_dialogs))
    edit_name = edit_dialog.editors["name"]
    assert isinstance(edit_name, QLineEdit)
    assert edit_name.text() == "Consulting"
    edit_name.setText("Consulting day")
    edit_dialog.save_button.click()
    _wait_until(
        application,
        lambda: len(client.updated) == 1 and not window._edit_dialogs,
    )
    assert client.updated == [
        (10, {"name": "Consulting day"}, '"3"')
    ]
    assert all(thread != gui_thread for thread in client.mutation_threads)

    window.close()
    assert window.wait_for_done(1000)


def _session(
    model: Any,
    *,
    principal: str = "qt:tester",
) -> TideSessionInfo:
    return TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="development",
        principal=principal,
        roles=("sales_clerk",),
        entities={
            name: TideEntityCapabilities(
                operations=("list", "get"),
                readable_fields=tuple(entity.fields),
            )
            for name, entity in model.entities.items()
        },
    )


def _product_session(model: Any) -> TideSessionInfo:
    product = model.entity("catalog.Product")
    return TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="development",
        principal="qt:editor",
        roles=("sales_clerk",),
        entities={
            "catalog.Product": TideEntityCapabilities(
                operations=("list", "get", "create", "update"),
                readable_fields=tuple(product.fields),
                writable_fields=("code", "name", "unit_price", "active"),
            )
        },
    )


def _layout_settings(path: Path) -> QSettings:
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    return settings


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
