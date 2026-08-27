# Contributing to TIDE

TIDE is contract-first: changes to metadata behavior should include a focused
test, a diagnostic when invalid input is possible, and an update to the living
specification when the public model changes.

## Development setup

With `uv`:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run tide model validate applications/invoicing
uv run tide model validate applications/contacts
```

These are the commands CI runs, so a green local run means a green build. The
suite takes about three minutes; `uv run pytest -n 4 --dist loadfile` is what CI
uses and roughly halves that. Four workers is deliberate rather than `auto`:
the Textual suites drive real terminal pilots with real timeouts, and
oversubscribing makes them miss waits instead of failing honestly.

With a standard virtual environment:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest
tide model validate applications/invoicing
tide model validate applications/contacts
```

Python 3.11 and later are supported. Pull requests should keep both application
fixtures valid and add a negative fixture when introducing a new diagnostic.

### Do not pass `--basetemp`

Run pytest without it. The repository's root `conftest.py` chooses the scratch
root, and a run that names its own defeats the choice.

`--basetemp` is not a tidier default -- it is a different branch of pytest's
`getbasetemp` with no retention at all. The default branch keeps the last three
runs and deletes the rest. The `--basetemp` branch clears only the one exact
directory it was handed, only at the start of a run that names it, and not at
all if the run is interrupted or killed.

That matters more on Windows than it sounds. pytest writes a
`<testname>current` symlink beside every numbered temp directory, those are
NTFS reparse points, and the User Profile Service resolves every one in the
profile at logon. On this project's development machine 43,224 accumulated
under `%LOCALAPPDATA%\Temp` from per-label basetemps and logon went from under
a second to about 105.

On Windows, scratch goes to `pytest-scratch` at the root of the drive the
checkout is on -- off the profile, and deliberately not inside the checkout.
Elsewhere nothing is imposed: `/tmp` was never the problem. Set
`PYTEST_DEBUG_TEMPROOT` to override the location; it wins, and it must name a
directory that already exists, because pytest's own `mkdir` there is not
recursive. A `--basetemp` that resolves inside the Windows user profile is
refused with an explanation rather than allowed to accumulate.

Scratch stays out of the tree for a measured reason, not a tidy one. An
earlier version of this put it in a git-ignored `.pytest-scratch/` inside the
checkout, and `test_designer_save.py` began failing intermittently -- 6
failures in 45 runs with scratch inside the tree, 0 in 45 with it outside. The
save service reads and locks project sources, and temporary projects sitting
inside the real one are not neutral scenery.

## Writing documentation

A command inside a `bash`, `powershell`, `sh`, `shell`, `console` or `pwsh`
fence is one a reader will copy, so `tests/test_launcher_contracts.py` resolves
every `tide` invocation in one against the real argument parser. An invented
subcommand or a retired flag fails the suite. This is not hypothetical:
`tide api describe` sat under "Useful commands" without ever existing, and the
Windows shortcut passed `--customers` for weeks after the CLI replaced it.

A command TIDE does not have yet belongs in a `text` fence, which is already how
this project marks illustrations and expected output. `docs/ROADMAP.md` sketches
the CLI it is growing towards, and `docs/APPLICATION-MODEL.md` a migration
workflow with no apply path; both explain themselves in the surrounding prose,
but prose does not travel with a copy-paste, so the fence has to say it too.

The check covers `tide` only — `uv`, `npm` and `git` lines in the same blocks
are on you.

That check reads syntax, so `tests/test_documentation.py` reads the rest. A
`Model is valid:` line inside a `text` fence must be a line the compiler
actually prints, counts included; and the claim that `tide serve` alone is REST
and the description is held by asserting that `--web-root` and `--mcp` default
to off and that nothing answers `/` without them. Both exist because the README
promised for months that one `serve` command also brought the Web UI and MCP,
and every check in the repository agreed with it. If you write what a command
will print, print it first and paste what came back.

## Screenshots

The images in `docs/images/` are generated, not collected. Nothing verifies
them — a screenshot is the one kind of documentation that cannot be compiled,
link-checked, or run — so the only defence against a stale one is that
replacing it costs a command:

```bash
uv run python tools/capture_screenshots.py
```

```bash
npm run build && npm run screenshots
```

The first drives the real Textual client and Studio headlessly and writes the
SVGs. The second, from `web/`, stands up the same server the end-to-end
journeys use, signs in through it, and writes the PNGs; it needs the built
renderer, which is what the `npm run build` in front of it is for. Neither runs
in CI, because both write into the working tree.

Regenerate after any change a reader would see, and look at what came out: the
capture waits for the screen it wants, but nothing checks that the screen is
worth showing. Sizes are chosen per capture and are part of the source — a
capture widened past what GitHub renders is legible in the file and unreadable
on the page.

## Compatibility discipline

- Unknown metadata properties are errors; do not silently accept misspellings.
- Diagnostic codes are user-facing API and should not be renumbered casually.
- Source and normalized models are separate contracts.
- New adapters must call application services rather than persistence directly.
- Generated schemas and CLI JSON output need tests when their shape changes.

By submitting a contribution, you agree that it may be distributed under the
project's [MIT License](LICENSE).
