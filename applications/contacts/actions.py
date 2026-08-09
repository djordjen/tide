"""TIDE-owned state-transition handlers generated from structured operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping


class StateTransitionError(ValueError):
    """The record cannot make the requested generated transition."""


def transition_crm_contact_archive(
    record: MutableMapping[str, Any],
    context: Any,
    payload: Mapping[str, Any],
) -> MutableMapping[str, Any]:
    current = record.get('status')
    if current == 'archived':
        return record
    if current not in ('active',):
        raise StateTransitionError(
            'invalid state for crm.Contact.archive'
        )
    record['status'] = 'archived'
    record['archived_at'] = datetime.now(timezone.utc)
    record['archived_by'] = context.principal.identifier
    return record
