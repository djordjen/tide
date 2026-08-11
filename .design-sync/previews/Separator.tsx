import { Separator } from "@tide-framework/web"

/** A hairline in `--border`; it takes its length from the parent, not a prop. */
export function Horizontal() {
  return (
    <div className="max-w-md">
      <p className="text-sm font-semibold">Invoice</p>
      <Separator className="my-3" />
      <p className="text-sm text-muted-foreground">
        Record 2 of 8 loaded in the current query
      </p>
    </div>
  )
}

export function Vertical() {
  return (
    <div className="flex h-8 items-center gap-3 text-sm">
      <span>Draft</span>
      <Separator orientation="vertical" />
      <span>EUR</span>
      <Separator orientation="vertical" />
      <span className="tabular-nums">480.00</span>
    </div>
  )
}
