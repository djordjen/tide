"""Background calls, so a secured request never blocks the event loop."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import (
    QObject,
    QRunnable,
    Signal,
    Slot,
)

from .contracts import (
    QtBrowseQuery,
)
from .presenter import (
    QtBrowseController,
)


class _CallSignals(QObject):
    completed = Signal(object, object)
    failed = Signal(object, object)


class CallWorker(QRunnable):
    """Run one arbitrary blocking controller call outside Qt's GUI thread."""

    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = _CallSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as error:  # Qt worker boundary reports failures to the GUI.
            self.signals.failed.emit(error, self)
            return
        self.signals.completed.emit(result, self)


class _BatchSignals(QObject):
    completed = Signal(int, object, object, object)
    failed = Signal(int, object, object, object)


class BatchWorker(QRunnable):
    """Run one blocking HTTP batch outside Qt's GUI thread."""

    def __init__(
        self,
        controller: QtBrowseController,
        generation: int,
        cursor: str | None,
        query: QtBrowseQuery,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.generation = generation
        self.cursor = cursor
        self.query = query
        self.signals = _BatchSignals()

    @Slot()
    def run(self) -> None:
        try:
            batch = self.controller.fetch_batch(
                self.cursor,
                query=self.query,
            )
        except Exception as error:  # Qt worker boundary reports failures to the GUI.
            self.signals.failed.emit(
                self.generation,
                self.cursor,
                error,
                self,
            )
            return
        self.signals.completed.emit(
            self.generation,
            self.cursor,
            batch,
            self,
        )
