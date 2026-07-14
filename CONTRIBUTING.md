# Contributing to TIDE

TIDE is contract-first: changes to metadata behavior should include a focused
test, a diagnostic when invalid input is possible, and an update to the living
specification when the public model changes.

## Development setup

With `uv`:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests applications/invoicing/actions.py
uv run tide model validate applications/invoicing
```

With a standard virtual environment:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest
tide model validate applications/invoicing
```

Python 3.11 and later are supported. Pull requests should keep the invoicing
fixture valid and add a negative fixture when introducing a new diagnostic.

## Compatibility discipline

- Unknown metadata properties are errors; do not silently accept misspellings.
- Diagnostic codes are user-facing API and should not be renumbered casually.
- Source and normalized models are separate contracts.
- New adapters must call application services rather than persistence directly.
- Generated schemas and CLI JSON output need tests when their shape changes.

By submitting a contribution, you agree that it may be distributed under the
project's [MIT License](LICENSE).
