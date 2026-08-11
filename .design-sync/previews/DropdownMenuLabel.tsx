import { DropdownMenuLabel } from "@tide-framework/web"

/**
 * The floor card rendered this blank: it is a styled `div` and nothing passed
 * it children. Rendered standalone with real text rather than inside an open
 * menu, because the menu portals and the section heading is the whole point of
 * this part — `DropdownMenu` already shows it in context.
 */
export function SectionHeading() {
  // `max-w-md`, not `w-56`: the shipped stylesheet only carries utilities
  // TIDE's own source uses, and `w-56` is not among them.
  return (
    <div className="max-w-md rounded-md border bg-popover p-1">
      <DropdownMenuLabel>Visible columns</DropdownMenuLabel>
    </div>
  )
}
