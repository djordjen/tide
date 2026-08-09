"""Register TIDE-owned generated behavior with the application runtime."""

from __future__ import annotations

from pathlib import Path

from tide.runtime import load_application_module
from tide.services import ActionService, RecordsService


def configure_runtime(records: RecordsService, actions: ActionService) -> None:
    generated = load_application_module(
        Path(__file__).with_name('actions.py')
    )
    actions.register('actions.transition_crm_contact_archive', generated.transition_crm_contact_archive)
