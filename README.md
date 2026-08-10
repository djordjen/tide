# TIDE Framework

**Terminal Integrated Data Environment**

[![CI](https://github.com/djordjen/tide/actions/workflows/ci.yml/badge.svg)](https://github.com/djordjen/tide/actions/workflows/ci.yml)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Tested on Windows and Linux](https://img.shields.io/badge/Tested-Windows%20%7C%20Linux-4C8BF5)](https://github.com/djordjen/tide/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-2EA44F.svg)](LICENSE)

> Model once. Run in any terminal.

TIDE is a proposed open-source, metadata-driven runtime and development
environment for database-oriented business applications. Its first-class
client is a keyboard-first, mouse-aware terminal interface that can run
locally or through SSH. REST, MCP, reports, and the Web UI use the same
application services, permissions, validation, and transaction model.

TIDE takes inspiration from:

- Clarion's integrated dictionary, browses, forms, reports, and extension
  points;
- web2py's coherent field-driven database, validation, and form behavior;
- XAF's Application Model, Object Space, Actions, security, modules, and model
  overlays.

It deliberately avoids editable generated code, implicit framework magic, and
deep abstraction hierarchies. Application structure is declarative; complex
business behavior remains ordinary Python.

## TUI preview

These captures come from the real Textual client running the bundled invoicing
application with deterministic demo data. Select an image to open it full size.

[![TIDE invoice browser](docs/images/tide-invoice-browser.svg)](docs/images/tide-invoice-browser.svg)

<table>
  <tr>
    <th>Metadata-driven invoice editor</th>
    <th>Searchable multi-column product lookup</th>
  </tr>
  <tr>
    <td>
      <a href="docs/images/tide-invoice-editor.svg">
        <img src="docs/images/tide-invoice-editor.svg" alt="TIDE invoice editor">
      </a>
    </td>
    <td>
      <a href="docs/images/tide-product-lookup.svg">
        <img src="docs/images/tide-product-lookup.svg" alt="TIDE searchable product lookup">
      </a>
    </td>
  </tr>
</table>

## Quick start

Python 3.11 is the development and CI-certified baseline. Project metadata
permits 3.11 or later; newer interpreters are best-effort rather than part of
the required CI matrix.

```bash
uv sync --extra dev
uv run tide model validate applications/invoicing
uv run tide run applications/invoicing --demo
uv run tide serve applications/invoicing --demo
uv run tide model validate applications/contacts
uv run pytest
```

`tide run` opens the terminal client against the bundled invoicing application.
`tide serve` puts the same application behind FastAPI, with the Web UI and MCP
available from the same process. Neither needs a database: `--demo` seeds an
in-memory one, and `tide serve` requires a development bearer token of at least
32 characters in `TIDE_API_TOKEN` and binds to loopback.

The smaller generated [Contacts application](applications/contacts/README.md)
is the second portability proof. On Windows, `start.bat contacts-demo` runs it
immediately; its README also covers Studio, Web, REST, runtime MCP, and
persistent Faker data.

[Getting started](docs/GETTING-STARTED.md) walks through the demo TUI, the
[Web UI](docs/WEB-UI.md), Studio, REST/OpenAPI, MCP, and optionally SQL Server.
[Build your first TIDE application](docs/FIRST-APPLICATION.md) starts from an
empty directory. On Windows, `start.bat` wraps each of these.

## What is implemented

| Area | State |
| --- | --- |
| Metadata compiler (v0.1) | Validated, resolved, immutable model; source-located diagnostics |
| Headless services | Records, queries, actions, validation, audit, idempotency, optimistic concurrency |
| Repositories | In-memory and SQLAlchemy Core; managed SQLite and legacy no-DDL mappings |
| Databases | SQLite for development; SQL Server as the first multi-user target, certified by an opt-in live suite; PostgreSQL later |
| Terminal client | Browse, forms, master-detail, lookups, three-way conflict review, reports |
| Web UI | The same journeys in React, plus deep links and TIDE-owned sign-in |
| REST + OpenAPI | Generated from the model; only explicitly exposed operations |
| MCP | Authenticated resources and tools over the same services |
| Reports | Secured documents with CSV, standalone HTML, and PDF export |
| Studio | First tranche: inspect, edit, diff — no source is written without an explicit approval |
| Schema migrations | Read-only diff plus an Alembic-compatible revision; TIDE does not apply DDL |

[docs/ROADMAP.md](docs/ROADMAP.md) is the forward view, and
[CHANGELOG.md](CHANGELOG.md) is the detail behind this table.

Metadata v0.1 is an executable experimental contract. Breaking authoring
changes require a new `schema_version`; stable 1.0 compatibility is not yet
promised.

## Repository layout

Framework code and user applications have an explicit boundary:

```text
src/tide/                  reusable TIDE runtime and compiler
applications/
    invoicing/             a self-contained TIDE application
        tide.yaml
        runtime.py           explicit action/generator registrations
        demo_data.py         opt-in local demonstration records
        models/
        views/
        reports/
        security/
    contacts/              generated second-application portability proof
        tide.yaml
        demo_data.py
        fake_data.py
        models/
        views/
        security/
tests/                     framework contract tests
web/                       the React renderer
```

Each direct child of `applications/` is an application root. It may be
developed beside the runtime, packaged separately, or deployed with an
installed `tide-framework`; application source is not part of the runtime
wheel.

## Guiding principles

1. Terminal-first, keyboard-first, and fully mouse-aware.
2. One normalized application model drives every interface.
3. All interfaces use the same secured application services.
4. Useful defaults must produce a working application without a designer.
5. Declarative metadata must always have a clean Python escape hatch.
6. Model evolution, overrides, and extension points must be deterministic.
7. TIDE must remain useful for real multi-user business applications.
8. AI access is explicit, inspectable, permission-aware, and never privileged.

## Built on

Python, Textual, SQLAlchemy and Alembic, Pydantic, FastAPI, the official Python
MCP SDK, React and TypeScript, standard-library HTML plus optional ReportLab
for PDF, and pytest. These are adapters around TIDE's
application core, not the definition of the core itself; see
[Architecture](docs/ARCHITECTURE.md) for where the boundary runs.

## Documentation

Start with [the documentation index](docs/README.md), which lists all of it.
The ones worth reading first:

- [Getting started](docs/GETTING-STARTED.md)
- [Build your first TIDE application](docs/FIRST-APPLICATION.md)
- [Vision](docs/VISION.md) and [Architecture](docs/ARCHITECTURE.md)
- [Application model](docs/APPLICATION-MODEL.md) and
  [Metadata contract v0.1](docs/METADATA-V0.md)
- [Security](docs/SECURITY.md) and [Threat model](docs/THREAT-MODEL.md)
- [REST API and MCP](docs/API-AND-MCP.md)
- [Web UI](docs/WEB-UI.md)
- [Roadmap](docs/ROADMAP.md), [Decision log](docs/DECISIONS.md), and
  [Changelog](CHANGELOG.md)

## License

TIDE is available under the permissive [MIT License](LICENSE). You may use,
modify, distribute, sublicense, and sell it, including as part of commercial or
private software, provided the copyright and license notice are retained.
