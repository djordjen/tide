# File attachments — design

**Date:** 2026-08-29 · **Status:** built; see "What the building changed"
at the end · **Decided with:** Djordje (scoping rulings noted inline)

## What this is

Records gain documents through a new field type: `file`. A file field is the
XAF `FileData` shape executed through TIDE's own field machinery — the schema
names the document's role (`signed_document`, `quotation`, `warranty`), and
everything fields already have applies unchanged: label, help, layout
placement, field security, audit, validations, workflow locks. The bytes live
outside every database on a configured filesystem root; the application
column stores only the attachment's GUID; the metadata row lives in a
framework-owned table in the application database.

An entity that needs an unbounded set of documents does not get a second
system: that case is a collection of file-bearing child rows, composed from
this primitive and the collections that already exist. v1 documents that
composition and builds only the field type.

## Rulings that shaped the design

- **Record-owned.** An attachment belongs to the record whose field holds it
  and has no life of its own. A shared document library referenced from many
  records is explicitly out — a possible later feature, not a v1 cost.
- **Web + REST in v1. TUI and MCP abstain from content, deliberately.**
  The abstention is from *operations on bytes*, not from the field: the
  terminal renders a file field read-only — filename and size from the
  projection, no upload, download, delete or replace — because terminal file
  pickers are awkward; MCP record reads carry the projection like any field
  and its generated mutations may write the field only within the ordinary
  claim rules (in practice: `null`, or the unchanged current value — an
  agent cannot stage bytes, so it cannot claim new ones), and there is no
  content tool, because handing file bytes to an agent is an
  exfiltration-surface decision to make on its own. Both abstentions are
  recorded in DECISIONS.
- **Attach yes, remove no on workflow-locked records.** A lock freezes a file
  field *only once it holds a file*: an empty `signed_document` on a posted
  invoice may still be filled (the countersigned PDF arrives after posting),
  but a filled one can be neither cleared nor swapped. An author wanting a
  full freeze writes `immutable_when` explicitly.
- **Metadata in the application database, not beside the files.** An earlier
  draft placed a SQLite metadata store at the attachments root; review killed
  it. The GUID columns live in the application database regardless, so a
  separate metadata store means a restore must align three units instead of
  two; a stray SQLite file beside a SQL Server database is operational
  clutter; and SQLite-with-WAL on the network share an operator will
  eventually choose for the root is a corruption hazard. The metadata table
  joins the existing `FrameworkStores` contract instead, so `--create-schema`,
  startup validation, `tide db diff` and backup verification all pick it up
  from the one declaration, on SQLite and SQL Server alike.
- **Managed-database feature in v1.** A legacy application declaring a file
  field is refused at compile time with a diagnostic saying so: no-DDL means
  the GUID column could not be created there anyway, so the refusal is honest
  and early. If a legacy deployment with a spare string column ever presents
  real demand, the store contract leaves room; the deferral goes in DECISIONS.
- **Invoice only in the reference application**: one field,
  `signed_document`, demonstrating the lock carve-out. Other entities opt in
  later with one line each.

## Declaration

`file` joins the closed `FieldType` set in `model/source.py`.

```yaml
signed_document:
  type: file
  label: Signed document
  max_size: 10mb          # required; refused above the compiled ceiling of 100mb
  accept: [pdf, png, jpg] # optional; extensions, lowercase; absent = any type
  audit: values
```

Rules, each with a compile diagnostic (codes assigned in implementation
order, not here):

- `max_size` is required, parsed from `kb`/`mb` forms, and refused above a
  hard ceiling of 100 MB — a bound the author must state, inside a bound the
  framework states.
- `accept` entries are lowercase extensions without dots; an empty list is
  refused (write nothing instead).
- A file field cannot be `computed`, `unique`, `primary_key`,
  `concurrency_token`, or carry `choices`/`values`/`target`; `required` is
  allowed and interacts with Delete (below).
- Legacy database mode refuses the type outright.

Storage is a nullable 36-character string column holding the GUID. Managed
schema creation emits it like any scalar column.

## Storage

**Bytes.** The root comes from `--attachments-root` or
`TIDE_ATTACHMENTS_ROOT` — deployment configuration, never YAML. If any
entity declares a file field and no root is configured, `tide serve` refuses
to start: files are business data, and a capability that silently drops them
is not a degraded mode. `tide run` needs no root — the terminal never
touches bytes (see the abstentions below), and record reads use only the
metadata rows. Layout:

```
<root>/
  tmp/            # in-flight uploads, named by GUID
  ab/             # shard = first two hex characters of the GUID
    ab3f9c…       # the bytes, GUID name, no extension
```

The file on disk carries no extension: the metadata row is the authority on
what the bytes are, and an extensionless tree cannot be accidentally served
or executed by a misconfigured web server. 256 shards, path computable from
the GUID alone, no second level (65k files per shard before it strains —
revisit at real scale, not before).

**Metadata.** A framework-store table (name following the existing
framework-table convention) with columns: `guid` (primary key), `entity`,
`field`, `record_id` (null while staged), `filename` (the original, display
only, never a path component), `extension`, `content_type` (client-claimed,
stored verbatim), `size`, `sha256`, `principal`, `uploaded_at`. Invariant:
a GUID is claimed by at most one `(entity, field, record_id)`.

**Two implementations of one store contract** — in-memory for tests and
`--demo` (which also skips the root requirement), SQLAlchemy in the
application database for real serving — the exact shape the audit and
idempotency stores already have.

## Upload — two-phase

A file must exist before a record can reference it (a create has no record
yet), so upload stages first:

1. `POST {resource}/_files/{field}` (multipart) requires the entity's create
   or update permission plus write on that field. The stream is capped at the
   field's `max_size` *while receiving* — never buffered whole — hashed as it
   flows, written under `tmp/`, fsynced, renamed into its shard; then the
   metadata row is inserted, staged and unclaimed. `accept` is enforced
   against the uploaded filename's extension. The response is the wire
   projection below.
2. The ordinary record write (create or changed-field PATCH) sets the field
   to the GUID.
3. At commit the service verifies the GUID names a row that is *staged,
   unclaimed, and uploaded by this principal* — or is already this field's
   current value. Anything else is refused as a validation issue on the
   field. Claiming stamps `record_id`.

Nothing can reference another record's file, another principal's staged
upload, or a GUID that does not exist.

## Wire contract

**Reading.** A record carries a file field as `null` or

```json
{"identity": "ab3f9c…", "filename": "confirmation.pdf",
 "size": 48211, "content_type": "application/pdf"}
```

— display needs only, never a path. An unreadable field stays the existing
`PROTECTED` sentinel. Browse pages carry the same projection, so a filename
column costs nothing new.

**Writing.** The mutation payload carries the GUID string, or `null` to
delete. The OpenAPI schema says so.

**Download.** `GET {resource}/{record}/_files/{field}` — record-scoped on
purpose, so entity permission, row policies, and field read security all
apply before a byte moves. The response streams with
`Content-Disposition: attachment; filename=…` (the original name, RFC 6266
encoded), the stored content type, `X-Content-Type-Options: nosniff`, and
exact `Content-Length`. Missing and forbidden answer exactly as the record
routes do today, so no new existence oracle opens. An empty field is 404.

## Lifecycle: Download, Replace, Delete

A filled, writable, unlocked field offers all three; the rules:

- **Delete** sets the field to `null` and touches nothing else on the row.
  Refused (control absent, service refuses) on a workflow-locked record and
  on a `required` field — a mandatory document can be swapped, never removed,
  otherwise Delete manufactures a record validation must refuse.
- **Replace** is one atomic save, never delete-then-upload: the new file
  stages, the field's GUID swaps, the save claims the new row and un-claims
  the old. The record never passes through an invalid empty state; a failed
  save leaves the original untouched. Refused on a locked record with a
  filled field.
- **Record delete** un-claims the record's file rows in the same service
  operation.

From the person's view a deleted or replaced attachment is gone immediately —
the row un-claims and the field empties. The *bytes* are reclaimed by the
sweep after a grace period (default 24 hours), which protects a concurrent
download mid-stream, keeps crash windows benign, and covers abandoned staged
uploads from cancelled drafts. `tide attachments check --sweep --grace 0`
exists for an operator who needs bytes gone now.

**Audit.** Field-level `audit: values` records the GUID transition in the
existing trail (the Web History tab renders it). Upload, download, delete and
replace each write an audit event naming principal, entity, field, record and
filename — the `records.export` event shape.

## Web UI

No new panel and no new modality: the form renders a file field where the
author's layout places it.

- Empty and writable → a file picker. Selecting a file uploads to staging at
  once (with progress); the draft holds the returned GUID; save claims it;
  cancelling the form abandons it to the sweep.
- Filled → filename and size, **Download**, and **Replace**/**Delete** per
  writability, lock state, and the required-field rule.
- Read-only or locked-and-filled → filename, size, Download only.
- `PROTECTED` → the existing protected treatment.
- Errors — too large, wrong type, claim refused — surface on the field like
  any validation issue, in the server's words.
- Works at 375 px like every other field control.

## Operations

`tide attachments check` reconciles three defect directions: claimed rows
whose file is missing, files no row names, and hash mismatches between row
and bytes. `--sweep` additionally reclaims unclaimed rows and their bytes
past the grace period. Exit code and output follow the backup-verification
command's conventions.

**Backup story, stated in OPERATIONS.md:** the database backup captures GUID
columns and metadata atomically under the existing verified contract; the
attachments root is a second unit — a plain file tree, copied by any means
the operator trusts — and `tide attachments check` is the integrity check
that ties the two together after a restore.

## Security summary

| Operation | Checks, in order |
| --- | --- |
| Stage upload | entity create-or-update permission → field write → `accept` → `max_size` (mid-stream) |
| Claim at commit | staged + unclaimed + same principal, or already current value → lock carve-out → required rule |
| Download | entity read → row policy → field read |
| Delete/Replace | as an ordinary field write → lock carve-out → required rule |
| Sweep/check | CLI only, operator context, no HTTP surface |

Content is never executed or interpreted: client-claimed content type is
stored and echoed with `nosniff`, filenames are display data, bytes are
opaque.

## Deliberately not in v1 (recorded in DECISIONS)

TUI and MCP abstain; no shared document library; no thumbnails or inline
previews; no virus scanning — the perimeter's job, said honestly in the
docs; no migration of a legacy application's existing blob or path schemes;
no second shard level; the unbounded-attachments case ships as documentation
of the collection composition, not as code.

## Testing

- Store contract tests run against both implementations, like the repository
  conformance suite.
- Service tests pin the claim invariant (wrong principal, already claimed,
  nonexistent GUID, cross-record theft), the lock carve-out (fill yes, swap
  and clear no), the required rule, and bound enforcement.
- REST tests pin 401/403/404 parity with record routes, the projection
  key-set, streaming size caps, and download headers.
- One Playwright journey on the real stack: upload to a draft invoice, save,
  post, download, verify the filled field refuses Replace and Delete.
- `tide attachments check` tests cover all three defect directions plus
  sweep grace behavior.
- Sabotage passes for the security table rows, per house practice.

## What the building changed

Recorded because a design read later should not quietly disagree with the
code. Everything above stands except these.

**Uploads are a raw streamed body, not multipart.** FastAPI needs
`python-multipart` for form parsing and the repository has no such
dependency; the file is the request body instead, with its name in
`X-Tide-Filename` (percent-encoded). The two-phase flow is unchanged, and
the bound is still enforced while the body arrives.

**The journey attaches after posting, not before.** The spec's journey
uploaded to a draft and then posted. Doing it in the other order proves the
carve-out instead of merely exercising the control, so the journey posts
first and attaches to the frozen record.

**A posted invoice is no longer drawn by the read-only renderer.** It is no
longer entirely read-only -- it keeps one writable field -- so the browser
uses the editable renderer with a single control on a screen of values. The
read-only renderer still draws records nobody may edit at all, and it
learned about file fields too: it had shown a filename with no way to fetch
the file.

**Runtime log events omit the filename.** The reviewed log-field allowlist
stays as it is; the metadata row and the field's own audit trail carry the
name, which is where the history tab reads it from anyway.

**`count(file)` is refused rather than allowed.** The summary set is derived
from the field types, so a file field had silently become countable.
Counting documents by presence is a real question and a real contract
decision; it is not a side effect of a derivation, so it is out until it is
asked for.

**Deferred, and worth knowing:** generated scaffolding can emit a
`type: file` document without `max_size`, which fails compilation one stage
later with a source-located `TIDE287` rather than at generation. `PlannedField`
would need the two new keys to close it.
