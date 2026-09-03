# Mass update: one change applied to a selection, answered row by row

**Ruling (2026-09-03):** proceed with mass update, selection only, per-row
outcomes. This spec records the design those two rulings produce.

## The gap

TIDE has grown strong tools to *find* a set of rows — global search, column
operator filters, funnels, saved views — and no tool to *act* on the set.
Export is the lone set-shaped verb and it is read-only. The XAF reflex after
filtering ("retire these products", "move these customers to EUR") has no
answer on any surface: each record is opened and saved by hand.

Verified before designing: the web grid has no multi-row selection of any
kind; `RecordsService`'s write surface is strictly single-record
(`create`, `begin_edit`/`commit`, `delete`); nothing set-shaped exists on the
wire; browse page rows are full record models, so a declared concurrency
token's *value* already rides every row — only its *name* is missing from the
manifest.

## The contract in one sentence

Mass update is the existing single-record update applied N times by the
service, under the existing `update` permission, with every per-record rule —
row policies, field policies, `immutable_when`, validation, soft-validation
acknowledgement, optimistic versions, audit — enforced per row exactly as a
hand-made edit would meet it, and the answers reported per row.

**No new YAML.** There is no `mass_update:` declaration and no new permission
dimension: which fields may be assigned is what the update contract already
says, and who may assign them is who may update. One rule, declared once.

## The seam (learning 45)

The loop lives in the service, not in renderers. `RecordsService.mass_update`
runs it locally; `RemoteRecordsService.mass_update` implements the same
signature with **one** HTTP call to a new door. A renderer-side loop would be
2N requests and a copied loop per surface; per-row outcomes and the
acknowledgement round-trip make the loop itself the rule that must not be
spelled twice.

Everything that crosses the wire is values: field values (the update payload
encoding), identities, version assertions, outcome codes. No expressions, no
sessions.

### Service surface

```python
@dataclass(frozen=True, slots=True)
class MassUpdateTarget:
    identity: Any
    expected_version: int | NullVersion | None = None

@dataclass(frozen=True, slots=True)
class MassUpdateRowOutcome:
    identity: Any
    status: Literal["updated", "refused"]
    code: str | None = None                     # refusal code, None when updated
    message: str | None = None
    issues: tuple[ValidationIssue, ...] = ()    # validation refusals, severity kept
    notices: tuple[ValidationIssue, ...] = ()   # a successful write's info notices
    version: int | None = None                  # stored version after update

@dataclass(frozen=True, slots=True)
class MassUpdateResult:
    outcomes: tuple[MassUpdateRowOutcome, ...]  # request order preserved
    updated: int
    refused: int

def mass_update(
    self,
    entity_name: str,
    changes: Mapping[str, Any],
    targets: Sequence[MassUpdateTarget],
    context: RequestContext,
    *,
    acknowledged_warnings: frozenset[str] = frozenset(),
) -> MassUpdateResult
```

`changes` is a mapping so the contract is ready for several fields per apply;
the dialogs offer one field at a time (UI simplicity, not a wire limit).

### Whole-request refusals versus per-row refusals

Two error classes, deliberately different:

- **Declaration-level** problems refuse the whole request before any row is
  touched, because they are client bugs, not domain answers: an unknown or
  non-assignable field in `changes` (identity, version token, `readonly`,
  computed, collection fields), empty `changes`, empty `targets`, more than
  **1,000** targets. The assignable-field check derives from the same
  metadata the generated update input model derives from — a derivation, not
  a second list.
- **Per-row** refusals are outcomes, never HTTP errors, because each target
  is independent and one posted invoice must not hold its siblings hostage:

  | code | raised by |
  |---|---|
  | `not_found` | the row is gone |
  | `forbidden` | row/field policies hide or refuse the row for this principal |
  | `stale_version` | the asserted version no longer matches |
  | `version_precondition_required` | versioned entity, target carried no assertion |
  | `immutable_field` | `immutable_when` is true on this row (e.g. posted invoice) |
  | `validation_failed` | issues attached, warnings marked by severity |
  | `invalid_identity` | the identity does not coerce to the key's type |

Each row is its own commit, exactly as if edited by hand: a refusal of row k
rolls back nothing about row j. Duplicate identities in `targets` are not
deduplicated — the second occurrence meets `stale_version` honestly.

### Soft validation across a set

The acknowledgement gate composes with no new machinery because
acknowledgement is **by rule id**: the first attempt refuses warning rows
with their issues; the client shows the distinct warnings once and resubmits
**only the warning-refused targets** with `acknowledge_warnings` (repeatable
query values, same spelling as create/update/action). Info notices ride each
updated outcome as `notices`, the same way `_tide.notices` rides a single
write.

### Audit

Per-row update events already flow from `commit` and are the truth. There is
deliberately **no** set-level `records.mass_update` event: unlike export
(which has no row events and so writes a summary), every row here writes its
own change record, and a summary would be a second copy of one truth.

## The wire

`POST {resource_path}/_mass-update` — the `_query`/`_distinct`/`_export`
family. ("Mass update", not "bulk": this repo's vocabulary already binds
*bulk* to the deferred import.)

Request body (per-entity generated model, so `changes` is typed by the same
generated update input model the PATCH door uses — `extra="forbid"`,
`exclude_unset` semantics preserved through the nested model):

```json
{
  "changes": { "active": false },
  "targets": [
    { "identity": 7, "version": 3 },
    { "identity": 9, "version": "null" }
  ]
}
```

`version` spells the assertion exactly as `If-Match` does: an integer, the
string `"null"` for a row whose adopted token is NULL (the write heals it),
or omitted only for an entity that declares no version field.
`acknowledge_warnings` rides as the existing repeatable query parameter.

Response is always `200` with a static result model — partial success is a
successful report, refusals live inside:

```json
{
  "outcomes": [
    { "identity": 7, "status": "updated", "version": 4 },
    { "identity": 9, "status": "refused", "code": "immutable_field",
      "message": "field 'currency' cannot be changed: ..." }
  ],
  "updated": 1,
  "refused": 1
}
```

Issue lists reuse `TideApiValidationIssue` (rule, message, fields, severity)
so a remote renderer can tell a warning it may acknowledge from an error it
must fix — the same property the single-record envelope already guarantees.

No `Idempotency-Key`: versions make a replay answer `stale_version` per row,
which is the honest report of "already applied".

## The manifest skew gate

`TideBrowsePresentation` gains one optional object:

```
mass_update: { path: str, version_field: str | null, limit: int } | null
```

Present only when this server generates the door **and** the principal holds
`update` — so absence means "do not offer", covering the old-server and the
no-permission cases with one signal (the `TidePresentationLookup.source`
pattern). `version_field` names the declared concurrency token whose value
the client reads off the rows it already loaded — `null` means the entity
declares none and targets travel without assertions. No `_tide` widening:
the version's value is already on every page row as an ordinary readonly
field; the manifest only supplies its name.

## Web

- **Selection**: a leading checkbox column on the grid (vendored Radix
  `ui/checkbox`, each box spelling its own `aria-label`), a header checkbox
  toggling every *loaded* row, Space toggling the focused row without
  breaking the roving-tab-stop grid pattern. Selection is a `Set` keyed by
  identity: it survives sorting, incremental fetches and the post-apply
  refetch; it clears when membership changes meaning — search, column
  filters, named filter, saved view, or view switch.
- **Affordance**: with a non-empty selection the browse toolbar shows the
  count, **Change…** and **Clear selection**. Offered only when the manifest
  carries `mass_update`.
- **Dialog**: pick one field (the detail form's editable, non-collection,
  non-file fields), edit one value with the field's own editor type —
  including a reference picker through the existing lookup dialog, which
  keeps honoring the field's `lookup_filter` edge — then Apply. The result
  panel reports "N updated, M refused" with each refusal's row and reason.
  When every refusal is warnings-only, an amber **Apply anyway** resubmits
  exactly those targets with the warnings acknowledged by rule id.
- 375px stays a requirement like everywhere else.

## TUI

`BrowseDataTable` gains a marks model: Space marks/unmarks the highlighted
row (rendered marker column, count in the footer area), and a **Mass update**
control joins the browse action bar when marks exist. The dialog follows the
parametrized-action screen's shape: a `Select` of assignable fields, the
field's own editable widget for the value, Apply. Warnings reuse
`WarningsScreen` for the acknowledge round-trip; outcomes render as a small
refusals table with counts. Local and remote modes both work because the
seam is the service interface.

## MCP

Abstains, like duplicate-record and global search: an agent can already loop
the single update tool, and the per-row outcome report is a human reviewing
a set, not a machine contract. Revisit on demand.

## Reference apps

Nothing to declare — the feature is pure runtime. The e2e journey uses
invoicing as-is: mass-retire two products created by the journey (they leave
the line picker but keep their browse rows — the filtered-lookups story), and
a mixed invoice selection showing one row refused by immutability beside one
updated.

## Deliberate outs (recorded in DECISIONS)

- **Filter-set scope** ("apply to everything matching") — out by ruling;
  selection only.
- **Mass delete** — a different, destructive verb; not smuggled in.
- **File fields** in the dialogs — a staged upload claims once; assigning one
  token to N rows is nonsense. The service refuses nothing special; the
  dialogs simply do not offer them.
- **Set-level audit event** — per-row events are the truth.
- **MCP tool** — abstains.
- **Multi-field dialogs** — the wire takes a mapping; the UI offers one field
  per apply until real use asks otherwise.

## Test plan

- `tests/test_mass_update.py`, cross-layer, both repositories: a fixture app
  with a versioned entity (immutable-when-locked field, a warning rule, an
  update row policy hiding one row, a field policy, a filtered reference)
  plus an unversioned entity; service matrix covering every refusal code,
  order preservation, duplicate targets, the acknowledgement round-trip,
  notices, and the declaration-level refusals; REST door (typed changes,
  "null" version spelling, per-row invalid identity, 400s); remote
  whole-stack over MockTransport pinning the serialized body and the
  acknowledge query values; manifest `mass_update` presence/absence
  (permission-less principal, unversioned entity's `version_field: null`).
- TUI pilots: marking, the dialog applying a change, a refusal rendered, the
  warnings screen acknowledging.
- Web vitest: selection lifecycle rules, the api body pin (targets +
  version spelling, acknowledge resubmit), dialog outcome rendering, the
  skew gate (no `mass_update` key → no affordance).
- One Playwright journey as above; the existing whole-dict manifest pins in
  `test_api_server.py` will strike and are budgeted.
