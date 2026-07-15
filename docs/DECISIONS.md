# TIDE Decision Log

This document records the current design direction. Dates indicate when a
decision was first recorded, not when implementation was completed.

## Accepted decisions

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-14 | The project name is **TIDE: Terminal Integrated Data Environment**. | Memorable, terminal-oriented, and not tied to a legacy product name. |
| 2026-07-14 | TIDE is a business-application runtime; Textual is its first UI adapter. | Domain behavior must remain independent from presentation technology. |
| 2026-07-14 | Python, Textual, SQLAlchemy, Alembic, Pydantic, SQLite, PostgreSQL, FastAPI, and pytest form the initial ecosystem. | They are mainstream, productive, and replaceable behind clear adapter contracts. |
| 2026-07-14 | YAML is the preferred human authoring format; JSON may be accepted and exported. | Compact YAML is readable and supports comments; the compiler remains format-independent. |
| 2026-07-14 | Models are split by entity and concern rather than stored in one large file. | Smaller files are easier to review, merge, and explain. |
| 2026-07-14 | All source formats compile into one typed, immutable `ApplicationModel`. | Every adapter must share identical metadata semantics. |
| 2026-07-14 | Co-located applications live under `applications/<name>/`, separate from `src/tide/`. | The framework/application ownership boundary should be obvious, while still allowing a web2py-style multi-application distribution. |
| 2026-07-14 | "Model compiler" means semantic metadata compilation, not Python bytecode or native executable compilation. | Validation, resolution, and normalization are required in development and at production startup; deployable Python remains ordinary source/package content. |
| 2026-07-14 | Runtime metadata precedes source generation. | It avoids regeneration conflicts while contracts are evolving. |
| 2026-07-14 | Generated views work without a designer; customizations are deterministic overlays. | Immediate productivity and safe model evolution are more important than early visual tooling. |
| 2026-07-14 | Application services, security, and `RecordSession` sit between every adapter and SQLAlchemy. | TUI, REST, MCP, reports, and web must not duplicate or bypass business rules. |
| 2026-07-14 | REST exposure is explicit through `expose.rest`; FastAPI supplies the initial adapter. | Exposure is security-sensitive and should generate OpenAPI without generated source files. |
| 2026-07-14 | TIDE has separate developer and runtime MCP use cases. | Project inspection and runtime data access have different risk and permission profiles. |
| 2026-07-14 | Security covers entity, row, field, action, report, navigation, and export permissions. | Hiding a control is not sufficient authorization. |
| 2026-07-14 | Protected fields use an internal sentinel, rendered as localized protected content in forms. | A display placeholder must never become a stored value or break typed fields. |
| 2026-07-14 | Cross-file and cross-module relationships are resolved through qualified entity names. | Modules need collision-free references and a global relationship graph. |
| 2026-07-14 | Computed fields, filters, validation, action conditions, and conditional presentation share a safe typed expression system. | One criteria model can be validated and translated consistently without `eval`. |
| 2026-07-14 | TIDE Studio edits metadata through a headless designer service. | Textual, web, and AI designer clients should share validation, undo/redo, and save behavior. |
| 2026-07-14 | Reports use a declarative banded model and secured application services. | This preserves Clarion-style reporting without creating a data-access back door. |
| 2026-07-14 | Metadata source schema `0.1` is explicit and separate from the application version. | Parsing changes cannot silently reinterpret an existing project. |
| 2026-07-14 | The v0.1 YAML reader uses safe construction, strict true/false booleans, duplicate-key rejection, and no YAML merge keys. | Ambiguous scalars and hidden duplicate properties are unsafe authoring behavior. |
| 2026-07-14 | The initial expression parser uses a validated, allow-listed Python AST subset but never `eval`. | It supplies a small familiar grammar while rejecting method calls, arbitrary nodes, and unknown paths. |
| 2026-07-14 | The first SQLAlchemy persistence adapter will be synchronous. | A single transaction model simplifies `RecordSession`; async action handlers remain possible and the adapter boundary permits revisiting this later. |
| 2026-07-14 | Basic optimistic version checking precedes every remote mutation. | REST and MCP writes must not ship with silent lost-update behavior; richer merge UX can follow later. |
| 2026-07-14 | Schema v0.1 is single-tenant per deployment. | Multi-user support is required, but tenancy needs explicit identity, uniqueness, cache, migration, and policy semantics rather than an optional context field. |
| 2026-07-14 | TIDE is distributed under the MIT License. | The project should be freely usable, modifiable, redistributable, sublicensable, and commercially usable with only preservation of the notice and warranty disclaimer. |
| 2026-07-14 | View resolution records leaf-level property provenance. | Defaults, presets, entity settings, base views, and overlays are only maintainable when the final value can identify its source. |
| 2026-07-14 | Compiler handler checks are static and do not import application modules. | Validation must confirm project-local functions without executing untrusted project code. |
| 2026-07-14 | The first application-service adapter is an in-memory repository. | Security, sessions, validation, actions, and concurrency can stabilize before SQLAlchemy mapping concerns are introduced. |
| 2026-07-14 | Persistence owns concurrency-token increments; domain actions own state transitions. | Central version management makes retries and stale-write handling consistent across all actions and adapters. |
| 2026-07-14 | Idempotency replay reauthorizes and reprojects its target. | Cached transport output could leak fields after permission changes; replay must use current authorization. |
| 2026-07-14 | Decimal fields use deterministic decimal arithmetic end to end: fractional literals preserve their source tokens, evaluation uses a private 38-digit round-half-even context, division and averages return `Decimal`, and record services normalize decimal inputs at the service boundary. | Business quantities such as money must not accumulate binary floating-point error or depend on process-global decimal settings. |
| 2026-07-14 | Field values are validated against their declared type at commit; values that cannot represent the type fail validation instead of being stored. | An invoicing runtime must not silently store a string in a date field; the SQLAlchemy adapter would otherwise disagree with the in-memory contract. |
| 2026-07-14 | Unique fields treat null as absent: multiple null values never conflict. | This matches SQL unique-index semantics, keeping the in-memory contract and the future SQLAlchemy adapter in agreement. |
| 2026-07-14 | Expression comparisons are limited to `==`, `!=`, `<`, `<=`, `>`, `>=`; membership `in` and identity `is` are rejected at compile time with `TIDE308`. | Operators the runtime and the future SQL translator cannot honor must fail at authoring time, not at evaluation. |
| 2026-07-14 | Compilation separates error and warning severities; errors fail compilation while warnings remain attached to the normalized model and CLI output. | Advisory diagnostics must be visible without making every non-fatal condition block application startup. |
| 2026-07-14 | Actions fail closed unless they declare a permission or explicitly set `unrestricted: true`; omission is `TIDE226`. | Accidentally omitting metadata must never grant mutation authority to every principal with record read access. |
| 2026-07-14 | Reference values are normalized against the target primary-key type and checked for existence before commit. | The in-memory contract must reject broken relationships before the SQL adapter adds database foreign-key enforcement. |
| 2026-07-15 | Database ownership is explicit: `managed` permits reviewed TIDE migrations, while `legacy` maps externally owned tables and columns under a hard no-DDL rule. | Existing third-party schemas must be usable without risking an implicit create, alter, drop, or migration operation. |
| 2026-07-15 | The first durable repository uses synchronous SQLAlchemy Core tables generated from the normalized model, not ORM domain classes. Construction never emits DDL; managed creation is explicit and legacy creation is refused. | Services already operate on typed dictionaries and `RecordSession`; Core preserves that boundary and makes schema authority visible. |
| 2026-07-15 | Repository queries receive validated `QuerySpec` values and row-policy criteria; SQL adapters must translate supported root predicates, ordering, and limits or fail closed during preflight. | Persistent security cannot load a root table and silently rely on Python post-filtering, while memory and SQL still need equivalent observable results. |
| 2026-07-15 | Create row policies evaluate finalized candidate values, and update policies are repeated in the persistence mutation predicate. | Authorization checked only when an edit session opens can become stale before commit, particularly on externally owned tables without version columns. |
| 2026-07-15 | Microsoft SQL Server through SQLAlchemy Core and `mssql+pyodbc` is the first multi-user database target; SQLite remains the fast local/test adapter and PostgreSQL follows after the SQL Server contract stabilizes. | SQL Server is the primary database used by the project owner, and early dialect tests prevent SQLite-specific schema, expression, identity, and concurrency assumptions from becoming architectural constraints. |
| 2026-07-15 | List continuation uses deterministic keyset predicates and opaque, expiring, server-side cursor state bound to the secured query and principal; the initial store is process-local behind a replaceable protocol. | Keyset paging avoids offset drift and cost, while random stateful tokens avoid exposing protected boundary values or inventing a cryptographic token format before deployment key management is defined. |
| 2026-07-15 | Collection hydration is driven by a bounded repository load plan that requires source-field and target-entity read access and applies target read-row policies in child and supported aggregate/reference SQL. | Loading every child before projection leaks protected rows into the runtime, while silent truncation makes business data incorrect; explicit policy predicates and fail-closed depth/item limits preserve both security and completeness. |
| 2026-07-15 | The first OpenAPI milestone is an adapter-independent 3.1 preview built from generated Pydantic record/page models and restricted to explicitly exposed list/get routes. | Contract generation can be tested with the secured core before FastAPI hosting exists, while mutation transport and authentication remain separate reviewed milestones. |
| 2026-07-15 | Idempotent actions reserve a durable key immediately before handler execution, and audited actions persist a started-to-terminal lifecycle; failed or interrupted keys require reconciliation rather than automatic re-execution. | The framework cannot prove whether an interrupted handler produced external effects, so availability must not be preferred over duplicate mutation risk. |
| 2026-07-15 | Multi-process continuation uses an explicitly managed SQLAlchemy cursor store with exact typed server state, transactionally bounded TTL/capacity, and only SHA-256 hashes of random bearer tokens at rest; the in-memory store remains the local default. | Restarts and load-balanced processes must share pagination state without exposing usable tokens or weakening the legacy database no-DDL boundary. |
| 2026-07-15 | Textual is an optional `tui` adapter dependency; `tide run --demo` may execute only the selected application's fixed `demo_data.py` provider and loads it into an in-memory repository. | Headless deployments should not require UI packages, while a deliberate demo flag provides a visible, database-safe golden slice without embedding application fixtures in the framework. |

## Open decisions

- Comment-preserving round-trip strategy for future designer writes.
- Compatibility policy and migration tooling for the eventual stable metadata 1.0 contract.
- Authentication provider and local credential strategy.
- Initial HTML/PDF rendering engine and pagination limits.
- Stable identifiers and explicit rename representation for schema evolution.
- Exact representation of protected fields in versioned REST contracts.
- Whether direct many-to-many syntax belongs in the first stable model.
- Composite-key representation and database-generated key/refresh behavior for
  broader legacy database compatibility.

## Deferred decisions

- Source-code generation.
- Public plugin and application marketplaces.
- Alternative native runtime implementations in Go or Rust.
- General-purpose workflow language.
- Full browser delivery of Textual applications versus a dedicated web
  renderer.
