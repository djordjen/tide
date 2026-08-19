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

test("names every reference in the grid without a request each", async ({
  page,
}) => {
  const reads: string[] = []
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname
    if (/^\/api\/v1\/(customers|products)\//.test(path)) {
      reads.push(path)
    }
  })

  await signIn(page)

  // Eight seeded invoices naming three customers. The names are resolved
  // with the page that carries the rows, so the grid draws them without
  // buying any of them -- which is the only place that shows.
  await expect(page.getByRole("row", { name: /INV-2026-0001/ })).toContainText(
    "ADRIA - Adria Consulting",
  )
  await expect(page.getByRole("row", { name: /INV-2026-0003/ })).toContainText(
    "LOV - Lovćen Studio",
  )
  expect(reads).toEqual([])
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

test("edits a row in place where the view says inline", async ({ page }) => {
  const code = unique("E2EP")
  await signIn(page)
  await page.getByRole("button", { name: "Products" }).click()

  // Creating still belongs to the form -- a grid row hides required fields
  // its columns do not show -- so the journey makes its own subject first.
  await page.getByRole("button", { name: "New" }).click()
  await page.getByRole("textbox", { name: "Code" }).fill(code)
  await page.getByRole("textbox", { name: "Name" }).fill(`${code} Widget`)
  await page.getByRole("textbox", { name: "Unit Price" }).fill("10.00")
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(createdRow(page)).toContainText(code)

  // Double-click puts the row itself into edit: the writable columns become
  // editors scoped by the fresh GET, and Enter is the save.
  await page.getByRole("row", { name: new RegExp(code) }).dblclick()
  const name = page.getByRole("textbox", { name: "Name" })
  await expect(name).toHaveValue(`${code} Widget`)
  await name.fill(`${code} Gadget`)
  await name.press("Enter")
  const row = page.getByRole("row", { name: new RegExp(code) })
  await expect(row).toContainText(`${code} Gadget`)
  await expect(page.getByRole("textbox", { name: "Name" })).toHaveCount(0)

  // A reload proves the server holds the edit, not the tab.
  await page.reload()
  await expect(
    page.getByRole("row", { name: new RegExp(code) }),
  ).toContainText(`${code} Gadget`)
})

test("creates a record, edits it, and finds the server kept it", async ({
  page,
}) => {
  const code = unique("E2EC")
  await signIn(page)
  await page.getByRole("button", { name: "Customers" }).click()

  // Two entries in one run. The first ends with `Save and New`, which writes
  // the record and hands the form back empty; only the second closes to the
  // grid. Every `Save` locator below is exact for that reason -- Playwright
  // matches an accessible name by substring, and "Save and New" contains it.
  const first = `${code}X`
  await page.getByRole("button", { name: "New" }).click()
  await page.getByRole("textbox", { name: "Code" }).fill(first)
  await page.getByRole("textbox", { name: "Name" }).fill(`${first} Holdings`)
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(`${first.toLowerCase()}@e2e.example`)
  await page.getByRole("button", { name: "Save and New" }).click()
  await expect(
    page.getByRole("heading", { level: 1, name: "New Customer" }),
  ).toBeVisible()
  await expect(page.getByRole("textbox", { name: "Code" })).toHaveValue("")

  await page.getByRole("textbox", { name: "Code" }).fill(code)
  await page.getByRole("textbox", { name: "Name" }).fill(`${code} Holdings`)
  await page
    .getByRole("textbox", { name: "Email" })
    .fill(`${code.toLowerCase()}@e2e.example`)
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(createdRow(page)).toContainText(code)
  // The one the run did not close on is on the server too, not just cleared
  // off the screen.
  await expect(
    page.getByRole("row", { name: new RegExp(first) }),
  ).toBeVisible()

  await page.getByRole("button", { name: "Open" }).click()
  await page.getByRole("textbox", { name: "Name" }).fill(`${code} Renamed`)
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled()

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
  await page.getByRole("button", { name: "Save", exact: true }).click()

  // Number allocated by the application's own action, total computed from the
  // line by the expression engine. The browser sent neither.
  const created = createdRow(page)
  await expect(created).toContainText(/INV-\d{4}-\d{4}/)
  await expect(created).toContainText("255.00")
  await expect(created).toContainText("Draft")

  await page.getByRole("button", { name: "Open" }).click()
  await page.getByRole("button", { name: "Post" }).click()

  await expect(page.getByText("Post completed successfully.")).toBeVisible()
  await expect(page.getByText("Posted", { exact: true })).toBeVisible()
  // The action is spent: `enabled_when` no longer holds.
  await expect(page.getByRole("button", { name: "Post" })).toBeDisabled()
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
  await page.getByRole("button", { name: "Save", exact: true }).click()
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
  await other.getByRole("button", { name: "Save", exact: true }).click()
  await expect(other.getByRole("button", { name: "Save", exact: true })).toBeDisabled()

  await page.getByRole("textbox", { name: "Currency" }).fill("GBP")
  await page.getByRole("button", { name: "Save", exact: true }).click()

  const conflict = page.getByRole("dialog", { name: "Record changed elsewhere" })
  await expect(conflict).toBeVisible()
  await expect(conflict).toContainText("1 decision remaining")
  await expect(page.getByText("This record changed on the server.")).toBeVisible()

  await conflict.getByRole("button", { name: "Use my Currency" }).click()
  await conflict.getByRole("button", { name: "Apply resolution" }).click()
  await page.getByRole("button", { name: "Save", exact: true }).click()

  await expect(page.getByRole("textbox", { name: "Currency" })).toHaveValue(
    "GBP",
  )
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled()
})

test("browses and opens a record without touching the mouse", async ({
  page,
}) => {
  await signIn(page)
  await expect(page.getByRole("row", { name: /INV-2026-0001/ })).toBeVisible()

  // The grid costs one tab stop, not one per visible row. Tabbing from the
  // last column header has to land on a row and the next Tab has to leave the
  // rows entirely -- with eight rendered rows the old behaviour spent eight
  // stops here, and a browse of any size spent one per rendered row.
  await page.getByRole("button", { name: "Total" }).focus()
  await page.keyboard.press("Tab")
  const first = page.getByRole("row", { name: /INV-2026-0001/ })
  await expect(first).toBeFocused()
  await page.keyboard.press("Tab")
  await expect(page.locator('[role="row"]:focus')).toHaveCount(0)

  await first.focus()

  await page.keyboard.press("ArrowDown")
  const second = page.getByRole("row", { name: /INV-2026-0002/ })
  await expect(second).toBeFocused()
  // Moving the caret selects, so `Open` and the record pane follow the
  // keyboard rather than the last click.
  await expect(second).toHaveAttribute("aria-selected", "true")

  await page.keyboard.press("Enter")
  await expect(
    page.getByRole("heading", { level: 1, name: /INV-2026-0002/ }),
  ).toBeVisible()
})

test("names the tab after the screen and wears a mark", async ({ page }) => {
  // Every screen used to be `TIDE Framework`, so two tabs of one application
  // were indistinguishable and the history was a column of identical entries.
  await page.goto("/")
  await expect(page).toHaveTitle("TIDE Framework")

  await signIn(page)
  await expect(page).toHaveTitle("Invoices · TIDE Invoicing")

  await page.getByRole("row", { name: /INV-2026-0002/ }).click()
  await page.getByRole("button", { name: "Open" }).click()
  await expect(page).toHaveTitle("Invoice — INV-2026-0002 · TIDE Invoicing")

  await page.goBack()
  await expect(page).toHaveTitle("Invoices · TIDE Invoicing")

  // Served as an image, not swallowed by the SPA catch-all that answers an
  // unknown path with `index.html` -- which is what `/favicon.ico` still gets
  // and is why the icon is declared rather than guessed at.
  const icon = page.locator('link[rel="icon"]')
  await expect(icon).toHaveAttribute("href", "/favicon.svg")
  const served = await page.request.get("/favicon.svg")
  expect(served.status()).toBe(200)
  expect(served.headers()["content-type"]).toContain("image/svg+xml")
})

test("does not report a cold load as a failure", async ({ page }) => {
  const failures: string[] = []
  page.on("response", (response) => {
    if (response.status() >= 400) {
      failures.push(`${response.status()} ${new URL(response.url()).pathname}`)
    }
  })

  await page.goto("/")
  await expect(
    page.getByRole("heading", { name: "Sign in to your application" }),
  ).toBeVisible()

  // The shell asks whether this browser is already signed in. It is not, and
  // that is an answer rather than a refusal: anyone opening the console on a
  // page where nothing has gone wrong should find it empty.
  expect(failures).toEqual([])
})

// A ceiling, not a target. The entry chunk was 563 kB when everything shipped
// together; splitting the shell and the record screen out took it to ~331 kB,
// and this leaves room to grow without letting the split quietly come undone —
// one static import of `app-shell` or `record-detail` puts it all back.
const ENTRY_CHUNK_BUDGET_BYTES = 420_000

test("does not block the first paint on the whole application", async ({
  page,
}) => {
  const html = await (await page.request.get("/")).text()
  const entry = /<script[^>]+type="module"[^>]+src="([^"]+)"/.exec(html)?.[1]
  expect(entry, "index.html references a module entry").toBeTruthy()

  // What a browser must download before it can run anything at all. The rest
  // arrives in its own time: the shell while the sign-in form is being read,
  // the record screen when a record is opened.
  const bytes = (await (await page.request.get(entry!)).body()).length
  expect(
    bytes,
    `${entry} is ${Math.round(bytes / 1000)} kB of render-blocking JavaScript`,
  ).toBeLessThan(ENTRY_CHUNK_BUDGET_BYTES)
})

test("renders its own API description under its own security headers", async ({
  page,
}) => {
  const refused: string[] = []
  page.on("console", (message) => {
    if (/Content Security Policy/i.test(message.text())) {
      refused.push(message.text().slice(0, 120))
    }
  })
  const failed: string[] = []
  page.on("requestfailed", (request) => failed.push(request.url()))

  // This server owns identities, so it sends `script-src 'self'`. FastAPI's
  // Swagger UI is a CDN script tag plus an inline initialiser, and every part
  // of it was refused here: `/docs` answered 200 and drew nothing, which the
  // status-code tests were happy with. TIDE serves the assets itself now.
  await page.goto("/docs")
  await expect(page.locator(".opblock").first()).toBeVisible()
  await expect(page.getByText("/api/v1/invoices").first()).toBeVisible()

  expect(refused, "nothing on this page may need an exception to the CSP").toEqual(
    [],
  )
  expect(failed).toEqual([])

  // Same-origin, so it works with no network at all -- which is also what
  // makes it load in a test that has none.
  const external = page.locator(
    'script[src^="http"], link[rel="stylesheet"][href^="http"]',
  )
  await expect(external).toHaveCount(0)
})
