# Changelog

TIDE has not cut a release yet. What follows is the running account of what has
been built, moved here from the README, where it had accreted a paragraph at a
time until the front door was mostly status.

`docs/ROADMAP.md` is the forward view and `docs/DECISIONS.md` records why things
are the way they are, with dates.

## Unreleased — metadata v0.1

Metadata v0.1 is an executable experimental contract. Breaking authoring changes
require a new `schema_version`; stable 1.0 compatibility is not yet promised.

### Compiler and headless runtime

Here, "compiler" means a **metadata compiler**, not native executable or Python
bytecode compilation. It turns an application's YAML into a validated,
resolved, immutable `ApplicationModel`; production still runs the ordinary
Python TIDE runtime and application handlers.

The compiler currently provides a strict versioned source schema, duplicate-key
detection, source-located diagnostics, project path confinement, cross-file
relationship and view resolution, safe expression validation, computed-cycle
detection, JSON Schema export, and immutable normalized model output.

An action may declare a `transition` over a `choice` field. The compiler
derives the action's state guard and, where a transition sets `locks_record`,
the `immutable_when` of every ordinarily writable field; it checks stamp
targets rather than writing them, and refuses a declared state no transition
reaches. That last check found one: `sales.Invoice` declared `cancelled` with
no action producing it, while the demo data seeded a record already in it, so
the reference application gained the `void` action that makes the state
reachable -- `void` and not `cancel`, because the form action bar owns that
name and an entity may no longer take it (`TIDE276`). The generated Contacts application emits the block from its plan
instead of a hand-written guard string.

The headless runtime adds secured record/query/action services, a repository
protocol with in-memory and synchronous SQLAlchemy Core implementations,
`RecordSession`, computed master-detail values, field protection, validation,
action-owned state, idempotency, and optimistic concurrency. Managed SQLite
schema creation and legacy no-DDL mappings are executable. SQL predicate,
reference-path, and single-collection aggregate row policies are pushed into
root queries. SQL Server schema/query compilation and an opt-in live integration
suite establish it as the first multi-user target. Secured keyset pagination
uses opaque, principal-bound continuation cursors with matching behavior in the
in-memory and SQLAlchemy adapters. Action idempotency plus action/CRUD audit
state now share storage-neutral contracts with in-memory and explicitly managed
SQLAlchemy implementations; protected change values are redacted and
interrupted reservations fail closed instead of executing a handler twice.
Opt-in REST deletion now crosses the same service boundary, with
explicit permission/exposure, row-policy and version enforcement, stable
reference conflicts, and transactional relationship behavior in memory,
managed SQL, and legacy no-DDL SQL.

Repository transaction scopes now keep SQL ownership in the current execution
context rather than one mutable thread identifier, so concurrent requests
cannot overwrite each other's bypass protection. The yielded repository still
joins nested work and the original repository is still refused inside its own
scope.

Milestones 0 and 1 are substantially implemented, and the secured application
core milestone is complete. The v0.1 compiler, resolved-view provenance, typed
expressions, headless services, in-memory and SQLite repositories, tests, and
executable invoicing workflow are implemented. Direct, reference-path, and
single-collection aggregate SQL policy translation and secured keyset
pagination are executable. Collection hydration now applies source-field,
target-entity, and target-row authorization through bounded relationship load
plans. Durable action reservations plus channel-aware action and CRUD audit
rows are implemented for memory and SQLAlchemy stores. SQL Server dialect
compilation is covered, with live certification available through an opt-in
integration suite.

Shared SQLAlchemy cursor storage preserves exact typed continuation state across
runtime restarts and processes while storing only hashes of bearer tokens. An
adapter-independent, read-only OpenAPI 3.1 preview now generates typed
Pydantic record/page schemas and explicitly exposed list/get contracts.

### Terminal client

The initial Textual adapter now interprets resolved browse and
form metadata for secured create/edit, inline InvoiceLine editing, validation,
cancel/save, optimistic-concurrency feedback, and audited invoice posting. It
can now select an explicitly configured SQLAlchemy deployment repository;
managed deployments use durable cursor, idempotency, action-audit, and
record-audit stores.

Stale TUI edits open a three-way Original/Current/Your draft review. Users
may reload, continue inspecting their draft, or explicitly choose Current/Mine
for every overlapping field before rebasing. Non-conflicting draft fields are
retained automatically, while newly immutable workflow fields are never carried
forward.

The invoicing TUI also provides Invoice, Customer, and Product workspaces,
nested create-and-select lookups, and confirmed, permission-driven Customer and
Product deletion with readable reference-conflict feedback in local or remote
mode. An explicit deterministic Faker seeder supports empty managed development
databases. The selected Invoice can now be rendered
through a secured report service into a Textual preview, controlled CSV,
standalone HTML, or an A4 PDF with shared field formats. A second bounded
posted-sales report groups authorized invoices by Customer/Currency and
calculates invoice count and Decimal sales totals.

### Qt client (retired 2026-08-10)

This renderer was removed. It is recorded here because it existed for most of
the project's life and shaped several contracts that remain; see the decision
log for why it went and what was kept. Everything below is past tense.

The optional PySide6 client reached full parity on the reference application
over the typed FastAPI client, with no local database dependency: cursor-backed
browse, search and sort; per-principal column layouts; a shared-YAML grouped
sidebar with retained per-view workspace state; metadata-driven Customer,
Product and Invoice forms; debounced multi-column lookups with authorized
nested **Save & Select**; transactional InvoiceLine drafts; three-way
Original/Current/Draft conflict review; the capability-gated Post action with
save-before-action ETag chaining and idempotency; and record plus summary
report preview with controlled CSV, HTML and PDF export.

It was removed on 2026-08-10, in one commit of roughly 10,000 lines. The
contracts it forced into existence stayed: the versioned presentation manifest,
renderer-neutral form-layout resolution, the shared field-state and three-way
conflict contracts, and batched reference displays on `QueryPage`.

### Web renderer

The Web renderer uses TIDE-owned username/password sign-in by default, backed
by a separate local identity file rather than application or legacy-database
tables. The first `start.bat web` or `web-demo` run prompts for the local
`admin` password. Optional provider-neutral OIDC remains isolated for future
deployments, but no third-party login is required. See
[Web authentication](docs/WEB-AUTHENTICATION.md).

Password work-factor upgrades now compare-and-swap the hash they verified, so
an administrator's concurrent reset always wins. Concurrent requests crossing
a session's revalidation boundary reuse the first refreshed result rather than
turning the second request into a false expiry. Windows identity-file ACLs are
granted to the process token's SID rather than the mutable `USERNAME`
environment value, and access is checked after hardening.

Form fields are no longer cards. Each one used to be a bordered, padded box
around its control — 84px of row to show a 36px input — so a thirteen-field
invoice was taller than a 1440×900 screen and its lines collection sat
permanently below the fold. A field now spends 24px on its label and gap and
nothing on packaging: the same invoice went from about 640px of rows to 424px,
and the collection plus the line editor beneath it are visible without
scrolling. A writable field is an input and a locked one is text, which is a
plainer read-only signal than a filled box and costs no height. The rule was
written twice, in the editable and read-only renderers; it is now one module
both import.

A Designer save no longer leaves the file carrying the timestamp of the file
it replaced. Permissions still carry over; `shutil.copystat` was copying mtime
with them, and git compares size and mtime before content — so a save that did
not change a file's length was invisible to `git status`, to `git diff`, and
even to `git add`. Found by reordering a row through Studio and finding a
clean tree afterwards. Receipts a save writes beside a checked-in application
are now ignored as `**/.tide/designer/`.

The browse grid is one tab stop instead of one per row. Every rendered row
carried `tabIndex={0}` and no key moved between them, so a keyboard user paid a
tab stop per visible row and — because the list is virtualized — could not
reach a row outside the rendered window at all. It now follows the ARIA grid
pattern: the selected row owns the tab stop, Up/Down move it, Home and End
reach the ends of what is loaded, and moving it selects, so `Open` and the
record pane follow the caret the way they already follow a click. The tab stop
is derived from the selection rather than stored beside it, so the two cannot
disagree. Found by driving the built renderer from the keyboard; every unit
test and journey passed with it broken.

`docs/WEB-UI.md` carries the current feature list.

Seven Playwright journeys run against a real `tide serve` hosting the built
bundle at its own origin: password sign-in, browsing, a record's nested lines,
create/edit/reload, drafting an Invoice through both lookups and posting it,
report preview and export, and a two-tab stale-edit conflict. They replaced a
single smoke test against a static copy of `dist/`. An eighth check measures
form density in a browser, through both a draft and a posted invoice so that
each renderer is covered.

A ninth drives the browse from the keyboard alone -- tab in, arrow down, Enter
-- and asserts that one more Tab leaves the rows, which is the claim jsdom
cannot make and the one the roving tab stop exists for.

### Machine interfaces and AI-assisted generation

`tide serve` requires a 32-character-or-longer development bearer token in
`TIDE_API_TOKEN` and binds to loopback. The Windows `start.bat api-demo`
shortcut generates one for local testing and prints the `/docs` address.
The separate `start.bat api-check` command securely prompts for that printed
token and verifies authentication plus application/wire compatibility through
the reusable remote client. `start.bat remote` then runs the same Textual
workflow through that API without giving the TUI a database connection string.
`start.bat mcp-demo` mounts authenticated schema/record/audit resources,
structured search and explicitly exposed CRUD tools, plus the Invoice Post
domain action at `/mcp`. They reuse the same service authorization, generated
inputs, protected values, exact types, concurrency, idempotency, correlation,
audit history, and principal-bound cursors as REST.
Use `start.bat mcp` for the equivalent persistent local SQL Server host.

The separate `tide mcp dev` stdio server exposes compiled project resources and
can turn an AI-authored sequence of logical TIDE operations into a deterministic
approval-required application proposal. It can also render that proposal into
a deleted temporary tree, run the normal compiler plus bounded static contract
checks, generate default views, and exercise fixed transition/sequence
templates through isolated in-memory CRUD, authorization, action, report, HTML
and optional PDF checks. It returns exact artifacts, hashes and a diff, but has
no MCP-side apply/workspace-write or arbitrary code/path tool. An explicit
local `tide app apply` command can bind those values to an absent destination,
require the exact interactive approval challenge, and atomically publish a new
application with an audit receipt; it never edits an existing application. Try
the complete local client workflow in the
[AI-assisted generation tutorial](docs/AI-GENERATION-TUTORIAL.md), and see
[AI-assisted application generation](docs/AI-APPLICATION-GENERATION.md) for the
architecture and security contract.

The repository now includes `applications/contacts`, a compact second
application backed by a checked-in structured generation plan. Its 12 generated
artifacts are compared byte-for-byte with a fresh no-write candidate, while the
real approval/apply boundary is exercised only in a temporary workspace.
Application-owned deterministic demo and Faker providers support Companies,
Contacts, references, editor/viewer roles, and an idempotent Archive action.
The same browse/form contract is resolved through shared, Textual, and Web
entry points, and the service workflow is certified against both in-memory and
managed SQLAlchemy storage. Windows shortcuts expose its TUI, Studio, Web,
REST, and runtime-MCP surfaces.
The generic seed command now requires an explicit application `--role`; the
remaining Invoicing-specific `sales_clerk` default was removed, and the Windows
Invoicing shortcut now uses the generic repeatable `--count NAME=NUMBER` form.
Every application's local identity store is now ignored by pattern rather than
by name, so a new demo shortcut cannot leave a password hash in reach of
`git add`. The superseded `docs/examples/first-application` copy was removed;
[Build your first TIDE application](docs/FIRST-APPLICATION.md) now follows the
maintained `applications/contacts` sources directly.
The developer shortcuts take the application as an argument instead of existing
once per application: `start.bat demo contacts`, `start.bat web-demo contacts`,
and a single `npm run dev:app -- --app <name>`. The documented `contacts-*`
names still work, and a third application needs one settings block in
`start.bat` and no `package.json` entry at all.

### Designers and Studio

Existing applications now have a headless DesignerService with typed property/
order commands, atomic in-memory batches, compiler validation, exact comment-
preserving diffs and bounded undo/redo. `tide designer preview` remains no-
write; the separate interactive `tide designer save` command binds approval to
the canonical project path, live base, candidate and diff before transactionally
replacing only approved YAML files and recording a receipt. Saves now retain an
OS-owned lock plus a durable phase journal until cleanup. The read-only
`tide designer recover --preview` command inspects actual hashes; explicitly
approved recovery either restores the original YAML set or finalizes an already
receipted save. See
[Designers and reporting](docs/DESIGNERS-AND-REPORTING.md).

The first visible TIDE Studio slice can now be opened with `tide studio`. It is
a separate Textual developer screen with an application/entity/view/report/
source tree, nested scalar property inspector, YAML source, compiler diagnostics
and exact unified-diff views. Editable scalar leaves use typed in-memory
Designer commands. Schema `Literal` values such as field type and Boolean
properties use generated selection controls. The YAML source is syntax-colored
through the `studio` extra, and `Ctrl+F` searches YAML, diff, or diagnostics
with highlighted next/previous matches. **Edit YAML** provides an explicit
expert buffer; `Ctrl+S` applies strict YAML to the in-memory candidate, `Esc`
cancels it, and semantic identity changes are refused. Container,
schema-version and semantic identity property rows remain locked. Apply, undo
and redo recompile the candidate without writing source or opening the
application database. **Save candidate** opens the exact diff and changed-file
review, requires the complete evidence-bound approval phrase, and only then
invokes the transactional YAML-only `DesignerSaveService`. Stale sources and
active/interrupted save locks fail closed with recovery-preview guidance. On
view documents, a resolved TUI structure panel now shows table/lookup columns
and form/inline left-right field tracks with their metadata origin. **Move up**
and **Move down** reorder fields within a track through atomic Designer
commands. Same-position **Swap left/right** controls preserve YAML group
boundaries, while an entity-field chooser can add local placements and
**Remove field** removes only the view placement. Inline membership updates its
table columns and editor layout atomically. Form/inline additions now use an
explicit destination-group selector; **Groups…** creates, renames, reorders,
and removes empty local groups without crossing collection sections. Every
operation immediately recompiles and refreshes the diff and preview. On
Windows, `start.bat studio` opens the bundled invoicing project directly.
Closing Studio discards only an unsaved candidate.

The first Studio tranche is now hardened: hidden-field behavior matches the
live browse/form runtime, compact terminals scroll instead of clipping tools,
and invalid view candidates retain an explanation while designer actions fail
closed.

Authoring metadata in an ordinary editor is now the documented path. The eight
exported JSON Schemas are checked in under `schemas/`, `.vscode/settings.json`
maps every application path to the right one, and any editor with JSON Schema
support can read the same files. Wiring it up found that the `transition` block
advertised `from` as a list while the loader also accepts a scalar — the
spelling both applications use — so an editor would have marked every action in
the repository invalid; the export now describes the accepted input. Three
tests hold it there: a checked-in schema must equal a fresh export, the
editor's globs must classify exactly what each `tide.yaml` declares, and every
checked-in document must validate against the schema it maps to.

Studio's own layout now fits the terminals the rest of the TUI is certified
for. Its view-structure table asked for a fixed 79 columns and was given ten
at 100×30, so every row of a browse view rendered as the same truncated
`Table c`; it now takes the columns that fit, most useful first — field name,
position, type, origin, label — and `Track` leaves the row for the heading
beside the table, where it names the selected field's track once instead of
repeating it down a contiguous run. Below 125 columns the table and its side
panel stack rather than splitting a pane too narrow for both. The action
toolbar is docked, so selecting a view no longer pushes Diagnostics, Edit YAML
and Save candidate past the bottom of the screen — which "compact terminals
scroll instead of clipping tools" above did not in fact cover, because a
toolbar is not content and two of those buttons have no key binding.

Studio sessions now reuse the compiled evaluation and semantic document index
for an unchanged candidate fingerprint. The cache is bounded by the same
history limit as undo/redo, candidate mutations refresh only the affected
state, and semantic identity changes still force a complete re-index. Repeated
panels and previews therefore no longer rematerialize the same temporary
project or reparse every YAML document.

### Databases and operations

`tide run --database-env` selects a persistent SQLAlchemy repository using the
`TIDE_DATABASE_URL` environment variable. The first managed-database run may
add `--create-schema`; later runs omit it. Database URLs and credentials remain
outside application metadata and command output. `tide db check` (or
`start.bat check` on Windows) performs a read-only connectivity, schema,
durable-state, and SQL-policy acceptance check. See
[Microsoft SQL Server](docs/SQL-SERVER.md#run-the-tui-against-sql-server).

Path-based SQLite deployments can use `tide db backup` to create a verified,
non-overwriting online snapshot plus SHA-256 manifest and
`tide db verify-backup` to recheck it. SQL Server uses native DBA-managed
backup and a real isolated restore followed by `tide db check`; see the
[recovery runbook](docs/OPERATIONS.md#database-changes-and-recovery).

`tide db diff` adds a deterministic, read-only schema proposal for managed
databases and a no-DDL compatibility report for legacy mappings. It classifies
changes, fingerprints the result, recognizes compiler-validated explicit rename
declarations without guessing, and performs no DDL. Exact reviewed fingerprints
can produce a non-overwriting Alembic-compatible revision plus SHA-256 manifest;
the optional migration adapter verifies that artifact and renders dialect SQL
without a database connection. TIDE still cannot apply it. See
[Schema migrations](docs/MIGRATIONS.md).

### Documentation and screenshots

The README shows all three visible surfaces rather than only the terminal:
browse, editor and lookup from the Textual client, browse and record editor
from the Web UI, the generated OpenAPI description, and Studio editing a view.

Every one of them is generated. `tools/capture_screenshots.py` drives the real
`TideApp` and `StudioApp` through the same headless pilot the Textual suites
use and exports the SVGs; `npm run screenshots` in `web/` stands up the server
the end-to-end journeys use, signs in through it, and writes the PNGs. The
first set, captured by hand in July, had no way to make another and had gone
six weeks stale. Neither command runs in CI: both write into the working tree.

Capturing the API description found that it cannot be read in a browser under
the configuration the Web UI documentation describes. `tide serve --auth local`
sends `script-src 'self'`, and FastAPI's Swagger UI is a CDN script tag plus an
inline initialiser, so `/docs` returns 200 and renders an empty page. The
existing exposure tests assert the status code, which is true either way. The
screenshot is therefore taken against the quick start's bearer-token server,
which sends no browser security headers; a fix is not yet chosen, because every
option trades the policy, a vendored copy of Swagger UI, or the CDN against
each other.

`tests/test_launcher_contracts.py` finds the Node scripts that compose a
`tide serve` by searching `web/` rather than by naming them, so the third one
was covered on arrival. It walks with `node_modules` pruned from the descent:
the design-sync setup leaves a junction inside it pointing back at `web/`, and
a recursive glob that filters the results instead of the walk recurses until
Windows refuses the path.

### Not yet

Shared encrypted multi-worker browser sessions, provider-wide logout, trusted
reverse proxies, richer report parameters/group bands, and broader
lookup-query capabilities remain roadmap work.

The Web UI is not yet usable on a phone, which matters because it is the only
surface that runs on one. At 375px the record action bar places its four
buttons past the right edge of the viewport with nothing to scroll to them.
Tablet width is sound. See the decision log for the requirement.
