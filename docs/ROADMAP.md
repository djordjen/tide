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
- explicit source-schema version, JSON Schema export, and stable diagnostic
  codes; **2026-08-11**: the eight schemas are checked in under `schemas/` and
  wired to VS Code, so authoring application YAML in an editor validates and
  completes against the real contract;
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
- declared state transitions on an action, with the state guard and
  `immutable_when` derived from the machine, stamp targets checked, and a
  declared state no transition reaches refused; **implemented 2026-08-10**,
  including the generator emitting the block instead of a guard string.
  Deliberately a transition table on one choice field, not the general-purpose
  workflow language the decision log defers

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
service validation. Stale edits now open an Original/Current/Draft review with
safe reload, automatic non-overlapping-field rebase, and explicit Current/Mine
selection for every overlap in local and remote TUI modes; permissions and
workflow immutability are reevaluated before rebasing. Product references now
support a secured,
case-insensitive, multi-column lookup window and declarative selection values
that copy description and unit price into the editable line draft. Inline
editor layout and keyboard order are now developer-controlled independently of
the collection table. `tide run` can now select and validate a persistent
SQLAlchemy deployment repository from secret-safe environment configuration,
including durable framework state for managed databases. A compiler-validated,
secured record-report service now drives TUI invoice preview plus standalone
HTML and A4 PDF export.
The same TUI now navigates Invoice, Customer, and Product workspaces, supports
secured master-data create/edit/delete, confirms destructive operations,
explains reference restrictions, and can create a missing reference from inside
a lookup with **Save & Select** while preserving the invoice draft. Local and
remote Textual modes route deletion through the same service/API boundary.
Managed development databases can be populated deterministically through an
application-owned Faker profile and the real secured services.

- generated browse, edit, and lookup views;
- view-level field ordering for form and inline editor controls, independent of
  collection-table column order (for example, Product before Description in
  InvoiceLine details); **implemented for the Textual invoice workflow**
- Customer and Product browse/edit/delete workspaces and create-enabled
  lookups; **implemented with permission/action visibility and confirmation**
- transactional Invoice/InvoiceLine master-detail editing; **implemented for
  Textual and Web**
- selectable in-memory or SQLAlchemy runtime persistence, with explicit managed
  schema creation and legacy no-DDL behavior;
- deterministic, empty-database-only Faker development seeding;
- parsing, formatting, and validation feedback;
- compiler-validated numeric and regular-expression edit masks, with shared
  service enforcement; **implemented for Textual and OpenAPI contracts**
- computed line and invoice totals;
- Post Invoice action and immutable posted invoices;
- configurable keyboard shortcuts and mouse-aware controls;
- compact/standard/wide terminal acceptance at 80×24, 100×30, and 140×40,
  including reachable actions, compact scrolling, and mixed wide/combining/RTL
  browse text; **implemented**; Windows Terminal/xterm/SSH and reduced/no-color
  release certification remain;
- server-side sorting, seamless incremental cursor loading, search, and named
  filters; **implemented for Textual and Web browse**
- compiler-validated shared application navigation in presentation YAML,
  capability filtering, Textual selector ordering, and `--view` deep links;
  **implemented**, including the authenticated Web presentation projection
  and browser shell
- renderer-neutral form-layout resolution for ordered YAML rows, groups,
  collections, tabs, actions, and field visibility, consumed by Textual and
  Web; plus an **Open** path whose form controls follow capability and
  workflow state; **implemented**, including Web detail collections and
  current-query Previous/Next navigation
- a PySide6 desktop renderer reached full Invoice editing, lookups, conflict
  review, actions, and report preview, and was **retired on 2026-08-10**. It
  was remote-only, so it was never an offline story; it made every parity
  feature a third implementation; and the Web shell surpassed it on both
  appearance and flexibility. What it forced into existence -- the presentation
  manifest, renderer-neutral form layout, the shared field-state contract, and
  batched reference displays -- is core and stays. See the decision log.
- basic secured printable invoice with TUI preview, HTML, and PDF;
  **implemented**
- opt-in REST list/get/create/update/delete and domain-action routes plus OpenAPI;
  **implemented with FastAPI hosting, local development and OIDC/JWKS bearer
  identity, direct TLS enforcement for non-loopback binds, opaque pagination,
  protected-field serialization, ETag concurrency, reference-safe deletion,
  and action idempotency**

Exit condition: the example application can create, edit, post, find, and print
an invoice entirely by keyboard or mouse.

## Milestone 4 - Machine interfaces

- opt-in generated REST delete routes with explicit permission/exposure,
  stable restrict conflicts, and transactional cascade/set-null behavior;
  **implemented across memory, managed SQL, and legacy no-DDL SQL**
- expected-version preconditions for versioned delete/update/action operations;
  **implemented**
- API structured filtering/sorting and concurrency transport contracts over
  the implemented pagination and protected-field primitives;
- typed remote HTTP client, authenticated session capabilities, application
  compatibility checks, exact wire-type/protection conversion, and a CLI
  connectivity check; **implemented**
- structured remote filtering/sorting plus Textual record/action service
  facades for browse, lookups, drafts, nested commits, concurrency, and
  actions; **implemented**
- secured renderer-neutral remote report transport with Textual preview and
  local CSV/HTML/PDF export; **implemented**
- runtime MCP schema/record/audit resources, structured query tools, CRUD
  mutations, and opt-in domain actions; **implemented with authenticated
  stateless Streamable HTTP, explicit metadata exposure, RFC 9728 metadata,
  DNS-rebinding controls, service reauthorization, strict generated inputs,
  exact protected wire values, bounded pages, principal-bound cursors,
  optimistic concurrency, action idempotency, correlation, and audit history**
- shared authentication-to-Principal adapters; **implemented for local
  development tokens, provider-neutral OIDC/JWKS access tokens, and optional
  same-origin Web Authorization Code/PKCE sessions with opaque cookies,
  server-held refresh, and CSRF protection**
- channel-aware audit events;
- OpenAPI and MCP schema conformance tests; **implemented for the current REST
  and secured runtime MCP read/write/action surfaces**
- authenticated, versioned, capability-filtered presentation manifest for
  remote renderers, containing safe application navigation and browse/query
  metadata without YAML, permission expressions, handlers, or database
  configuration; **implemented**
- first dedicated Web application shell using the manifest plus existing
  server-mode REST search/filter/sort and opaque-cursor loading; **implemented,
  including responsive grouped navigation, TanStack virtualization, automatic
  incremental fetch, safe reference display, exact formatting, personal
  column order/widths, Best Fit/Fill/Reset, and same-origin static hosting**
- production Web identity without credentials in React; **implemented first
  with a framework-owned username/password store that is separate from the
  application database, administrator-assigned application roles, versioned
  salted password hashes, bounded opaque HTTP-only sessions, CSRF, restored
  sessions, local logout, and simple CLI/bootstrap management. Provider-neutral
  OIDC remains an isolated optional adapter for later deployments; it is not a
  prerequisite for TIDE. Shared multi-worker sessions and trusted reverse-proxy
  hosting remain later work.**
- Web detail view using the shared form layout, read-only states, collections,
  and current-query Previous/Next navigation; **implemented**
- Web editing parity in reviewed slices: CRUD, lookups and **Save & Select**,
  collection drafts, actions, conflicts, and reports; **flat scalar
  Customer/Product create/update and Invoice Customer lookup/Save & Select are
  implemented, together with transactional InvoiceLine Add/Apply/Remove and
  Product lookup/Save & Select** with typed controls,
  defaults, masks, field-addressable validation, changed-field PATCH, and
  conditional ETag preconditions; lookup search uses readable metadata columns
  and the server-owned reference-selection operation; nested line changes use
  one complete ETag-protected parent update and server-owned recomputation;
  **three-way stale-conflict review is implemented** with explicit
  Original/Current/Draft comparison, Current/Mine overlap choices,
  collection-as-one-unit semantics, workflow-lock reevaluation, and review on
  the fresh ETag before a separate save; **metadata-driven domain actions are
  implemented**, including server-evaluated per-record state, save-first
  chaining onto the returned ETag, per-attempt idempotency keys, and in-place
  post-action state refresh; **Web record/summary report preview and controlled
  CSV/HTML/PDF export are implemented** through the server-built
  renderer-neutral document and authorized export routes
- executable renderer acceptance matrix for shared TUI/Web semantics;
  **implemented** with a versioned YAML matrix, a compiled Invoicing golden
  contract resolved through each renderer's own entry point, and Python
  evidence links resolved by importing the named test rather than matching its
  source text; web evidence remains a source match, because the Python suite
  cannot ask vitest what ran;
  the matrix records the closed Web scalar-lookup, master-detail, conflict,
  action, and report gaps explicitly, and its own checks are exercised against
  deliberately broken input

Exit condition: TUI, REST, and MCP produce equivalent secured outcomes through
the same services.

## Milestone 5 - Production data and security

- read-only SQL Server operational acceptance (`tide db check` and
  `start.bat check`) covering connectivity, application/system schemas, and
  SQL policy translation without exposing the URL; **implemented and passed
  against the local Windows-integrated MSSQL database**;
- complete multi-version live SQL Server certification and operational guidance;
- additional SQLAlchemy dialect certification for legacy databases, based on
  demand and dialect availability;
- deterministic read-only managed migration proposal with application/runtime
  table reflection, safety classifications, stable fingerprint, JSON/CI output,
  and legacy compatibility-report behavior; **implemented as `tide db diff`;
  approval-bound Alembic revisions plus verified driverless offline SQL and
  manifests are also implemented. This is the early-stage stopping point;
  revision lineage, signed review receipts, rehearsal/apply, and automated
  rollback are deferred until real deployment demand justifies them**;
- stable schema identity plus explicit, no-guess table/column rename handling
  in read-only proposals; **implemented; rename execution and destructive
  approval remain**;
- interactive conflict inspection and permitted field-level merge assistance;
  **implemented through shared three-way comparison/resolution contracts plus
  local/remote TUI reload, safe-field rebase, and explicit Current/Mine choices**;
- roles and permissions administration; **implemented for the identities
  TIDE owns**, as a reserved `tide.users.administer` permission granted
  through an ordinary role, REST routes that list the compiled roles and
  administer account assignment, and a browser screen. Roles and their grants
  stay compiled -- what is administered is which account holds which declared
  role, and whether it may sign in. Under an identity provider the provider
  administers, and the capability reports itself unavailable rather than
  failing when used;
- permission-gated action and CRUD audit history through local/remote TUI and
  REST, with safe protected logging; **implemented for domain-action lifecycle
  events and successful root create/update/delete changes; collection-detail,
  retention, purge, and broader MCP/report/export audit remain**;
- controlled export; **implemented for reports as authorized CSV/HTML/PDF
  routes**. Bulk import is **deferred on 2026-08-10**: TIDE reads databases it
  does not own, so rows already arrive through the database itself, and the
  loading tools an operator has do not need a metadata framework in front of
  them. See the decision log for what that costs;
- deployment configuration, SSH guidance, and container packaging.
- process-only liveness plus dependency-aware persistence/durable-state
  readiness with safe 200/503 responses; **implemented**;
- correlated secret-safe JSON request/readiness logging shared with REST/MCP
  service audit, including bounded `X-Correlation-ID` propagation and disabled
  duplicate access logs; **implemented**;
- shared REST/MCP request-body and concurrency caps, idle keep-alive and
  graceful-shutdown limits, bounded body-receive deadlines, safe correlated
  408/413 responses, disabled server-identification and forwarded-header
  processing, and OpenAPI limit disclosure; **implemented**;
- reviewed proxy allowlists; **implemented as `--forwarded-allow-ips`, which
  names the peers whose `X-Forwarded-*` headers are believed and refuses the
  `*` uvicorn would accept. External TLS is the separate `--behind-tls-proxy`
  declaration and is never inferred from a header: it makes the session cookie
  `Secure`, allows a routable bind without a certificate, and switches the API
  description off by default, because a loopback bind stops meaning "only this
  machine" the moment a proxy forwards to it**;
- request-rate policy, and dialect-certified statement timeout/cancellation
  behavior;
- shared browser-session storage and multi-worker/session-instance
  coordination; **implemented for the password and development modes** as a
  store contract with the process-local dict kept as the default and a
  SQLAlchemy implementation on the application's own engine, carrying the
  failed-login counters with it so a per-process budget cannot silently become
  a per-worker one; a managed database gains the two tables from
  `--create-schema` and a legacy database keeps process-local sessions.
  `tide serve` still runs one uvicorn process -- uvicorn spawns workers only
  from an import string -- so the shape this serves is several processes behind
  a proxy;
- encrypted browser-session state at rest, which is what OIDC needs before it
  can share a store: its sessions hold the provider's access and refresh
  tokens, and it keeps its single-process constraint until then. **That
  constraint is now enforced rather than described**: a managed database
  carries a server lease, a second OIDC process refuses to start and names the
  incumbent, and the lease expires on its own so a killed server does not have
  to be cleaned up by hand. Where sessions stay in one process the cookie also
  carries a stamp naming it, so a request that reaches a sibling is answered
  `401 session_from_another_server` rather than a bare 401;
- provider-wide logout/revocation and reviewed session-key rotation;
- verified, non-overwriting online backup plus manifest/integrity/application
  checks for path-based SQLite, and a native SQL Server isolated-restore and
  migration-recovery runbook; **implemented for this initial operator contract;
  automated SQL Server backup is deliberately not application authority**;

Exit condition: multiple users can safely work against certified SQL Server
deployments and receive clear concurrency feedback.

## Milestone 6 - Reporting

Status: **the grouped, parameterized summary slice is implemented**. Invoicing
now has a secured, bounded posted-sales listing grouped by Customer/Currency
with per-group subtotals and grand totals, optional date-range parameters
prompted for in the terminal and collected in the browser through the
manifest's parameter metadata, local/remote Textual preview, REST transport
with the Web preview rendering the same bands, and controlled CSV export that
re-flattens groups into leading columns. Richer page behavior, spreadsheet
formats, report MCP actions, and designer tooling remain.

- stable declarative band model;
- parameters, groups, totals, headers, and footers; **implemented: typed
  optional/required parameters validated once in the report service (an
  unsupplied optional parameter drops its criteria clause), `columns:` turning
  a summary into a grouped listing whose `group_by` runs head their own rows
  and close with the aggregates as subtotals, and the same aggregates totaling
  the report at the foot. The presentation manifest now carries each summary
  parameter's name, label, type and required flag -- required meaning "the
  caller must supply this", since a declared default satisfies the service on
  its own -- so the browser asks with a form the way the terminal asks with a
  modal. A parameter narrowing by reference stays out until reference-typed
  parameters are their own contract decision (TIDE306)**;
- HTML preview and PDF output;
- page behavior and repeatable-band tests;
- CSV export; **implemented for renderer-neutral detail tables; a grouped
  listing exports flat with the group values repeated per row, because a
  spreadsheet pivots for itself**
- spreadsheet export;
- report actions through TUI, REST, and MCP where exposed;
- initial report property editor and preview tools.

Exit condition: invoices and grouped operational reports render predictably and
respect every relevant permission.

## Milestone 7 - TIDE Studio

Status: **the initial Textual Studio/view-designer tranche is complete**. The
headless contracts, safe candidate lifecycle, structural editing, role/terminal
preview, and compact-terminal/invalid-candidate hardening are implemented.
Deeper report design and developer-MCP editing remain later Milestone 7 work.

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
- second-application portability proof generated from a checked-in structured
  plan: Company/Contact reference model, editor/viewer roles, idempotent Archive
  action, deterministic demo and Faker data, exact artifact-regeneration check,
  temporary-workspace approval/apply test, managed SQL storage, and shared
  TUI/Web resolution; **implemented as `applications/contacts`**
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
  preview; **implemented as the separate `tide studio` developer screen**
- Studio save review showing the exact diff, changed YAML files and
  candidate-bound approval phrase, followed only by transactional
  `DesignerSaveService` execution, clean-session reload, receipt reporting,
  stale-base refusal and recovery-preview guidance; **implemented**
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
- structural TUI view designer; **initial resolved table/lookup columns and
  form/inline left-right tracks, source origin, group-bounded move-up/down and
  same-position left/right swaps, local entity-field add/remove, live preview,
  compiler validation, diff, undo/redo and approved save integration
  implemented**, including explicit add destinations and local group
  create/rename/adjacent-reorder/empty-remove, plus portable tab assignment,
  complete group/collection section reorder, compatible collection add/remove,
  record/collection action-bar ordering and real Textual tab/button rendering;
  unmatched-cell transfers remain;
- role and terminal-size previews; **implemented with shared entity/field/
  action permission resolution, record-dependent-state markers, 80×24,
  100×30 and 140×40 exact-width canvases, layout-fit warnings, and no database
  or application-code execution**;
- Studio usability and semantic hardening; **implemented with live/preview
  parity for hidden browse, form, and collection placements, scrollable compact
  layouts, a usable YAML minimum height, and visible fail-closed explanations
  for invalid view candidates**; **2026-08-11**: Studio's own layout now fits
  80×24, 100×30 and 140×40 — the structure table drops columns in priority
  order, `Track` became the heading beside it, the panel stacks below 125
  columns, and the action toolbar is docked. The property inspector at 80×24
  still puts its Value and Mode columns off the right edge;
- developer MCP designer tools;
- browser page canvas for report design.

## Later possibilities

- responsive web view designer;
- reusable application modules and plugin packaging;
- TUI report band editor;
- user-level permitted view preferences;
- public module or application repository;
- source generation only if runtime metadata proves insufficient;
- alternative runtimes only after the model contract is stable.

## Command-line direction

The shape the CLI is growing towards. Some of these exist today; see
[the changelog](../CHANGELOG.md) for which.

```text
tide new invoicing
tide model validate
tide model explain sales.Invoice.customer
tide view explain sales.Invoice.edit
tide api export-openapi
tide auth check-oidc
tide db check
tide db diff
tide db migrate
tide studio
tide run
tide serve
tide report preview sales.invoice
```
