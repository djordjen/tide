// The round trip is the feature: a grid restored from a saved view must
// show the same lit funnels, named filter, sort and columns it was saved
// with. These two functions are the whole mapping, so the round trip is
// provable without a browser.
import { describe, expect, it } from "vitest"

import { applySavedView, captureSavedView } from "@/lib/saved-views"
import type { GridState } from "@/lib/saved-views"

const ARRANGED: GridState = {
  filterName: "drafts",
  columnFilters: { status: { kind: "values", values: ["draft", null] } },
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
    expect(captured.value_filters).toEqual({ status: ["draft", null] })
    expect(captured.conditions).toBeUndefined()
    expect(applySavedView(captured)).toEqual(ARRANGED)
  })

  it('maps the unfiltered choice to a null named filter and back', () => {
    const unfiltered: GridState = {
      filterName: "all",
      columnFilters: {},
      sort: [],
      columns: null,
    }
    const captured = captureSavedView("Everything", unfiltered)
    expect(captured.named_filter).toBeNull()
    expect(captured.columns).toBeNull()
    expect(applySavedView(captured)).toEqual(unfiltered)
  })

  it("carries ranges and contains as conditions and relights them", () => {
    // A saved view must relight the range inputs it was saved with: the
    // bounds travel as conditions, and apply folds a field's gte+lte
    // pair back into the one range state the funnel shows.
    const filtered: GridState = {
      filterName: "all",
      columnFilters: {
        invoice_date: {
          kind: "range",
          from: "2026-07-04",
          to: "2026-07-12",
        },
        total: { kind: "range", from: "500", to: null },
        number: { kind: "contains", text: "INV" },
        status: { kind: "values", values: ["draft"] },
      },
      sort: [],
      columns: null,
    }
    const captured = captureSavedView("July over 500", filtered)
    expect(captured.value_filters).toEqual({ status: ["draft"] })
    expect(captured.conditions).toEqual([
      { field: "invoice_date", operator: "gte", value: "2026-07-04" },
      { field: "invoice_date", operator: "lte", value: "2026-07-12" },
      { field: "total", operator: "gte", value: "500" },
      { field: "number", operator: "icontains", value: "INV" },
    ])
    expect(applySavedView(captured)).toEqual(filtered)
  })
})
