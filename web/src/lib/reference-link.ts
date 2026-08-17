import type {
  TidePresentationManifest,
  TidePresentationReference,
} from "@/lib/contracts"

/**
 * The deep link to a referenced record's own screen, or null when the
 * manifest offers none.
 *
 * The manifest's views are already capability-filtered, so a person who may
 * not browse Customers simply gets no door -- the control does not have to
 * know why. The target needs a detail view as well as a browse, because
 * `?record=` opens the record screen of the view it rides on, and a view
 * without a form has nowhere to put the record.
 */
export function referenceRecordHref(
  views: TidePresentationManifest["views"] | undefined,
  reference: TidePresentationReference | null | undefined,
  value: unknown,
): string | null {
  if (!views || !reference || value === null || value === undefined) {
    return null
  }
  const browse = Object.values(views).find(
    (candidate) =>
      candidate.entity === reference.entity &&
      candidate.detail_view !== null,
  )
  if (!browse) {
    return null
  }
  const query = new URLSearchParams({
    view: browse.view,
    record: String(value),
  })
  return `?${query.toString()}`
}
