import { expect, test } from "@playwright/test"

import { createdRow, signIn, unique } from "./session"

/**
 * Journeys against the real stack: a compiled application, FastAPI, the
 * security engine, a repository holding demo data, and the built renderer at
 * the same origin.
 *
 * Every journey that writes creates what it writes to, so the order they run
 * in does not matter and running one twice is safe. The seeded records are
 * only ever read.
 */

test("signs in with a password the server owns and reads its records", async ({
  page,
}) => {
  await signIn(page)

  await expect(page.getByText("local:e2e")).toBeVisible()
  await expect(page.getByText("sales_clerk")).toBeVisible()

  // The renderer does the formatting, but only because the manifest told it
  // how: `%d.%m.%Y` and a thousands separator are compiled out of the YAML
  // and carried over HTTP. Left to its own defaults it would show
  // 2026-07-05 and 1200.00, and the reference would be a bare identity.
  const invoice = page.getByRole("row", { name: /INV-2026-0003/ })
  await expect(invoice).toContainText("05.07.2026")
  await expect(invoice).toContainText("1,200.00")
  await expect(invoice).toContainText("LOV - Lovćen Studio")
})

test("opens a record and reads the lines a field policy guards", async ({
  page,
}) => {
  await signIn(page)
  await page.getByRole("row", { name: /INV-2026-0002/ }).click()
  await page.getByRole("button", { name: "Open" }).click()

  await expect(
    page.getByRole("heading", { level: 1, name: /INV-2026-0002/ }),
  ).toBeVisible()
  // `lines` is a field policy away: it is readable only through
  // sales.invoice.detail, and this principal holds that. Reaching the nested
  // rows at all is the assertion -- the numbers in them are stored values
  // being read back, not arithmetic. Journey four covers the arithmetic.
  const line = page.getByRole("row", { name: /Priority support/ })
  await expect(line).toContainText("240.00")
  await expect(line).toContainText("480.00")
})

test("creates a record, edits it, and finds the server kept it", async ({
  page,
}) => {
  const code = unique("E2EC")
  await signIn(page)
  await page.getByRole("button", { name: "Customers" }).click()

  await page.getByRole("button", { name: "New" }).click()
  await page.getByRole("textbox", { name: "Code" }).fill(code)
  await page.getByRole("textbox", { name: "Name" }).fill(`${code} Holdings`)
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(`${code.toLowerCase()}@e2e.example`)
  await page.getByRole("button", { name: "Save" }).click()
  await expect(createdRow(page)).toContainText(code)

  await page.getByRole("button", { name: "Open" }).click()
  await page.getByRole("textbox", { name: "Name" }).fill(`${code} Renamed`)
  await page.getByRole("button", { name: "Save" }).click()
  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled()

  // A reload proves the server holds this, not the tab: the session cookie
  // survives, and the view and the open record both come back out of the
  // address bar. Nothing asserted after this line was ever in React state.
  await page.reload()
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: new RegExp(`${code} Renamed`),
    }),
  ).toBeVisible()
  await expect(page.getByRole("textbox", { name: "Name" })).toHaveValue(
    `${code} Renamed`,
  )
})

test("drafts an invoice through both lookups and posts it", async ({
  page,
}) => {
  await signIn(page)
  await page.getByRole("button", { name: "New" }).click()

  await page.getByRole("button", { name: "Select Customer" }).click()
  const customers = page.getByRole("dialog", { name: "Select Customer" })
  await customers.getByRole("row", { name: /ADRIA/ }).click()
  await customers.getByRole("button", { name: "Select" }).click()

  await page.getByRole("button", { name: "Add Line" }).click()
  await page.getByRole("button", { name: "Select Product" }).click()
  const products = page.getByRole("dialog", { name: "Select Product" })
  await products.getByRole("row", { name: /CONS/ }).click()
  await products.getByRole("button", { name: "Select" }).click()

  // The server's `on_select` assignments came back and filled the line. This
  // is the whole point of a selection round trip, and it is the assertion the
  // stubbed suite could not make.
  await expect(page.getByRole("textbox", { name: "Unit Price" })).toHaveValue(
    "85.00",
  )
  await expect(page.getByRole("textbox", { name: "Description" })).toHaveValue(
    "Consulting hour",
  )

  await page.getByRole("textbox", { name: "Quantity" }).fill("3")
  await page.getByRole("button", { name: "Apply Line" }).click()
  await page.getByRole("button", { name: "Save" }).click()

  // Number allocated by the application's own action, total computed from the
  // line by the expression engine. The browser sent neither.
  const created = createdRow(page)
  await expect(created).toContainText(/INV-\d{4}-\d{6}/)
  await expect(created).toContainText("255.00")
  await expect(created).toContainText("Draft")

  await page.getByRole("button", { name: "Open" }).click()
  await page.getByRole("button", { name: "Post invoice" }).click()

  await expect(page.getByText("Post invoice completed successfully.")).toBeVisible()
  await expect(page.getByText("Posted", { exact: true })).toBeVisible()
  // The action is spent: `enabled_when` no longer holds.
  await expect(page.getByRole("button", { name: "Post invoice" })).toBeDisabled()
})

test("previews the report the server rendered and exports it", async ({
  page,
}) => {
  await signIn(page)
  await page.getByRole("button", { name: "Posted Sales Summary" }).click()
  const report = page.getByRole("dialog", { name: "Posted Sales Summary" })

  await expect(report.getByRole("row", { name: /Adria Consulting/ })).toBeVisible()
  // The report's own criteria decide this, not the caller: Mora Trade holds
  // drafts and a cancellation, so it has nothing posted to report.
  await expect(report).not.toContainText("Mora Trade")

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    report.getByRole("button", { name: "Download CSV" }).click(),
  ])
  // Named after the report and the day it covers -- not after whichever record
  // happened to be open when it was asked for.
  expect(download.suggestedFilename()).toMatch(
    /^posted-sales-summary-\d{4}-\d{2}-\d{2}\.csv$/,
  )
})

test("refuses a stale save and offers the change for review", async ({
  page,
  context,
}) => {
  await signIn(page)
  await page.getByRole("button", { name: "New" }).click()
  await page.getByRole("button", { name: "Select Customer" }).click()
  const customers = page.getByRole("dialog", { name: "Select Customer" })
  await customers.getByRole("row", { name: /LOV/ }).click()
  await customers.getByRole("button", { name: "Select" }).click()
  await page.getByRole("button", { name: "Add Line" }).click()
  await page.getByRole("button", { name: "Select Product" }).click()
  const products = page.getByRole("dialog", { name: "Select Product" })
  await products.getByRole("row", { name: /SUP/ }).click()
  await products.getByRole("button", { name: "Select" }).click()
  await page.getByRole("textbox", { name: "Quantity" }).fill("9")
  await page.getByRole("button", { name: "Apply Line" }).click()
  await page.getByRole("button", { name: "Save" }).click()
  await expect(createdRow(page)).toContainText("2,160.00")

  // This tab opens the invoice first, so it is holding the version the other
  // tab is about to replace.
  await page.getByRole("button", { name: "Open" }).click()
  await expect(page.getByRole("textbox", { name: "Currency" })).toHaveValue(
    "EUR",
  )

  const other = await context.newPage()
  await other.goto(page.url())
  await other.getByRole("textbox", { name: "Currency" }).fill("USD")
  await other.getByRole("button", { name: "Save" }).click()
  await expect(other.getByRole("button", { name: "Save" })).toBeDisabled()

  await page.getByRole("textbox", { name: "Currency" }).fill("GBP")
  await page.getByRole("button", { name: "Save" }).click()

  const conflict = page.getByRole("dialog", { name: "Record changed elsewhere" })
  await expect(conflict).toBeVisible()
  await expect(conflict).toContainText("1 decision remaining")
  await expect(page.getByText("This record changed on the server.")).toBeVisible()

  await conflict.getByRole("button", { name: "Use my Currency" }).click()
  await conflict.getByRole("button", { name: "Apply resolution" }).click()
  await page.getByRole("button", { name: "Save" }).click()

  await expect(page.getByRole("textbox", { name: "Currency" })).toHaveValue(
    "GBP",
  )
  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled()
})
