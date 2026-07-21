# TIDE Architecture

## Central rule

Every client invokes the same application services. A Textual screen, REST
route, MCP tool, report, and future web form may authenticate differently, but
none implements separate business or authorization rules.

For a server deployment, TUI, Qt GUI, browser, and MCP clients are untrusted
remote adapters. They authenticate to the TIDE HTTP server and never receive a
database URL. FastAPI is the transport boundary, not the business layer:

```text
Remote TUI / Qt / Web / MCP
            | HTTPS + credentials
            v
        FastAPI adapter
            | RequestContext
            v
RecordsService / ActionService / ReportService
            |
            v
 Repository / SQL Server
```

The default `tide run` Textual adapter remains an in-process trusted-host mode
and calls services directly. Its opt-in `--api-url` mode calls the same HTTP
contracts intended for Qt and web clients and never embeds a database driver or
connection string.

The remote client validates an authenticated session
capability document against the locally compiled model before transferring
records. It owns JSON/TIDE type conversion, protected-value reconstruction,
opaque cursors, ETags, idempotency headers, and stable transport errors.
Record/action facades present this client behind the interfaces already
consumed by Textual; widgets do not construct URLs or authorize operations.
Local edit drafts remain `RecordSession` objects, while every load, lookup
assignment, commit, and action crosses the authenticated service boundary. The
initial Qt adapter implements the read-only half of this split: a Qt-neutral
browse/detail controller consumes `TideApiClient`, and a lazy optional PySide6
widget adapter renders compiled browse columns, form groups, and inline
collections without database packages or credentials.

```text
applications/<name>/ (YAML + Python handlers + overlays)
                         |
                         v
                  Model compiler
        parsing | merging | typing | diagnostics
                         |
                         v
            Normalized ApplicationModel
                         |
          +--------------+---------------+
          |                              |
          v                              v
  Application services            Presentation model
  queries | records | actions      views | formats | reports
          |                              |
          +--------------+---------------+
                         |
       +---------+-------+-------+----------+
       |         |               |          |
       v         v               v          v
    Textual   FastAPI REST        MCP      Reports
                         |
                         v
          Security + RecordSession/Unit of Work
                         |
                         v
              SQLAlchemy persistence
```

## Model compiler

Adapters must never consume arbitrary YAML dictionaries. The compiler loads
all files, resolves namespaces and references, applies defaults and overlays,
type-checks expressions, and creates an immutable `ApplicationModel`.

The compiler is also responsible for diagnostics such as:

- unknown fields and targets;
- invalid override paths;
- circular computed-field dependencies;
- expressions that cannot be translated for a requested database filter
  (planned with the SQLAlchemy adapter);
- conflicting action shortcuts;
- migration-sensitive renames;
- permission references that do not exist.

The resolved model should be inspectable through the CLI and developer MCP
server.

This is semantic compilation: the result is a typed runtime model. It does not
turn the application into a native executable or replace its Python files with
bytecode. Production should validate during CI and fail fast by compiling the
model again at process startup; a future cache may store the normalized model
as a disposable optimization.

## Application services

The core presents a small set of UI-independent operations:

```python
records.query(entity, query_spec, context)
records.query_page(entity, query_spec, context)
records.get(entity, identity, context)
records.begin_edit(entity, identity, context)
records.commit(record_session, context)
actions.execute(action, target, payload, context)
reports.build(report, parameters, context)
```

Adapters may provide transport concerns such as HTTP serialization or terminal
focus management. They may not reimplement validation, permission checks,
transactions, or lifecycle hooks.

The executable implementation defines a structural `Repository` protocol.
`InMemoryRepository` remains the fast contract-test adapter, while
`SQLAlchemyRepository` supplies synchronous SQLAlchemy Core persistence without
leaking SQLAlchemy sessions into application services.

The SQLAlchemy boundary supports two schema-ownership modes. `managed` maps a
TIDE-owned schema whose reviewed migrations may be executed explicitly.
`legacy` maps externally owned tables and columns, permits compatibility
inspection and normal secured data operations, and rejects all DDL and
migration entry points. Both modes use the same repository and service
conformance suite.

Repository construction never changes a schema. Managed creation is an
explicit call; legacy creation is rejected. Root structured filters, direct
and reference-path policies, single-collection aggregates, ordering, and limits
translate to bound SQL and are checked for adapter support before deployment
readiness. Bounded collection hydration applies source-field, target-entity,
and target-row authorization before projection. Multiple-collection policy
translation remains required before the adapter is production-ready.

## Request context

Every operation carries a context similar to:

```python
@dataclass(frozen=True)
class RequestContext:
    principal: Principal
    locale: str
    timezone: str
    channel: Channel
    correlation_id: str
```

The initial runtime is single-tenant per deployment. A multi-tenant version
must define tenant-scoped identities, uniqueness, migrations, policies, caches,
and audit behavior before adding a tenant identifier to this core contract.

The channel identifies TUI, REST, WEB, MCP, REPORT, or SYSTEM for auditing and
carefully defined policy differences. It is not a shortcut around security.

## RecordSession and unit of work

TIDE needs an XAF Object Space-like editing boundary. A `RecordSession` owns:

- loaded object graphs;
- original values and change tracking;
- unsaved master-detail additions and deletions;
- validation state;
- optimistic concurrency tokens;
- commit, rollback, refresh, and conflict resolution.

Generated views work against this abstraction rather than exposing a
SQLAlchemy session. This keeps cancel/save semantics identical across TUI, web,
REST, and MCP.

## Actions

CRUD commands and domain operations share a first-class action model. An
action can define visibility, enabled state, required selection, permissions,
confirmation, input schema, handler, audit behavior, and presentations such as
buttons, menu entries, shortcuts, REST routes, and MCP tools.

The runtime rechecks action conditions and permissions during execution. A
disabled UI control is never the enforcement boundary.

## Lifecycle and extension model

The initial lifecycle distinguishes:

- parsing and field validation;
- object validation;
- action preconditions;
- `before_commit` behavior inside the transaction;
- `after_commit` behavior after durable success.

External side effects must not run in an `after_save` hook before a transaction
has committed. Applications that need reliable integration events may later
use an outbox adapter.

Pure validation remains synchronous where possible. Actions and I/O-dependent
handlers may be asynchronous.

## Proposed package structure

```text
src/tide/
    model/            Typed metadata and normalized model
    compiler/         Loading, merging, reference resolution, diagnostics
    expressions/      Typed expression AST and evaluators
    services/         Query, record, and action services
    runtime/          Lifecycle, context, events, configuration
    sessions/         RecordSession, transactions, concurrency
    security/         Principals, permissions, policies, redaction
    api/              Adapter-independent Pydantic/OpenAPI contracts
    presentation/     View resolution, formats, presets, overlays
    data/             SQLAlchemy and Alembic integration
    adapters/
        textual/      Terminal UI
        rest/         FastAPI and OpenAPI
        mcp/          Developer and runtime MCP servers
    reporting/        Secured report documents, HTML, and PDF renderers
    designer/         Headless designer command service
    cli/              Project and runtime commands
```

## Runtime metadata first

The first versions interpret metadata at runtime. Source generation may be
considered only after the model and extension contracts are stable. Runtime
metadata avoids generated-file ownership and regeneration conflicts while
preserving immediate feedback.
