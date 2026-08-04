# Expressions and Validation

## One criteria system

TIDE uses one safe, typed expression model for:

- computed fields and aggregates;
- developer-defined and named filters;
- validation rules;
- action visibility and enabled conditions;
- conditional presentation;
- SQL-translatable row policies.

The expression surface is intentionally small. Complex business behavior stays
in Python.

## Safety and compilation

Expressions are never passed to Python `eval()` and never concatenated into
SQL. They are parsed and compiled:

```text
Expression text
      -> parser
      -> typed expression tree
          -> Python evaluator
          -> SQLAlchemy predicate translator
          -> dependency tracker
          -> diagnostic formatter
```

The compiler verifies names and types, detects computed-field cycles, and
records dependencies. The SQLAlchemy translator handles stored fields, exact
bound literals, arithmetic, comparisons, boolean operators, relationship
paths, and the allow-listed scalar functions. Collection aggregates translate
through one collection traversal into correlated aggregate or `EXISTS`
subqueries. Multiple collection traversals fail query preflight instead of
falling back to in-process filtering.

An initial vocabulary may contain:

```text
Arithmetic:  + - * / %
Comparison:  == != < <= > >=
Logical:     and or not
Values:      null true false
Functions:   round coalesce min max today length
Aggregates:  sum count average any all
```

Function calls are allow-listed. Attribute paths may traverse declared model
relationships but cannot call arbitrary Python methods. Comparison operators
outside the vocabulary, such as membership `in` and identity `is`, are
rejected at compile time with `TIDE308`.

Some contexts also supply values the record does not contain, written `$name`.
Row policies receive `$principal`, the authenticated principal's identifier,
which is what makes ownership scoping expressible. The accepted names are fixed
per context, so an unknown one fails compilation with `TIDE304` rather than
evaluating to nothing at runtime. A parameter is bound at evaluation, never
spliced into the expression text: the Python evaluator receives it as a value
and the SQLAlchemy translator emits a bound parameter, so no caller-derived
string ever reaches a parsed expression or generated SQL.

## Numeric semantics

Fractional literals are constructed from their original source token rather
than an intermediate binary float: `1.21` evaluates as
`decimal.Decimal("1.21")`, and high-precision literals retain every authored
digit. Expression arithmetic runs in a framework-owned 38-significant-digit
decimal context with round-half-even behavior, independent of ambient Python
decimal settings. Division and `average` return `Decimal` for exact numeric
inputs. Record services coerce incoming values to their declared field types
before evaluation, so decimal fields always carry `decimal.Decimal` at runtime.
Collection `sum`, `average`, `min`, and `max` ignore null items consistently
with SQL aggregates. `sum` returns zero when no non-null values exist;
`average`, `min`, and `max` return null.

Declared decimal precision and scale are enforced before commit. TIDE rejects
extra integral or fractional digits instead of rounding them implicitly, so
every adapter observes the same storage-safe value contract. A numeric
`edit_mask` such as `"0.00"` can prevent common entry mistakes in a renderer,
but does not weaken or replace that service check.

## Computed fields

The field name supplies the assignment target:

```yaml
total:
  type: decimal
  format: money
  readonly: true
  computed:
    expression: "round(quantity * unit_price, 2)"
    materialization: virtual
```

Supported materialization concepts are:

- `virtual`: evaluated from the current record state;
- `stored`: evaluated by TIDE before commit and persisted;
- `database`: delegated to a database-generated expression where supported.

`database` is deferred until adapter differences are understood.

Relationship aggregates use the current `RecordSession`, including unsaved
master-detail changes:

```yaml
total:
  type: decimal
  format: money
  readonly: true
  computed:
    expression: "sum(lines.total)"
    materialization: stored
```

A virtual computed field is not currently sortable or filterable. The shared
query validator rejects virtual and collection fields consistently for memory
and SQL adapters rather than loading and filtering an entire table in Python.

## Named filters

Developer-defined filters are reusable application metadata:

```yaml
filters:
  overdue:
    label: Overdue invoices
    criteria: "status == 'open' and due_date < today()"

  high_value:
    label: High-value invoices
    criteria: "total >= 10000"
```

User-created filters should compile into a validated structured expression;
REST and MCP clients should not submit arbitrary SQL or unrestricted expression
text. Filterable fields and operators are allow-listed by the application
model and permissions.

## Validation rules

Simple constraints remain field properties:

```yaml
quantity: {type: decimal, required: true, minimum: 0.01}
```

String fields may also declare a completed-value regular-expression contract:

```yaml
code:
  type: string
  length: 30
  edit_mask: {regex: "[A-Z][A-Z0-9-]{0,29}"}
```

This rule is enforced at commit for TUI, API, and future GUI callers, and is
also exposed as an OpenAPI `pattern` where applicable.

`edit_mask` is the only per-field pattern contract. There is no library of
named field rules to reference by id — a semantic constraint such as an email
address is written as the mask it is:

```yaml
email:
  type: string
  length: 254
  edit_mask: {regex: '[^\s@]+@[^\s@]+\.[^\s@]+'}
```

Write a pattern both engines accept: the server matches it with Python
`re.fullmatch` and the web client compiles the same text as a JavaScript
`RegExp`. The common subset — character classes, quantifiers, groups,
alternation, anchoring by the surrounding contract — covers field masks.
Named groups and other engine-specific syntax do not belong here.

## Uniqueness

`unique: true` is enforced by a database unique index. Before writing, the
service asks one indexed question per unique field — "does another record
already hold this value?" — so a duplicate is reported as an ordinary
field-shaped validation issue rather than a failed write.

That question is answered before the write transaction opens, so it can be
stale: a duplicate may land in the gap. The index is what actually guarantees
uniqueness, and when it refuses a write the service asks again to identify the
field and raises the same structured error. A caller therefore sees the same
result whether it lost the race or not. Constraints the service does not model
keep their own error, because they are not something a caller can fix by
editing a field.

A null never collides. SQL uniqueness ignores NULL, and the service matches it,
so any number of records may leave an optional unique field empty.

Cross-field and conditional rules are explicit:

```yaml
validations:
  - id: positive_quantity
    assert: "quantity > 0"
    message: Quantity must be greater than zero.
    fields: [quantity]
    run: [on_change, before_commit]

  - id: posted_invoice_has_date
    when: "status == 'posted'"
    assert: "posted_at != null"
    message: A posted invoice must have a posting date.
    fields: [status, posted_at]
    run: [before_commit]

  - id: unusually_large_discount
    when: "discount > 30"
    severity: warning
    message: The discount exceeds 30 percent.
```

Errors prevent commit. Warnings may require confirmation. Informational rules
provide guidance without blocking.

Pure local rules may run on change. Database- or service-dependent checks run
before commit or before a named action, not on every keystroke.

## Action conditions

```yaml
actions:
  post:
    label: Post invoice
    shortcut: Ctrl+P
    enabled_when: "status == 'draft' and count(lines) > 0"
    permission: sales.invoice.post
    execute: sales.actions.post_invoice
```

The renderer uses the expression for feedback, and the action service evaluates
it again during execution.

## Python escape hatch

Rules that require external services, unusual algorithms, or complex queries
remain ordinary Python:

```yaml
validations:
  - id: customer_credit_limit
    handler: sales.validation.validate_customer_credit
```

YAML criteria should not grow into a general-purpose workflow or scripting
language.

## Security of derived values

A computed value can disclose its inputs. By default, it inherits the most
restrictive read permission of its dependencies unless an explicit separately
reviewed policy grants access. The same protection applies to filtering,
sorting, totals, grouping, exports, and validation messages.
