import { beforeEach, expect, it } from "vitest"

import {
  clearColumnLayout,
  layoutStorageKey,
  loadColumnLayout,
  saveColumnLayout,
} from "@/lib/layout-preferences"

beforeEach(() => window.localStorage.clear())

it("scopes personal layouts by application, principal, and view", () => {
  expect(
    layoutStorageKey(
      "TIDE Invoicing",
      "development:api",
      "sales.Invoice.browse",
    ),
  ).toBe(
    "tide.web.column-layout.v1:TIDE Invoicing:development:api:sales.Invoice.browse",
  )
})

it("reconciles stored field names without changing application metadata", () => {
  const key = "layout"
  saveColumnLayout(key, {
    version: 1,
    order: ["total", "removed", "number"],
    sizes: { total: 160, removed: 200, number: 40 },
  })

  expect(loadColumnLayout(key, ["number", "customer", "total"])).toEqual({
    version: 1,
    order: ["total", "number", "customer"],
    sizes: { total: 160, number: 72 },
  })

  clearColumnLayout(key)
  expect(loadColumnLayout(key, ["number"])).toBeNull()
})
