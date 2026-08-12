import { readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

import { documentTitle, SHELL_TITLE } from "@/lib/document-title"

/**
 * The browser tab said `TIDE Framework` on the sign-in screen, on a browse and
 * on an open record alike, so two tabs of the same application were
 * indistinguishable and the back-button history was a column of identical
 * entries. What the tab shows is the screen, then the application it belongs
 * to -- most specific first, because a tab truncates from the right.
 */
describe("documentTitle", () => {
  it("puts the screen before the application", () => {
    expect(documentTitle("Invoices", "TIDE Invoicing")).toBe(
      "Invoices · TIDE Invoicing",
    )
  })

  it("uses what the heading says, so a tab and a screen agree", () => {
    expect(documentTitle("Invoice — INV-2026-0002", "TIDE Invoicing")).toBe(
      "Invoice — INV-2026-0002 · TIDE Invoicing",
    )
  })

  it("falls back to the application alone when there is no screen", () => {
    expect(documentTitle(null, "TIDE Invoicing")).toBe("TIDE Invoicing")
    expect(documentTitle("", "TIDE Invoicing")).toBe("TIDE Invoicing")
  })
})

it("agrees with the title index.html ships", () => {
  // Two files cannot both hold this string without one of them drifting, and
  // there is no way to have only one: the document needs a title before any
  // script runs, and the disconnected shell needs to put that same title back
  // when a session ends. So they stay in two places and this asserts it.
  const html = readFileSync(
    join(
      resolve(dirname(fileURLToPath(import.meta.url)), "..", ".."),
      "index.html",
    ),
    "utf8",
  )
  const shipped = /<title>([^<]*)<\/title>/.exec(html)?.[1]

  expect(shipped).toBe(SHELL_TITLE)
})
