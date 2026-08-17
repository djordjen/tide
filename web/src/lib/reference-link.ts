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
/**
 * Follow a reference link in place: one history entry carrying both `view`
 * and `record`, marked as a reference-follow so the record screen's Close
 * knows it can walk back to exactly where the person was. The synthetic
 * popstate is what tells both URL hooks to read the address bar -- pushState
 * alone notifies nobody.
 */
export function followReferenceLink(href: string): void {
  window.history.pushState({ tideReference: true }, "", href)
  window.dispatchEvent(new PopStateEvent("popstate"))
}

/** True when the open record was reached by following a reference link. */
export function cameFromReferenceLink(): boolean {
  const state = window.history.state as { tideReference?: boolean } | null
  return Boolean(state?.tideReference)
}

/**
 * The click handler for a reference link. A plain left click navigates in
 * place; anything modified -- ctrl, cmd, shift, middle button -- is left to
 * the browser, which is what makes "open in new tab" keep working on a real
 * anchor.
 */
export function referenceLinkClick(
  event: {
    button: number
    metaKey: boolean
    ctrlKey: boolean
    shiftKey: boolean
    altKey: boolean
    preventDefault: () => void
    stopPropagation: () => void
  },
  href: string,
): void {
  if (
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return
  }
  event.preventDefault()
  // A link inside a selectable grid row must not also select the row it is
  // leaving.
  event.stopPropagation()
  followReferenceLink(href)
}

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
