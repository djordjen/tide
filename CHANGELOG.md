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

A scalar field may declare `values:`, captioning the codes it stores. A legacy integer column usually carries an enumeration whose member names live in the application that wrote the rows, not in the database; this says what they are without changing what is stored. The column stays an integer in SQL, in filters and over REST, and the map decides two things: a reader sees the caption on every surface, and a writer may only choose a declared code -- refused in `RecordsService`, so it holds for the terminal, the browser, REST and MCP alike. An uncaptioned code is shown as itself rather than blanked, because a legacy column holds values nobody wrote down. The terminal and the browser both offer a dropdown of the captions without the field becoming a `choice`, and `tide db inspect --runnable` names each table's plain integer columns in a comment, since reflection cannot read an enumeration.

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

An action may declare `parameters:` — the typed input it needs at the moment
of execution, in the same scalar declaration reports use. One block lands on
all four surfaces: the REST invoke body is the parameters object itself, the
generated MCP tool grows a typed `parameters` argument (required in the tool
schema when any declared parameter is), and the Web and terminal renderers
open a dialog for an action with a required parameter while an optional-only
action stays one click. The `ActionService` owns the payload for every door —
string forms accepted, defaults filled, refusals gathered under
`action_parameter`, an undeclared action holding to the empty payload the
transports used to enforce one by one — and coercion runs before the
idempotency fingerprint, so two spellings of one request replay as one
request. Invoicing demonstrates both dialog rules: `void` requires a `reason`
recorded to the new `cancelled_reason` field, and `post` declares an optional
`occurred_at`, the seam the fake-data seeder always used to backdate posts
through the real pipeline, now a declared door for any caller holding the
permission. The `TIDE292` identifier gate covers report parameter names too:
a dashed name used to compile cleanly and crash `tide serve` at MCP startup,
since every declared name becomes a field on the generated tool arguments.

A column's filter popover speaks **operator modes by field type**: dates a
From/To pair, numbers Min/Max, text a Values-or-Contains toggle, and the
enumerable kinds keep their distinct-value checklist — one active mode per
column, an emptied popover releasing it. The query wire changes not at
all: the operators (`gte`, `lte`, `icontains`) were always in the
contract, and the funnel stops being the only caller that never emits
them. Saved views store the new kinds as `conditions` beside their
membership map, so a range relights its bounds on restore and a Home tile
counts the same window the browse shows. `datetime` columns deliberately
keep the checklist until aware-range semantics are settled.

A declared rule's severity now means something: **`warning` gates the
commit until acknowledged, `info` never gates**. Errors refuse as they
always did; a warning-only refusal lists its rule ids and the caller
resubmits naming the ids it accepts — the repeatable
`acknowledge_warnings` query parameter on the create, update and action
routes, the same-named argument on the MCP mutation tools. The commit
proceeds only if every warning raised is listed, so a warning appearing
between the two requests still gates, and acknowledgement is per request,
never stored. Info issues, plus the warnings that were accepted, ride the
success as `_tide.notices`. The browser renders the question as an amber
**Save anyway** panel rather than a red failure, the terminal as a
confirmation dialog, and an agent on MCP must name what it accepts — the
reason the gate is deliberately stricter than the XAF ancestor, whose
warnings never block: a warning attached to a success is a warning no
client is obliged to render. The severity had been schema-legal since the
initial commit and documented with an example, while the service
evaluated warnings and silently kept only the errors. Invoicing ships the
first rule: an unusually large line quantity warns at the invoice save,
and one acknowledgement covers every line it fired on.

The web lands on **Home**: a personal dashboard assembled from what the
principal keeps rather than declared in YAML. Saved views are tiles wearing
their live numbers — the browse's declared summaries, guaranteed a count,
asked of the ordinary query door under the saved components — and open
their browse with the whole state relit; the navigation becomes workspace
tiles; the summary reports become shortcuts into the existing preview. One
new REST door serves it: `GET /_tide/saved-views`, the principal's whole
catalogue with view names attached. Home keeps the clean URL as the
default landing, and an unknown view in a shared link now falls back to it
rather than to the first navigation item.

A record can be **duplicated** into a new unsaved draft of what a person
could have typed on the original: writable scalars, chosen references, and
owned collection rows minus their identities and computed values — never
identity, workflow state, stamps, file bytes, or anything field security
protects. The rule lives once in `RecordsService.duplicate_draft`; REST
serves it as `GET {resource}/{id}/duplicate-draft` wherever get and create
are both exposed, the terminal browse gains a Duplicate button beside Edit,
and the browser record's footer reopens the form in create mode seeded with
the draft, defaults intact for anything it does not carry. The draft goes
through the ordinary create, so the new record allocates its own number,
starts in the default state, and recomputes its own totals. MCP abstains.

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

An entity may declare `appearance:` rules -- what a record means on sight,
before anyone opens it. A rule is a boolean `when` over the entity's own
fields, an `emphasis` from a closed set (`info`, `success`, `warning`,
`danger`, `muted`), and optionally the `fields` it speaks for; naming none
speaks for the record. Rules are ordered and the first match owns a target, so
precedence is read off the page instead of assigned as a number. The compiler
checks the condition is a question rather than a value, that named fields
exist, and that no two rules share a name (`TIDE280`). Evaluation is
server-side, per record, beside the workflow locks and action guards, and the
verdict travels as `_tide.appearance` on record responses **and on query
pages** -- a rule that only shows its colour once the record is open is not a
warning. Nothing matched means nothing is sent, so an application with no
rules pays nothing.

A rule may also carry `enabled: false` or `visible: false`. `enabled: false`
locks what it speaks for -- a field, or every ordinarily writable field when
it names none -- and is enforced where `immutable_when` is enforced, so the
service refuses the write and REST and MCP honour it rather than the browser
alone; the locked fields simply leave `writable_fields`, so there is no second
list on the wire saying what the first already says. `visible: false` hides a
field from a screen and is presentation only: the value is still in the record
the API returns, and a principal who must not read it is stopped by a field
permission. It requires `fields:`, since hiding a record is narrowing which
records appear, which named filters and row policies already do and which
paging and counts account for (`TIDE282`). Both are subtractive -- a rule may
not grant either (`TIDE281`) -- because granting would have to overrule the
workflow lock or the permission that withheld the thing. The evaluator moved
to its own module (`tide/appearance.py`) so the service can enforce a lock
without importing the presentation layer, which reaches the data layer, which
reaches back into the service.

The invoicing demo seeds a ninth invoice: a draft with no lines, which totals
zero and therefore cannot be posted. It exists because `nothing_to_post`
declares that state interesting and nothing in the seed matched it -- a rule
the demo never fires is a rule nobody meets, and the checked-in screenshots
now show both of the application's rules rather than one. The seeded numbers
reach `INV-2026-0009`, so a fresh run allocates `INV-2026-0010` first.

That number used to read `INV-2026-000010`: `allocate_invoice_number`
rendered six digits while the seeded history wrote four, so every invoice
created in the demo looked unlike every invoice already in it. The 2026-08-04
decision log had noticed and filed it as luck rather than intent ("seeded
values are four digits and generated ones six"), and it stayed invisible only
because the two never appeared side by side. The ninth invoice put them there.
The generator now renders the width the application's own history uses.

A browse view may declare how the browser offers editing: `settings:
{edit: inline}` puts the selected row's writable scalar columns into
editors in place, while the default `form` keeps today's record screen.
The setting rides the ordinary browse-settings layers -- application
defaults, presets, an entity's presentation block, the view itself -- and
the closed set is validated at every declaration site (`TIDE285`),
because a typo that silently fell back to form editing would be a switch
nobody knows is off. The terminal deliberately ignores it and keeps its
form. The manifest carries the resolved mode to remote renderers.

The filter contract gained membership and enumeration. `in` takes a
non-empty list of typed values and keeps rows whose column equals any of
them -- with a null element meaning blanks count as chosen, because SQL's
IN alone never matches NULL and both repositories must agree on what the
blank checkbox means. `POST .../_distinct` answers one column's distinct
values under the caller's conditions: bounded at 200 with a truncated
flag, ordered ascending with a null last, row policies applied, and a
reference column named through the same batched display machinery grids
use. Conditions still only AND together; `in` is the one disjunction,
scoped to one column's values.

Permissions beginning `tide.` are reserved for capabilities the framework
itself answers for. An application declares one in `permissions:` and grants
it through a role exactly like its own -- so the single role expansion still
decides everything and administration is not a second authority -- but it may
not invent one, because a permission nothing checks reads as a granted
capability while granting nothing (`TIDE286`). There is one today,
`tide.users.administer`, and the reference application declares a role that
grants it and nothing else: administering who may sign in is not a reason to
be able to sell anything.

A query may now ask for aggregates beside its page. `QuerySpec` carries
`(field, function)` requests from a closed set -- `sum`, `count`, `avg`,
`min`, `max` -- and the page answers each over the **whole filtered set**:
the same filters, search and row policies as the records, and none of the
sort, limit or cursor, so the value is true however far anyone has paged.
Both repositories answer identically because the primitives are the four
aggregates every database computes the same way, and `avg` never reaches
one: it is the service dividing sum by count exactly and rounding half-even
to the field's declared scale. The semantics are SQL's -- aggregates answer
for values, so a count is of non-null values, a null joins no sum, and an
empty set answers null for everything but `count`'s zero. A summary over a
field the caller cannot read is refused the way reading it would be. On the
wire the request is a `summaries` list on the `_query` body, the answer a
`summaries` list on the page envelope, and the MCP `search` tool speaks the
same shape -- so an agent can ask what the draft invoices come to without
paging through them. A browse view declares what its footer wants with
`summaries:`, one function per shown column, checked by the compiler against
the column list and each field's type (`TIDE283`, `TIDE284`); the
presentation manifest carries the declaration filtered the way columns are,
so a principal who cannot read a column is never handed a request the server
would refuse. The invoicing browse declares a count of numbers and a sum of
totals, which is also where the demo's footer comes from.
Regenerating the terminal screenshots also found two stale selectors in
`tools/capture_screenshots.py`, left behind when collections gained per-name
widget ids; the tool runs outside CI, so nothing had said so.

**A record keeps its documents in fields.** `type: file` joins the closed
field types: the field holds an attachment's key, the bytes live outside
every database on a filesystem the deployment names, and the metadata --
filename, type, size, digest, who uploaded it and when -- is a framework
table in the application's own database. So the schema says what each
document *is*: a purchase carrying a `quotation`, a `supplier_invoice` and a
`warranty` is three declared fields with their own labels, layout, field
security and audit, rather than three anonymous rows in a bucket beside the
record. Where documents are genuinely unbounded, that is a collection of
file-bearing child rows, composed from the same primitive rather than built
as a second system.

An author states a bound (`max_size`, required) inside the bound the
framework states (100mb), and may narrow what a picker offers (`accept`).
Everything that decides about a *value* is refused, because the field does
not hold one -- comparing, defaulting, masking or computing a random key
would be a declaration that compiles and does nothing (`TIDE287`-`TIDE291`).
A file field is not something a query may ask about either: one gate refuses
it for sorting, filtering and summarizing together, so no surface has to
remember the rule. Legacy databases are refused at compile time, where no
column could be created anyway.

Uploads are staged and claimed at commit, because a create has no record yet
and a form somebody may still cancel is no place to write business data. The
claim asks four things -- does the key name an upload, is it still unclaimed,
was it staged for this entity and field, and was it staged by the identity
now committing -- since a staged key is a bearer token until it is claimed,
and an identity that has merely seen one must not be able to attach somebody
else's document to its own record. Refusals are validation issues on the
field: from where the person stands, a key that will not claim is something
wrong with the file they picked, not a locked door.

**A workflow lock freezes a file field only once it holds a file.** The
countersigned copy arrives *after* the invoice is posted, which is the real
order of events, so a locked record still accepts a document it does not have
and refuses to exchange or remove the one it has. It is derived rather than
enforced twice -- the field compiles to `(<lock>) and <field> != null` -- so
the one `immutable_when` machinery every renderer, REST and MCP already asks
decides it. The reference invoice declares `signed_document` and demonstrates
exactly that: on a posted invoice every other field has collapsed to
read-only text while this one still offers a picker.

Downloading is scoped to the record, so the ordinary record read applies the
entity permission, the row policies and the field's own read security before
a byte moves, and missing, forbidden and unauthenticated answer as they do on
the record route. Nothing deletes bytes inline: releasing a file starts a
grace clock, because a download may still be streaming and a crash must leave
a sweep something to reason about. `tide attachments check` reconciles the
two stores in three directions -- rows without files, files without rows, and
digests that moved, the last never swept because those bytes are the only
copy of whatever they have become.

The browser gets the control: a picker when empty, and one line when filled
-- the filename is the door, the way a reference value is itself the link to
the record it names, with the size beside it and replace and delete as the
same trailing icons the reference well carries. Replace but not delete on a
field the model requires, since a mandatory document can be exchanged and
never removed. The terminal reads documents and does not move them, showing the
filename and never the key. MCP carries the projection and no content tool,
because handing file bytes to an agent is its own decision.


### Terminal client

Every terminal screen composes `TideHeader` rather than Textual's `Header`.
The one it replaces registers four title watchers when it mounts, and their
callback queries the `HeaderTitle` its own `compose` has not mounted yet,
catching only `NoScreen`. In that window -- one message cycle, and wider on a
loaded machine -- `NoMatches` leaves the callback, leaves the message pump,
and ends the application. It cost one CI run on the Windows job. `TideHeader`
puts the title into the widget as it is composed, so there is no moment when
the answer is unknown, and tolerates the absence rather than dying of it.

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

That summary is now a **grouped, parameterized listing**. Naming `columns:` on
a summary report makes the matching records themselves the detail rows: each
`group_by` run heads its own contiguous slice and closes with the aggregates
as a subtotal, and the same aggregates -- folded through one accumulator, so a
group total and the grand total cannot disagree -- close the report at the
foot. The group fields are prepended to the declared sort, which keeps every
group one run and leaves the declared sort ordering the rows inside it.
Without `columns:` a summary keeps its one-row-per-group shape, and both
shapes gained the grand-total footer they previously lacked. Report
parameters are typed and validated in the report service alone -- every
surface sends strings -- and a criteria clause comparing against a declared
optional parameter that was not supplied is **dropped**, so the posted-sales
summary answers "everything", "since a date" or "a period" from one
declaration. The terminal asks for parameter values before building, with
blank meaning omitted; the browser asks with a form built from the
presentation manifest, which now carries each summary parameter's name,
label, type and required flag -- required meaning "the caller must supply
this", since a declared default satisfies the service on its own. An
all-optional summary builds in the browser immediately and the form narrows
it; one with a required parameter waits for the form instead of rendering
the service's refusal; dates get native pickers, and exports send the values
the visible preview was built with, so a download cannot disagree with the
screen. Record reports carry no parameters over the manifest, because their
identity is bound from the URL. HTML, PDF, the
Textual preview and the Web preview render groups as bands inside the one
detail table; CSV re-flattens them into leading columns repeated per row,
because a spreadsheet has no headings to put them in and a row that does not
say whose it is cannot be pivoted. The grouped document crosses REST intact:
the wire model carries the group slices and the typed client refuses one
that names rows outside the table.

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

### Carrying records off

A browse view can now be taken away as a file. `POST {resource}/_export/{csv|xlsx}`
carries the view name plus the same filters and sort the grid sent; the cursor
and the page size are the server's, because how far an export walks is a bound
it owns rather than one the caller names. The service pages the same secured
query the grid ran, so row policies, field reads and the row-policy recheck
all hold without being restated, and the columns are the view's declared ones
in declared order -- so the file is reproducible from the query alone, and two
readers exporting the same query get the same bytes rather than each other's
column arrangement.

Gated by `tide.records.export`, the second capability in the reserved `tide.`
namespace, additional to `list` rather than a replacement for it. It is
deliberately **not** a confidentiality boundary and the documentation says so:
a caller holding `list` can already page every row an export would carry. What
it separates is reading a grid from carrying it off, which is a distinction
deployments make -- and what makes that real rather than decorative is the
log, which now writes a `records.export` event naming the principal, the view,
the format, and how many rows of how many left. The log allowlist gained
`rows` and `total`: two integers rather than a truncation flag, because they
say more and the allowlist drops booleans by construction.

Bounded at 10,000 rows, which is not about secrecy either -- it is what stops
one request becoming an unbounded scan on a shared server. The file still
arrives and says it is partial, the way `_distinct` returns 200 values and
reports that it truncated. Where the format has room the file says it in
words: the workbook's second sheet, `Export details`, carries the conditions,
the sort and the count. A CSV says it in its filename, which gains a
`-partial` qualifier, because `render_csv` writes the table and nothing else
on purpose -- a CSV export exists to be sorted, filtered and pivoted, and a
preamble row breaks exactly that. Both responses carry `X-Tide-Export-Rows`
beside `X-Tide-Export-Total`, so a client knows before it opens the file.

XLSX holds numbers as numbers, which is the only reason to prefer it, so the
export renders its table twice: once as text for anything that reads text,
once as typed values beside it. Which cells are typed is decided by the
*column* rather than by the value -- a reference stores an integer identity
whose text is a customer's name, and typing by value would have put `1` in the
cell where the grid showed `ACME - ACME Ltd`. The workbook also turned out
*not* to be inherently safer than CSV about formulas, which is what this
started out assuming: openpyxl infers a cell's type from what it is handed and
infers `formula` for anything beginning `=`, so a stored `=SUM(A1:A9)` was
being written as something Excel evaluates. The guess is overruled per cell
now, which is better than CSV's apostrophe rather than merely equal to it --
the value survives intact instead of carrying an escape into whatever reads it
back.

Optional, like PDF. XLSX sits behind a `spreadsheet` extra, and the
presentation manifest's `export_formats` is filtered twice: by whether the
principal holds the capability and by whether the server has a writer for the
format, so a renderer can never present a download that would come back a 503.

Formats are narrower than a report's on purpose. A 10,000-row PDF is not a
document anybody wanted and HTML of a grid is a worse CSV, so neither is
offered. The terminal and MCP deliberately abstain: the export service is
surface-neutral, so the terminal costs a screen rather than a contract when it
wants one, and an MCP client can already page the query and format it however
it likes. Honouring the reader's on-screen column order stays out too -- the
grid saves only order and widths and cannot hide a column, so the set is the
same either way and only the order would differ, which is not worth making
presentation state into a wire concept.

One extraction came with it. `_format_field` and `_format_scalar` were private
to `ReportService` and a browse export needs exactly the same answers, so they
moved to a shared `FieldFormatter` that the report service now delegates to.
A protected value answers as blank there rather than falling through to
`str(value)`: reports never met the sentinel because they format declared
report columns, and a browse view can name a field a field policy protects.

A report exports as a workbook too, beside CSV, HTML and PDF, and needs no
extra capability -- a report is already gated by its own permission and export
comes with it. One writer serves both kinds now: `render_xlsx` took a browse
export and only ever wanted two things out of it, so it takes those instead,
and `ReportDocument` already carried everything the second sheet needs.

Sheet one is the flat table and nothing else. A grouped listing flattens the
way its CSV flattens, with the group values repeated as leading columns,
because a spreadsheet pivots for itself and a banded sheet is not a table.
Sheet two is where the rest goes: the header text, a record report's own
fields, each group's values and subtotal, and the grand total. That last one
needed a heading, `Report total` -- a subtotal and a grand total are the same
labels over different scopes, and the sheet was printing the same block twice
explaining neither, worst exactly when a single group makes them identical.

Typed values reach the workbook without touching the report contract.
`ReportCell` carries text and only text, deliberately: it is mirrored to the
wire by `TideReportDocument`, which forbids extras, so it cannot grow a field
without breaking every report at once. The values travel beside the document
as `TypedReport` instead, server-side and positional over the detail rows.
`build` and `build_for_record` keep the shape every caller has; `build_export`
and `build_export_for_record` are the same work returning the pair. Record
columns decide by the *column*, so a reference and a choice stay untyped and
their text reaches the cell; aggregates decide by the value, which is safe
precisely there because `_initial_aggregates` seeds every one as `0` or
`Decimal(0)`.

And a defect went with it. `TidePresentationReport.export_formats` defaulted
to every format unconditionally and was never set, so a server without
`reportlab` offered a PDF download that answered 503 -- the same rule browse
export had just been written to follow, broken for reports. Report formats are
now derived from what the process can actually write, PDF included.

### Web renderer

The browse toolbar carries an **Export** control where the principal holds the
capability -- a button for one format, a menu for more. It sends the conditions
on screen rather than the rows the browser is holding, because the grid is
virtualised and what is loaded is a page rather than the table, and it warns
before handing over a capped file, naming both numbers.


The shell now carries TIDE's own identity instead of a template's. The primary
hue moved from framework-default blue to a sea-teal, the sidebar ink took the
same cast, and headings, the wordmark and report titles render in a bundled
Bricolage Grotesque while data stays in the named system stack -- tables are
the instrument, headings are the voice. A three-crest tide-line marks the
connect headline, the sidebar wordmark and the workspace title, and nothing
else. Choice values wear a soft identity tint chosen by their own text -- six
buckets carrying no success green and no danger red, because the framework
cannot know whether an application's `posted` is good news -- so the same
value wears the same tint on every screen of every application and a status
column reads at a glance. A resolving reference shows an ellipsis instead of
flashing its raw key. On a phone, the browse toolbar became one wrapping row
instead of six stacked full-width controls, so the records start a screen
earlier; the grid's meta strip stopped wrapping mid-word; the record action
bar's clusters each keep to their own aligned row; the report paper gives up
its desktop minimum height and side padding at phone widths, so a
three-column summary fits 375px without cutting a column; and a one-row lines
table stopped reserving a hundred pixels of emptiness that pushed its own
editors below the fold.

The record screen now ranks its parts. Group names are eyebrow headings under
the one display-face title; text controls are filled wells while buttons keep
the raised card background, so "type here" and "click here" stop wearing the
same coat; the label column narrowed from 10rem to 8rem, which is the
difference between `MORA - Mora ...` and the customer's actual name; and
read-only values share their label's baseline. Collections left the record
card entirely: with no YAML-declared tabs, every collection renders in one
tabbed panel below the record -- one tab per collection, the first open, the
strip visible even for one -- so the record's fields keep the full card width
and a second collection is a visible tab rather than more page. A YAML tab
layout keeps its declared shape. The line editor is an inner card wearing the
selection's primary as a left accent, the same edge the selected table row
carries, and its heading says which row it is editing ("Line details · row 2
of 3"). An empty editable collection invites the first row in the manifest's
own words; a domain action steps back to an outline while the draft is dirty,
because the natural next step is Save and two filled buttons shout over each
other.

The reference well carries two more of the reference application's
affordances. An open-record door deep-links the referenced record's own
screen in a new tab, so an open draft can never be lost to a side trip; it is
offered only when the capability-filtered manifest carries a browse view with
a detail form for the target entity, which means a person without access to
Customers simply gets no door. A clear control empties a reference through
the same draft path as selection, and only where the model says empty is a
legal value -- emptying a required reference could only manufacture the
service's refusal, so the control does not offer to.

Where a reference is read rather than edited -- a locked field on a posted
record, a collection row, a browse grid cell -- the resolved name itself is
now the door. Following it navigates in place with one marked history entry,
and the opened record's Close walks back to exactly where the person was;
modified clicks still open a tab, because these are real anchors. The
editable well's door keeps its new tab deliberately, since a side trip from
an open draft must not be able to lose it, and grid links stay out of the
tab order so the grid's roving tab stop keeps owning the keyboard.

Three more of the reference application's habits followed. Entry arrives in
runs -- a stack of paper forms, one after the other -- so the create screen
gained **Save and New**: the record is written, the form comes back empty with
the model's defaults in place and the cursor in the first field, and the grid
behind it refreshes for whenever the run ends. Plain Save still closes to the
grid, and a record that already exists is offered neither, because it is one
record. A collection's row controls moved above the rows they act on; below
the table they sat under an editor card whose height changes with every row
selected, so the control being reached for was somewhere new each time. And
the sidebar names the application version the manifest already carried, so a
support conversation can start with which build is on the screen, and grows a
filter box once the navigation passes ten entries -- a group whose every entry
the filter removed leaves with them, since a heading over nothing is a promise
the list is not keeping.

Both renderers now draw an application's conditional appearance rules. The
browser gives a marked row a left edge and a wash, carries the same edge onto
the card of the record it opens, and marks a named field's value in the grid
and its label on the record; the terminal, which has no wash to give a row,
colours the row's cells. Neither ever sees a rule -- the server sends a
verdict -- and an emphasis a renderer does not know is left undrawn rather
than drawn wrong, since a server may be one version ahead of the bundle.

Column reordering now starts only at the grip dots. The header cell used
to be draggable as a whole, so grabbing the resize border started a
native drag instead: the column appeared to shift, and because the drag
swallowed the mouseup, the resize tracker kept resizing after the button
was up. The grip is revealed by opacity rather than display, because a
drag source that goes display:none between mousedown and the drag
threshold makes Chromium abort the drag before it starts.

Identity administration reaches the browser. A principal holding
`tide.users.administer` gets an **Identities** entry below the application's
navigation -- and in the workspace select a phone uses -- listing the accounts
with their roles and sign-in state beside the compiled roles and what each
grants. Accounts are created, regraded, disabled and given new passwords
there; roles are reported and never offered for editing, because a role is
authored in YAML. A refusal comes back in the server's own words, including
the one that will not let the last enabled administrator take its own way
back in. The screen is framework chrome rather than an application view: it
travels in the same `?view=` parameter under a name no compiled view can
hold, and it loads as its own chunk when it is opened. The reference
application grants `administrator` nothing else, so the shell now answers a
principal with no browse views at all by opening on Identities instead of
"No available workspaces".

Every stored column in the browser's grid now carries a funnel, the way
the reference application's grids do. It opens the column's distinct
values as a checkbox list -- served and bounded by the server, captioned
codes as their captions, references by name, a `(Blank)` entry for empty
cells, a search that narrows the list in place, and Apply/Cancel staging.
The list arrives fully checked when the column is unconstrained and
exactly as applied when it is, and it reflects every other active
condition but never the column's own, so an applied filter can be widened
from its own popup. Checked values become an `in` condition beside the
named filter and the search; the funnel stays lit while its column
constrains the view, and checking everything releases it, because a
filter that admits every value is not a filter. The summary footer
answers for the same filtered set, so the count follows the checkboxes
with no extra wiring.

The browser edits rows in place where a view asks for it. Double-click or
Enter starts the edit by reading the record fresh -- what is writable, what
an appearance rule locked and which version the row is are the server's
current word, not the page's memory -- and the columns that may change
become compact editors: text and masked numbers, dates, a tick box, the
captioned dropdown. Enter saves, Escape cancels, and leaving a dirty row
saves it the way the reference application does. The save is the form's own
diff through the same version-checked PATCH: nothing is sent that did not
change, a refused save keeps the row editing with the offending cell marked
and the field's words on the feedback line, and a stale row stops editing
and says the record moved. References and collections stay the form's
business, as does creating, which needs fields a grid does not show. The
demo's Products browse works this way; invoices keep the form, where their
lines live.

Both also answer a browse view's `summaries:` declaration. The browser draws
a footer band under the grid, each answer beneath the column it describes in
that column's own format and alignment, scrolled with the columns and living
outside the grid's keyboard pattern; the terminal draws a one-line bar under
the table. The author picks the function and each renderer owns its words --
`Sum`, `Count` -- and a count is printed as a number of values, never in the
column's money dress. The values follow every search and named filter and
answer for the whole filtered set, so three loaded rows under a bar that says
nine is the feature working, not a mismatch. An aggregate over nothing is a
dash, and a view that declares nothing pays nothing.

A study of the reference EMS application then tightened the dress. Group
captions are bands with their own background, spanning their panel edge to
edge -- the floating eyebrow that preceded them read as one more field label.
Tabs are underlines on a hairline rail rather than filled pills. Record
navigation is a pair of chevrons and the reference picker is an ellipsis
button riding inside its own well, combobox-fashion; their names live on as
accessible labels, and the words they no longer print were width. The native
date input's calendar indicator sits at the trailing edge where every other
control puts one, a label sits 8px from its control while field columns sit
24px apart -- proximity now says what belongs to what -- and field rows and
panel padding tightened a step throughout. With the record itself as the
scope, a lone record report is simply **Preview**, and the invoicing
application's own action labels dropped the object they repeated: **Post**
and **Void**, changed in the YAML where a label belongs, so every surface
follows.

A reference picker showed the literal word `uniqueid` where the record's display value belonged. `display: uniqueid` compiles to a template with no placeholder, meaning the name of the field to show, and the two web formatters resolving the same declaration disagreed about that: one substituted `{braces}` only and returned everything else verbatim. Both now read one rule. Field labels are 14px and wrap instead of 12px and truncating, which was sized for a label stacked above its control; the colour was already 5.6:1 on the light card and 6.4:1 on the dark one.

The closed-set field editor is a code-owned `Select` rather than a native one, so its popup is styled like the rest of the application. The general scalar editor now uses the vendored `Input`, whose styling it had been carrying a drifted second copy of. A boolean is still a native tick box and the phone navigation is still a native select, both for stated reasons.

Form fields put the label beside the control from 768px up, and stack it above below that. A label above doubles the vertical space a record needs -- the same budget the field cards used to waste -- and a form is read down its value column, which is how the terminal renderer already draws one. A Playwright journey measures it at 1440px and at 375px, because jsdom computes no layout.

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

The record action bar survives a phone. Its two groups shared one unbreakable
line, so at 375px the actions were laid out from 249px to 416px and clipped
rather than scrolled: `Cancel`, `Save`, `Preview` and the domain actions could
not be reached by any means, while the document reported no horizontal
overflow at all -- which is why nothing that asks the page how wide it is had
ever noticed. The footer now wraps. Letting the groups shrink instead was the
first attempt and is recorded because it is the instructive one: every button
came back inside the viewport and `Preview Invoice` printed across `Next`.
`mobile.spec.ts` therefore asserts both properties at 375x812 -- nothing off
the screen, and no two controls on top of each other. 375px is the supported
floor, because the Web UI is the only surface a phone can run.

The browser tab now says which screen it is showing. It read `TIDE Framework`
on the sign-in form, on a browse and on an open record alike, so two tabs of
one application were indistinguishable and the history was a column of
identical entries. It is now `<screen> · <application>`, most specific first
because a tab truncates from the right, and the record's half is the same
string its heading renders rather than a second derivation of it. The browse
yields while a record is open, so there is one writer and no restore-on-unmount
to get the ordering wrong. The shell name lives in `index.html` and in
`SHELL_TITLE`, which is a duplicate that cannot be removed -- the document
needs a title before any script runs -- so a test asserts the two agree.

There is also a favicon, an SVG carrying the mark the sidebar already wears.
The journey asserts the served content type, because the SPA catch-all answers
an unknown path with `index.html` and would have made a missing icon look
present.

Asking whether a browser already has a session is no longer answered as a
failure. `GET /_tide/browser-auth/session` returned 401 when no cookie was
presented at all, which is what every cold load of the Web UI does, so the
first thing anyone opening the console found was a red line on a page where
nothing had gone wrong and nobody had tried anything -- and the server log
carried the same. It now answers 204, and keeps 401 for a cookie that was
presented and rejected, which is a real failure and worth seeing. The two were
indistinguishable before, to the console and to the client alike.

The bundle is split. Everything shipped in one 563 kB chunk, so reading the
sign-in form meant downloading the data grid, the form editor, the editable
collection, the conflict review and the report preview first. The entry is now
331 kB: the shell is a 179 kB chunk, the record screen 49 kB, the report
preview 7 kB. The shell is fetched while the sign-in form is on screen rather
than when it is submitted, so the split buys a smaller first paint without
paying for it with a blank frame. A journey holds the entry under a stated
ceiling, because a single static import would quietly undo all of it.

`/docs` renders under TIDE's own security headers, because TIDE now serves
Swagger UI rather than pointing at a CDN. FastAPI's page is a CDN script tag, a
CDN stylesheet, a CDN favicon and an inline initialiser, and `script-src 'self'`
— sent on every response whenever TIDE owns identities — refused all four, so
the page answered 200 and drew nothing under exactly the configuration
`docs/WEB-UI.md` describes. The exposure tests asserted the status code and
were green throughout.

swagger-ui-dist 5.32.13 is vendored at `src/tide/api/swagger_ui/`, Apache-2.0,
with `PROVENANCE.md` recording the version, sizes and checksums; the assets are
served from the application under `/_tide/docs-assets/`, registered only when
the description is, so they are withheld with the document they draw. The
initialiser is a generated same-origin file rather than an inline block, which
is what lets `'self'` cover it without a hash or `unsafe-inline`. **No security
header changed**, and a Playwright journey now loads the page under the real
headers and fails on any CSP refusal — which is the layer the status-code tests
could not reach. The page also needs no network: the CDN was taking 14.6s where
this was found, and answers not at all on an isolated machine.

`/redoc` is still FastAPI's, still CDN-hosted, and therefore still blank under
those headers. A second vendored megabyte for a second view of the same
document did not look worth it; the decision log says so rather than leaving
the asymmetry to be discovered.

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

**One box over everything**: the header gained a search that asks the new
`POST /_tide/search`, where the server sweeps every REST-listed entity whose
application-declared searchable fields this identity may read -- through the
same secured lookup the reference pickers use, so the entity permission, row
policies and field security decide what a search can see, and sweeping an
unreadable field is refused before it can become a probe. Hits come back
bounded and grouped in model order, each naming itself through the entity's
own display template; the panel renders each hit as a door with the standing
reference-link behaviour, withholds a group whose entity has no view in the
manifest, and a truncated group says "first matches only". A journey against
the real stack searches "consulting", meets a product and a customer in the
same panel, and opens one.

What is searchable is one declared list: the entity's `search_fields:`, the
same list its lookups already search. The reference application's invoices
joined it by `number`, so a clerk finds an invoice from the one box, and the
identity that may read invoices alone finds exactly those; the per-field
`searchable:` flags that two reference fields carried were read by nothing on
any surface, and the reference model no longer shows them.

The schema then stopped admitting that spelling at all: `searchable:` on a
field is refused by the loader and the exported editor schema alike, because
a directive the loader swallows without effect teaches whoever reads the file
something false. This is not counted as the kind of breaking authoring change
that demands a new `schema_version`: no document that meant anything changes
meaning, since the key never did anything -- an author who wrote it was
already not getting what they asked for, and is now told so at the source
line instead.

The record's audit trail reached this surface as a **History** tab on the
panel below the open record, after its collections -- present exactly where
the entity declares audit and the session's capabilities grant viewing it,
absent rather than empty anywhere else. It renders the wire's bounded
newest-first trail and its redaction vocabulary without re-judging either:
redacted changes stay `[redacted]`, field-only changes are a name alone, and
changed fields speak the form's declared labels. Saving or running an action
marks the open panel's history stale so it asks again, and a journey against
the real stack now creates a customer, renames it, and reads both writes back
from the trail.

A browse grid can be arranged by the person reading it. The toolbar's
column chooser opens the manifest's offer -- `available_columns`, every
readable non-collection field of the entity -- and an arrangement chooses
which the grid shows, in what order, under what names; a rename shortens
a label that reads long in a header. The arrangement is stored on the
server per principal and view, a sixth framework store reached through
`/_tide/view-state/{view}`, and validated once in a service every
transport shares: a real browse view, real readable fields, no
collections, no repeats, bounded labels. The YAML `columns:` stay the
only declaration -- the arrangement is an overlay in the XAF
user-differences sense, and Reset to default returns to the declaration.
Adding a column never refetches records, because a list row already
carries every readable field; funnels and sorting follow the shown
columns, because the manifest's capability lists widened to the whole
offer; exports keep the view's declared columns, because the export is
the view rather than one person's arrangement of it. While an
arrangement is active it is the order authority and the drag-remembered
client order stands aside -- without that rule a chooser reorder was
silently beaten by any column ever dragged. Parent-type property paths
(`customer.city` as a column) stay out deliberately as their own
contract decision; the terminal and MCP abstain, the same split as
search and export.

A grid state can be named and kept. "Save current view" beside the
named-filter dropdown stores the screen's components -- the declared
filter, the funnel checks, the sort, and a snapshot of the columns as
arranged -- as one of up to twenty saved views per browse, per person,
in a seventh framework store reached through `/_tide/saved-views/`.
Selecting one restores the components wholesale, so the controls
relight exactly as saved rather than the rows merely matching; the
snapshot outlives a reset of the standing arrangement, which is what
makes it a state worth naming. Validation happens once in a service
every transport shares, with the funnel and sort fields judged by the
same field-type rules the manifest's capability lists are built from;
what a stored value matches is deliberately not re-validated, because
a replayed condition goes through the query service like any other.
Search text is not part of a saved view, edits after selection are
just edits, and the terminal and MCP abstain.

### Machine interfaces and AI-assisted generation

`tide serve` requires a 32-character-or-longer development bearer token in
`TIDE_API_TOKEN` and binds to loopback. Under `--auth development` the Web
renderer now opens with no credential at all -- the connect screen offers
**Open without signing in** instead of a token box -- so checking a screen no
longer means moving a 32-character secret into a browser by hand. The session
carries the `--principal` and `--role` the server was started with, which is
also how to see an application as one role meets it. The REST API is unchanged
and still wants its bearer token. Three fences hold the mode to one machine and
none of them is a document: the bind must be loopback (refused at startup),
`build_fastapi_app` refuses to attach it to a production identity adapter, and
any request whose `Host` header names something other than this machine is
answered `403 non_loopback_host` -- which is what closes DNS rebinding, since
an attacker domain resolving to 127.0.0.1 is same-origin to the browser and
neither the bind address nor the absent CORS headers can see it. See
[Open any application while developing](docs/WEB-UI.md#open-any-application-while-developing). The Windows `start.bat api-demo`
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

Where the server owns the identities -- `--auth local`, and only there -- it
now registers an identity administration surface behind
`tide.users.administer`: the compiled roles and what each grants, the accounts
with their roles and sign-in state, creating one, replacing its roles,
enabling or disabling it, and replacing its password. Roles themselves are
compiled and stay that way; what is administered is *assignment*. Until now
that lived only in `tide auth`, which means a console on the server, so
withdrawing a role from somebody who left required SSH.

Two invariants hold wherever the request comes from: an account may not be
left with no roles, and the store may not be left with no *enabled* account
that can administer it -- a disabled administrator does not count, because the
guard is about who can still sign in. Deleting an account is deliberately not
offered, since disabling is reversible and is what the console offers.
Refusals are told apart -- 403 for the permission, 404 for an account that is
not there, 409 where the store's state refuses, 400 for a request that was
wrong -- and none of them repeats a value it refused, because one of them is a
password. An account on the wire carries when its password last changed and
nothing else about it; the store's listing never loads a hash at all, so there
is no projection to remember. The bootstrap stays on the console, because a
running server cannot be the only way into an application whose accounts are
all locked out. Administration is not exposed over MCP: an agent that can
grant itself a role is an agent with every role.

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

Reports reach MCP, closing the one surface that still did arithmetic by hand.
`ReportExposureSource` had carried `mcp: false` since the exposure contract
was written, compiled into every model and read by nothing; it now serves
each opted-in report as its own generated tool -- `report_sales_summary`,
`report_sales_invoice` -- whose arguments are the report's declared
parameters, required ones required in the tool schema. The answer is
deliberately not the REST document: on this wire a decimal is a JSON string,
so preformatted text alone cannot say what is a number, and an agent asked
for July's sales would have paged raw records and summed them itself --
unaudited, client-side, at whatever scale rounding it chose. The MCP document
pairs every detail cell's display text with the exact typed value, types each
column by reading back the typed table the workbook writer already ran on,
and keeps groups, spans, subtotals and report totals. Presentation -- page
footers, filenames, alignments -- stays behind. And every report build on
every channel now writes a `reports.render` event from the one service entry
they all share, the way `records.export` made browse export real: REST
preview, REST export, TUI preview and MCP name the channel, kind, principal,
report and row count, and a refused build writes nothing.

A concurrency token TIDE did not write is now a version the wire can say.
Adopted tables hold rows whose token column is NULL -- the reference XAF
store leaves exactly that behind until a row's first save -- and while the
service already compared an expected None as `IS NULL`, no `If-Match` value
ever matched, so such a row was permanently un-editable over REST and MCP
while the TUI edited it freely. Its `GET` now answers `ETag: "null"`,
`If-Match: "null"` (and the equivalent MCP `expected_version`) asserts
exactly that against an atomic `IS NULL` compare-and-swap, and the first
successful write heals the row to version 1. `None` keeps meaning "nothing
was supplied" at every precondition boundary; the assertion travels as its
own `NULL_VERSION` value. The commit no longer refuses the heal either: the
write assigns the system-owned token itself, so its stored NULL is not a
required-field violation, and the memory adapter learned the same
`or 0` arithmetic the SQL adapter always healed with.

An administration `PATCH` carrying `roles` and `enabled` together is now one
store transaction rather than two. Applied as two, the roles half landed
before the enabled half was refused by the last-administrator guard -- a
half-applied update reporting total failure, in the store that decides who
may administer. The identity store gained a combined `update_user` write
that `set_roles` and `set_enabled` now delegate to, and the guard decides
over the account's final state: an update is guarded exactly when it would
take an enabled administrator out of that set, whichever half does the
taking.

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

The view-structure table no longer settles for one column. Its fit is
scheduled with `call_after_refresh`, and a refresh is not a layout: the table
can be displayed and still measure zero, `view_field_columns(0)` returns the
one column that always survives, and the fit then compared that against a
table already holding exactly it and returned early -- so a width read too
early became the answer for the life of the selection. It now asks again, up
to four refreshes, and treats zero as "not yet" rather than as an answer.
Surfaced as a one-run-in-five flake at whichever certified size lost the race;
reproduced deterministically by forcing the first two measurements to zero.

That fit lands late by design, and landing late had a second consequence.
Repopulating the table re-highlights the field that was already highlighted,
and answering a highlight by writing its group into the add-target selector
took back the group a person had just chosen -- silently, because the Add
button stays enabled either way, so the field arrived in a group nobody asked
for. Highlighting a *different* field is the choice that retargets Add;
re-asserting the highlight already in place is not, and no longer does. Found
as a Windows CI failure reading `assert 'Invoice' == 'Totals'`, on a runner one
refresh slower than the test. Both halves of the rule are now pinned: the
retarget itself turned out to be observed by nothing, so the gate that keeps a
chosen group could have removed the retarget entirely with every studio test
still green.

The fit is now driven by the table rather than by the terminal. `Resize`
does not bubble, so an application handling the terminal's resize is told the
new terminal size and not the new table size, and the fit it scheduled
measured the table at the width it had before -- then found the columns it
wanted already in place and returned. Zero was read as "not yet"; a stale
positive width was taken for the answer, so narrowing a terminal left the
table declaring four columns needing 47 cells inside 35: the same
over-declared table the certified-size check exists to prevent, arrived at by
resizing rather than by starting there. The view-field table now reports its
own changes of width, which is the one report that cannot arrive early.

Studio sessions now reuse the compiled evaluation and semantic document index
for an unchanged candidate fingerprint. The cache is bounded by the same
history limit as undo/redo, candidate mutations refresh only the affected
state, and semantic identity changes still force a complete re-index. Repeated
panels and previews therefore no longer rematerialize the same temporary
project or reparse every YAML document.

### Databases and operations

Browser sessions are no longer a fact about one process. A managed database now
carries `tide_browser_session` and `tide_login_failure`, created by
`--create-schema` beside the query-cursor and action-audit tables, and the
password and development authenticators keep their sessions there: several TIDE
processes behind one address agree about who is signed in, and a restart does
not sign everybody out. Only the digest of a session identifier is stored, on
the same reasoning as the query cursors. The failed-login counters moved with
them, because a limit of five attempts counted per process is five attempts
*per process* -- a second worker silently doubled the budget for guessing a
password, and nothing said so. A legacy database gets neither table (TIDE may
not create one in a database it does not own) and keeps process-local sessions;
the startup banner now states which is in force. OIDC is deliberately excluded
and keeps its single-process constraint: its sessions hold the provider's
access and refresh tokens, which want encryption at rest before they want
sharing. **This adds two tables to the managed schema**; run `--create-schema`
once against an existing managed database, or `tide db diff` to see the
proposal.

Where browser sessions cannot be shared, only one process may serve. A managed
database carries `tide_server_lease`, and a server whose sessions stay in the
process that issued them -- `--auth oidc` always -- takes it at startup, renews
it while it runs, and releases it on the way out. A second one refuses to start
and names the incumbent. The lease expires on its own, so a server that was
killed rather than stopped blocks a restart for at most `--lease-ttl` seconds
(120 by default) instead of until somebody finds the row. Beside it, a
process-local session cookie now carries an opaque stamp naming its issuing
process, so a request that reaches a sibling is answered
`401 session_from_another_server` rather than the bare 401 that is
indistinguishable from an expired session. A shared deployment emits no stamp,
because there a session that cannot be found has genuinely expired.

Those several processes now have somewhere to stand. `--behind-tls-proxy`
declares that a reverse proxy terminates HTTPS in front of the server -- the one
thing TIDE could not work out for itself, and had been guessing wrongly in both
directions. A non-loopback bind was refused for want of a certificate such a
deployment does not have, and the arrangement that did start (bind loopback,
proxy on the same host) read the session cookie's `Secure` flag off that absent
certificate and issued the cookie without it, on a site whose address bar says
`https`. The declaration makes the cookie `__Host-tide_session`, allows a
routable bind without a certificate, requires `--mcp-resource-url` where `--mcp`
is used, and switches the API description off by default -- a loopback bind
stops meaning "only this machine" the moment something forwards to it. It
refuses `--ssl-certfile`, since HTTPS is terminated in one place, and
`--auth development`, which grants a browser session to whoever asks and had
only the bind address keeping it honest. Forwarded headers stay a separate
decision: `--forwarded-allow-ips` names the peers whose `X-Forwarded-*` headers
this server believes, as addresses or CIDR networks, and refuses the `*` uvicorn
would accept.

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

`tide db inspect` proposes legacy application metadata from an existing schema:
one entity per table, every column mapped explicitly, foreign keys as
references. It is read-only — a test asserts no statement it issues begins with
`CREATE`, `ALTER`, `DROP`, `INSERT`, `UPDATE` or `DELETE` — writes reviewable
source files rather than hidden state, refuses to overwrite a previous run, and
reports what it could not map instead of guessing. A table with a composite or
absent primary key is named as skipped, with the reason. See
[Legacy databases](docs/LEGACY-DATABASES.md#adopting-an-existing-schema).

Proposed names are snake-cased: `SerialNo` becomes `serial_no`, and the physical
name stays in `column:`, which is what binds the field to the table. The name is
spent on the REST payload key, the MCP argument, the YAML you edit, and the
label — no proposed field declares one, so `humanize` derives it from the name,
and a name flattened to `serialno` reads `Serialno` on all four surfaces with
nothing downstream able to split it again. An acronym still reads poorly
(`UniqueID` labels as `Unique Id`) and is left to an explicit `label:`.
Everything derived from an entity name is split the same way, so a project no
longer holds two conventions at once: `legacy.EquipmentInstance` writes
`models/equipment_instance.yaml` and grants `legacy.equipment_instance.list`.
The heading over a generated form is humanized rather than snake-cased and
reads `Equipment Instance`, derived from the same name the compiler labels the
entity with, so a form cannot disagree with the navigation entry that opened
it. It was the last unsplit string in a generated project, and the only one no
terminal renders: a single group draws no heading in Textual, so the Web UI is
where it showed.
See [The names it chooses](docs/LEGACY-DATABASES.md#the-names-it-chooses).

A synthesized collection is declined, and named on stderr, when it would point an entity at itself, close a cycle, or put a record further from a list than hydration follows. Collections load eagerly with no cycle guard, and past `RelationshipLoadPlan.max_depth` the repository raises `RelationshipExpansionLimit` instead of truncating -- so one over-long chain returns an empty browse rather than a slow one. The reference itself always survives; only the turned-around collection is declined.

`--runnable` also turns every reference around: the entity a foreign key points
at gets a `collection`, an `inline_edit` view and a section on its form, so a
record shows what points at it. A table pointed at twice, or one pointing at
itself, keeps the key in the collection name. This is what makes an XPO-style
many-to-many table usable, since those carry their own surrogate key and map as
ordinary entities. References now ask for `editor: lookup`; the default select
loads the first 500 target rows and raised `InvalidSelectValueError` on a
stored key outside that window.

A Textual form declaring more than one collection used to crash with
`Tried to insert 2 widgets with the same ID 'collection-records'` — the screen
resolved one collection and the layout loop emitted a section per declaration.
For a while it rendered the one it supported, which meant the terminal and the
browser disagreed about what a record contains. **It now renders them all**:
each collection owns a pane — its table, its line editors, its drafts — with
widget ids that carry the collection name, and the one bar of line actions
acts on whichever collection has the focus, following that collection's
declared action order (a collection without `remove` never shows the button;
the bar holds the union of every pane's actions and toggles visibility,
because mounting buttons at runtime raced its own teardown). Save sweeps the
unapplied line edits of every pane, not just the focused one. Two latent
defects fell out of the generalization: `Add line` no longer injects a
`line_number` key into a child entity that never declared the field, and the
reference-option caches are keyed by target entity rather than field name, so
two collections sharing a field name aimed at different targets no longer
collide — which also means one query now serves every field pointing at the
same target. A two-collection fixture application exists precisely because
neither checked-in application declares two, which is why the suite pinned
the old behaviour as correct for a year of its life.

`--runnable` proposes the rest of an application, not just its metadata:
`expose` for the TUI, REST and MCP plus five permissions per entity, a
browse/edit/lookup view trio per entity with `lookup_view` wired on every
reference, and a security policy granting one role (`--role`, default
`operator`) everything declared. All three surfaces, because the Web UI is a
REST client and an application exposed only to the TUI renders empty. Without
it the proposal compiles and matches the database but no surface can open it —
the TUI refuses a model with no browse view. See
[Compiling is not running](docs/LEGACY-DATABASES.md#compiling-is-not-running).
An entity name now keeps the capitals its table name already had, so
`EquipmentInstance` no longer becomes `Equipmentinstance`.

Which tables to adopt is now a choice the command supports. `--table` and
`--exclude` take exact names or glob patterns, repeat, and match
case-insensitively and identically on every platform; `--list` reports what
each table would become and writes nothing. A `--table` pattern matching no
table stops the run before any file is written. Foreign keys are resolved
after every selected table has been planned, so a column pointing outside the
proposal keeps its physical mapping, loses only its reference, and is reported
— previously it became a reference to an entity that did not exist, and
`tide model validate` rejected the result the command had just called
complete. See
[Choosing which tables to adopt](docs/LEGACY-DATABASES.md#choosing-which-tables-to-adopt).

Two SQL Server spellings now map onto TIDE types. `money` and `smallmoney`
satisfy a `decimal` field at their fixed capacities of 19,4 and 10,4; before
this, any legacy table with a money column failed `validate_schema()` and the
application refused to start. A `uuid` field type maps to `UNIQUEIDENTIFIER` on
SQL Server and `CHAR(32)` on SQLite, so a GUID can be a primary key; TIDE
generates the value before the insert unless the field declares a
`server_default`, which leaves a legacy `NEWSEQUENTIALID()` column alone.

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

Capturing the API description found that it could not be read in a browser
under the configuration the Web UI documentation describes, and for a while the
capture used a second server without browser security headers to work around
it. That is fixed below, and the second server is gone.

`tests/test_launcher_contracts.py` finds the Node scripts that compose a
`tide serve` by searching `web/` rather than by naming them, so the third one
was covered on arrival. It walks with `node_modules` pruned from the descent:
the design-sync setup leaves a junction inside it pointing back at `web/`, and
a recursive glob that filters the results instead of the walk recurses until
Windows refuses the path.

The Quick start opens one surface and finishes it, then names the others one
command at a time. It was six commands in a single block, two of which run
until stopped, so a reader pasting the block never reached the last two; the
fourth exited 1 without `TIDE_API_TOKEN`, which no document showed how to
produce on any platform but Windows; and the paragraph beneath it said that
command made the Web UI and MCP "available from the same process", when both
answer 404 until asked for with `--web-root` and `--mcp`. Four surfaces and
Studio are photographed directly above it, and exactly one of them had a
command that worked.

`docs/GETTING-STARTED.md` gives the Web UI a section of its own. It had none:
fifty lines covering sign-in, editing, lookups and report preview sat inside a
127-line section titled "Run REST and OpenAPI locally", so a reader scanning
the headings for the browser UI concluded it was not covered — on the one
surface a phone can run. Node moves from "optional, for the MCP Inspector" to a
stated requirement at the version the renderer needs, every first-run step says
what it should print, and a closing section names the failures worth
recognising rather than debugging. One of them had no written prerequisite
anywhere: `--auth local` reads an identity store and will not create one.

That refusal now names the command that does. `serve --auth local` and the four
`tide auth` subcommands that read a store they do not create all answered with
the path they could not find — the one thing the reader already knew — and the
remedy existed only inside `start.bat`. All five print it now, along with the
roles the application defines, since choosing a permission set for someone else
is not the error message's job. It appears only when the file is genuinely
absent: "create it" is wrong advice for a store that exists and failed to open
for some other reason. The subcommands are derived from the parser, so the
sixth is covered the day it arrives.

`tests/test_documentation.py` now checks the output a document promises against
the compiler that produces it, and pins which surfaces `serve` provides. The
command check beside it resolves every documented `tide` invocation against the
real parser and stayed green through all of the above, because nothing that was
wrong was wrong in the syntax.

### Not yet

Encrypted session state at rest (which is what OIDC needs before it can share a
store), session-key rotation, provider-wide logout, trusted reverse proxies,
richer report parameters/group bands, and broader lookup-query capabilities
remain roadmap work. `tide serve` still runs one uvicorn process: uvicorn wants
an import string rather than an application object to spawn workers itself, so
several processes behind a proxy is the shape that works today.

