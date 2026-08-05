"""PySide6 Qt Widgets adapter for the initial remote browse/detail prototype."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from PySide6.QtCore import (
    QSettings,
    QSignalBlocker,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QCloseEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tide.api.contracts import TideSessionInfo
from tide.compiler.normalized import ApplicationModel
from tide.presentation import application_navigation
from tide.reporting import (
    ReportDocument,
)
from tide.runtime import TideRuntimeError

from .columns import (
    apply_column_order,
    column_layout_key,
    configure_interactive_header,
    fit_content_columns,
    fit_interactive_columns,
    known_column_order,
    known_column_widths,
)
from .detail import TideQtDetailDialog
from .form import TideQtEditDialog
from .presenter import (
    BrowseApiClient,
    QtBrowseController,
    QtBrowseQuery,
    QtEditForm,
)
from .report import TideQtReportDialog
from .table import TideQtTableModel
from .workers import CallWorker


class TideQtWindow(QMainWindow):
    """Remote Qt workspace that delegates all data access to TideApiClient."""

    closeRequested = Signal()

    def __init__(
        self,
        controller: QtBrowseController,
        *,
        source_label: str,
        layout_settings: QSettings | None = None,
        report_output_directory: str | Path | None = None,
        report_opener: Callable[[Path], bool] | None = None,
        embedded: bool = False,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.source_label = source_label
        self._layout_settings = (
            layout_settings
            if layout_settings is not None
            else QSettings("TIDE Framework", "TIDE Qt")
        )
        self._column_layout_key = column_layout_key(controller)
        self._restoring_column_layout = False
        self._column_widths_initialized = False
        self._detail_dialogs: set[TideQtDetailDialog] = set()
        self._edit_dialogs: set[TideQtEditDialog] = set()
        self._report_dialogs: set[TideQtReportDialog] = set()
        self._operation_workers: set[CallWorker] = set()
        self._navigation_workers: dict[
            CallWorker,
            tuple[TideQtEditDialog, Any],
        ] = {}
        self._pending_navigation: dict[TideQtEditDialog, int] = {}
        self._operation_pool = QThreadPool(self)
        self._operation_pool.setMaxThreadCount(1)
        self._form_loading = False
        self._summary_loading = False
        self._report_temp_directory = (
            TemporaryDirectory(
                prefix="tide-qt-report-",
                ignore_cleanup_errors=True,
            )
            if report_output_directory is None
            else None
        )
        # One decision rather than the same condition asked twice: the second
        # branch only reaches `.name` because the first one made the directory.
        self.report_output_directory = (
            Path(self._report_temp_directory.name)
            if self._report_temp_directory is not None
            else Path(str(report_output_directory))
        )
        self.report_opener = report_opener
        self._notice: str | None = None
        self.setWindowTitle(f"{controller.model.name} — {controller.title}")
        self.resize(1100, 650)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        heading = QLabel(controller.title)
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        context = QLabel(f"{controller.context_text}  ·  {source_label}")
        context.setStyleSheet("color: palette(mid);")
        layout.addWidget(heading)
        layout.addWidget(context)

        query_controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("browse-search")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText(
            (
                f"Search {controller.search_label}…"
                if controller.search_label is not None
                else "Search is not configured"
            )
        )
        self.search.setEnabled(controller.search_field is not None)
        self.named_filter = QComboBox()
        self.named_filter.setObjectName("browse-filter")
        self.named_filter.addItem("All records", None)
        for named_filter in controller.named_filters.values():
            self.named_filter.addItem(named_filter.label, named_filter.name)
        self.named_filter.setEnabled(bool(controller.named_filters))
        self.clear_query = QPushButton("Clear")
        self.clear_query.setObjectName("clear-query")
        query_controls.addWidget(self.search, 2)
        query_controls.addWidget(self.named_filter, 1)
        query_controls.addWidget(self.clear_query)
        layout.addLayout(query_controls)

        self.table = QTableView()
        self.table_model = TideQtTableModel(controller, self)
        self.table.setModel(self.table_model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerItem
        )
        self.table.verticalHeader().setVisible(False)
        configure_interactive_header(self.table)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSectionsMovable(True)
        header.setSortIndicatorShown(False)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.status = QLabel()
        self.new = QPushButton("New")
        self.new.setObjectName("new-record")
        self.open = QPushButton("Open")
        self.open.setObjectName("open-record")
        self.summary_report = QPushButton(
            (
                controller.summary_report.title
                if controller.summary_report is not None
                else "Summary"
            )
        )
        self.summary_report.setObjectName("summary-report")
        self.summary_report.setToolTip(
            "Build the secured summary report and open its preview"
        )
        self.summary_report.setVisible(controller.summary_report_available)
        self.best_fit = QPushButton("Best Fit")
        self.best_fit.setObjectName("best-fit-columns")
        self.best_fit.setToolTip("Best fit all columns to their current contents")
        self.reset_layout = QPushButton("Reset Layout")
        self.reset_layout.setObjectName("reset-column-layout")
        self.reset_layout.setToolTip(
            "Restore metadata column order and default fitted widths"
        )
        self.refresh = QPushButton("Refresh")
        close = QPushButton("Close")
        actions.addWidget(self.status, 1)
        actions.addWidget(self.new)
        actions.addWidget(self.open)
        actions.addWidget(self.summary_report)
        actions.addWidget(self.best_fit)
        actions.addWidget(self.reset_layout)
        actions.addWidget(self.refresh)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.setCentralWidget(root)

        self.refresh.clicked.connect(self.table_model.reload)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._apply_query_controls)
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(250)
        self._layout_save_timer.timeout.connect(self._save_column_layout)
        self.search.textChanged.connect(self._queue_search)
        self.named_filter.currentIndexChanged.connect(self._apply_query_controls)
        self.clear_query.clicked.connect(self._clear_query)
        header.sectionClicked.connect(self._sort_by_section)
        header.sectionMoved.connect(self._queue_column_layout_save)
        header.sectionResized.connect(self._queue_column_layout_save)
        self.new.clicked.connect(self._open_new_form)
        self.open.clicked.connect(self._open_selected_form)
        self.summary_report.clicked.connect(self._open_summary_report)
        self.best_fit.clicked.connect(self._best_fit_all_columns)
        self.reset_layout.clicked.connect(self._reset_column_layout)
        self.table.selectionModel().selectionChanged.connect(
            self._update_detail_action
        )
        self.table.activated.connect(lambda index: self._open_row(index.row()))
        self.table_model.loadingChanged.connect(self._loading_changed)
        self.table_model.batchLoaded.connect(self._batch_loaded)
        self.table_model.loadFailed.connect(self._load_failed)
        self.table.verticalScrollBar().valueChanged.connect(
            self._prefetch_if_near_end
        )
        close.clicked.connect(self.closeRequested.emit)
        if not embedded:
            self.closeRequested.connect(self.close)
        self._column_widths_initialized = self._restore_column_layout()
        self._update_detail_action()
        self.table_model.reload()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._column_widths_initialized:
            self._initialize_column_widths()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._layout_save_timer.isActive():
            self._layout_save_timer.stop()
            self._save_column_layout()
        self._layout_settings.sync()
        super().closeEvent(event)

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        """Wait for browse, form, summary, export, and active save workers."""

        browse_done = self.table_model.wait_for_done(milliseconds)
        operations_done = self._operation_pool.waitForDone(milliseconds)
        edit_results = tuple(
            dialog.wait_for_done(milliseconds)
            for dialog in tuple(self._edit_dialogs)
        )
        report_results = tuple(
            dialog.wait_for_done(milliseconds)
            for dialog in tuple(self._report_dialogs)
        )
        return (
            browse_done
            and operations_done
            and all(edit_results)
            and all(report_results)
        )

    def _initialize_column_widths(self) -> None:
        """Fit once, then leave every section under direct user control."""

        if self.table_model.rowCount() == 0:
            return
        self._restoring_column_layout = True
        try:
            fit_interactive_columns(self.table, self.controller.columns)
        finally:
            self._restoring_column_layout = False
        self._column_widths_initialized = True

    def _restore_column_layout(self) -> bool:
        raw = self._layout_settings.value(self._column_layout_key)
        if not isinstance(raw, str) or len(raw) > 65_536:
            return False
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return False

        current_names = tuple(column.name for column in self.controller.columns)
        configured_order = payload.get("order")
        configured_widths = payload.get("widths")
        if not isinstance(configured_order, list) or not isinstance(
            configured_widths,
            dict,
        ):
            return False
        has_known_order = any(
            isinstance(name, str) and name in current_names
            for name in configured_order
        )
        known_order = known_column_order(configured_order, current_names)
        widths = known_column_widths(configured_widths, current_names)
        if not has_known_order and not widths:
            return False

        self._restoring_column_layout = True
        try:
            apply_column_order(
                self.table.horizontalHeader(),
                known_order,
                current_names,
            )
            minimum = self.table.horizontalHeader().minimumSectionSize()
            for logical_index, field_name in enumerate(current_names):
                width = widths.get(field_name)
                if width is not None:
                    self.table.setColumnWidth(
                        logical_index,
                        min(max(width, minimum), 2_000),
                    )
        finally:
            self._restoring_column_layout = False
        return True

    def _queue_column_layout_save(self, *_args: Any) -> None:
        if not self._restoring_column_layout:
            self._layout_save_timer.start()

    def _save_column_layout(self) -> None:
        if self._restoring_column_layout:
            return
        header = self.table.horizontalHeader()
        column_names = tuple(column.name for column in self.controller.columns)
        order = tuple(
            column_names[header.logicalIndex(visual_index)]
            for visual_index in range(header.count())
        )
        widths = {
            name: self.table.columnWidth(logical_index)
            for logical_index, name in enumerate(column_names)
        }
        self._layout_settings.setValue(
            self._column_layout_key,
            json.dumps(
                {
                    "version": 1,
                    "order": order,
                    "widths": widths,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        self._layout_settings.sync()

    def _best_fit_all_columns(self) -> None:
        self._layout_save_timer.stop()
        self._restoring_column_layout = True
        try:
            fit_content_columns(self.table)
        finally:
            self._restoring_column_layout = False
        self._column_widths_initialized = True
        self._save_column_layout()

    def _reset_column_layout(self) -> None:
        self._layout_save_timer.stop()
        self._restoring_column_layout = True
        try:
            column_names = tuple(
                column.name for column in self.controller.columns
            )
            apply_column_order(
                self.table.horizontalHeader(),
                column_names,
                column_names,
            )
            fit_interactive_columns(self.table, self.controller.columns)
            self._layout_settings.remove(self._column_layout_key)
            self._layout_settings.sync()
        finally:
            self._restoring_column_layout = False
        self._column_widths_initialized = True

    def _update_detail_action(self, *_args: Any) -> None:
        selected = self.table.currentIndex().isValid()
        busy = self._form_loading or self._summary_loading
        self.new.setEnabled(
            self.controller.create_available and not busy
        )
        self.open.setEnabled(
            self.controller.open_available
            and selected
            and not busy
        )
        self.summary_report.setEnabled(
            self.controller.summary_report_available and not busy
        )

    def _open_summary_report(self) -> None:
        if self._summary_loading or not self.controller.summary_report_available:
            return
        self._summary_loading = True
        self._notice = None
        self._update_detail_action()
        self._update_status()
        worker = CallWorker(self.controller.load_summary_report)
        self._operation_workers.add(worker)
        worker.signals.completed.connect(self._summary_report_ready)
        worker.signals.failed.connect(self._summary_report_failed)
        self._operation_pool.start(worker)

    @Slot(object, object)
    def _summary_report_ready(
        self,
        document: ReportDocument,
        worker: CallWorker,
    ) -> None:
        self._operation_workers.discard(worker)
        self._summary_loading = False
        self._notice = f"{document.title} ready"
        self._update_detail_action()
        self._update_status()
        dialog = TideQtReportDialog(
            document,
            self.report_output_directory,
            parent=self,
        )
        self._report_dialogs.add(dialog)
        dialog.finished.connect(
            lambda _result, current=dialog: self._report_dialogs.discard(current)
        )
        dialog.show()

    @Slot(object, object)
    def _summary_report_failed(
        self,
        error: Exception,
        worker: CallWorker,
    ) -> None:
        self._operation_workers.discard(worker)
        self._summary_loading = False
        self._update_detail_action()
        self._update_status()
        QMessageBox.critical(
            self,
            "TIDE Qt",
            f"Unable to build summary report: {error}",
        )

    def _open_new_form(self) -> None:
        if self.controller.create_available:
            self._start_form_load(self.controller.new_form)

    def _open_selected_form(self) -> None:
        index = self.table.currentIndex()
        if not index.isValid() or not self.controller.open_available:
            return
        try:
            identity = self.table_model.identity_at(index.row())
        except ValueError as error:
            QMessageBox.critical(self, "TIDE Qt", str(error))
            return
        self._start_form_load(lambda: self.controller.edit_form(identity))

    def _open_row(self, row_index: int) -> None:
        if not self.controller.open_available:
            return
        try:
            identity = self.table_model.identity_at(row_index)
        except ValueError as error:
            QMessageBox.critical(self, "TIDE Qt", str(error))
            return
        self._start_form_load(lambda: self.controller.edit_form(identity))

    def _start_form_load(self, operation: Callable[[], QtEditForm]) -> None:
        if self._form_loading:
            return
        self._form_loading = True
        self._notice = None
        self._update_detail_action()
        self._update_status()
        worker = CallWorker(operation)
        self._operation_workers.add(worker)
        worker.signals.completed.connect(self._form_ready)
        worker.signals.failed.connect(self._form_load_failed)
        self._operation_pool.start(worker)

    @Slot(object, object)
    def _form_ready(
        self,
        form: QtEditForm,
        worker: CallWorker,
    ) -> None:
        self._operation_workers.discard(worker)
        self._form_loading = False
        self._update_detail_action()
        self._update_status()
        self._show_edit_form(form)

    def _show_edit_form(
        self,
        form: QtEditForm,
        message: str | None = None,
    ) -> None:
        navigation_enabled = (
            form.operation == "update"
            and self.table_model.row_for_identity(form.identity) is not None
        )
        dialog = TideQtEditDialog(
            self.controller,
            form,
            parent=self,
            report_directory=self.report_output_directory,
            report_opener=self.report_opener,
            navigation_enabled=navigation_enabled,
        )
        self._edit_dialogs.add(dialog)
        self._refresh_dialog_navigation(dialog)
        if message:
            dialog.message.setText(message)
        dialog.recordSaved.connect(self._record_saved)
        dialog.recordActionCompleted.connect(self._record_action_completed)
        dialog.reopenRequested.connect(self._reopen_edit_form)
        dialog.navigationRequested.connect(
            lambda direction, current=dialog: self._navigate_dialog(
                current,
                direction,
            )
        )
        dialog.finished.connect(
            lambda _result, current=dialog: self._edit_dialog_closed(current)
        )
        dialog.show()

    def _edit_dialog_closed(self, dialog: TideQtEditDialog) -> None:
        self._edit_dialogs.discard(dialog)
        self._pending_navigation.pop(dialog, None)

    def _refresh_dialog_navigation(
        self,
        dialog: TideQtEditDialog,
    ) -> None:
        row = self.table_model.row_for_identity(dialog.form.identity)
        dialog.set_navigation_state(
            can_previous=row is not None and row > 0,
            can_next=(
                row is not None
                and (
                    row + 1 < self.table_model.rowCount()
                    or self.table_model.has_more
                )
            ),
        )

    def _refresh_open_dialog_navigation(self) -> None:
        for dialog in tuple(self._edit_dialogs):
            self._refresh_dialog_navigation(dialog)

    def _navigate_dialog(
        self,
        dialog: TideQtEditDialog,
        direction: int,
    ) -> None:
        if dialog not in self._edit_dialogs or direction not in {-1, 1}:
            return
        row = self.table_model.row_for_identity(dialog.form.identity)
        if row is None:
            self._refresh_dialog_navigation(dialog)
            dialog.message.setText(
                "This record is no longer part of the current list query."
            )
            return
        target_row = row + direction
        if 0 <= target_row < self.table_model.rowCount():
            self._start_navigation_load(
                dialog,
                self.table_model.identity_at(target_row),
            )
            return
        if direction > 0 and self.table_model.has_more:
            self._pending_navigation[dialog] = direction
            dialog.set_navigation_loading(True)
            if self.table_model.canFetchMore():
                self.table_model.fetchMore()
            return
        self._refresh_dialog_navigation(dialog)

    def _continue_pending_navigation(self) -> None:
        for dialog, direction in tuple(self._pending_navigation.items()):
            if dialog not in self._edit_dialogs:
                self._pending_navigation.pop(dialog, None)
                continue
            row = self.table_model.row_for_identity(dialog.form.identity)
            if row is None:
                self._pending_navigation.pop(dialog, None)
                dialog.set_navigation_loading(False)
                self._refresh_dialog_navigation(dialog)
                dialog.message.setText(
                    "This record is no longer part of the current list query."
                )
                continue
            target_row = row + direction
            if 0 <= target_row < self.table_model.rowCount():
                self._pending_navigation.pop(dialog, None)
                self._start_navigation_load(
                    dialog,
                    self.table_model.identity_at(target_row),
                )
                continue
            if direction > 0 and self.table_model.has_more:
                if self.table_model.canFetchMore():
                    self.table_model.fetchMore()
                continue
            self._pending_navigation.pop(dialog, None)
            dialog.set_navigation_loading(False)
            self._refresh_dialog_navigation(dialog)
            dialog.message.setText(
                "There is no next record in the current list."
            )

    def _start_navigation_load(
        self,
        dialog: TideQtEditDialog,
        identity: Any,
    ) -> None:
        dialog.set_navigation_loading(True)
        worker = CallWorker(lambda: self.controller.edit_form(identity))
        self._operation_workers.add(worker)
        self._navigation_workers[worker] = (dialog, identity)
        worker.signals.completed.connect(self._navigation_form_ready)
        worker.signals.failed.connect(self._navigation_form_failed)
        self._operation_pool.start(worker)

    @Slot(object, object)
    def _navigation_form_ready(
        self,
        form: QtEditForm,
        worker: CallWorker,
    ) -> None:
        source, target_identity = self._navigation_workers.pop(worker)
        self._operation_workers.discard(worker)
        if source not in self._edit_dialogs:
            return
        try:
            source.replace_form(form)
        except ValueError as error:
            source.set_navigation_loading(False)
            self._refresh_dialog_navigation(source)
            source.message.setText(
                f"Unable to display adjacent record: {error}"
            )
            return
        source.set_navigation_loading(False)
        target_row = self.table_model.row_for_identity(target_identity)
        if target_row is not None:
            self.table.selectRow(target_row)
        self._refresh_dialog_navigation(source)

    @Slot(object, object)
    def _navigation_form_failed(
        self,
        error: Exception,
        worker: CallWorker,
    ) -> None:
        source, _target_identity = self._navigation_workers.pop(worker)
        self._operation_workers.discard(worker)
        if source not in self._edit_dialogs:
            return
        source.set_navigation_loading(False)
        self._refresh_dialog_navigation(source)
        source.message.setText(f"Unable to open adjacent record: {error}")

    @Slot(object, str)
    def _reopen_edit_form(
        self,
        form: QtEditForm,
        message: str,
    ) -> None:
        QTimer.singleShot(
            0,
            lambda current=form, notice=message: self._show_edit_form(
                current,
                notice,
            ),
        )

    @Slot(object, object)
    def _form_load_failed(
        self,
        error: Exception,
        worker: CallWorker,
    ) -> None:
        self._operation_workers.discard(worker)
        self._form_loading = False
        self._update_detail_action()
        self._update_status()
        QMessageBox.critical(
            self,
            "TIDE Qt",
            f"Unable to open record form: {error}",
        )

    @Slot(object)
    def _record_saved(self, _stored: Mapping[str, Any]) -> None:
        self._notice = "Record saved"
        self.table_model.reload()

    @Slot(str, object)
    def _record_action_completed(
        self,
        label: str,
        _stored: Mapping[str, Any],
    ) -> None:
        self._notice = f"{label} completed"
        self.table_model.reload()

    def _open_selected_detail(self) -> None:
        index = self.table.currentIndex()
        if index.isValid():
            self._open_detail(index.row())

    def _open_detail(self, row_index: int) -> None:
        try:
            identity = self.table_model.identity_at(row_index)
            detail = self.controller.load_detail(identity)
        except (TideRuntimeError, ValueError) as error:
            QMessageBox.critical(
                self,
                "TIDE Qt",
                f"Unable to load record detail: {error}",
            )
            return
        dialog = TideQtDetailDialog(
            self.controller.model.name,
            detail,
            parent=self,
        )
        self._detail_dialogs.add(dialog)
        dialog.finished.connect(
            lambda _result, current=dialog: self._detail_dialogs.discard(current)
        )
        dialog.show()

    def _loading_changed(self, loading: bool) -> None:
        self.refresh.setEnabled(not loading)
        self._update_status()

    def _batch_loaded(self, _count: int, _has_more: bool) -> None:
        if not self._column_widths_initialized and self.isVisible():
            self._initialize_column_widths()
        self._update_status()
        self._refresh_open_dialog_navigation()
        self._continue_pending_navigation()
        self._prefetch_if_near_end()

    def _load_failed(self, message: str) -> None:
        self._update_status()
        if self._pending_navigation:
            pending = tuple(self._pending_navigation)
            self._pending_navigation.clear()
            for dialog in pending:
                if dialog not in self._edit_dialogs:
                    continue
                dialog.set_navigation_loading(False)
                self._refresh_dialog_navigation(dialog)
                dialog.message.setText(
                    f"Unable to load the next list records: {message}"
                )
            return
        QMessageBox.critical(self, "TIDE Qt", f"Unable to load records: {message}")

    def _queue_search(self, _text: str) -> None:
        self._search_timer.start()

    def _apply_query_controls(self, *_args: Any) -> None:
        self._search_timer.stop()
        current = self.table_model.query
        self.table_model.set_query(
            QtBrowseQuery(
                search_text=self.search.text(),
                filter_name=self.named_filter.currentData(),
                sort_field=current.sort_field,
                sort_descending=current.sort_descending,
            )
        )

    def _sort_by_section(self, section: int) -> None:
        if section < 0 or section >= len(self.controller.columns):
            return
        field_name = self.controller.columns[section].name
        if field_name not in self.controller.sortable_fields:
            return
        current = self.table_model.query
        descending = (
            not current.sort_descending
            if current.sort_field == field_name
            else False
        )
        self.table.horizontalHeader().setSortIndicator(
            section,
            (
                Qt.SortOrder.DescendingOrder
                if descending
                else Qt.SortOrder.AscendingOrder
            ),
        )
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table_model.set_query(
            QtBrowseQuery(
                search_text=current.search_text,
                filter_name=current.filter_name,
                sort_field=field_name,
                sort_descending=descending,
            )
        )

    def _clear_query(self) -> None:
        self._search_timer.stop()
        search_blocker = QSignalBlocker(self.search)
        filter_blocker = QSignalBlocker(self.named_filter)
        self.search.clear()
        self.named_filter.setCurrentIndex(0)
        del search_blocker, filter_blocker
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table_model.set_query(QtBrowseQuery())

    def _update_status(self) -> None:
        count = self.table_model.rowCount()
        noun = "record" if count == 1 else "records"
        if self.table_model.loading:
            state = "Loading more records…" if count else "Loading records…"
        elif self.table_model.load_error:
            state = "Loading failed · Refresh to retry"
        elif self.table_model.has_more:
            state = "Scroll for more"
        else:
            state = "All available records loaded"
        summary = self.controller.query_summary(self.table_model.query)
        query_text = f"  ·  {summary}" if summary else ""
        if self._form_loading:
            state = "Loading record form…"
        elif self._summary_loading:
            state = "Building summary report…"
        notice = f"{self._notice}  ·  " if self._notice else ""
        self.status.setText(
            f"{notice}{count} {noun} loaded  ·  {state}  ·  "
            f"{self.source_label}{query_text}"
        )

    def _prefetch_if_near_end(self, *_args: Any) -> None:
        scrollbar = self.table.verticalScrollBar()
        if (
            scrollbar.maximum() - scrollbar.value() <= 2
            and self.table_model.canFetchMore()
        ):
            self.table_model.fetchMore()


class TideQtWorkspaceWindow(QMainWindow):
    """Application shell driven by the renderer-neutral navigation contract."""

    def __init__(
        self,
        model: ApplicationModel,
        client: BrowseApiClient,
        session: TideSessionInfo,
        *,
        view_name: str | None = None,
        page_size: int | None = None,
        source_label: str = "remote API",
        layout_settings: QSettings | None = None,
        report_output_directory: str | Path | None = None,
        report_opener: Callable[[Path], bool] | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.client = client
        self.session = session
        self.page_size = page_size
        self.source_label = source_label
        self._layout_settings = (
            layout_settings
            if layout_settings is not None
            else QSettings("TIDE Framework", "TIDE Qt")
        )
        self.report_output_directory = report_output_directory
        self.report_opener = report_opener
        self._workspaces: dict[str, TideQtWindow] = {}
        self._view_items: dict[str, QTreeWidgetItem] = {}

        initial_controller = QtBrowseController(
            model,
            client,
            session,
            view_name=view_name,
            page_size=page_size,
        )
        accessible_views = tuple(
            view.name
            for view in model.views.values()
            if view.kind == "browse"
            and (
                capabilities := session.entities.get(view.entity)
            ) is not None
            and "list" in capabilities.operations
        )
        self.navigation_groups = application_navigation(
            model,
            accessible_views,
            include_views=(initial_controller.view.name,),
        )

        self.setWindowTitle(model.name)
        self.resize(1280, 720)
        root = QWidget(self)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.navigation = QTreeWidget(root)
        self.navigation.setObjectName("application-navigation")
        self.navigation.setHeaderHidden(True)
        self.navigation.setMinimumWidth(190)
        self.navigation.setMaximumWidth(280)
        for group in self.navigation_groups:
            group_item = QTreeWidgetItem(self.navigation, [group.label])
            group_item.setFlags(
                group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
            )
            group_font = group_item.font(0)
            group_font.setBold(True)
            group_item.setFont(0, group_font)
            group_item.setExpanded(True)
            for item in group.items:
                view_item = QTreeWidgetItem(group_item, [item.label])
                view_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    item.view,
                )
                self._view_items[item.view] = view_item

        self.stack = QStackedWidget(root)
        self.stack.setObjectName("application-workspaces")
        layout.addWidget(self.navigation)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.navigation.currentItemChanged.connect(
            self._navigation_item_changed
        )
        self.activate_view(
            initial_controller.view.name,
            controller=initial_controller,
        )

    @property
    def current_workspace(self) -> TideQtWindow | None:
        current = self.stack.currentWidget()
        return current if isinstance(current, TideQtWindow) else None

    def activate_view(
        self,
        view_name: str,
        *,
        controller: QtBrowseController | None = None,
    ) -> TideQtWindow:
        """Activate a lazily-created workspace without discarding its UI state."""

        item = self._view_items.get(view_name)
        if item is None:
            raise ValueError(f"Qt browse view {view_name!r} is not in navigation")
        workspace = self._workspaces.get(view_name)
        if workspace is None:
            selected_controller = controller or QtBrowseController(
                self.model,
                self.client,
                self.session,
                view_name=view_name,
                page_size=self.page_size,
            )
            workspace = TideQtWindow(
                selected_controller,
                source_label=self.source_label,
                layout_settings=self._layout_settings,
                report_output_directory=self.report_output_directory,
                report_opener=self.report_opener,
                embedded=True,
            )
            workspace.closeRequested.connect(self.close)
            self._workspaces[view_name] = workspace
            self.stack.addWidget(workspace)
        self.stack.setCurrentWidget(workspace)
        selection_blocker = QSignalBlocker(self.navigation)
        self.navigation.setCurrentItem(item)
        del selection_blocker
        self.setWindowTitle(
            f"{self.model.name} — {workspace.controller.title}"
        )
        return workspace

    @Slot(object, object)
    def _navigation_item_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            return
        view_name = current.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(view_name, str):
            self.activate_view(view_name)

    def closeEvent(self, event: QCloseEvent) -> None:
        for workspace in tuple(self._workspaces.values()):
            workspace.close()
        self._layout_settings.sync()
        super().closeEvent(event)

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        results = tuple(
            workspace.wait_for_done(milliseconds)
            for workspace in tuple(self._workspaces.values())
        )
        return all(results)


def run_qt_application(
    model: ApplicationModel,
    client: BrowseApiClient,
    session: TideSessionInfo,
    *,
    view_name: str | None = None,
    page_size: int | None = None,
    source_label: str = "remote API",
    report_output_directory: str | Path | None = None,
) -> int:
    """Run the remote Qt application shell and return Qt's process result."""

    application = QApplication.instance() or QApplication([model.name])
    application.setApplicationName(model.name)
    window = TideQtWorkspaceWindow(
        model,
        client,
        session,
        view_name=view_name,
        page_size=page_size,
        source_label=source_label,
        report_output_directory=report_output_directory,
    )
    window.show()
    result = int(application.exec())
    window.wait_for_done()
    return result
