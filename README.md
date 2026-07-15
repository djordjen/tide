# TIDE

**Terminal Integrated Data Environment**

> Model once. Run in any terminal.

TIDE is a proposed open-source, metadata-driven runtime and development
environment for database-oriented business applications. Its first-class
client is a keyboard-first, mouse-aware terminal interface that can run
locally or through SSH. REST, MCP, reports, and a future web interface use the
same application services, permissions, validation, and transaction model.

TIDE takes inspiration from:

- Clarion's integrated dictionary, browses, forms, reports, and extension
  points;
- web2py's coherent field-driven database, validation, and form behavior;
- XAF's Application Model, Object Space, Actions, security, modules, and model
  overlays.

It deliberately avoids editable generated code, implicit framework magic, and
deep abstraction hierarchies. Application structure is declarative; complex
business behavior remains ordinary Python.

## Initial technology direction

- Python
- Textual
- SQLAlchemy and Alembic
- Pydantic
- SQLite for local development
- Microsoft SQL Server as the first multi-user deployment target
- PostgreSQL as a later certified dialect
- FastAPI for generated REST/OpenAPI
- the official Python MCP SDK
- Jinja2 plus an HTML/PDF renderer for initial reports
- pytest

These are adapters around TIDE's application core, not the definition of the
core itself.

## Runnable headless slice

The initial metadata compiler and CLI are executable on Python 3.11 or later:

```bash
uv sync --extra dev
uv run tide model validate applications/invoicing
uv run tide model explain sales.Invoice.total --project applications/invoicing
uv run tide api export-openapi applications/invoicing
uv run tide run applications/invoicing --demo --page-size 3
uv run pytest
```

Here, "compiler" means a **metadata compiler**, not native executable or Python
bytecode compilation. It turns an application's YAML into a validated,
resolved, immutable `ApplicationModel`; production still runs the ordinary
Python TIDE runtime and application handlers.

The compiler currently provides a strict versioned source schema, duplicate-key
detection, source-located diagnostics, project path confinement, cross-file
relationship and view resolution, safe expression validation, computed-cycle
detection, JSON Schema export, and immutable normalized model output.

The headless runtime adds secured record/query/action services, a repository
protocol with in-memory and synchronous SQLAlchemy Core implementations,
`RecordSession`, computed master-detail values, field protection, validation,
action-owned state, idempotency, and optimistic concurrency. Managed SQLite
schema creation and legacy no-DDL mappings are executable. SQL predicate,
reference-path, and single-collection aggregate row policies are pushed into
root queries. SQL Server schema/query compilation and an opt-in live integration
suite establish it as the first multi-user target. Secured keyset pagination
uses opaque, principal-bound continuation cursors with matching behavior in the
in-memory and SQLAlchemy adapters. Action idempotency and audit state now share
a storage-neutral lifecycle with in-memory and explicitly managed SQLAlchemy
implementations; interrupted reservations fail closed instead of executing a
handler twice. The initial Textual adapter now interprets resolved browse and
form metadata for secured create/edit, inline InvoiceLine editing, validation,
cancel/save, optimistic-concurrency feedback, and audited invoice posting.

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
tests/                     framework contract tests
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

## Documentation

Start with [the documentation index](docs/README.md). Important documents are:

- [Vision](docs/VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Application model](docs/APPLICATION-MODEL.md)
- [Legacy databases](docs/LEGACY-DATABASES.md)
- [Microsoft SQL Server](docs/SQL-SERVER.md)
- [Compilation and application layout](docs/COMPILATION-AND-LAYOUT.md)
- [Metadata contract v0.1](docs/METADATA-V0.md)
- [Presentation model](docs/PRESENTATION.md)
- [Expressions and validation](docs/EXPRESSIONS-AND-VALIDATION.md)
- [Security](docs/SECURITY.md)
- [REST API and MCP](docs/API-AND-MCP.md)
- [Query and concurrency](docs/QUERY-AND-CONCURRENCY.md)
- [Shared cursor storage](docs/CURSOR-STORAGE.md)
- [Action audit and idempotency](docs/AUDIT-AND-IDEMPOTENCY.md)
- [Designers and reporting](docs/DESIGNERS-AND-REPORTING.md)
- [Terminal compatibility](docs/TERMINAL-COMPATIBILITY.md)
- [Threat model](docs/THREAT-MODEL.md)
- [Operational baseline](docs/OPERATIONS.md)
- [Headless runtime](docs/HEADLESS-RUNTIME.md)
- [Roadmap](docs/ROADMAP.md)
- [Decision log](docs/DECISIONS.md)

## Command-line direction

```bash
tide new invoicing
tide model validate
tide model explain sales.Invoice.customer
tide view explain sales.Invoice.edit
tide api export-openapi
tide db diff
tide db migrate
tide run
tide serve
tide report preview sales.invoice
```

## Current status

Milestones 0 and 1 are substantially implemented, and the secured application
core milestone is complete. The v0.1 compiler, resolved-view provenance, typed
expressions, headless services, in-memory and SQLite repositories, tests, and
executable invoicing workflow are implemented. Direct, reference-path, and
single-collection aggregate SQL policy translation and secured keyset
pagination are executable. Collection hydration now applies source-field,
target-entity, and target-row authorization through bounded relationship load
plans. Durable action reservations and channel-aware action audit rows are
implemented for memory and SQLAlchemy stores. SQL Server dialect compilation is
covered, with live certification available through an opt-in integration suite.
Shared SQLAlchemy cursor storage preserves exact typed continuation state across
runtime restarts and processes while storing only hashes of bearer tokens. An
adapter-independent, read-only OpenAPI 3.1 preview now generates typed
Pydantic record/page schemas and explicitly exposed list/get contracts. The
first metadata-driven Textual invoicing workflow is runnable with
application-owned demo data, secured reference display, opaque paging,
create/edit forms, master-detail line editing, validation and concurrency
feedback, audited posting, invoice-number incremental search, named filters,
and sortable stored scalar columns through keyboard or mouse controls. Reference
lookup search, REST hosting, MCP, migrations, and report rendering remain
roadmap work.

Metadata v0.1 is an executable experimental contract. Breaking authoring
changes require a new `schema_version`; stable 1.0 compatibility is not yet
promised.

## License

TIDE is available under the permissive [MIT License](LICENSE). You may use,
modify, distribute, sublicense, and sell it, including as part of commercial or
private software, provided the copyright and license notice are retained.
