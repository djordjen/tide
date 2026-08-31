# Soft validation: warning and info severities — design

Date: 2026-08-31. Status: ratified (acknowledgement gate chosen over
XAF-faithful non-blocking warnings).

## Why

`ValidationSource.severity` has accepted `error | warning | info` since the
initial commit, and `docs/EXPRESSIONS-AND-VALIDATION.md` documents a
`severity: warning` example with the sentence "Errors prevent commit.
Warnings may require confirmation. Informational rules provide guidance
without blocking." Today the service evaluates every declared rule and then
keeps only the error-severity issues (`records.py`, the
`severity == "error"` filter before `ValidationFailed`), so a declared
warning compiles clean, runs on every save, and is shown to nobody. This
slice makes the documented sentence true. The XAF ancestor is the Validation
module's `ValidationResultType.Warning` / `.Information`; TIDE deliberately
chooses a stricter warning contract than XAF (XAF warnings never block),
because over REST and MCP a warning attached to a success response is a
warning no client is obliged to render.

## The contract

One rule, three severities, evaluated where they always were (commit):

- **error** — refuses the commit. Unchanged.
- **warning** — refuses the commit *until acknowledged*. The refusal carries
  the warning issues; the caller resubmits naming the acknowledged rule ids;
  the commit proceeds only if every warning raised is in the acknowledged
  set. A warning that appears between the two requests still gates.
- **info** — never gates. Issues travel on the *success* response as
  notices.

Precisely, at the existing commit-time gate:

- `blockers = errors + [w for w in warnings if w.rule not in acknowledged]`
- `blockers` non-empty → `ValidationFailed(blockers)` (HTTP 422, unchanged
  code `validation_failed`; each wire issue already carries `severity`).
- otherwise the commit proceeds and
  `notices = infos + [w for w in warnings if w.rule in acknowledged]`
  travels with the result. Acknowledged warnings are echoed so the caller
  (and an MCP transcript) shows what was accepted.

Notes that fall out of the shape:

- Acknowledgement is by **rule id**, the string the issue's `rule` field
  reports. A rule firing on several collection rows produces several issues
  with one id; one acknowledgement covers them — XAF's dialog semantics.
  Rule ids are per-entity; a child entity's rule id is acknowledged by the
  same flat set (the issue list is already flat across parent and rows).
- Acknowledging an id that did not fire is ignored, not refused — a client
  may echo back the previous response's ids wholesale.
- Info issues do **not** ride refusals. Refusals carry blockers; success
  carries notices. Two channels, one meaning each.
- Built-in checks (required, minimum, choice, unique, attachments, coercion)
  stay error-severity; only declared rules can soften.
- The `run:` list is untouched: declared rules evaluate at commit exactly as
  before, whatever they list.
- Delete does not evaluate declared rules today and still does not.

## Wire

The request body is the values object (create/update) or the parameters
object (action) on every door, so the acknowledgement travels as a
**repeatable query parameter** on all three:

    POST /api/invoices?acknowledge_warnings=unusual_currency
    PATCH /api/invoices/7?acknowledge_warnings=a&acknowledge_warnings=b
    POST /api/invoices/7/actions/post?acknowledge_warnings=zero_total

Success responses attach notices to the record's existing `_tide` sidecar,
absent when there is nothing to say (the `writable_fields` /

`appearance` pattern):

    "_tide": {"notices": [{"rule": "…", "message": "…", "fields": [],
                            "severity": "info"}]}

- The typed client's sidecar decoder refuses keys it has not been told
  about; `notices` joins its allow-set. (Version skew: an older client
  against a newer server refuses a record whose save raised an info notice —
  the same skew every `_tide` addition has had; `appearance` walked this
  exact path and is named in the decoder's comment.)
- `TideApiClientError` today drops the error envelope's `issues`; it gains
  them, and the remote services stop flattening `ValidationFailed` into one
  `("remote", str(error))` issue — severity and rule ids survive the HTTP
  hop, which is what makes the TUI's remote mode able to offer the gate at
  all.
- **Idempotency**: acknowledgements are protocol, not domain — they stay
  out of the action fingerprint (as `If-Match` does). A gated action attempt
  under an `Idempotency-Key` records FAILED, exactly as an error-severity
  refusal always has, so the acknowledged resubmit uses a fresh key. The TUI
  already generates one per invocation; the docs say so for API callers.

## Service shape

- `RecordSession.notices: tuple[ValidationIssue, ...] = ()` — set by a
  successful commit, empty otherwise. The endpoints that hold the session
  read it there; no return types change on the records service.
- `RecordsService.commit(session, context, *, source=…,
  acknowledged_warnings: frozenset[str] = frozenset())`.
- `ActionService.execute(…, acknowledged_warnings=frozenset())` forwards to
  commit and returns `ActionOutcome(record, notices)` — a typed return
  replacing the bare stored dict (three callers: REST endpoint, MCP runtime,
  TUI form; `RemoteActionService` mirrors it, reading notices off the
  envelope). Replays carry no notices: no validation ran.

## Surfaces

- **REST** — the query parameter on create/update/action; 422 issues carry
  severity (already on the wire model); `_tide.notices` on success;
  OpenAPI documents all three.
- **MCP** — `create` / `update` / action tools gain
  `acknowledge_warnings: list[str] | None`; `TideMcpMutationResult` gains
  `notices`, excluded when empty so whole-document pins keep their shape.
  The agent flow is the feature's best case: the tool result names exactly
  what to acknowledge, and the agent can escalate instead of proceeding.
- **TUI** — save and action paths already funnel `ValidationFailed` into one
  handler each. When every issue is a warning, a modal lists them over
  "Save anyway" / "Cancel"; confirming reruns the same door with the
  accumulated acknowledged set (an action's pre-save commit and the action's
  own commit can each gate — the set accumulates across the retries). Info
  notices arrive as `notify(severity="information")`.
- **Web** — the record form's save failure handler renders warning-only
  refusals as an amber panel with **Save anyway**; the resubmit carries the
  ids. Actions reuse the same panel from the action failure path. Info
  notices toast on success. Inline browse editing (grid cell) shows the
  warning message but offers no acknowledgement in v1 — the record form is
  where a person weighs a warning (deliberate out, below). Child-row
  warnings surface at parent save and are covered by the form panel.

## Reference application

Two seed-proof rules in invoicing (fake_data prices are random decimals and
quantities cap at 20, so neither can refuse a seed):

```yaml
# sales.Invoice
- id: unusual_currency
  assert: "currency == 'EUR'"
  severity: warning
  message: The invoice currency is not EUR.
  fields: [currency]
```

```yaml
# sales.InvoiceLine
- id: unusual_quantity
  when: "quantity != null"
  assert: "quantity <= 100"
  severity: warning
  message: The line quantity is unusually large.
  fields: [quantity]
```

The line rule exercises the child-row path end to end. The action door is
covered in the Python suite by a fixture entity whose warning fires on a
transition commit.

## Deliberate outs

- Inline browse editing gets the message, not the acknowledge affordance.
- No "don't warn me again" persistence — acknowledgement is per request,
  and a later save of the same record gates again (XAF re-shows warnings on
  every save; same here).
- No audit persistence of acknowledgements beyond what already exists (a
  gated attempt is a failed audit event exactly as an error refusal is).
- No new expression capability; `when`/`assert` unchanged.
- `on_change` (in-form, pre-commit) warning display deferred.
- Delete-context rules deferred (XAF's Delete context; TIDE's delete door
  evaluates no declared rules today).

## Tests

- Service: gate/acknowledge/superset/info/mixed matrices; notices only on
  success; child-row ids; acknowledged-but-unfired ignored.
- Actions: `ActionOutcome`, gate on transition commit, fresh-key contract
  (burned key still conflicts — existing behavior, now pinned with a
  warning).
- API: query param round-trip on all three doors; `_tide.notices`; 422
  severity; OpenAPI.
- Client/remote: issues survive the hop; sidecar allow-set; remote services
  forward and reconstruct.
- MCP: tool arguments; result notices; whole-document pins updated
  symmetrically where `notices` appears.
- TUI: pilot drives warn → confirm → saved, and cancel → unsaved.
- Web: vitest for the panel, Save anyway resubmission, info toast; a
  Playwright journey drives the currency rule end to end; the panel obeys
  375px (measured, not assumed).
