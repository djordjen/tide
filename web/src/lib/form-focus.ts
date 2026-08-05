import { formEditorId } from "@/components/record-form-editor"
import type { TideFormPresentation } from "@/lib/contracts"
import type { TideFormErrors } from "@/lib/form-draft"

/**
 * Putting the cursor where the problem is.
 *
 * Separate from `dialog-focus`, which keeps focus inside a modal: this is
 * about moving it to the first thing the server or the validator objected
 * to, after a save the person expected to succeed.
 */

export function focusFirstError(
  form: TideFormPresentation,
  errors: TideFormErrors,
) {
  const name = Object.keys(errors)[0]
  if (!name) {
    return
  }
  requestAnimationFrame(() => {
    document.getElementById(formEditorId(form, name))?.focus()
  })
}


export function focusFirstCollectionError(
  collectionName: string,
) {
  requestAnimationFrame(() => {
    document
      .querySelector<HTMLElement>(
        `[data-tide-collection="${collectionName}"]`,
      )
      ?.scrollIntoView({ block: "nearest" })
  })
}
