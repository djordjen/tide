"""What a verified browser session grants, independent of who verified it.

This is its own module so that the two authenticators can share it without
sharing their dependencies. `local_auth` needs the type and nothing else from
`browser_auth`, and importing it from there dragged `httpx` into every server
start: `server` imports `local_auth`, so `tide serve` could not run on the
`api` extra alone whatever `--auth` it was given, despite that being what the
documentation tells people to install.
"""

from __future__ import annotations

from dataclasses import dataclass

from tide.runtime import Principal


@dataclass(frozen=True, slots=True)
class BrowserSessionAccess:
    """A principal restored from a browser session, with its CSRF proof."""

    principal: Principal
    csrf_token: str
