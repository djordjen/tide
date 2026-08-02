from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest


pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QTableView,
)

from tide import compile_project
from tide.api import TideApiClientError
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.data import FilterCondition, QuerySpec, SortField
from tide.qt import (
    QtBrowseController,
    TideQtReferenceEditor,
    TideQtWindow,
    TideQtWorkspaceWindow,
)
from tide.reporting import (
    ReportCell,
    ReportColumn,
    ReportDocument,
    ReportTable,
    ReportValue,
)
from tide.sessions import ConflictValueChoice


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _invoice_report_document(number: str = "INV-QT-001") -> ReportDocument:
    return ReportDocument(
        report="sales.invoice",
        title="Invoice",
        application="TIDE Invoicing",
        generated_at=datetime(2026, 7, 25, 10, 0, tzinfo=UTC),
        header_text=("Invoice",),
        record_values=(
            ReportValue("Invoice number", number),
            ReportValue("Status", "Draft"),
        ),
        detail=ReportTable(
            columns=(
                ReportColumn("description", "Description"),
                ReportColumn("total", "Total", "right"),
            ),
            rows=(
                (
                    ReportCell("Consulting day"),
                    ReportCell("1,250.00", "right"),
                ),
            ),
        ),
        footer_values=(ReportValue("Total", "1,250.00", "right"),),
        page_footer_template="Page {page_number}",
        suggested_filename=f"invoice-{number}",
    )


def _sales_summary_document() -> ReportDocument:
    return ReportDocument(
        report="sales.summary",
        title="Posted Sales Summary",
        application="TIDE Invoicing",
        generated_at=datetime(2026, 7, 26, 10, 0, tzinfo=UTC),
        header_text=("Posted invoices grouped by Customer and Currency",),
        record_values=(),
        detail=ReportTable(
            columns=(
                ReportColumn("customer", "Customer"),
                ReportColumn("currency", "Currency"),
                ReportColumn("invoice_count", "Invoices", "right"),
                ReportColumn("sales_total", "Sales total", "right"),
            ),
            rows=(
                (
                    ReportCell("ADRIA - Adria Consulting"),
                    ReportCell("EUR"),
                    ReportCell("2", "right"),
                    ReportCell("2,400.00", "right"),
                ),
            ),
        ),
        footer_values=(),
        page_footer_template="Page {page_number}",
        suggested_filename="posted-sales-summary",
    )


class _WidgetClient:
    def __init__(self) -> None:
        self.queries: list[QuerySpec] = []
        self.query_threads: list[int] = []
        self.report_calls: list[tuple[str, Any]] = []
        self.report_threads: list[int] = []
        self.summary_calls: list[tuple[str, dict[str, Any]]] = []
        self.summary_threads: list[int] = []

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
            if identity == 2:
                return SimpleNamespace(
                    values={
                        "id": 2,
                        "number": "INV-QT-002",
                        "invoice_date": date(2026, 7, 22),
                        "currency": "EUR",
                        "status": "posted",
                        "posted_at": datetime(2026, 7, 22, 12, 0),
                        "posted_by": "qt:auditor",
                        "version": 2,
                        "customer": 1,
                        "lines": [],
                        "total": Decimal("25.00"),
                    },
                    etag='"2"',
                )
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
                },
                etag='"1"',
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

    def build_report_for_record(
        self,
        report_name: str,
        identity: Any,
    ) -> ReportDocument:
        self.report_threads.append(threading.get_ident())
        self.report_calls.append((report_name, identity))
        return _invoice_report_document()

    def build_report(
        self,
        report_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> ReportDocument:
        self.summary_threads.append(threading.get_ident())
        self.summary_calls.append((report_name, dict(parameters or {})))
        return _sales_summary_document()


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


class _CursorProductWidgetClient(_ProductWidgetClient):
    def __init__(self) -> None:
        super().__init__()
        self.records = [
            {
                "id": identity,
                "code": f"P{identity:04d}",
                "name": f"Product {identity}",
                "unit_price": Decimal(f"{identity}.00"),
                "active": True,
            }
            for identity in range(1, 52)
        ]
        self.queries: list[QuerySpec] = []

    def query_records(self, entity_name: str, query: QuerySpec) -> Any:
        assert entity_name == "catalog.Product"
        self.queries.append(query)
        if query.cursor is None:
            return SimpleNamespace(
                records=tuple(self.records[:50]),
                next_cursor="products-batch-2",
            )
        assert query.cursor == "products-batch-2"
        return SimpleNamespace(
            records=tuple(self.records[50:]),
            next_cursor=None,
        )


class _FailingNavigationProductWidgetClient(_ProductWidgetClient):
    def __init__(self) -> None:
        super().__init__()
        self.records.append(
            {
                "id": 11,
                "code": "SUPPORT",
                "name": "Support",
                "unit_price": Decimal("12.50"),
                "active": True,
            }
        )

    def get_record(self, entity_name: str, identity: Any) -> Any:
        if identity == 11:
            raise RuntimeError("adjacent record is temporarily unavailable")
        return super().get_record(entity_name, identity)


class _NoAutomaticPrefetchWindow(TideQtWindow):
    def _prefetch_if_near_end(self, *_args: Any) -> None:
        pass


class _ConflictingProductWidgetClient(_ProductWidgetClient):
    def __init__(self) -> None:
        super().__init__()
        self.etag = '"3"'
        self.stale_attempts: list[tuple[Any, dict[str, Any], Any]] = []
        self._raised_stale = False

    def get_record(self, entity_name: str, identity: Any) -> Any:
        assert entity_name == "catalog.Product"
        record = next(item for item in self.records if item["id"] == identity)
        return SimpleNamespace(values=dict(record), etag=self.etag)

    def update_record(
        self,
        entity_name: str,
        identity: Any,
        values: dict[str, Any],
        *,
        if_match: str | int | None = None,
    ) -> Any:
        self.mutation_threads.append(threading.get_ident())
        if not self._raised_stale:
            self._raised_stale = True
            self.stale_attempts.append((identity, dict(values), if_match))
            self.records[0]["name"] = "Server consulting"
            self.etag = '"4"'
            raise TideApiClientError(
                409,
                "stale_version",
                "record changed after it was loaded",
            )
        assert if_match == '"4"'
        self.updated.append((identity, dict(values), if_match))
        self.records[0].update(values)
        self.etag = '"5"'
        return SimpleNamespace(values=dict(self.records[0]), etag=self.etag)


class _InvoiceLookupWidgetClient:
    def __init__(self) -> None:
        self.invoice = {
            "id": 1,
            "number": "INV-QT-LOOKUP",
            "invoice_date": date(2026, 7, 25),
            "currency": "EUR",
            "status": "draft",
            "posted_at": None,
            "posted_by": None,
            "version": 1,
            "customer": 1,
            "lines": [],
            "total": Decimal("0.00"),
        }
        self.customers = [
            {
                "id": 1,
                "code": "ADRIA",
                "name": "Adria Consulting",
                "email": "office@adria.test",
                "active": True,
            },
            {
                "id": 2,
                "code": "NORTH",
                "name": "Northwind",
                "email": "office@north.test",
                "active": True,
            },
        ]
        self.queries: list[tuple[str, QuerySpec]] = []
        self.updated: list[tuple[Any, dict[str, Any], Any]] = []
        self.created_customers: list[dict[str, Any]] = []
        self.reference_selections: list[Any] = []
        self.network_threads: list[int] = []

    def query_records(self, entity_name: str, query: QuerySpec) -> Any:
        self.network_threads.append(threading.get_ident())
        self.queries.append((entity_name, query))
        if entity_name == "sales.Invoice":
            return SimpleNamespace(records=(dict(self.invoice),), next_cursor=None)
        assert entity_name == "crm.Customer"
        records = self.customers
        if query.filters:
            condition = query.filters[0]
            candidate = str(condition.value).casefold()
            records = [
                record
                for record in records
                if candidate in str(record.get(condition.field, "")).casefold()
            ]
        return SimpleNamespace(
            records=tuple(dict(record) for record in records),
            next_cursor=None,
        )

    def get_record(self, entity_name: str, identity: Any) -> Any:
        self.network_threads.append(threading.get_ident())
        if entity_name == "sales.Invoice":
            return SimpleNamespace(values=dict(self.invoice), etag='"9"')
        assert entity_name == "crm.Customer"
        record = next(item for item in self.customers if item["id"] == identity)
        return SimpleNamespace(values=dict(record), etag=None)

    def create_record(
        self,
        entity_name: str,
        values: dict[str, Any],
    ) -> Any:
        self.network_threads.append(threading.get_ident())
        assert entity_name == "crm.Customer"
        self.created_customers.append(dict(values))
        stored = {"id": 3, **values}
        self.customers.append(stored)
        return SimpleNamespace(values=dict(stored), etag=None)

    def update_record(
        self,
        entity_name: str,
        identity: Any,
        values: dict[str, Any],
        *,
        if_match: str | int | None = None,
    ) -> Any:
        self.network_threads.append(threading.get_ident())
        assert entity_name == "sales.Invoice"
        self.updated.append((identity, dict(values), if_match))
        self.invoice.update(values)
        return SimpleNamespace(values=dict(self.invoice), etag='"10"')

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: dict[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        self.network_threads.append(threading.get_ident())
        self.reference_selections.append(
            (entity_name, field_name, dict(values), identity)
        )
        return {**values, field_name: identity}


class _InvoiceLinesWidgetClient(_InvoiceLookupWidgetClient):
    def __init__(self) -> None:
        super().__init__()
        self.action_calls: list[
            tuple[str, Any, dict[str, Any], Any, str | None]
        ] = []
        self.action_threads: list[int] = []
        self.product = {
            "id": 10,
            "code": "CONSULT",
            "name": "Consulting",
            "unit_price": Decimal("500.00"),
            "active": True,
        }

    def query_records(self, entity_name: str, query: QuerySpec) -> Any:
        if entity_name != "catalog.Product":
            return super().query_records(entity_name, query)
        self.network_threads.append(threading.get_ident())
        self.queries.append((entity_name, query))
        return SimpleNamespace(records=(dict(self.product),), next_cursor=None)

    def get_record(self, entity_name: str, identity: Any) -> Any:
        if entity_name != "catalog.Product":
            return super().get_record(entity_name, identity)
        self.network_threads.append(threading.get_ident())
        assert identity == 10
        return SimpleNamespace(values=dict(self.product), etag='"2"')

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: dict[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        if entity_name != "sales.InvoiceLine":
            return super().apply_reference_selection(
                entity_name,
                field_name,
                values,
                identity,
            )
        self.network_threads.append(threading.get_ident())
        self.reference_selections.append(
            (entity_name, field_name, dict(values), identity)
        )
        return {
            **values,
            field_name: identity,
            "description": self.product["name"],
            "unit_price": self.product["unit_price"],
        }

    def execute_action(
        self,
        entity_name: str,
        action_name: str,
        identity: Any,
        payload: dict[str, Any] | None = None,
        *,
        if_match: str | int | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        self.network_threads.append(threading.get_ident())
        self.action_threads.append(threading.get_ident())
        assert entity_name == "sales.Invoice"
        assert action_name == "post"
        self.action_calls.append(
            (
                action_name,
                identity,
                dict(payload or {}),
                if_match,
                idempotency_key,
            )
        )
        self.invoice.update(
            {
                "status": "posted",
                "version": 2,
                "posted_by": "qt:editor",
            }
        )
        return SimpleNamespace(values=dict(self.invoice), etag='"11"')


def test_qt_widget_adapter_incrementally_loads_the_server_list(
    tmp_path: Path,
) -> None:
    gui_thread = threading.get_ident()
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    session = _invoice_lines_session(model)
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
    assert window.open.isEnabled() is True
    window.table.activated.emit(window.table_model.index(0, 0))
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    detail = next(iter(window._edit_dialogs))
    original_geometry = detail.geometry()
    original_number_editor = detail.editors["number"]
    original_customer_editor = detail.editors["customer"]
    original_lines_editor = detail.collection_editors["lines"]
    assert detail.editors["number"].text() == "INV-QT-001"
    assert isinstance(original_customer_editor, TideQtReferenceEditor)
    assert (
        original_customer_editor.display.text()
        == "ADRIA - Adria Consulting"
    )
    assert detail.save_button.isVisible() is True
    assert detail.previous_button.isEnabled() is False
    assert detail.next_button.isEnabled() is True
    assert detail.previous_shortcut.key() == QKeySequence(
        Qt.Key.Key_PageUp
    )
    assert detail.previous_shortcut.isEnabled() is False
    assert detail.next_shortcut.key() == QKeySequence(
        Qt.Key.Key_PageDown
    )
    assert detail.next_shortcut.isEnabled() is True
    assert detail.previous_button.mapTo(detail, QPoint()).x() < (
        detail.cancel_button.mapTo(detail, QPoint()).x()
    )
    lines = detail.collection_editors["lines"].table
    assert lines.rowCount() == 1
    assert lines.item(0, 1).text() == "CONSULT - Consulting"
    assert lines.item(0, 5).text() == "1,250.00"
    detail.next_shortcut.activated.emit()
    _wait_until(
        application,
        lambda: len(window._edit_dialogs) == 1
        and next(iter(window._edit_dialogs)).form.identity == 2,
    )
    next_detail = next(iter(window._edit_dialogs))
    assert next_detail is detail
    assert next_detail.geometry() == original_geometry
    assert next_detail.editors["number"] is original_number_editor
    assert next_detail.editors["customer"] is original_customer_editor
    assert next_detail.collection_editors["lines"] is original_lines_editor
    assert next_detail.editors["number"].text() == "INV-QT-002"
    assert next_detail.save_button.isVisible() is False
    assert next_detail.collection_editors["lines"].table.rowCount() == 0
    assert all(
        not editor.isEnabled()
        for editor in original_lines_editor.editors.values()
    )
    assert next_detail.previous_button.isEnabled() is True
    assert next_detail.previous_shortcut.isEnabled() is True
    assert next_detail.next_button.isEnabled() is False
    assert next_detail.next_shortcut.isEnabled() is False
    assert window.table.currentIndex().row() == 1

    next_detail.previous_shortcut.activated.emit()
    _wait_until(
        application,
        lambda: len(window._edit_dialogs) == 1
        and next(iter(window._edit_dialogs)).form.identity == 1,
    )
    previous_detail = next(iter(window._edit_dialogs))
    assert previous_detail is detail
    assert previous_detail.geometry() == original_geometry
    assert previous_detail.editors["number"].text() == "INV-QT-001"
    assert previous_detail.save_button.isVisible() is True
    assert previous_detail.collection_editors["lines"].table.rowCount() == 1
    assert all(
        editor.isEnabled()
        for editor in original_lines_editor.editors.values()
    )
    assert all(
        button.isEnabled()
        for button in original_lines_editor.action_buttons.values()
    )
    assert previous_detail.previous_button.isEnabled() is False
    assert previous_detail.next_button.isEnabled() is True
    assert window.table.currentIndex().row() == 0
    previous_detail.close()

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


def test_qt_record_report_previews_and_exports_remote_document(
    tmp_path: Path,
) -> None:
    gui_thread = threading.get_ident()
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    session = _session(model).model_copy(
        update={"reports": ("sales.invoice",)}
    )
    client = _WidgetClient()
    opened: list[Path] = []

    def open_report(path: Path) -> bool:
        opened.append(path)
        return True

    window = TideQtWindow(
        QtBrowseController(model, client, session, page_size=5),
        source_label="report test",
        layout_settings=_layout_settings(tmp_path / "report-layout.ini"),
        report_output_directory=tmp_path,
        report_opener=open_report,
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() >= 1
        and not window.table_model.loading,
    )

    assert window.summary_report.isHidden()
    assert window.open.isEnabled() is False
    window.table.selectRow(0)
    application.processEvents()
    assert window.open.isEnabled() is True
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    dialog = next(iter(window._edit_dialogs))
    assert dialog.preview_button is not None
    dialog.preview_button.click()
    _wait_until(application, lambda: len(opened) == 1 and not dialog._saving)

    assert client.report_calls == [("sales.invoice", 1)]
    assert all(thread != gui_thread for thread in client.report_threads)
    assert opened[0].parent == tmp_path
    assert opened[0].read_bytes().startswith(b"%PDF-")
    assert "Opened temporary PDF preview" in dialog.message.text()
    dialog.close()
    window.close()
    assert window.wait_for_done(1000)


def test_qt_summary_report_opens_native_preview_and_exports_csv(
    tmp_path: Path,
) -> None:
    gui_thread = threading.get_ident()
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    session = _session(model).model_copy(
        update={"reports": ("sales.summary",)}
    )
    client = _WidgetClient()
    window = TideQtWindow(
        QtBrowseController(model, client, session, page_size=5),
        source_label="summary report test",
        layout_settings=_layout_settings(tmp_path / "summary-layout.ini"),
        report_output_directory=tmp_path,
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() >= 1
        and not window.table_model.loading,
    )

    assert window.summary_report.isVisible()
    assert window.summary_report.text() == "Posted Sales Summary"
    assert window.summary_report.isEnabled()
    window.summary_report.click()
    _wait_until(application, lambda: len(window._report_dialogs) == 1)
    dialog = next(iter(window._report_dialogs))

    assert client.summary_calls == [("sales.summary", {})]
    assert all(thread != gui_thread for thread in client.summary_threads)
    assert dialog.windowTitle() == "TIDE Invoicing — Posted Sales Summary"
    assert dialog.detail.columnCount() == 4
    assert dialog.detail.item(0, 0).text() == "ADRIA - Adria Consulting"
    assert dialog.detail.item(0, 3).text() == "2,400.00"
    assert "Posted Sales Summary ready" in window.status.text()

    dialog.export_csv.click()
    destination = tmp_path / "posted-sales-summary.csv"
    _wait_until(
        application,
        lambda: destination.is_file() and not dialog._exporting,
    )
    exported = destination.read_text(encoding="utf-8-sig")
    assert "Customer,Currency,Invoices,Sales total" in exported
    assert "ADRIA - Adria Consulting,EUR,2,\"2,400.00\"" in exported

    dialog.close_button.click()
    window.close()
    assert window.wait_for_done(1000)


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
    assert window.open.isEnabled() is False
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
    assert create_dialog.previous_button.isVisible() is False
    assert create_dialog.next_button.isVisible() is False
    code.setFocus()
    QTest.keyClick(code, Qt.Key.Key_Return)
    application.processEvents()
    assert name.hasFocus()

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
    assert window.open.isEnabled() is True
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    edit_dialog = next(iter(window._edit_dialogs))
    edit_name = edit_dialog.editors["name"]
    assert isinstance(edit_name, QLineEdit)
    assert edit_name.text() == "Consulting"
    edit_name.setText("Consulting day")
    assert edit_dialog.next_button.isEnabled() is True
    edit_dialog.next_button.click()
    application.processEvents()
    assert edit_dialog in window._edit_dialogs
    assert edit_dialog.form.identity == 10
    assert (
        edit_dialog.message.text()
        == "Save or cancel your changes before navigating to another record."
    )
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


def test_qt_detail_next_fetches_the_adjacent_cursor_batch(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _CursorProductWidgetClient()
    window = _NoAutomaticPrefetchWindow(
        QtBrowseController(
            model,
            client,
            _product_session(model),
            view_name="catalog.Product.browse",
            page_size=50,
        ),
        source_label="cursor navigation test",
        layout_settings=_layout_settings(tmp_path / "cursor-navigation.ini"),
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 50
        and not window.table_model.loading,
    )
    assert len(client.queries) == 1
    assert window.table_model.has_more is True

    window.table.selectRow(49)
    application.processEvents()
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    last_loaded = next(iter(window._edit_dialogs))
    assert last_loaded.form.identity == 50
    assert last_loaded.next_button.isEnabled() is True

    last_loaded.next_button.click()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 51
        and len(window._edit_dialogs) == 1
        and next(iter(window._edit_dialogs)).form.identity == 51,
    )
    adjacent = next(iter(window._edit_dialogs))
    assert adjacent is last_loaded
    assert [query.cursor for query in client.queries] == [
        None,
        "products-batch-2",
    ]
    assert adjacent.previous_button.isEnabled() is True
    assert adjacent.next_button.isEnabled() is False
    adjacent.close()

    window.close()
    assert window.wait_for_done(1000)


def test_qt_detail_navigation_failure_keeps_the_current_form(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _FailingNavigationProductWidgetClient()
    window = TideQtWindow(
        QtBrowseController(
            model,
            client,
            _product_session(model),
            view_name="catalog.Product.browse",
            page_size=5,
        ),
        source_label="failed navigation test",
        layout_settings=_layout_settings(tmp_path / "failed-navigation.ini"),
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 2
        and not window.table_model.loading,
    )

    window.table.selectRow(0)
    application.processEvents()
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    current = next(iter(window._edit_dialogs))
    current.next_button.click()
    _wait_until(
        application,
        lambda: not current._saving
        and "temporarily unavailable" in current.message.text(),
    )
    assert window._edit_dialogs == {current}
    assert current.form.identity == 10
    assert current.next_button.isEnabled() is True
    current.close()

    window.close()
    assert window.wait_for_done(1000)


def test_qt_stale_product_edit_opens_three_way_review_and_rebase(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _ConflictingProductWidgetClient()
    window = TideQtWindow(
        QtBrowseController(
            model,
            client,
            _product_session(model),
            view_name="catalog.Product.browse",
            page_size=5,
        ),
        source_label="conflict edit test",
        layout_settings=_layout_settings(tmp_path / "conflict-layout.ini"),
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 1
        and not window.table_model.loading,
    )
    window.table.selectRow(0)
    application.processEvents()
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    stale_form = next(iter(window._edit_dialogs))
    name = stale_form.editors["name"]
    unit_price = stale_form.editors["unit_price"]
    assert isinstance(name, QLineEdit)
    assert isinstance(unit_price, QLineEdit)
    name.setText("My consulting")
    unit_price.setText("525.00")
    stale_form.save_button.click()

    _wait_until(
        application,
        lambda: len(stale_form._conflict_dialogs) == 1,
    )
    review = next(iter(stale_form._conflict_dialogs))
    assert review.table.rowCount() == 2
    assert review.table.horizontalHeaderItem(1).text() == "Original"
    assert review.table.horizontalHeaderItem(2).text() == "Current"
    assert review.table.horizontalHeaderItem(3).text() == "Your draft"
    name_choice = review.choice_editors["name"]
    assert isinstance(name_choice, QComboBox)
    assert review.apply_resolution.isEnabled() is False
    name_choice.setCurrentIndex(
        name_choice.findData(ConflictValueChoice.DRAFT)
    )
    assert review.apply_resolution.isEnabled() is True
    review.apply_resolution.click()

    _wait_until(
        application,
        lambda: stale_form not in window._edit_dialogs
        and len(window._edit_dialogs) == 1,
    )
    rebased_form = next(iter(window._edit_dialogs))
    rebased_name = rebased_form.editors["name"]
    rebased_price = rebased_form.editors["unit_price"]
    assert isinstance(rebased_name, QLineEdit)
    assert isinstance(rebased_price, QLineEdit)
    assert rebased_form.form.etag == '"4"'
    assert rebased_name.text() == "My consulting"
    assert rebased_price.text() == "525.00"
    assert "Review, then save or run the action again" in (
        rebased_form.message.text()
    )
    assert client.stale_attempts == [
        (
            10,
            {
                "unit_price": Decimal("525.00"),
                "name": "My consulting",
            },
            '"3"',
        )
    ]

    rebased_form.save_button.click()
    _wait_until(
        application,
        lambda: len(client.updated) == 1
        and not window._edit_dialogs
        and not window.table_model.loading,
    )
    assert client.updated == [
        (
            10,
            {
                "unit_price": Decimal("525.00"),
                "name": "My consulting",
            },
            '"4"',
        )
    ]

    window.close()
    assert window.wait_for_done(1000)


def test_qt_invoice_customer_lookup_search_create_and_select(
    tmp_path: Path,
) -> None:
    gui_thread = threading.get_ident()
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _InvoiceLookupWidgetClient()
    window = TideQtWindow(
        QtBrowseController(
            model,
            client,
            _invoice_edit_session(model),
            page_size=5,
        ),
        source_label="lookup edit test",
        layout_settings=_layout_settings(tmp_path / "invoice-lookup.ini"),
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 1
        and not window.table_model.loading,
    )

    window.table.selectRow(0)
    application.processEvents()
    assert window.open.isEnabled() is True
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    edit_dialog = next(iter(window._edit_dialogs))
    assert edit_dialog.form.omitted_collections == ()
    assert edit_dialog.form.collections[0].editable is False
    customer = edit_dialog.editors["customer"]
    assert isinstance(customer, TideQtReferenceEditor)
    assert customer.identity == 1
    assert customer.display.text() == "ADRIA - Adria Consulting"

    customer.select_button.click()
    _wait_until(
        application,
        lambda: any(
            dialog.isVisible()
            and dialog.table.rowCount() == 2
            and "matches" in dialog.status.text()
            for dialog in edit_dialog._lookup_dialogs
        ),
    )
    lookup = next(
        dialog for dialog in edit_dialog._lookup_dialogs if dialog.isVisible()
    )
    lookup.search.setText("north")
    _wait_until(
        application,
        lambda: lookup.table.rowCount() == 1
        and lookup.table.item(0, 0).text() == "NORTH"
        and "for 'north'" in lookup.status.text(),
    )
    lookup.select_button.click()
    _wait_until(
        application,
        lambda: customer.identity == 2
        and not edit_dialog._saving
        and "selected" in edit_dialog.message.text(),
    )
    assert customer.display.text() == "NORTH - Northwind"

    edit_dialog.save_button.click()
    _wait_until(
        application,
        lambda: client.updated
        and not window._edit_dialogs
        and not window.table_model.loading,
    )
    assert client.updated == [(1, {"customer": 2}, '"9"')]

    window.table.selectRow(0)
    application.processEvents()
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    second_edit = next(iter(window._edit_dialogs))
    second_customer = second_edit.editors["customer"]
    assert isinstance(second_customer, TideQtReferenceEditor)
    assert second_customer.identity == 2
    second_customer.select_button.click()
    _wait_until(
        application,
        lambda: any(
            dialog.isVisible()
            and "matches" in dialog.status.text()
            for dialog in second_edit._lookup_dialogs
        ),
    )
    second_lookup = next(
        dialog for dialog in second_edit._lookup_dialogs if dialog.isVisible()
    )
    assert second_lookup.new_button.isEnabled() is True
    second_lookup.new_button.click()
    _wait_until(
        application,
        lambda: any(
            dialog.isVisible() for dialog in second_lookup._create_dialogs
        ),
    )
    create_customer = next(
        dialog
        for dialog in second_lookup._create_dialogs
        if dialog.isVisible()
    )
    assert create_customer.save_button.text() == "Save & Select"
    code = create_customer.editors["code"]
    name = create_customer.editors["name"]
    email = create_customer.editors["email"]
    assert isinstance(code, QLineEdit)
    assert isinstance(name, QLineEdit)
    assert isinstance(email, QLineEdit)
    code.setText("NEWCO")
    name.setText("New Company")
    email.setText("office@newco.test")
    create_customer.save_button.click()
    _wait_until(
        application,
        lambda: len(client.created_customers) == 1
        and second_customer.identity == 3
        and not second_edit._saving,
    )
    assert second_customer.display.text() == "NEWCO - New Company"
    assert [item[3] for item in client.reference_selections] == [2, 3]
    assert all(thread != gui_thread for thread in client.network_threads)

    second_edit.cancel_button.click()
    window.close()
    assert window.wait_for_done(1000)


def test_qt_invoice_line_editor_applies_product_defaults_and_total(
    tmp_path: Path,
) -> None:
    gui_thread = threading.get_ident()
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _InvoiceLinesWidgetClient()
    window = TideQtWindow(
        QtBrowseController(
            model,
            client,
            _invoice_lines_session(model),
            page_size=5,
        ),
        source_label="line edit test",
        layout_settings=_layout_settings(tmp_path / "invoice-lines.ini"),
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 1
        and not window.table_model.loading,
    )
    window.table.selectRow(0)
    application.processEvents()
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    edit_dialog = next(iter(window._edit_dialogs))
    lines = edit_dialog.collection_editors["lines"]
    assert lines.collection.editable is True
    assert lines.table.rowCount() == 0

    lines.action_buttons["add"].click()
    application.processEvents()
    assert lines.table.rowCount() == 1
    assert lines._selected_row == 0
    line_number = lines.editors["line_number"]
    product = lines.editors["product"]
    description = lines.editors["description"]
    quantity = lines.editors["quantity"]
    unit_price = lines.editors["unit_price"]
    assert isinstance(line_number, QLineEdit)
    assert isinstance(product, TideQtReferenceEditor)
    assert isinstance(description, QLineEdit)
    assert isinstance(quantity, QLineEdit)
    assert isinstance(unit_price, QLineEdit)
    assert line_number.text() == "1"

    product.select_button.click()
    _wait_until(
        application,
        lambda: any(
            dialog.isVisible()
            and dialog.spec.target_entity == "catalog.Product"
            and dialog.table.rowCount() == 1
            for dialog in edit_dialog._lookup_dialogs
        ),
    )
    lookup = next(
        dialog
        for dialog in edit_dialog._lookup_dialogs
        if dialog.isVisible()
        and dialog.spec.target_entity == "catalog.Product"
    )
    assert tuple(
        lookup.table.horizontalHeaderItem(index).text()
        for index in range(lookup.table.columnCount())
    ) == ("Code", "Name", "Unit Price")
    lookup.select_button.click()
    _wait_until(
        application,
        lambda: product.identity == 10
        and description.text() == "Consulting"
        and unit_price.text() == "500.00"
        and not edit_dialog._saving,
    )
    assert product.display.text() == "CONSULT - Consulting"

    quantity.setText("2.000")
    lines.action_buttons["apply"].click()
    application.processEvents()
    assert lines.table.item(0, 1).text() == "CONSULT - Consulting"
    assert lines.table.item(0, 5).text() == "1,000.00"
    total = edit_dialog.editors["total"]
    assert isinstance(total, QLineEdit)
    assert total.text() == "1,000.00"
    assert "applied locally" in edit_dialog.message.text()

    lines.action_buttons["add"].click()
    application.processEvents()
    assert lines.table.rowCount() == 2
    lines.action_buttons["remove"].click()
    application.processEvents()
    assert lines.table.rowCount() == 1
    assert lines._selected_row == 0

    edit_dialog.save_button.click()
    _wait_until(
        application,
        lambda: len(client.updated) == 1
        and not window._edit_dialogs
        and not window.table_model.loading,
    )
    assert client.updated[0] == (
        1,
        {
            "lines": [
                {
                    "line_number": 1,
                    "product": 10,
                    "description": "Consulting",
                    "quantity": Decimal("2.000"),
                    "unit_price": Decimal("500.00"),
                }
            ]
        },
        '"9"',
    )
    assert client.reference_selections[-1][0:2] == (
        "sales.InvoiceLine",
        "product",
    )
    assert all(thread != gui_thread for thread in client.network_threads)

    window.close()
    assert window.wait_for_done(1000)


def test_qt_invoice_post_saves_lines_then_runs_the_secured_action(
    tmp_path: Path,
) -> None:
    gui_thread = threading.get_ident()
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _InvoiceLinesWidgetClient()
    window = TideQtWindow(
        QtBrowseController(
            model,
            client,
            _invoice_lines_session(model),
            page_size=5,
        ),
        source_label="invoice action test",
        layout_settings=_layout_settings(tmp_path / "invoice-action.ini"),
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 1
        and not window.table_model.loading,
    )
    window.table.selectRow(0)
    application.processEvents()
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    dialog = next(iter(window._edit_dialogs))
    post = dialog.action_buttons["post"]
    assert post.text() == "Post invoice"
    assert post.isEnabled() is False

    lines = dialog.collection_editors["lines"]
    lines.action_buttons["add"].click()
    product = lines.editors["product"]
    description = lines.editors["description"]
    quantity = lines.editors["quantity"]
    unit_price = lines.editors["unit_price"]
    assert isinstance(product, TideQtReferenceEditor)
    assert isinstance(description, QLineEdit)
    assert isinstance(quantity, QLineEdit)
    assert isinstance(unit_price, QLineEdit)
    product.set_selection(10, "CONSULT - Consulting")
    description.setText("Consulting")
    quantity.setText("2.000")
    unit_price.setText("500.00")
    lines.action_buttons["apply"].click()
    application.processEvents()
    assert post.isEnabled() is True

    post.click()
    _wait_until(
        application,
        lambda: len(client.action_calls) == 1
        and not window._edit_dialogs
        and not window.table_model.loading,
    )
    assert client.updated[0][0] == 1
    assert client.updated[0][2] == '"9"'
    assert client.updated[0][1]["lines"][0] == {
        "line_number": 1,
        "product": 10,
        "description": "Consulting",
        "quantity": Decimal("2.000"),
        "unit_price": Decimal("500.00"),
    }
    action_name, identity, payload, etag, idempotency_key = (
        client.action_calls[0]
    )
    assert (action_name, identity, payload, etag) == (
        "post",
        1,
        {},
        '"10"',
    )
    assert idempotency_key is not None
    assert idempotency_key.startswith("qt:")
    assert window.table_model.data(
        window.table_model.index(0, 3),
    ) == "Posted"
    assert "Post invoice completed" in window.status.text()
    assert all(thread != gui_thread for thread in client.action_threads)

    window.close()
    assert window.wait_for_done(1000)


def test_qt_invoice_poster_gets_a_read_only_action_form(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _InvoiceLinesWidgetClient()
    client.invoice["lines"] = [
        {
            "line_number": 1,
            "product": 10,
            "description": "Consulting",
            "quantity": Decimal("1.000"),
            "unit_price": Decimal("500.00"),
            "total": Decimal("500.00"),
        }
    ]
    client.invoice["total"] = Decimal("500.00")
    window = TideQtWindow(
        QtBrowseController(
            model,
            client,
            _invoice_poster_session(model),
            page_size=5,
        ),
        source_label="invoice poster test",
        layout_settings=_layout_settings(tmp_path / "invoice-poster.ini"),
    )
    window.show()
    _wait_until(
        application,
        lambda: window.table_model.rowCount() == 1
        and not window.table_model.loading,
    )
    assert window.open.text() == "Open"
    window.table.selectRow(0)
    application.processEvents()
    assert window.open.isEnabled() is True
    window.open.click()
    _wait_until(application, lambda: len(window._edit_dialogs) == 1)
    dialog = next(iter(window._edit_dialogs))

    assert dialog.save_button.isVisible() is False
    assert dialog.action_buttons["post"].isEnabled() is True
    assert all(not field.editable for field in dialog.form.fields)
    assert dialog.form.collections[0].editable is False

    dialog.cancel_button.click()
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


def _invoice_edit_session(model: Any) -> TideSessionInfo:
    invoice = model.entity("sales.Invoice")
    customer = model.entity("crm.Customer")
    return TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="development",
        principal="qt:editor",
        roles=("sales_clerk",),
        entities={
            "sales.Invoice": TideEntityCapabilities(
                operations=("list", "get", "create", "update"),
                readable_fields=tuple(invoice.fields),
                writable_fields=("invoice_date", "currency", "customer"),
                actions=("post",),
            ),
            "crm.Customer": TideEntityCapabilities(
                operations=("list", "get", "create", "update"),
                readable_fields=tuple(customer.fields),
                writable_fields=("code", "name", "email", "active"),
            ),
        },
    )


def _invoice_lines_session(model: Any) -> TideSessionInfo:
    base = _invoice_edit_session(model)
    line = model.entity("sales.InvoiceLine")
    product = model.entity("catalog.Product")
    return base.model_copy(
        update={
            "entities": {
                **base.entities,
                "sales.Invoice": base.entities["sales.Invoice"].model_copy(
                    update={
                        "writable_fields": (
                            "invoice_date",
                            "currency",
                            "customer",
                            "lines",
                        )
                    }
                ),
                "sales.InvoiceLine": TideEntityCapabilities(
                    draft_operations=("create", "update"),
                    readable_fields=tuple(line.fields),
                    writable_fields=(
                        "line_number",
                        "description",
                        "quantity",
                        "unit_price",
                        "product",
                    ),
                ),
                "catalog.Product": TideEntityCapabilities(
                    operations=("list", "get", "create", "update"),
                    readable_fields=tuple(product.fields),
                    writable_fields=("code", "name", "unit_price", "active"),
                ),
            }
        }
    )


def _invoice_poster_session(model: Any) -> TideSessionInfo:
    base = _invoice_lines_session(model)
    return base.model_copy(
        update={
            "principal": "qt:poster",
            "roles": ("invoice_poster",),
            "entities": {
                **base.entities,
                "sales.Invoice": base.entities["sales.Invoice"].model_copy(
                    update={
                        "operations": ("list", "get"),
                        "writable_fields": (),
                    }
                ),
                "sales.InvoiceLine": base.entities[
                    "sales.InvoiceLine"
                ].model_copy(
                    update={
                        "draft_operations": (),
                        "writable_fields": (),
                    }
                ),
            },
        }
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


def test_qt_application_navigation_preserves_visited_workspace_state(
    tmp_path: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    model = compile_project(INVOICING)
    client = _InvoiceLinesWidgetClient()
    window = TideQtWorkspaceWindow(
        model,
        client,
        _invoice_lines_session(model),
        source_label="off-screen test",
        layout_settings=_layout_settings(tmp_path / "workspace-layout.ini"),
    )
    window.show()
    invoice_workspace = window.current_workspace
    assert invoice_workspace is not None
    _wait_until(
        application,
        lambda: not invoice_workspace.table_model.loading,
    )

    assert [
        window.navigation.topLevelItem(index).text(0)
        for index in range(window.navigation.topLevelItemCount())
    ] == ["Sales", "Master Data"]
    assert window.windowTitle() == "TIDE Invoicing — Invoices"

    product_workspace = window.activate_view("catalog.Product.browse")
    _wait_until(
        application,
        lambda: product_workspace.table_model.rowCount() == 1
        and not product_workspace.table_model.loading,
    )
    product_workspace.search.setText("Consulting")
    product_workspace.table.setColumnWidth(0, 231)

    customer_workspace = window.activate_view("crm.Customer.browse")
    _wait_until(
        application,
        lambda: customer_workspace.table_model.rowCount() == 2
        and not customer_workspace.table_model.loading,
    )
    restored_product_workspace = window.activate_view("catalog.Product.browse")

    assert restored_product_workspace is product_workspace
    assert window.current_workspace is product_workspace
    assert product_workspace.search.text() == "Consulting"
    assert product_workspace.table.columnWidth(0) == 231
    assert window.windowTitle() == "TIDE Invoicing — Products"

    window.close()
    window.wait_for_done()
