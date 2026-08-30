# Duplicate record

Date: 2026-08-30. Status: approved as the second half of "proceed with 1
and then 2" (XAF CloneObject; parametrized actions were the first half).

## What

A person points at a record and gets a **new, unsaved draft** filled with
what a person could have typed on the original: writable scalars, chosen
references, and a deep copy of the owned collection rows. Nothing touches
the database until Save, and every validation, default, generator and
security rule applies to the draft exactly as it applies to any new
record — duplicate is a head start, not a second create path.

## What copies — one rule, in the records service

`RecordsService.duplicate_draft(entity, identity, context)` reads the
source through the ordinary secured `get` and returns the values a create
form opens with. Per field:

- **Copies**: ordinary writable scalars (`invoice_date`, `currency`),
  reference selections (`customer`), and owned collections
  (`cascade: create`) — each row minus its own identity, readonly,
  system-written and computed fields, so line numbers and quantities
  travel while ids and stored totals are the new record's own.
- **Never copies**: the primary key; `readonly` fields; `write: system` /
  `write: action_only` fields (number, status, stamps — the new record
  starts in its default state); computed fields (recomputed); **file
  fields** (the bytes belong to the source record — the duplicate opens
  with an empty slot); and any value the field security protected —
  a duplicate must not be a way to read what the grid would not show.
- **Unique writable fields copy as-is** (XAF's behaviour): the draft is
  editable, and if the person saves without changing one, the ordinary
  uniqueness refusal names it. Predictability over cleverness.

No new permission: reading the source and creating a record are already
both authorized, and the button follows the create capability.

## Surfaces

- **REST**: `GET {resource}/{identity}/duplicate-draft` answers
  `{values}` as a wire draft (the reference-selection shape). A GET
  because it stores nothing and is safely repeatable.
- **Web**: a **Duplicate** button on the open record (next to the domain
  actions bar's left side, always visible in update mode when the create
  capability holds); it fetches the draft and reopens the form in create
  mode seeded with it. Saving allocates a fresh number and identity.
- **TUI**: `Ctrl+D` / a **Duplicate** button on the open record form,
  building the same draft through the service directly and reopening the
  edit screen as a create session.
- **MCP abstains**: an agent already composes read + create, and the
  server-owned fields it must not copy are exactly the ones create
  refuses anyway.

## Testing

Service: copies/never-copies table proven field by field on the invoice
(lines deep-copied without ids, protected values dropped, file field
empty); the draft saves into a genuinely new record with its own number.
REST: the endpoint answers the draft, 404s a missing record, and refuses
an unreadable one. Web: journey duplicates a seeded invoice, edits
nothing but the date, saves, and the browse shows two invoices sharing a
customer and total with different numbers. TUI: pilot duplicates and
saves, then reads both records back.
