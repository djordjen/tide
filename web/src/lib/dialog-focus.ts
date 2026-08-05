import { useEffect, useRef, type KeyboardEvent, type RefObject } from "react"

/**
 * What `Tab` is allowed to reach.
 *
 * `querySelectorAll` returns document order, which is tab order here: a
 * positive `tabindex` reorders the sequence, and TIDE writes none. Anchors and
 * textareas are in the list even though no dialog renders one yet -- the set is
 * "what a browser would tab to", and a list that only covers today's markup
 * starts leaking focus the first time a form grows a multi-line field.
 */
const FOCUSABLE = [
  "a[href]",
  "button:not(:disabled)",
  "input:not(:disabled)",
  "select:not(:disabled)",
  "textarea:not(:disabled)",
  "[tabindex]",
]
  .map((selector) => `${selector}:not([tabindex='-1'])`)
  .join(", ")

/**
 * Hold keyboard focus inside a modal dialog, and give it back when it closes.
 *
 * `aria-modal="true"` is a promise to assistive technology that the rest of the
 * page is unreachable. Nothing enforces it: a dialog that only says so leaves
 * someone tabbing from the last button into the page behind it, still hidden
 * behind the backdrop, operating controls they cannot see. Three dialogs made
 * that promise and one kept it, so the keeping is moved here.
 *
 * Takes the dialog's ref rather than handing one back so that the caller's
 * `useRef` stays visible to `react-hooks/exhaustive-deps`, which only trusts a
 * ref it watched being created. Returns the `onKeyDown` to put on the same
 * element.
 */
export function useDialogFocus<T extends HTMLElement>(
  dialogRef: RefObject<T | null>,
): (event: KeyboardEvent<HTMLElement>) => void {
  // Captured during render rather than in an effect, because React applies
  // `autoFocus` while it commits -- before a layout effect and long before a
  // passive one. A dialog that autofocuses a button would otherwise record
  // *itself* as the thing to hand focus back to, and closing it would focus a
  // node that is no longer in the document, which browsers answer by focusing
  // the body. The next Tab then starts over at the top of the page.
  const openerRef = useRef<Element | null>(null)
  if (openerRef.current === null) {
    openerRef.current = document.activeElement
  }

  useEffect(() => {
    const dialog = dialogRef.current
    // Only when the dialog did not already place focus itself: `autoFocus`, or
    // an effect aiming at the field that matters, knows better than "the first
    // control in the markup".
    if (dialog && !dialog.contains(document.activeElement)) {
      ;(focusableWithin(dialog)[0] ?? dialog).focus()
    }
    return () => {
      const opener = openerRef.current
      if (opener instanceof HTMLElement && opener.isConnected) {
        opener.focus()
      }
    }
  }, [dialogRef])

  return (event) => trapDialogFocus(event, dialogRef.current)
}

/**
 * Wrap `Tab` around the dialog's own controls.
 *
 * Safe to call for every key: anything but `Tab` returns untouched, so a dialog
 * with its own `Escape` handling can hand the whole event over.
 */
export function trapDialogFocus(
  event: KeyboardEvent<HTMLElement>,
  dialog: HTMLElement | null,
): void {
  if (!dialog || event.key !== "Tab") {
    return
  }
  const focusable = focusableWithin(dialog)
  const first = focusable[0]
  const last = focusable.at(-1)
  if (!first || !last) {
    return
  }
  const active = document.activeElement
  if (event.shiftKey) {
    // The dialog element itself counts as the front edge. It holds focus when
    // there was nothing inside to give it to, and tabbing backwards out of it
    // lands behind the backdrop just as surely as tabbing backwards off the
    // first button does.
    if (active === first || active === dialog) {
      event.preventDefault()
      last.focus()
    }
  } else if (active === last) {
    event.preventDefault()
    first.focus()
  }
}

function focusableWithin(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE))
}
