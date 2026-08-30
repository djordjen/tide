// What constrains a browse grid, composed once for every asker -- the
// workspace's live query and a dashboard tile must agree on what the
// named filter and the funnels mean, so the composition is one function
// with two callers rather than two spellings.
import { describe, expect, it } from "vitest"

import type { TideBrowsePresentation } from "@/lib/contracts"
import { gridStateFilters, tileSummaries } from "@/lib/grid-query"

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
  it("lays the named filter's conditions before the funnels", () => {
    expect(
      gridStateFilters(VIEW, {
        filterName: "drafts",
        valueFilters: { currency: ["EUR", null] },
      }),
    ).toEqual([
      { field: "status", operator: "eq", value: "draft" },
      { field: "currency", operator: "in", value: ["EUR", null] },
    ])
  })

  it("means everything when no named filter is chosen", () => {
    expect(
      gridStateFilters(VIEW, { filterName: "all", valueFilters: {} }),
    ).toEqual([])
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
