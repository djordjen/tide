# Parametrized actions

Date: 2026-08-30. Status: approved (scalars only; parameters may not appear
in `enabled_when`; reference-typed parameters stay deferred with TIDE306).

## What

A domain action can declare typed input it needs at the moment of
execution — "Void with a reason", "post as of a date" — and one YAML block
lands on all four surfaces: a REST invoke body, arguments on the generated
MCP tool, a dialog in the Web form, a dialog in the TUI form.

```yaml
actions:
  void:
    label: Void
    permission: sales.invoice.void
    execute: actions.void_invoice
    parameters:
      reason: {type: string, required: true}
```

## The shape is the report-parameter shape

Reports already declare parameters (`ParameterSource`: `type` of
string/integer/decimal/boolean/date/datetime, `required`, `default`), the
service already coerces and refuses them with per-name issues, the MCP
server already builds a typed arguments model from them, and both human
surfaces already render a dialog for them. Actions reuse every piece:

- `ActionSource` gains `parameters: dict[str, ParameterSource]`. Same
  scalar types, same `required`/`default` semantics. No labels: the
  renderer humanizes the name, exactly as report dialogs do.
- The runtime coercion moves from `tide.reporting.service` privates into
  `tide.runtime.parameters` (`coerce_parameters(definitions, supplied,
  rule=...)`), imported by both the report service and the action
  service. One coercion rule, declared once; the issue `rule` string is
  the only difference (`report_parameter` / `action_parameter`).
- The wire descriptor `TideReportParameter` is renamed to the neutral
  `TideParameter` (`name`, humanized `label`, `type`, `required`) and is
  what both reports and actions project. Client-side interface names
  follow; no JSON changes shape.

## One rule, one place

`ActionService.execute` validates the payload against the declaration —
always, for every door. Unknown names, missing required values, and
uncoercible values are refused together in one `ValidationFailed` whose
issues carry rule `action_parameter` and the parameter name as the field
— arriving over REST as the house 422, exactly as report parameters do.
An action that declares no parameters accepts only an empty payload — the
same refusal today's transports enforce separately, now owned by the
service. Transports carry and never judge:

- REST: the invoke body becomes the parameters object itself
  (`dict[str, Any]`, defaulting to `{}`), exactly like the report route.
  `TideEmptyActionPayload` is deleted. Today's `{}` bodies stay valid.
- MCP: `RuntimeMcpService.execute_action` drops its own empty-payload
  refusal and forwards. The generated tool gains a `parameters` argument
  built by the same model builder the report tools use — absent when
  nothing is declared, required when any parameter is, optional
  otherwise — so the tool schema itself teaches the caller.

Coercion happens before the idempotency fingerprint is computed, so the
string form and the typed form of the same value replay as the same
request, and defaults participate in the fingerprint. The existing
fingerprint-mismatch refusal already covers "same key, different
parameters"; nothing new is needed.

`enabled_when`/`visible_when` never see parameters: they guard the
button, which exists before the input does.

## When a dialog opens

An action with at least one **required** parameter opens a dialog; the
dialog offers **every** declared parameter. An action whose parameters
are all optional executes in one click with an empty payload — optional
parameters are a programmatic door (REST/MCP), not a question to ask a
person on every click. This keeps Post a single keystroke while making
Void ask for its reason.

- Web: a popover form on the action button in the record detail (the
  column-chooser/save-view pattern; fits 375px), text inputs per
  parameter, submit executes with the collected values as strings — the
  service coerces, and a refusal renders through the existing action
  error surface.
- TUI: the report parameters modal generalizes (`tide/tui/parameters.py`,
  ids unchanged) and `_execute_record_action` pushes it first when
  required parameters exist, then executes with the collected raw text.

## Compile-time checks

Parameter names must be identifiers (`[A-Za-z_][A-Za-z0-9_]*`): they
become Pydantic model fields on the MCP tool and `$`-style report names
already assume it. Nothing else is checked at compile time — defaults are
coerced per execution, as reports do.

## The invoicing demo

- `void` declares `parameters: {reason: {type: string, required: true}}`;
  a new `cancelled_reason` field (string 200, readonly, `write:
  action_only`, audited) records it; the handler writes it alongside the
  existing stamps. The void passthrough of `occurred_at` is removed —
  nothing used it.
- `post` declares `parameters: {occurred_at: {type: datetime}}` —
  optional. This is the seam `fake_data` already uses to backdate seeded
  posts through the real pipeline; declaring it is a deliberate widening
  (REST/MCP callers holding `sales.invoice.post` may now post as-of),
  and Post stays one-click because the parameter is optional.
- The browse `available_columns` pin grows from 11 to 12 fields — a
  deliberate contract change.

## Out of scope

Reference-typed parameters (TIDE306 stands). Parameters in
`enabled_when`. Stamp values sourced from parameters (the handler owns
business writes). Choice-typed parameters and length caps on the
declaration — the storage layer already refuses over-length values with
its own named rule.

## Testing

Compiler: declaration accepted, bad names refused. Service: unknown /
missing / uncoercible refused together; defaults applied; declared-empty
actions refuse payloads; coercion-before-fingerprint proven by replaying
the string and typed forms under one key. REST: parametrized invoke,
house 422 with `action_parameter` issues, `{}` still valid for
parameterless actions. MCP: tool schema carries the parameters model;
execution forwards. TUI: pilot drives the Void dialog. Web: vitest for
the popover, one Playwright journey voiding with a reason and reading
`cancelled_reason` back.
