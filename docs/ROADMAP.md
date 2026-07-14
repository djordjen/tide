# TIDE Roadmap

The roadmap favors a complete invoicing vertical slice over many disconnected
features. Designers and alternative renderers follow stable model contracts.

## Milestone 0 - Contracts and skeleton

Status: **in progress**. The package, CLI, typed v0.1 sources, project discovery,
strict diagnostics, tests, and compiler-ready invoicing fixture are implemented.
The Textual, SQLite, and developer MCP skeletons remain.

- living architecture documentation and decision log;
- Python package, CLI, configuration, and test skeleton;
- initial typed source metadata models;
- explicit source-schema version, JSON Schema export, and stable diagnostic codes;
- project/file discovery;
- basic Textual application shell;
- SQLite connection;
- invoicing project fixture;
- read-only developer MCP skeleton for project information.

Exit condition: `tide model validate` can load a small project and report useful
source locations and diagnostics.

## Milestone 1 - Model compiler and expressions

Status: **compiler contract complete; developer MCP pending**. Strict YAML,
normalized immutable entities, two-pass references, typed safe expressions,
cycle detection, layered view resolution/provenance, static handler checks,
presets, views, and reports compile.

- YAML loading with strict scalar behavior;
- normalized immutable `ApplicationModel`;
- namespaces and two-pass relationship resolution;
- defaults, presets, and overlay merge contracts;
- safe typed expression AST;
- local computed fields and cross-field validation;
- resolved-model and resolved-view explanation;
- developer MCP resources for entities, views, and diagnostics.

Exit condition: the compiler resolves Customer, Product, Invoice, and
InvoiceLine, including references and calculated line totals.

## Milestone 2 - Secured application core

Status: **in progress**. `RequestContext`, principals, role/permission expansion,
row and field policies, `ProtectedValue`, in-memory query/record services,
`RecordSession`, validation, stored master-detail computation, action execution,
idempotency, and optimistic concurrency are implemented. SQLAlchemy, durable
audit, and OpenAPI preview remain.

- query and record application services;
- `RequestContext` and principal abstraction;
- entity, row, field, and action policy interfaces;
- threat-model regression tests and cross-adapter authorization scenarios;
- `ProtectedValue` sentinel and secure projection;
- `RecordSession`, change tracking, commit, and rollback;
- integer version tokens and stale-commit rejection;
- first-class action registry;
- SQLAlchemy adapter and generated schema for SQLite;
- repository conformance tests for both managed and legacy database modes;
- legacy table/schema/column mapping, compatibility inspection, and a hard
  no-DDL guard for externally owned schemas;
- generated Pydantic/OpenAPI preview without mutation routes.

Exit condition: core behavior can be tested without Textual or FastAPI.

## Milestone 3 - Golden invoicing slice

- generated browse, edit, and lookup views;
- Customer and Product lookups;
- transactional Invoice/InvoiceLine master-detail editing;
- parsing, formatting, and validation feedback;
- computed line and invoice totals;
- Post Invoice action and immutable posted invoices;
- configurable keyboard shortcuts and mouse-aware controls;
- terminal compatibility checks at the documented viewport/Unicode/color matrix;
- sorting, paging, incremental search, and named filters;
- basic secured printable invoice;
- read-only REST endpoints and OpenAPI for selected entities.

Exit condition: the example application can create, edit, post, find, and print
an invoice entirely by keyboard or mouse.

## Milestone 4 - Machine interfaces

- opt-in generated REST create, update, delete, and action routes;
- required expected-version preconditions for update, delete, and targeted actions;
- API pagination, filtering, sorting, protected-field representation, and
  concurrency contracts;
- runtime MCP resources and read-only query tools;
- opt-in MCP domain actions and mutations;
- shared authentication-to-Principal adapters;
- channel-aware audit events;
- OpenAPI and MCP schema conformance tests.

Exit condition: TUI, REST, and MCP produce equivalent secured outcomes through
the same services.

## Milestone 5 - Production data and security

- PostgreSQL support;
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

Exit condition: multiple users can safely work against PostgreSQL and receive
clear concurrency feedback.

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

- headless DesignerService and command model;
- undo, redo, validation, source diff, and save;
- Textual model/view tree and property inspector;
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
