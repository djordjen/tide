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
| Lost updates or retry duplication | Version preconditions and idempotency records |
| Report image/template resource access | Constrained resource loaders; no arbitrary URL/file access |
| MCP prompt/tool abuse | Least-privilege principal, explicit exposure, reauthorization, audit |
| Secret/protected-value leakage | Structured redaction in serialization, errors, logs, and diagnostics |

## Non-goals for v0.1

The compiler validates project structure; it does not sandbox imported Python
code. Application code executes with the deployment process's authority and
must come from a trusted build artifact. Multi-tenant isolation is also outside
v0.1; a deployment is single-tenant even though it may have many users.

Security regression tests should exercise the same scenario through every
enabled adapter and verify both denial and non-inference.
