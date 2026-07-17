# AI-Assisted Application Generation

## Two MCP trust boundaries

TIDE deliberately separates two MCP servers:

- **Runtime MCP** operates on authorized data in a deployed application.
- **Developer MCP** inspects and proposes changes to application definitions.

A runtime identity must never gain source-editing authority merely because it
can query business records. Conversely, a local development agent does not
receive production database credentials through the developer server.

## Intended conversation

A user should eventually be able to ask an MCP-capable AI client:

> Create an invoicing system for XY Company with companies, products,
> invoices and line items. Export invoices to PDF. Invoice creators may create
> drafts, while invoice posters may post them.

The developer MCP translates that intent into logical TIDE operations, for
example:

```text
create_application
define_entity crm.Company
define_entity catalog.Product
define_entity sales.Invoice
define_entity sales.InvoiceLine
define_state_transition sales.Invoice.post
define_record_report sales.invoice
define_role invoice_creator
define_role invoice_poster
```

These are typed model operations, not instructions to write arbitrary paths or
execute generated shell/Python content. A state transition is a constrained
workflow template with a choice state field, permitted source/target states,
an optional required collection, and optional timestamp/principal stamps. A
record report identifies existing fields and a detail collection; PDF is a
renderer capability over the declarative report rather than generated PDF code.

## Implemented proposal and candidate boundary

Install the optional MCP adapter and start the local developer server:

```bash
uv sync --extra mcp
uv run tide mcp dev applications/invoicing
```

The command uses stdio, writes no banner to stdout, and currently exposes:

```text
tide://developer/project
tide://developer/application
tide://developer/model
tide://developer/entities/{qualified-name}
tide://developer/views/{qualified-name}

tide_validate_project
tide_list_entities
tide_describe_entity
tide_get_resolved_view
tide_preview_openapi
tide_propose_application
tide_preview_application
```

`tide_propose_application` accepts a discriminated list of the structured
operations above. It validates identifiers, primary keys, relationships and
inverses, state-transition fields/choices, report fields/detail columns, role
grants, and declared permissions. Its deterministic result contains a proposal
ID, normalized operations, warnings/errors, and these explicit flags:

```json
{
  "approval_required": true,
  "writes_performed": false
}
```

`tide_preview_application` accepts the same plan. A valid proposal is rendered
with deterministic TIDE-owned formatting into an operating-system temporary
directory, compiled with the normal TIDE compiler, and checked against the
proposed entity, permission/role, constrained workflow, and record-report
contracts. It also generates conventional browse, form and lookup views plus
inline editors for cascade-owned collections. Field order in the structured
entity operation drives the default form/inline layout.

Generated transitions and sequence numbers come only from fixed framework
templates. After compilation and static checks succeed, those templates may be
imported and executed against a fresh `InMemoryRepository`, `RecordsService`
and `ActionService`. The smoke run creates one bounded synthetic record per
creatable entity, verifies unauthorized create/action/report denial, secured
CRUD, state/stamp changes, idempotent replay, report-document construction,
standalone HTML, and optional PDF output. It never runs a caller-supplied test
or external command and never opens the application's configured database.
Missing optional PDF support and plans above the documented smoke limits are
reported as visible skipped checks, not silently treated as executed checks.

The result contains every artifact's exact UTF-8 content and SHA-256 digest, an
exact new-tree unified diff, a proposal ID, an empty-base fingerprint, and a
candidate fingerprint. Compiler diagnostics use candidate-relative paths. The
temporary tree is deleted before the result is returned, and the contract says
so explicitly:

```json
{
  "approval_required": true,
  "workspace_writes_performed": false,
  "candidate_persisted": false,
  "external_commands_executed": false,
  "application_database_accessed": false,
  "fixed_template_code_executed": true,
  "in_memory_runtime_checks_performed": true,
  "temporary_candidate_deleted": true
}
```

The temporary directory and in-memory repository provide data/source
isolation, not an operating-system security sandbox. Execution is safe at this
stage because the input schema has no Python/module/path operation and every
executable line comes from a reviewed TIDE template with typed, escaped
identifiers/literals. A future custom-code operation must not use this execution
path; it needs a separate conspicuous approval unit and a real sandbox.

The developer MCP service accepts no destination path and exposes no
apply/write tool. It can therefore help an AI and user refine and compile a
design without silently creating or changing workspace files. Project
inspection also remains available when compilation fails: validation,
proposal, and isolated candidate preview remain advertised until the existing
project becomes valid.

## Local approval and apply flow

The same structured plan passed to the MCP proposal/preview tools can be saved
as JSON and prepared from the repository root without writing anything:

```bash
uv run tide app preview plan.json --workspace .
uv run tide app preview plan.json --workspace . --json
```

Preparation reruns the complete candidate preview, inspects the actual
`applications/<application-id>` destination, and binds an approval ID to the
proposal ID, canonical destination, absent-base fingerprint, candidate ID, and
candidate fingerprint. It rejects existing or case-colliding targets, unsafe
or symbolic-link application roots, invalid previews, and inconsistent
artifact hashes.

Application is a separate interactive command:

```bash
uv run tide app apply plan.json --workspace .
```

The command displays the exact candidate diff and requires the user to type
the complete `APPLY tide-approval-...` challenge. There is deliberately no
`--yes` switch. After confirmation, TIDE regenerates and revalidates the plan,
rechecks the destination and all bound fingerprints, takes an exclusive
per-application apply lock, writes into a temporary sibling directory, verifies
the staged bytes, compiles that exact tree, and publishes it by a same-filesystem
rename. Existing applications are never edited or replaced. Failure removes
the staging tree and TIDE-owned lock.

A successful new application contains `.tide-apply.json` with the approval,
proposal, base, candidate, diff-hash, and artifact-hash receipt. The applied
files make the exact approved source change visible to source control. Reusing
the approval fails because the destination now exists.

The developer MCP itself remains read/propose/preview-only. A future MCP apply
tool must obtain a real host-level human approval and then call this service;
it must not manufacture an approval merely because it can read preview output.

## Approval stages

The new-application flow now covers all eight stages:

1. Materialize a valid proposal into an isolated candidate source tree;
   **implemented for new applications**.
2. Compile the candidate with the normal TIDE compiler; **implemented**.
3. Run bounded static entity, presentation, security, workflow,
   handler-registration, generator, report and cleanup checks; **implemented**.
4. Return relative compiler diagnostics, exact artifacts/digests, an exact
   source diff, and proposal/base/candidate fingerprints; **implemented**.
5. Run bounded isolated in-memory persistence, authorization, CRUD, action,
   idempotency, report-document, HTML and optional PDF checks using only fixed
   TIDE templates; **implemented**.
6. Bind an explicit approval to the proposal ID, actual destination base
   fingerprint and exact candidate fingerprint; **implemented by local
   approval preparation**.
7. Apply only through an explicit user-approved command, refusing an existing
   new-application target or a stale base; **implemented by the interactive
   local CLI with atomic publication**.
8. Preserve the approved diff for source control, undo and audit;
   **implemented through the applied source tree and `.tide-apply.json`
   receipt**.

The preview base fingerprint identifies an empty logical tree at
`applications/<application-id>`; local approval preparation now checks that the
actual destination is absent before binding it. Editing an existing application
still needs a comment-preserving round-trip strategy, stable model paths,
conflict handling, and explicit rename/delete semantics. Until those contracts
exist, TIDE applies new applications only.

Custom business logic remains ordinary trusted Python, but AI generation does
not use an unrestricted Python-writing tool. The current state-transition code
is a reviewed TIDE-owned template whose identifiers and literal values come
from typed operations; timestamp stamps use the server clock rather than a
caller-supplied value. Date-aware sequence numbering is likewise a constrained
template with prefix, separator, width and local date-field inputs. A future
proposal requiring custom Python must present
that code and its tests as a separate, conspicuous approval unit.

## Client and hosting direction

The first developer server is local stdio because it shares the developer's
source workspace and does not use the HTTP authorization flow intended for
remote resource servers. An MCP-capable AI development client launches the
command and consumes protocol messages on stdin/stdout. A future remotely
hosted TIDE Studio service will need authenticated workspace isolation,
repository authorization, proposal ownership, quotas and audit before it can
offer the same capabilities safely to browser-based AI clients.
