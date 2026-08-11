import { Input } from "@tide-framework/web"

/**
 * TIDE pairs an Input with a small muted label and nothing else — no card, no
 * border around the pair. That rule lives in `form-field.ts`; these stories
 * follow it so the card shows the real field shape.
 */
export function FormField() {
  return (
    <div className="grid max-w-md grid-cols-2 gap-3">
      <div className="min-w-0">
        <label className="mb-1 block truncate text-xs font-medium text-muted-foreground">
          Invoice Date
        </label>
        <Input type="date" defaultValue="2026-03-07" />
      </div>
      <div className="min-w-0">
        <label className="mb-1 block truncate text-xs font-medium text-muted-foreground">
          Currency
        </label>
        <Input defaultValue="EUR" />
      </div>
    </div>
  )
}

export function States() {
  return (
    <div className="grid max-w-md gap-3">
      <Input placeholder="Search invoices…" />
      <Input defaultValue="INV-2026-0002" />
      <Input defaultValue="Read-only value" disabled />
    </div>
  )
}

export function Types() {
  return (
    <div className="grid max-w-md gap-3">
      <Input type="number" defaultValue="240.00" inputMode="decimal" />
      <Input type="date" defaultValue="2026-07-03" />
      <Input type="password" defaultValue="secret" />
    </div>
  )
}
