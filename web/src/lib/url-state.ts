import { useCallback, useEffect, useState } from "react"

/** Keep one query parameter and one piece of component state in step.
 *
 * View selection used to live only in a `useState`, so the address bar never
 * changed: there was nothing to send someone, the back button left the
 * application, and a refresh returned to whichever view happened to be first.
 *
 * A query parameter rather than a path segment, because the built client is
 * served by `StaticFiles`, which returns `index.html` for the root and a 404
 * for anything deeper -- a path-shaped URL would deep-link fine and then fail
 * on the first refresh. Each caller owns one parameter and merges into
 * whatever else is in the query, so two of these do not overwrite each other.
 *
 * A value equal to the fallback is removed rather than written, which keeps
 * the default view's URL clean and makes "no parameter" and "the default"
 * the same state rather than two.
 *
 * `clears` names parameters this one invalidates, dropped in the same history
 * entry. A record identity only means something inside the view it came from
 * -- entities number their rows from one, so carrying `record=1` from Invoices
 * to Products opens a customer nobody asked for -- and pushing two entries for
 * one click would make Back a half-step.
 */
export function useUrlParameter(
  name: string,
  fallback: string,
  clears: readonly string[] = [],
): [string, (next: string) => void] {
  const [value, setValue] = useState(() => readParameter(name) ?? fallback)

  useEffect(() => {
    function synchronize() {
      setValue(readParameter(name) ?? fallback)
    }
    // The browser changed the URL without us: back, forward, or a restored
    // session. The state follows the address bar here, not the other way.
    window.addEventListener("popstate", synchronize)
    return () => window.removeEventListener("popstate", synchronize)
  }, [name, fallback])

  const update = useCallback(
    (next: string) => {
      setValue(next)
      const parameters = new URLSearchParams(window.location.search)
      if (next && next !== fallback) {
        parameters.set(name, next)
      } else {
        parameters.delete(name)
      }
      for (const cleared of clears) {
        parameters.delete(cleared)
      }
      const query = parameters.toString()
      window.history.pushState(
        null,
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}`,
      )
    },
    [name, fallback, clears],
  )

  return [value, update]
}

function readParameter(name: string): string | null {
  const value = new URLSearchParams(window.location.search).get(name)
  return value === null || value === "" ? null : value
}
