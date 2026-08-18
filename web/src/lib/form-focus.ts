import { formEditorId } from "@/components/record-form-editor"
import type { TideFormPresentation } from "@/lib/contracts"
import type { TideFormErrors } from "@/lib/form-draft"

/**
 * Putting the cursor where the problem is -- or where the work continues.
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


/**
 * The first field the form offers, in the order the author laid it out.
 *
 * For a form handed back empty to be filled again: the run continues where
 * it started rather than wherever the pointer happened to be.
 */
export function focusFirstEditor(
  form: TideFormPresentation,
  editable: ReadonlySet<string>,
) {
  const name = form.sections
    .flatMap((section) =>
      section.kind === "group" ? section.rows.flat() : [],
    )
    .find((candidate) => editable.has(candidate))
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
