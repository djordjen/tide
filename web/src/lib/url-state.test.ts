import { act, renderHook } from "@testing-library/react"
import { afterEach, expect, it } from "vitest"

import { useUrlParameter } from "@/lib/url-state"

afterEach(() => {
  window.history.replaceState(null, "", "/")
})

it("re-choosing the current value adds no history entry", () => {
  // Clicking the already-selected view stacked identical entries, so Back
  // was a dead key until the duplicates ran out.
  const { result } = renderHook(() => useUrlParameter("view", ""))

  act(() => result.current[1]("invoices"))
  expect(window.location.search).toBe("?view=invoices")
  const after = window.history.length

  act(() => result.current[1]("invoices"))
  act(() => result.current[1]("invoices"))

  expect(window.history.length).toBe(after)
  expect(window.location.search).toBe("?view=invoices")
})
