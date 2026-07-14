"""Stable, source-located compiler diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    file: Path
    line: int = 1
    column: int = 1


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    location: SourceLocation
    severity: Severity = Severity.ERROR
    path: tuple[str | int, ...] = ()
    hint: str | None = None

    def format(self, *, root: Path | None = None) -> str:
        file = self.location.file
        if root is not None:
            try:
                file = file.relative_to(root)
            except ValueError:
                pass
        path = ""
        if self.path:
            path = " (" + ".".join(str(part) for part in self.path) + ")"
        result = (
            f"{file}:{self.location.line}:{self.location.column}: "
            f"{self.severity.value} [{self.code}] {self.message}{path}"
        )
        if self.hint:
            result += f"\n  hint: {self.hint}"
        return result

    def as_dict(self, *, root: Path | None = None) -> dict[str, object]:
        file = self.location.file
        if root is not None:
            try:
                file = file.relative_to(root)
            except ValueError:
                pass
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "file": file.as_posix(),
            "line": self.location.line,
            "column": self.location.column,
            "path": list(self.path),
            "hint": self.hint,
        }


class CompilationFailed(Exception):
    def __init__(self, diagnostics: Iterable[Diagnostic]):
        self.diagnostics = tuple(diagnostics)
        super().__init__(f"model compilation failed with {len(self.diagnostics)} diagnostic(s)")

