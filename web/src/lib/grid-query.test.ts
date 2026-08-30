// What constrains a browse grid, composed once for every asker -- the
// workspace's live query, a dashboard tile, and a saved view's capture
// must agree on what the named filter and the column filters mean, so
// the composition is one function with three callers rather than three
// spellings. A column's filter is one discriminated state: a membership
// list, a range, or a contains -- never two at once.
import { describe, expect, it } from "vitest"

import type { TideBrowsePresentation } from "@/lib/contracts"
import {
  columnFilterConditions,
  gridStateFilters,
  tileSummaries,
} from "@/lib/grid-query"

const VIEW = {
  view: "sales.Invoice.browse",
  entity: "sales.Invoice",
  identity_field: "id",
  named_filters: [
    {
      name: "drafts",
      label: "Drafts",
      conditions: [{ field: "status", operator: "eq", value: "draft" }],
    },
  ],
  summaries: [
    { field: "number", function: "count" },
    { field: "total", function: "sum" },
  ],
} as unknown as TideBrowsePresentation

describe("the one filter composition", () => {
  it("lays the named filter's conditions before the column filters", () => {
    expect(
      gridStateFilters(VIEW, {
        filterName: "drafts",
        columnFilters: {
          currency: { kind: "values", values: ["EUR", null] },
        },
      }),
    ).toEqual([
      { field: "status", operator: "eq", value: "draft" },
      { field: "currency", operator: "in", value: ["EUR", null] },
    ])
  })

  it("means everything when no named filter is chosen", () => {
    expect(
      gridStateFilters(VIEW, { filterName: "all", columnFilters: {} }),
    ).toEqual([])
  })

  it("speaks a range as its bounds, either side open-ended", () => {
    expect(
      columnFilterConditions("invoice_date", {
        kind: "range",
        from: "2026-07-04",
        to: "2026-07-12",
      }),
    ).toEqual([
      { field: "invoice_date", operator: "gte", value: "2026-07-04" },
      { field: "invoice_date", operator: "lte", value: "2026-07-12" },
    ])
    expect(
      columnFilterConditions("total", {
        kind: "range",
        from: "500",
        to: null,
      }),
    ).toEqual([{ field: "total", operator: "gte", value: "500" }])
  })

  it("speaks contains with the search box's case-insensitive verb", () => {
    expect(
      columnFilterConditions("number", {
        kind: "contains",
        text: "INV-2026",
      }),
    ).toEqual([
      { field: "number", operator: "icontains", value: "INV-2026" },
    ])
  })
})

describe("what a tile asks for", () => {
  it("keeps the declared summaries when a count is among them", () => {
    expect(tileSummaries(VIEW)).toEqual([
      { field: "number", function: "count" },
      { field: "total", function: "sum" },
    ])
  })

  it("adds a count over the identity when the declaration has none", () => {
    const sumsOnly = {
      ...VIEW,
      summaries: [{ field: "total", function: "sum" }],
    } as TideBrowsePresentation
    expect(tileSummaries(sumsOnly)).toEqual([
      { field: "id", function: "count" },
      { field: "total", function: "sum" },
    ])
  })

  it("asks for at least the count when nothing is declared", () => {
    const bare = { ...VIEW, summaries: [] } as TideBrowsePresentation
    expect(tileSummaries(bare)).toEqual([{ field: "id", function: "count" }])
  })
})
