// The round trip is the feature: a grid restored from a saved view must
// show the same lit funnels, named filter, sort and columns it was saved
// with. These two functions are the whole mapping, so the round trip is
// provable without a browser.
import { describe, expect, it } from "vitest"

import { applySavedView, captureSavedView } from "@/lib/saved-views"
import type { GridState } from "@/lib/saved-views"

const ARRANGED: GridState = {
  filterName: "drafts",
  valueFilters: { status: ["draft", null] },
  sort: [{ field: "total", descending: true }],
  columns: [
    { name: "number", label: "No." },
    { name: "total", label: null },
  ],
}

describe("capturing and applying a saved view", () => {
  it("round-trips the whole grid state under a name", () => {
    const captured = captureSavedView("Overdue", ARRANGED)
    expect(captured.name).toBe("Overdue")
    expect(applySavedView(captured)).toEqual(ARRANGED)
  })

  it('maps the unfiltered choice to a null named filter and back', () => {
    const unfiltered: GridState = {
      filterName: "all",
      valueFilters: {},
      sort: [],
      columns: null,
    }
    const captured = captureSavedView("Everything", unfiltered)
    expect(captured.named_filter).toBeNull()
    expect(captured.columns).toBeNull()
    expect(applySavedView(captured)).toEqual(unfiltered)
  })
})
