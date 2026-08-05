"""The record editor: save, domain actions, lookups, conflict review."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from functools import partial
from typing import Any, Callable, Mapping
from uuid import uuid4

from PySide6.QtCore import (
    QEvent,
    QObject,
    QSignalBlocker,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QCloseEvent,
    QKeyEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tide.presentation import action_label, field_label
from tide.reporting import (
    write_pdf,
)

from .collection import TideQtCollectionEditor
from .conflict import TideQtConflictDialog
from .editors import (
    TideQtReferenceEditor,
    build_field_editor,
    configure_field_editor,
    configure_field_label,
    editor_value,
    form_structure,
    set_editor_value,
)
from .lookup import TideQtLookupDialog
from .presenter import (
    QtBrowseController,
    QtEditActionError,
    QtEditConflict,
    QtEditField,
    QtEditForm,
    QtLookupSelection,
)
from .report import open_local_report
from .workers import CallWorker


class TideQtEditDialog(QDialog):
    """Metadata-driven create/update dialog for supported form fields."""

    recordSaved = Signal(object)
    recordActionCompleted = Signal(str, object)
    reopenRequested = Signal(object, str)
    navigationRequested = Signal(int)

    def __init__(
        self,
        controller: QtBrowseController,
        form: QtEditForm,
        parent: QWidget | None = None,
        *,
        save_label: str = "Save",
        report_directory: Path | None = None,
        report_opener: Callable[[Path], bool] | None = None,
        navigation_enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.form = form
        self.editors: dict[str, QWidget] = {}
        self.collection_editors: dict[str, TideQtCollectionEditor] = {}
        self._field_labels: dict[str, QLabel] = {}
        self._fields = {field.name: field for field in form.fields}
        self._workers: set[CallWorker] = set()
        self._lookup_targets: dict[
            CallWorker,
            TideQtCollectionEditor | None,
        ] = {}
        self._lookup_dialogs: set[TideQtLookupDialog] = set()
        self._conflict_dialogs: set[TideQtConflictDialog] = set()
        self._save_attempts: dict[
            CallWorker,
            tuple[QtEditForm, dict[str, Any]],
        ] = {}
        self._action_attempts: dict[
            CallWorker,
            tuple[QtEditForm, dict[str, Any], str],
        ] = {}
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._saving = False
        self._save_label = save_label
        self._navigation_enabled = navigation_enabled
        self._can_previous = False
        self._can_next = False
        self.report_directory = report_directory
        self.report_opener = report_opener or open_local_report
        self.setWindowTitle(f"{controller.model.name} — {form.title}")
        self.resize(1050 if form.collections else 760, 720 if form.collections else 360)

        layout = QVBoxLayout(self)
        self.heading = QLabel(form.title)
        self.heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(self.heading)
        if form.omitted_collections:
            collection_labels = ", ".join(
                field_label(controller.entity.field(name))
                for name in form.omitted_collections
                if name in controller.entity.fields
            )
            note = QLabel(
                (
                    f"{collection_labels} are not editable in this Qt header "
                    "form and will start empty."
                    if form.operation == "create"
                    else (
                        f"{collection_labels} remain unchanged in this Qt "
                        "header form; use View to inspect them."
                    )
                )
            )
            note.setWordWrap(True)
            note.setStyleSheet(
                "background: palette(alternate-base); padding: 6px;"
            )
            layout.addWidget(note)
        focus_order: list[QWidget] = []
        for group in form.groups:
            container = QWidget()
            section_layout = QVBoxLayout(container)
            section_layout.setContentsMargins(0, 4, 0, 4)
            section_title = QLabel(group.label)
            section_title.setStyleSheet("font-weight: 600;")
            section_layout.addWidget(section_title)
            fields = QWidget()
            grid = QGridLayout(fields)
            grid.setContentsMargins(8, 0, 8, 0)
            positioned: dict[tuple[int, int], QWidget] = {}
            for row_index, row in enumerate(group.rows):
                for column_index, field in enumerate(row):
                    offset = column_index * 2
                    label = QLabel(
                        f"{field.label} *" if field.required else field.label
                    )
                    editor = self._field_editor(field)
                    self.editors[field.name] = editor
                    self._field_labels[field.name] = label
                    positioned[row_index, column_index] = editor
                    if not field.editable:
                        label.setStyleSheet("color: palette(mid); font-style: italic;")
                    grid.addWidget(label, row_index, offset)
                    grid.addWidget(editor, row_index, offset + 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 1)
            section_layout.addWidget(fields)
            layout.addWidget(container)
            column_count = max((len(row) for row in group.rows), default=0)
            for column_index in range(column_count):
                for row_index in range(len(group.rows)):
                    placed = positioned.get((row_index, column_index))
                    if placed is not None:
                        focus_order.append(placed)

        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

        for collection in form.collections:
            editor = TideQtCollectionEditor(self, collection)
            self.collection_editors[collection.name] = editor
            layout.addWidget(editor, 1)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: palette(link-visited);")
        layout.addWidget(self.message)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button.clicked.connect(self._save)
        self.save_button.setText(save_label)
        self._save_available = any(field.editable for field in form.fields) or any(
            collection.editable for collection in form.collections
        )
        self.save_button.setVisible(self._save_available)
        self.cancel_button.clicked.connect(self.reject)
        self.preview_button: QPushButton | None = None
        if (
            form.operation == "update"
            and controller.record_report_available
            and report_directory is not None
        ):
            self.preview_button = QPushButton("Preview PDF")
            self.preview_button.setObjectName("preview-report")
            self.buttons.addButton(
                self.preview_button,
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            self.preview_button.clicked.connect(self._preview_report)
        self.action_buttons: dict[str, QPushButton] = {}
        for action in form.actions:
            button = QPushButton(action.label)
            button.setObjectName(f"record-action-{action.name}")
            if action.name == "post":
                button.setStyleSheet(
                    "font-weight: 600; color: palette(highlighted-text); "
                    "background: palette(highlight); padding: 5px 12px;"
                )
            self.buttons.addButton(
                button,
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            button.clicked.connect(
                lambda _checked=False, name=action.name: self._execute_action(
                    name
                )
            )
            self.action_buttons[action.name] = button
        footer = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.previous_button.setObjectName("previous-record")
        self.previous_button.setToolTip("Previous record (Page Up)")
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("next-record")
        self.next_button.setToolTip("Next record (Page Down)")
        self.previous_button.setVisible(navigation_enabled)
        self.next_button.setVisible(navigation_enabled)
        self.previous_button.clicked.connect(
            lambda: self._request_navigation(-1)
        )
        self.next_button.clicked.connect(lambda: self._request_navigation(1))
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        footer.addStretch(1)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        self.previous_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_PageUp),
            self,
        )
        self.previous_shortcut.setAutoRepeat(False)
        self.previous_shortcut.setEnabled(False)
        self.previous_shortcut.activated.connect(
            lambda: self._request_navigation(-1)
        )
        self.next_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_PageDown),
            self,
        )
        self.next_shortcut.setAutoRepeat(False)
        self.next_shortcut.setEnabled(False)
        self.next_shortcut.activated.connect(
            lambda: self._request_navigation(1)
        )
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self._save)

        self._connect_action_state_editors()
        self.refresh_action_state()
        first_editable = next(
            (
                editor
                for editor in focus_order
                if editor.isEnabled()
                and editor.focusPolicy() != Qt.FocusPolicy.NoFocus
            ),
            None,
        )
        if first_editable is not None:
            first_editable.setFocus()

    def replace_form(self, form: QtEditForm) -> None:
        """Replace an adjacent record without closing or moving this dialog."""

        if form_structure(form) != form_structure(self.form):
            raise ValueError("the adjacent record form layout is incompatible")
        self.form = form
        self._fields = {field.name: field for field in form.fields}
        self.setWindowTitle(f"{self.controller.model.name} — {form.title}")
        self.heading.setText(form.title)
        for name, editor in self.editors.items():
            field = self._fields[name]
            configure_field_editor(field, editor, saving=self._saving)
            configure_field_label(self._field_labels[name], field)
            blocker = QSignalBlocker(editor)
            set_editor_value(
                field,
                editor,
                field.value,
                reference_display=field.reference_display,
            )
            del blocker
        for collection in form.collections:
            self.collection_editors[collection.name].replace_collection(
                collection
            )
        self._save_available = any(
            field.editable for field in form.fields
        ) or any(collection.editable for collection in form.collections)
        self.save_button.setVisible(self._save_available)
        for action in form.actions:
            button = self.action_buttons[action.name]
            button.setText(action.label)
        self.message.clear()
        self.refresh_computed_preview()
        self.refresh_action_state()

    def set_navigation_state(
        self,
        *,
        can_previous: bool,
        can_next: bool,
    ) -> None:
        """Update adjacent-record availability without changing form data."""

        self._can_previous = can_previous
        self._can_next = can_next
        self._apply_navigation_state()

    def set_navigation_loading(self, loading: bool) -> None:
        """Temporarily lock the form while an adjacent record is loaded."""

        self._set_saving(
            loading,
            label=self._save_label,
            message="Loading the adjacent record through the TIDE API…",
        )

    def _apply_navigation_state(self) -> None:
        available = self._navigation_enabled and not self._saving
        previous_available = available and self._can_previous
        next_available = available and self._can_next
        self.previous_button.setEnabled(previous_available)
        self.previous_shortcut.setEnabled(previous_available)
        self.next_button.setEnabled(next_available)
        self.next_shortcut.setEnabled(next_available)

    def _request_navigation(self, direction: int) -> None:
        if self._saving or direction not in {-1, 1}:
            return
        if (
            (direction < 0 and not self._can_previous)
            or (direction > 0 and not self._can_next)
        ):
            return
        try:
            values = self._collect_values(enforce_required=True)
        except ValueError as error:
            self.message.setText(str(error))
            return
        if self.controller.form_has_changes(self.form, values):
            self.message.setText(
                "Save or cancel your changes before navigating to another "
                "record."
            )
            return
        self.navigationRequested.emit(direction)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and watched in self.editors.values()
            and not isinstance(watched, QComboBox)
        ):
            self.focusNextChild()
            return True
        return super().eventFilter(watched, event)

    def reject(self) -> None:
        if not self._saving:
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._saving:
            event.ignore()
            return
        super().closeEvent(event)

    def wait_for_done(self, milliseconds: int = -1) -> bool:
        work_done = self._thread_pool.waitForDone(milliseconds)
        lookups_done = all(
            dialog.wait_for_done(milliseconds)
            for dialog in tuple(self._lookup_dialogs)
        )
        return work_done and lookups_done

    def _field_editor(
        self,
        field: QtEditField,
        *,
        lookup_handler: Callable[[], None] | None = None,
    ) -> QWidget:
        """Build one field's editor, bound to this dialog."""

        return build_field_editor(
            field,
            event_filter=self,
            lookup_handler=(
                lookup_handler
                if lookup_handler is not None
                else partial(self._open_lookup, field)
            ),
            saving=self._saving,
        )

    def _save(self) -> None:
        if self._saving or not self._save_available:
            return
        try:
            values = self._collect_values(enforce_required=True)
        except ValueError as error:
            self.message.setText(str(error))
            return
        self._start_save(self.form, values)

    def _execute_action(self, action_name: str) -> None:
        if self._saving:
            return
        try:
            values = self._collect_values(enforce_required=True)
            actions = {
                action.name: action
                for action in self.controller.form_actions(self.form, values)
            }
            action = actions.get(action_name)
            if action is None or not action.visible or not action.enabled:
                raise ValueError(
                    f"{action_name.replace('_', ' ').title()} is unavailable"
                )
        except ValueError as error:
            self.message.setText(str(error))
            self.refresh_action_state()
            return
        self._set_saving(
            True,
            label=self._save_label,
            message=f"Saving the draft and running {action.label} through TIDE…",
        )
        draft = deepcopy(values)
        worker = self._start(
            lambda: self.controller.execute_form_action(
                self.form,
                action_name,
                draft,
                idempotency_key=f"qt:{uuid4()}",
            ),
            completed=self._action_completed,
            failed=self._action_failed,
        )
        self._action_attempts[worker] = (self.form, draft, action_name)

    def _preview_report(self) -> None:
        if (
            self._saving
            or self.preview_button is None
            or self.report_directory is None
        ):
            return
        try:
            values = self._collect_values(enforce_required=False)
            if self.controller.form_has_changes(self.form, values):
                raise ValueError("Save your changes before previewing the PDF.")
        except ValueError as error:
            self.message.setText(str(error))
            return
        self._set_saving(
            True,
            label=self._save_label,
            message="Building the secured PDF preview…",
        )
        self._start(
            self._build_report_pdf,
            completed=self._report_pdf_ready,
            failed=self._report_pdf_failed,
        )

    def _build_report_pdf(self) -> Path:
        assert self.report_directory is not None
        document = self.controller.load_record_report(self.form.identity)
        destination = self.report_directory / (
            f"{document.suggested_filename}-{uuid4().hex}.pdf"
        )
        return write_pdf(document, destination)

    @Slot(object, object)
    def _report_pdf_ready(
        self,
        path: Path,
        worker: CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        if self.report_opener(path):
            self.message.setText(f"Opened temporary PDF preview: {path.name}")
        else:
            self.message.setText(
                f"PDF created at {path}, but no system viewer accepted it."
            )

    @Slot(object, object)
    def _report_pdf_failed(
        self,
        error: Exception,
        worker: CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        self.message.setText(f"PDF preview failed: {error}")

    def _collect_values(
        self,
        *,
        enforce_required: bool,
    ) -> dict[str, Any]:
        if enforce_required:
            for collection in self.collection_editors.values():
                collection.prepare_save()
        values = self._editor_values(enforce_required=enforce_required)
        values.update(
            {
                name: collection.values()
                for name, collection in self.collection_editors.items()
                if collection.collection.editable
            }
        )
        return values

    def _start_save(
        self,
        form: QtEditForm,
        values: Mapping[str, Any],
    ) -> None:
        draft = deepcopy(dict(values))
        self._set_saving(True)
        worker = self._start(
            lambda: self.controller.save_form(form, draft),
            completed=self._save_completed,
            failed=self._save_failed,
        )
        self._save_attempts[worker] = (form, draft)

    def _editor_values(
        self,
        *,
        enforce_required: bool = True,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name, editor in self.editors.items():
            field = self._fields[field_name]
            if not field.editable:
                continue
            values[field_name] = editor_value(
                field,
                editor,
                enforce_required=enforce_required,
            )
        return values

    def refresh_computed_preview(self) -> None:
        try:
            values = self._editor_values(enforce_required=False)
        except ValueError:
            return
        values.update(
            {
                name: collection.values()
                for name, collection in self.collection_editors.items()
            }
        )
        preview = self.controller.preview_form(self.form, values)
        for name, editor in self.editors.items():
            if (
                name not in self.controller.entity.fields
                or not self.controller.entity.field(name).metadata.get("computed")
                or not isinstance(editor, QLineEdit)
            ):
                continue
            editor.setText(
                self.controller.format_form_value(name, preview.get(name))
            )

    def refresh_action_state(self) -> None:
        if not self.action_buttons:
            return
        try:
            values = self._collect_values(enforce_required=False)
            states = {
                action.name: action
                for action in self.controller.form_actions(self.form, values)
            }
        except ValueError:
            states = {}
        for name, button in self.action_buttons.items():
            state = states.get(name)
            button.setVisible(bool(state and state.visible))
            button.setEnabled(
                bool(state and state.visible and state.enabled and not self._saving)
            )

    def _connect_action_state_editors(self) -> None:
        for editor in self.editors.values():
            if isinstance(editor, QLineEdit):
                editor.textChanged.connect(self.refresh_action_state)
            elif isinstance(editor, QCheckBox):
                editor.toggled.connect(self.refresh_action_state)
            elif isinstance(editor, QComboBox):
                editor.currentIndexChanged.connect(self.refresh_action_state)
            elif isinstance(editor, TideQtReferenceEditor):
                editor.selectionChanged.connect(self.refresh_action_state)

    def _open_lookup(
        self,
        field: QtEditField,
        *,
        collection_editor: TideQtCollectionEditor | None = None,
    ) -> None:
        if self._saving:
            return
        try:
            spec = self.controller.lookup_spec(
                field.name,
                collection_name=(
                    collection_editor.collection.name
                    if collection_editor is not None
                    else None
                ),
            )
        except ValueError as error:
            self.message.setText(f"Lookup unavailable: {error}")
            return
        dialog = TideQtLookupDialog(self.controller, spec, parent=self)
        self._lookup_dialogs.add(dialog)
        dialog.finished.connect(
            lambda result, current=dialog, item=field: self._lookup_finished(
                current,
                item,
                result,
                collection_editor,
            )
        )
        dialog.show()

    def _lookup_finished(
        self,
        dialog: TideQtLookupDialog,
        field: QtEditField,
        result: int,
        collection_editor: TideQtCollectionEditor | None,
    ) -> None:
        selected = dialog.selected_record
        if result != QDialog.DialogCode.Accepted or selected is None:
            return
        try:
            values = (
                collection_editor.current_draft(enforce_required=False)
                if collection_editor is not None
                else self._editor_values(enforce_required=False)
            )
        except ValueError as error:
            self.message.setText(str(error))
            return
        self._set_saving(
            True,
            label="Applying…",
            message="Applying the secured lookup selection…",
        )
        # `selected` rather than `dialog.selected_record`: this runs on a
        # worker thread, so re-reading the dialog's attribute there would read
        # it again long after the check above, and could read a different one.
        worker = self._start(
            lambda: self.controller.apply_lookup_selection(
                self.form,
                field.name,
                values,
                selected,
                collection_name=(
                    collection_editor.collection.name
                    if collection_editor is not None
                    else None
                ),
            ),
            completed=self._lookup_applied,
            failed=self._lookup_failed,
        )
        self._lookup_targets[worker] = collection_editor

    @Slot(object, object)
    def _lookup_applied(
        self,
        selection: QtLookupSelection,
        worker: CallWorker,
    ) -> None:
        self._workers.discard(worker)
        collection_editor = self._lookup_targets.pop(worker, None)
        if collection_editor is not None:
            collection_editor.apply_lookup_selection(selection)
        else:
            for name, value in selection.values.items():
                editor = self.editors.get(name)
                field = self._fields.get(name)
                if editor is None or field is None or not field.editable:
                    continue
                set_editor_value(
                    field,
                    editor,
                    value,
                    reference_display=(
                        selection.display
                        if name == selection.field_name
                        else None
                    ),
                )
        self._set_saving(False)
        self.refresh_action_state()
        self.message.setText(
            f"{selection.display} selected; initial values applied."
        )

    @Slot(object, object)
    def _lookup_failed(self, error: Exception, worker: CallWorker) -> None:
        self._workers.discard(worker)
        self._lookup_targets.pop(worker, None)
        self._set_saving(False)
        self.message.setText(f"Lookup selection failed: {error}")

    @Slot(object, object)
    def _action_completed(
        self,
        stored: Mapping[str, Any],
        worker: CallWorker,
    ) -> None:
        attempt = self._action_attempts.pop(worker, None)
        self._workers.discard(worker)
        self._set_saving(False)
        action_name = attempt[2] if attempt is not None else "action"
        action = self.controller.entity.actions.get(action_name, {})
        label = action_label(action_name, action)
        self.recordActionCompleted.emit(label, dict(stored))
        super().accept()

    @Slot(object, object)
    def _action_failed(
        self,
        error: Exception,
        worker: CallWorker,
    ) -> None:
        attempt = self._action_attempts.pop(worker, None)
        self._workers.discard(worker)
        if isinstance(error, QtEditActionError):
            form = error.form
            draft = dict(error.draft)
            action_name = error.action.name
        elif attempt is not None:
            form, draft, action_name = attempt
        else:
            self._set_saving(False)
            self.message.setText(f"Action failed: {error}")
            return
        if getattr(error, "code", None) == "stale_version":
            self._start_conflict_review(form, draft)
            return
        self._set_saving(False)
        label = action_label(
            action_name,
            self.controller.entity.actions.get(action_name, {}),
        )
        if isinstance(error, QtEditActionError) and error.saved_before_action:
            self.reopenRequested.emit(
                error.form,
                f"Draft saved, but {label} failed: {error}. "
                "Correct the record and run the action again.",
            )
            super().reject()
            return
        self.message.setText(f"{label} failed: {error}")

    @Slot(object, object)
    def _save_completed(
        self,
        stored: Mapping[str, Any],
        worker: CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._save_attempts.pop(worker, None)
        self._set_saving(False)
        self.recordSaved.emit(dict(stored))
        super().accept()

    @Slot(object, object)
    def _save_failed(self, error: Exception, worker: CallWorker) -> None:
        self._workers.discard(worker)
        attempt = self._save_attempts.pop(worker, None)
        if (
            getattr(error, "code", None) == "stale_version"
            and attempt is not None
            and attempt[0].operation == "update"
        ):
            form, draft = attempt
            self._start_conflict_review(form, draft)
            return
        self._set_saving(False)
        self.message.setText(f"Save failed: {error}")

    def _start_conflict_review(
        self,
        form: QtEditForm,
        draft: Mapping[str, Any],
    ) -> None:
        self._set_saving(
            True,
            label="Reviewing…",
            message=(
                "The record changed elsewhere. Loading the current "
                "version for a three-way review…"
            ),
        )
        self._start(
            lambda: self.controller.review_edit_conflict(form, draft),
            completed=self._conflict_ready,
            failed=self._conflict_failed,
        )

    @Slot(object, object)
    def _conflict_ready(
        self,
        conflict: QtEditConflict,
        worker: CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        dialog = TideQtConflictDialog(self.controller, conflict, parent=self)
        self._conflict_dialogs.add(dialog)
        dialog.finished.connect(
            lambda result, current=dialog: self._conflict_review_closed(
                current,
                result,
            )
        )
        dialog.show()

    @Slot(object, object)
    def _conflict_failed(
        self,
        error: Exception,
        worker: CallWorker,
    ) -> None:
        self._workers.discard(worker)
        self._set_saving(False)
        self.message.setText(
            f"Unable to inspect the current record: {error}"
        )

    def _conflict_review_closed(
        self,
        dialog: TideQtConflictDialog,
        result: int,
    ) -> None:
        self._conflict_dialogs.discard(dialog)
        if result != QDialog.DialogCode.Accepted or dialog.action is None:
            self.message.setText(
                "Your stale draft remains open and unsaved. "
                "Reload or rebase before saving."
            )
            return
        if dialog.action == "reload":
            form = dialog.conflict.current_form
            message = "Current data reloaded; the stale draft was discarded."
        else:
            try:
                rebase = self.controller.rebase_edit_conflict(
                    dialog.conflict,
                    dialog.choices,
                )
            except ValueError as error:
                self.message.setText(str(error))
                return
            form = rebase.form
            if rebase.retained_fields:
                message = (
                    "Current data reloaded; resolved draft fields were "
                    "retained. Review, then save or run the action again."
                )
            else:
                message = "Current data reloaded; no draft fields were retained."
            if rebase.dropped_fields:
                message += (
                    " Workflow rules now lock: "
                    + ", ".join(
                        self.controller.conflict_field_label(name)
                        for name in rebase.dropped_fields
                    )
                    + "."
                )
        self.reopenRequested.emit(form, message)
        super().reject()

    def _start(
        self,
        call: Callable[[], Any],
        *,
        completed: Callable[[Any, CallWorker], None],
        failed: Callable[[Exception, CallWorker], None],
    ) -> CallWorker:
        """Run one secured call on this dialog's single worker thread.

        Save, domain actions, report preview, lookup selection and conflict
        review all reach the server the same way, and each of these lines
        matters: a worker nobody holds is collected mid-flight, and one that
        is never started leaves the dialog saving for good. Callers that have
        to remember what they sent key it on the returned worker.
        """

        worker = CallWorker(call)
        self._workers.add(worker)
        worker.signals.completed.connect(completed)
        worker.signals.failed.connect(failed)
        self._thread_pool.start(worker)
        return worker

    def _set_saving(
        self,
        saving: bool,
        *,
        label: str = "Saving…",
        message: str = "Saving through the TIDE API…",
    ) -> None:
        self._saving = saving
        for field_name, editor in self.editors.items():
            if self._fields[field_name].editable:
                editor.setEnabled(not saving)
        for collection in self.collection_editors.values():
            collection.set_working(saving)
        self.save_button.setEnabled(not saving and self._save_available)
        self.cancel_button.setEnabled(not saving)
        for button in self.action_buttons.values():
            button.setEnabled(not saving)
        if self.preview_button is not None:
            self.preview_button.setEnabled(not saving)
        self._apply_navigation_state()
        self.save_button.setText(label if saving else self._save_label)
        if saving:
            self.message.setText(message)
        else:
            self.refresh_action_state()
