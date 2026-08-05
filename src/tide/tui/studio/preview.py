"""Showing what a view would look like to a chosen role."""

from __future__ import annotations


from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Select,
    Static,
    TextArea,
)

from tide.labels import value_label
from tide.development.designer import DesignerDocumentReference
from tide.development.studio import (
    StudioError,
    StudioService,
    StudioViewPreview,
)


class StudioPreviewScreen(ModalScreen[None]):
    """Inspect one compiled view as a selected role and terminal size."""

    ENABLE_COMMAND_PALETTE = False
    _SIZES: tuple[tuple[str, str, int, int], ...] = (
        ("Compact · 80 × 24", "80x24", 80, 24),
        ("Standard · 100 × 30", "100x30", 100, 30),
        ("Wide · 140 × 40", "140x40", 140, 40),
    )

    CSS = """
    StudioPreviewScreen {
        align: center middle;
        background: $background 70%;
    }

    #studio-preview-dialog {
        width: 96%;
        height: 94%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #studio-preview-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    #studio-preview-controls {
        height: 3;
    }

    #studio-preview-role, #studio-preview-size {
        width: 1fr;
        margin-right: 1;
    }

    #studio-preview-summary {
        height: 3;
        color: $text-muted;
    }

    #studio-preview-canvas {
        height: 1fr;
        border: round $primary;
    }

    #studio-preview-actions {
        height: 3;
        align-horizontal: right;
    }

    #studio-preview-actions Button {
        min-width: 14;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(
        self,
        service: StudioService,
        target: DesignerDocumentReference,
    ) -> None:
        super().__init__()
        self.service = service
        self.target = target
        probe = service.preview_view(target, role=None, width=100, height=30)
        self.role = probe.available_roles[0] if probe.available_roles else None
        self.preview = service.preview_view(
            target,
            role=self.role,
            width=100,
            height=30,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="studio-preview-dialog"):
            yield Static(
                f"Role & terminal preview — {self.preview.view}",
                id="studio-preview-title",
            )
            with Horizontal(id="studio-preview-controls"):
                yield Select[str](
                    (("(No role)", "__none__"),)
                    + tuple((value_label(role), role) for role in self.preview.available_roles),
                    value=self.role or "__none__",
                    allow_blank=False,
                    id="studio-preview-role",
                )
                yield Select[str](
                    tuple((label, value) for label, value, _width, _height in self._SIZES),
                    value="100x30",
                    allow_blank=False,
                    id="studio-preview-size",
                )
            yield Static(
                _studio_preview_summary(self.preview),
                id="studio-preview-summary",
                markup=False,
            )
            yield TextArea(
                _studio_view_preview_text(self.preview),
                read_only=True,
                show_line_numbers=False,
                soft_wrap=False,
                id="studio-preview-canvas",
            )
            with Horizontal(id="studio-preview-actions"):
                yield Button("Close", id="studio-preview-close")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id not in {"studio-preview-role", "studio-preview-size"}:
            return
        role_value = self.query_one("#studio-preview-role", Select).value
        size_value = self.query_one("#studio-preview-size", Select).value
        if role_value is Select.NULL or size_value is Select.NULL:
            return
        role = None if str(role_value) == "__none__" else str(role_value)
        size = next(
            (
                (width, height)
                for _label, value, width, height in self._SIZES
                if value == str(size_value)
            ),
            None,
        )
        if size is None:
            return
        try:
            self.preview = self.service.preview_view(
                self.target,
                role=role,
                width=size[0],
                height=size[1],
            )
        except (StudioError, ValueError) as error:
            self.app.notify(str(error), severity="error")
            return
        self.role = role
        self.query_one("#studio-preview-summary", Static).update(
            _studio_preview_summary(self.preview)
        )
        self.query_one("#studio-preview-canvas", TextArea).load_text(
            _studio_view_preview_text(self.preview)
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "studio-preview-close":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


def _studio_preview_minimum(preview: StudioViewPreview) -> str:
    """Describe declared minimums, or say plainly that none were declared."""

    parts = []
    if preview.minimum_width is not None:
        parts.append(f"width {preview.minimum_width}")
    if preview.minimum_height is not None:
        parts.append(f"height {preview.minimum_height}")
    return "minimum " + " × ".join(parts) if parts else "no declared minimum"


def _studio_preview_summary(preview: StudioViewPreview) -> str:
    role = preview.role or "(no role)"
    return (
        f"{role} · {preview.width} × {preview.height} · {preview.fit.upper()} · "
        f"{_studio_preview_minimum(preview)} · "
        f"{len(preview.effective_permissions)} effective permission(s)\n"
        "Static metadata/security preview only · no records, database, or application code"
    )


def _studio_view_preview_text(preview: StudioViewPreview) -> str:
    status_marker = {
        "editable": "E",
        "conditional": "?",
        "read_only": "R",
        "protected": "P",
        "hidden": "H",
    }
    body: list[str] = []
    access = "  ".join(
        f"{item.operation}:{'yes' if item.allowed else 'no'}"
        for item in preview.access
    )
    body.append(f"Access  {access}")
    if preview.sections:
        tabs: list[str] = []
        for section in preview.sections:
            label = section.tab or "General"
            if label not in tabs:
                tabs.append(label)
        if any(section.tab for section in preview.sections):
            body.append("Tabs    " + " | ".join(tabs))
        body.append(
            "Layout  "
            + " -> ".join(
                f"{section.label} [{section.kind}]" for section in preview.sections
            )
        )
    track_order: list[str] = []
    fields_by_track: dict[str, list[str]] = {}
    for field in preview.fields:
        if field.track_label not in fields_by_track:
            track_order.append(field.track_label)
            fields_by_track[field.track_label] = []
        fields_by_track[field.track_label].append(
            f"{status_marker[field.status]}:{field.label}"
        )
    for track in track_order:
        body.append(f"{track}  " + " | ".join(fields_by_track[track]))
    action_bars: dict[str, list[str]] = {}
    section_labels = {section.key: section.label for section in preview.sections}
    for action in preview.actions:
        label = "Record actions" if action.bar == "record" else (
            f"{section_labels.get(action.bar, action.bar)} actions"
        )
        action_bars.setdefault(label, []).append(
            f"{action.name}:{'on' if action.enabled else 'off'}"
            + ("?" if action.runtime_condition else "")
        )
    for label, actions in action_bars.items():
        body.append(f"{label}  " + " | ".join(actions))
    if preview.warnings:
        body.append("Warnings")
        body.extend(f"! {warning}" for warning in preview.warnings)
    else:
        body.append("No preview warnings.")

    width = preview.width
    height = preview.height
    inner_width = width - 2
    title = f" {preview.view} · {preview.role or 'no role'} · {preview.fit} "
    title = title[:inner_width]
    canvas = ["+" + title + "-" * (inner_width - len(title)) + "+"]
    available_body_rows = height - 2
    if len(body) > available_body_rows:
        body = body[: max(0, available_body_rows - 1)] + [
            "... preview content clipped at selected terminal height ..."
        ]
    for line in body:
        clipped = line[:inner_width]
        canvas.append("|" + clipped + " " * (inner_width - len(clipped)) + "|")
    while len(canvas) < height - 1:
        canvas.append("|" + " " * inner_width + "|")
    canvas.append("+" + "-" * inner_width + "+")

    details = [
        "",
        "Resolved preview details",
        "Legend: E editable, ? record-dependent, R read-only, P protected, H hidden",
        "",
        "Entity access:",
    ]
    details.extend(
        f"- {item.operation}: {'allowed' if item.allowed else 'denied'}"
        + (f" ({item.permission})" if item.permission else " (no permission declared)")
        for item in preview.access
    )
    details.append("")
    details.append("Field placements:")
    details.extend(
        f"- [{status_marker[field.status]}] {field.track_label} / {field.label}: "
        f"{field.reason}"
        for field in preview.fields
    )
    details.append("")
    details.append("Actions:")
    details.extend(
        f"- {action.label}: {'enabled' if action.enabled else 'disabled'}"
        f"{' (record-dependent)' if action.runtime_condition else ''} · {action.reason}"
        for action in preview.actions
    )
    if preview.warnings:
        details.append("")
        details.append("Warnings:")
        details.extend(f"- {warning}" for warning in preview.warnings)
    return "\n".join((*canvas, *details)) + "\n"
