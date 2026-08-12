import { useEffect } from "react"

/**
 * The title `index.html` ships, and the one the shell puts back when no
 * application is connected.
 *
 * Duplicated there by necessity -- the document needs a title before any
 * script runs -- and `document-title.test.ts` asserts the two agree.
 */
export const SHELL_TITLE = "TIDE Framework"

/** `<screen> · <application>`, most specific first: a tab truncates right. */
export function documentTitle(
  screen: string | null,
  application: string,
): string {
  return screen ? `${screen} · ${application}` : application
}

/**
 * Name the tab after the screen that is showing.
 *
 * `null` means "not mine to say", which is how two screens that are mounted
 * at once stay out of each other's way: the browse yields while a record is
 * open rather than racing it, so there is one writer at any moment and no
 * restore-on-unmount to get the ordering wrong.
 */
export function useDocumentTitle(title: string | null): void {
  useEffect(() => {
    if (title !== null) {
      document.title = title
    }
  }, [title])
}
