# Threat Model

**Status: Accepted baseline; expand with each adapter.**

## Protected assets

TIDE protects application records, credentials, authorization policy,
protected-field values, audit history, migration intent, metadata source, and
the integrity of Python business handlers.

## Trust boundaries

Untrusted input crosses boundaries through YAML/JSON projects, TUI fields,
REST requests, MCP tools, imports, report parameters/resources, and custom
Python modules. Adapters, designers, and AI agents are not trusted to enforce
authorization; application services are the enforcement boundary.

## Initial threats and controls

| Threat | Required control |
|---|---|
| YAML object construction or ambiguous scalars | Safe typed loading, strict booleans, no arbitrary tags |
| Duplicate/unknown metadata hiding author mistakes | Reject duplicate keys and unknown properties |
| Project paths escaping the source root | Canonicalize and confine discovery paths |
| Expression code or SQL injection | Allow-listed parsed AST and parameterized SQL translation |
| Python handler substitution | Qualified allow-listed handlers; deployment-controlled code |
| Row/field inference through filters and totals | Apply policies before query, aggregate, sort, and export |
| Child-record leakage through relationship hydration | Require source-field and target-entity access; bind target row policies into bounded child queries |
| Direct status mutation bypassing an action | Action-owned read-only fields and service-side transition checks |
| Lost updates or retry duplication | Version preconditions and durable pre-handler idempotency reservations; interrupted/failed keys require reconciliation |
| Report image/template resource access | Constrained resource loaders; no arbitrary URL/file access |
| MCP prompt/tool abuse | Least-privilege principal, explicit exposure, reauthorization, audit |
| Secret/protected-value leakage | Structured redaction in serialization, errors, logs, and diagnostics |
| Oversized or slow request-body resource exhaustion | Shared pre-parse declared/chunked body cap and receive deadline, server concurrency limit, idle keep-alive timeout, bounded graceful drain; rate policy and database statement cancellation remain deployment work |
| Schema autogeneration mistaken for approved migration intent | Read-only deterministic diff, explicit safety classes, compiler-validated stable identities, no rename inference, complete reflected-state plus proposal fingerprints, exact per-change acknowledgements, bounded backup evidence, confined non-overwriting revisions, strict non-executing source verification, driverless Alembic offline SQL plus source-bound manifests, no apply command, and legacy no-DDL compatibility mode |
| Forged, confused, or replayed bearer type | Exact OIDC issuer/audience, asymmetric algorithm allow-list, signature/expiry/subject validation, required `kid` and accepted `typ` |
| External identity role escalation | Explicit external-to-application role mapping; ignore unmapped roles; reject malformed role claims |
| Bearer interception on a network bind | Development auth is loopback-only; non-loopback OIDC serving requires direct TLS certificate and key |
| MCP capability discovery mistaken for permission | Metadata exposure is opt-in; every resource/tool call reauthorizes through services with `Channel.MCP` |
| MCP DNS rebinding or resource confusion | Canonical resource URI, exact path, HTTPS off-loopback, and Host/Origin allow-list; token audience remains deployment-configured |
| MCP query inference or cursor theft | Field/operator/type authorization, row policies, protected projections, bounded pages, and opaque principal-bound cursors |
| Developer MCP arbitrary file/code execution | Process-selected project root; discriminated logical operations; no caller path/Python/module/shell/apply tools; confined deleted candidate; only reviewed fixed templates execute after AST/compiler checks, never custom code |
| Candidate path traversal or case collision | Framework-derived relative paths; reject absolute/parent paths and case-insensitive collisions before temporary materialization |
| Candidate smoke test reaches real data or commands | Fresh in-memory repository/services only; no configured database or external command; explicit result flags; bounded entity/action/report counts and relationship depth |
| AI source changes applied without informed consent | Proposal/base/candidate fingerprints, exact artifacts and diff, isolated compilation/static/runtime checks, actual absent-destination checks, a candidate-bound challenge typed through the local CLI, regeneration/revalidation, exclusive apply lock, same-filesystem staged publication, and an approval/artifact receipt; developer MCP remains no-write |
| Generated audit timestamp forgery | Fixed transition templates use the server UTC clock and ignore caller payload for generated stamps |

## Non-goals for v0.1

The compiler validates project structure; it does not sandbox imported Python
code. Application code executes with the deployment process's authority and
must come from a trusted build artifact. Multi-tenant isolation is also outside
v0.1; a deployment is single-tenant even though it may have many users.

Security regression tests should exercise the same scenario through every
enabled adapter and verify both denial and non-inference.
