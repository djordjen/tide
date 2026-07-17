# TIDE Roadmap

The roadmap favors a complete invoicing vertical slice over many disconnected
features. Designers and alternative renderers follow stable model contracts.

## Milestone 0 - Contracts and skeleton

Status: **complete**. The package, CLI, typed v0.1 sources, project discovery,
strict diagnostics, tests, compiler-ready invoicing fixture, and managed SQLite
schema/persistence skeleton are implemented. The metadata-driven Textual browse
shell is runnable; the local developer MCP provides project resources and
structured no-write application proposals plus deleted, compiler/runtime-
checked candidate previews.

- living architecture documentation and decision log;
- Python package, CLI, configuration, and test skeleton;
- initial typed source metadata models;
- explicit source-schema version, JSON Schema export, and stable diagnostic codes;
- project/file discovery;
- basic Textual application shell;
- SQLite connection;
- invoicing project fixture;
- read-only developer MCP project/model/entity/view resources plus structured
  approval-required new-application proposals and isolated no-apply previews;
  **implemented**

Exit condition: `tide model validate` can load a small project and report useful
source locations and diagnostics.

## Milestone 1 - Model compiler and expressions

Status: **complete**. Strict YAML,
normalized immutable entities, two-pass references, typed safe expressions,
cycle detection, layered view resolution/provenance, static handler checks,
presets, views, and reports compile and are inspectable through developer MCP.

- YAML loading with strict scalar behavior;
- normalized immutable `ApplicationModel`;
- namespaces and two-pass relationship resolution;
- defaults, presets, and overlay merge contracts;
- safe typed expression AST;
- local computed fields and cross-field validation;
- resolved-model and resolved-view explanation;
- developer MCP resources for entities, views, and diagnostics; **implemented**

Exit condition: the compiler resolves Customer, Product, Invoice, and
InvoiceLine, including references and calculated line totals.

## Milestone 2 - Secured application core

Status: **complete**. `RequestContext`, principals, role/permission expansion,
row and field policies, `ProtectedValue`, query/record services, `RecordSession`,
validation, stored master-detail computation, action execution, idempotency,
optimistic concurrency, managed SQLite persistence, and legacy no-DDL mapping
are implemented. Root SQL filters, ordering, limits, reference paths, and
single-collection aggregate row-policy translation are also implemented. SQL
Server dialect compilation and an opt-in live integration harness establish the
first multi-user target. Opaque, principal-bound keyset pagination is
implemented across both repositories. Policy-aware, bounded collection
hydration and the adapter-independent Pydantic/OpenAPI 3.1 preview are also
implemented. Durable action audit/idempotency is implemented behind in-memory
and SQLAlchemy stores. Continuation cursor state is implemented behind
in-memory and process-shared SQLAlchemy stores with exact typed values, expiry,
bounded capacity, hashed bearer tokens, and explicit schema ownership.

- query and record application services;
- deterministic keyset pagination and opaque continuation cursor contracts;
- source-field, target-entity, and target-row secured collection hydration;
- `RequestContext` and principal abstraction;
- entity, row, field, and action policy interfaces;
- threat-model regression tests and cross-adapter authorization scenarios;
- `ProtectedValue` sentinel and secure projection;
- `RecordSession`, change tracking, commit, and rollback;
- integer version tokens and stale-commit rejection;
- first-class action registry;
- durable pre-handler idempotency reservations and channel-aware action audit;
- SQLAlchemy adapter and generated schema for SQLite and SQL Server dialect
  compilation;
- repository conformance tests for both managed and legacy database modes;
- legacy table/schema/column mapping, compatibility inspection, and a hard
  no-DDL guard for externally owned schemas;
- generated Pydantic/OpenAPI preview without mutation routes.
- restart-safe shared cursor storage with legacy no-DDL behavior.

Exit condition: core behavior can be tested without Textual or FastAPI.

## Milestone 3 - Golden invoicing slice

Status: **in progress**. The resolved invoice browse and form now run in Textual
against `RecordsService` and `ActionService`, with application-owned demo data
and runtime registration. Create/edit, secured Customer/Product selectors,
inline InvoiceLine add/apply/remove, computed previews, Save/Cancel, validation
and stale-version feedback, immutable posted records, audited posting,
invoice-number incremental search, simple compiled named filters, and sortable
columns are executable through keyboard or clickable controls. Forms use
compact, visually distinct editable/read-only fields, localized date parsing,
model-owned today defaults, keyboard date stepping, column-first Tab traversal,
Enter-to-advance data entry, and typed numeric/string edit masks backed by
service validation. Product references now support a secured,
case-insensitive, multi-column lookup window and declarative selection values
that copy description and unit price into the editable line draft. Inline
editor layout and keyboard order are now developer-controlled independently of
the collection table. `tide run` can now select and validate a persistent
SQLAlchemy deployment repository from secret-safe environment configuration,
including durable framework state for managed databases. A compiler-validated,
secured record-report service now drives TUI invoice preview plus standalone
HTML and A4 PDF export.
The same TUI now navigates Invoice, Customer, and Product workspaces, supports
secured master-data create/edit forms, and can create a missing reference from
inside a lookup with **Save & Select** while preserving the invoice draft.
Managed development databases can be populated deterministically through an
application-owned Faker profile and the real secured services.

- generated browse, edit, and lookup views;
- view-level field ordering for form and inline editor controls, independent of
  collection-table column order (for example, Product before Description in
  InvoiceLine details); **implemented for the Textual invoice workflow**
- Customer and Product browse/edit workspaces and create-enabled lookups;
- transactional Invoice/InvoiceLine master-detail editing;
- selectable in-memory or SQLAlchemy runtime persistence, with explicit managed
  schema creation and legacy no-DDL behavior;
- deterministic, empty-database-only Faker development seeding;
- parsing, formatting, and validation feedback;
- compiler-validated numeric and regular-expression edit masks, with shared
  service enforcement; **implemented for Textual and OpenAPI contracts**
- computed line and invoice totals;
- Post Invoice action and immutable posted invoices;
- configurable keyboard shortcuts and mouse-aware controls;
- terminal compatibility checks at the documented viewport/Unicode/color matrix;
- sorting, paging, incremental search, and named filters;
- basic secured printable invoice with TUI preview, HTML, and PDF;
  **implemented**
- opt-in REST list/get/create/update and domain-action routes plus OpenAPI;
  **implemented with FastAPI hosting, local development and OIDC/JWKS bearer
  identity, direct TLS enforcement for non-loopback binds, opaque pagination,
  protected-field serialization, ETag concurrency, and action idempotency**

Exit condition: the example application can create, edit, post, find, and print
an invoice entirely by keyboard or mouse.

## Milestone 4 - Machine interfaces

- opt-in generated REST delete routes; create/update/action are implemented;
- expected-version preconditions for delete; update/action are implemented;
- API structured filtering/sorting and concurrency transport contracts over
  the implemented pagination and protected-field primitives;
- typed remote HTTP client, authenticated session capabilities, application
  compatibility checks, exact wire-type/protection conversion, and a CLI
  connectivity check; **implemented**
- structured remote filtering/sorting plus Textual record/action service
  facades for browse, lookups, drafts, nested commits, concurrency, and
  actions; **implemented**
- secured renderer-neutral remote report transport with Textual preview and
  local HTML/PDF export; **implemented**
- reuse the same client/service boundary for the future Qt renderer;
- runtime MCP schema/record resources and read-only structured query tools;
  **implemented with authenticated stateless Streamable HTTP, RFC 9728
  metadata, DNS-rebinding controls, service reauthorization, exact protected
  wire values, bounded pages, and principal-bound cursors**
- opt-in MCP domain actions and mutations;
- shared authentication-to-Principal adapters; **implemented for local
  development tokens and provider-neutral OIDC/JWKS access tokens**
- channel-aware audit events;
- OpenAPI and MCP schema conformance tests; **implemented for the current REST
  and read-only runtime MCP surfaces**

Exit condition: TUI, REST, and MCP produce equivalent secured outcomes through
the same services.

## Milestone 5 - Production data and security

- complete live SQL Server certification and operational guidance;
- PostgreSQL support after the SQL Server contract is stable;
- additional SQLAlchemy dialect certification for legacy databases, based on
  demand and dialect availability;
- Alembic migration proposal workflow;
- explicit rename and destructive-change handling;
- interactive conflict inspection and permitted field-level merge assistance;
- roles and permissions administration;
- audit history and protected logging;
- import and controlled export;
- deployment configuration, SSH guidance, and container packaging.
- health/readiness checks, structured logging, backup/restore, and migration
  recovery guidance.

Exit condition: multiple users can safely work against certified SQL Server
deployments and receive clear concurrency feedback.

## Milestone 6 - Reporting

- stable declarative band model;
- parameters, groups, totals, headers, and footers;
- HTML preview and PDF output;
- page behavior and repeatable-band tests;
- CSV and spreadsheet export;
- report actions through TUI, REST, and MCP where exposed;
- initial report property editor and preview tools.

Exit condition: invoices and grouped operational reports render predictably and
respect every relevant permission.

## Milestone 7 - TIDE Studio

- structured new-application proposal operations and semantic validation;
  **implemented without source writes**
- isolated new-application candidate-tree materialization, normal compiler,
  generated default views, bounded static and isolated in-memory CRUD/security/
  action/idempotency/report/HTML/optional-PDF checks, exact artifacts/digests/
  diff, and proposal/base/candidate fingerprints; **implemented without apply**
- actual destination/stale-base detection, candidate-bound approval challenge,
  interactive local apply, atomic new-tree publication, failure cleanup, and
  an approval/artifact receipt; **implemented for new applications; developer
  MCP remains no-write pending a host-level human-approval contract**
- headless DesignerService and command model; **implemented with semantic
  document references, bounded typed property/order/sequence commands and
  atomic command batches**
- undo, redo, validation, source diff, and save; **implemented through exact
  comment-preserving candidates plus a separate local save service with live-
  base checks, exact interactive approval, exclusive locking, staged compiler
  verification, per-file atomic replacement, rollback and receipts**
- interrupted-save recovery journal, OS-lock ownership detection, hash-derived
  rollback/finalize preview, exact approval and resumable recovery command;
  **implemented**
- Textual application/entity/view/report/source tree, nested typed scalar
  property editing, locked structural/identity rows, compiler validation,
  undo/redo, diagnostics, exact unified-diff review and line-numbered YAML
  preview; **implemented in memory as the separate `tide studio` developer
  screen; approved persistence is not connected yet**
- schema-aware property editors generated from the authoritative metadata
  contract: dropdowns for `Literal`/enumerated values such as field type, view
  kind and delete behavior plus Boolean selection; **initial schema-derived
  choice/Boolean controls implemented**; richer numeric controls,
  path/reference selectors, descriptions and required/conditional-property
  hints remain;
- terminal-theme-aware YAML syntax coloring using Textual's optional syntax
  support and YAML parser with a plain-text fallback; **implemented for the
  YAML source view through the `studio` extra**; dedicated unified-diff coloring
  remains;
- source-panel search with `Ctrl+F`, case-insensitive next/previous match
  navigation, visible match counts and selection highlighting across YAML,
  diff and diagnostics; **implemented**;
- explicit expert YAML edit mode backed by a bounded whole-document Designer
  command: edit only the in-memory candidate, parse strict YAML, recompile,
  show diagnostics and exact diff, participate in undo/redo, and retain the
  same approval-required persistence boundary; never write directly from the
  text widget; **implemented with apply/cancel controls, `Ctrl+S`/`Esc`, stable
  document-identity enforcement and shared history**;
- structural TUI view designer;
- role and terminal-size previews;
- developer MCP designer tools;
- browser page canvas for report design.

## Later possibilities

- dedicated web application renderer;
- responsive web view designer;
- reusable application modules and plugin packaging;
- TUI report band editor;
- user-level permitted view preferences;
- public module or application repository;
- source generation only if runtime metadata proves insufficient;
- alternative runtimes only after the model contract is stable.
