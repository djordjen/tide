"""Printing a machine-readable result."""

from __future__ import annotations

import json
from typing import Any




def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))
