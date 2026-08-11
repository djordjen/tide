import { Button } from "@tide-framework/web"

/**
 * Composed from TIDE's own record detail footer, which is where every one of
 * these variants actually appears together.
 */
export function RecordActions() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline">Cancel</Button>
      <Button variant="secondary">Save</Button>
      <Button variant="outline">Preview Invoice</Button>
      <Button>Post invoice</Button>
    </div>
  )
}

export function Variants() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button>Default</Button>
      <Button variant="secondary">Secondary</Button>
      <Button variant="outline">Outline</Button>
      <Button variant="ghost">Ghost</Button>
      <Button variant="destructive">Delete record</Button>
    </div>
  )
}

export function Sizes() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm">Small</Button>
      <Button>Default</Button>
      <Button size="lg">Large</Button>
      <Button size="icon" aria-label="Next record">
        ›
      </Button>
    </div>
  )
}

export function Disabled() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button disabled>Save</Button>
      <Button variant="outline" disabled>
        Previous
      </Button>
      <Button variant="destructive" disabled>
        Delete
      </Button>
    </div>
  )
}
