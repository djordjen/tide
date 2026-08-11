import { Badge } from "@tide-framework/web"

/**
 * `success` and `warning` are TIDE's own additions to the base variant set —
 * they carry record state, which is why the workflow row below is the primary
 * story rather than a swatch strip.
 */
export function RecordState() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant="outline">Draft</Badge>
      <Badge variant="success">Posted</Badge>
      <Badge variant="warning">Cancelled</Badge>
    </div>
  )
}

export function ScreenMarkers() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant="outline">Secured editor</Badge>
      <Badge variant="outline">Secured detail</Badge>
      <Badge variant="outline">New record</Badge>
      <Badge>1 draft rows</Badge>
    </div>
  )
}

export function Variants() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge>Default</Badge>
      <Badge variant="secondary">Secondary</Badge>
      <Badge variant="outline">Outline</Badge>
      <Badge variant="success">Success</Badge>
      <Badge variant="warning">Warning</Badge>
    </div>
  )
}
