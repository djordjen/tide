import { Skeleton } from "@tide-framework/web"

/**
 * Skeleton carries no size of its own — every use sets it through className.
 * These are the shapes TIDE's record detail actually shows while a record
 * loads (`h-17 rounded-xl` rows above an `h-52 rounded-xl` collection).
 */
export function RecordDetail() {
  return (
    <div className="max-w-xl space-y-3">
      <Skeleton className="h-17 rounded-xl" />
      <Skeleton className="h-17 rounded-xl" />
      <Skeleton className="h-52 rounded-xl" />
    </div>
  )
}

export function Shapes() {
  return (
    <div className="max-w-md space-y-3">
      <Skeleton className="h-4 w-24 rounded-md" />
      <Skeleton className="h-9 w-full rounded-lg" />
      {/* `size-9`, not `size-10`: the shipped stylesheet is TIDE's own app
          build, so it carries only the utilities TIDE's source uses. `size-10`
          is absent and the element collapsed to nothing. */}
      <Skeleton className="size-9 rounded-full" />
    </div>
  )
}
