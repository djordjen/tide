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

test("filters a column by its checked values, and the footer follows", async ({
  page,
}) => {
  await signIn(page)
  await expect(page.getByRole("row", { name: /INV-2026-0001/ })).toBeVisible()
  await expect(page.getByTestId("grid-summary-number")).toHaveText(/Count9/)

  // The funnel's list is the server's distinct answer; the gesture is the
  // reference application's -- clear everything, choose what stays.
  await page.getByRole("button", { name: "Filter Status" }).click()
  const popover = page.getByRole("dialog", { name: "Status values" })
  await popover.getByRole("checkbox", { name: "Select all" }).click()
  await popover.getByRole("checkbox", { name: "Draft" }).click()
  await popover.getByRole("button", { name: "Apply" }).click()

  // Five seeded drafts -- and the summary footer answers for the same
  // filtered set the rows come from, so the two agree on their own.
  await expect(page.getByTestId("grid-summary-number")).toHaveText(/Count5/)
  const statuses = page.getByRole("row").getByText("Draft", { exact: true })
  await expect(statuses).toHaveCount(5)
  await expect(
    page.getByRole("row", { name: /INV-2026-0001/ }),
  ).toHaveCount(0)
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


test("reads the history the server kept of what it just did", async ({
  page,
}) => {
  const code = unique("E2EH")
  await signIn(page)
  await page.getByRole("button", { name: "Customers" }).click()

  // The journey writes its own subject, so the trail it reads back is one
  // whose every event it caused: a create, then a rename.
  await page.getByRole("button", { name: "New" }).click()
  await page.getByRole("textbox", { name: "Code" }).fill(code)
  await page.getByRole("textbox", { name: "Name" }).fill(`${code} Holdings`)
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(createdRow(page)).toContainText(code)

  await page.getByRole("button", { name: "Open" }).click()
  await page.getByRole("textbox", { name: "Name" }).fill(`${code} Renamed`)
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Save", exact: true }),
  ).toBeDisabled()

  // History is a tab on the panel below the record, present because this
  // principal's auditor hat grants it. The server answers with both writes,
  // newest first; the changed field speaks the form's label, and the values
  // are the stored ones read back -- nothing here was kept by the tab.
  await page.getByRole("tab", { name: "History" }).click()
  const panel = page.getByRole("tabpanel")
  await expect(panel.getByText("Updated")).toBeVisible()
  await expect(
    panel.getByText(`Name: ${code} Holdings → ${code} Renamed`),
  ).toBeVisible()
  await expect(panel.getByText("Created")).toBeVisible()
  await expect(
    panel.getByText("Newest first · Protected values stay redacted"),
  ).toBeVisible()
})


test("finds records from one box over everything", async ({ page }) => {
  await signIn(page)

  // One text, two entities: the server sweeps every searchable entity this
  // identity may read, so "consulting" answers a product and a customer in
  // the same panel -- grouped, labeled, and each hit a door.
  await page.getByRole("button", { name: "Search everywhere" }).click()
  await page
    .getByRole("searchbox", { name: "Search everywhere" })
    .fill("consulting")
  const panel = page.getByRole("dialog")
  await expect(
    panel.getByRole("heading", { name: "Products" }),
  ).toBeVisible()
  await expect(
    panel.getByRole("heading", { name: "Customers" }),
  ).toBeVisible()
  await expect(
    panel.getByRole("link", { name: "CONS - Consulting hour" }),
  ).toBeVisible()

  // Scoped to the panel: the invoice grid behind it names the same
  // customer in its own reference links.
  await panel
    .getByRole("link", { name: "ADRIA - Adria Consulting" })
    .click()
  await expect(
    page.getByRole("heading", { level: 1, name: /ADRIA/ }),
  ).toBeVisible()
})


test("administers who holds which role, and refuses the last way back in", async ({
  page,
}) => {
  // The account this run creates, and a password that exists only here: the
  // store is built fresh for the run and thrown away with it.
  const username = unique("clerk").toLowerCase()
  const password = "a journey passphrase"

  await signIn(page)
  await page.getByRole("button", { name: "Identities" }).click()
  const accounts = page.getByRole("table", { name: "Accounts" })
  await expect(accounts.getByRole("button", { name: "e2e" })).toBeVisible()

  // Roles are compiled, and this screen says so rather than offering to
  // change them.
  const roles = page.getByRole("region", { name: "Roles" })
  await expect(roles).toContainText("tide.users.administer")
  expect(await roles.getByRole("checkbox").count()).toBe(0)

  await page.getByRole("button", { name: "New account" }).click()
  await page.getByLabel("Username").fill(username)
  await page.getByLabel("Display name").fill("Journey Clerk")
  await page.getByLabel("Password", { exact: true }).fill(password)
  await page.getByRole("checkbox", { name: "sales_clerk" }).check()
  await page.getByRole("button", { name: "Create account" }).click()
  await expect(page.getByRole("status")).toContainText(username)

  // Replaced, not added to: the role it arrived with is gone.
  await page.getByRole("checkbox", { name: "auditor" }).check()
  await page.getByRole("checkbox", { name: "sales_clerk" }).uncheck()
  await page.getByRole("button", { name: "Save roles" }).click()
  const created = page.getByRole("row", { name: new RegExp(username) })
  await expect(created).toContainText("auditor")
  await expect(created).not.toContainText("sales_clerk")

  await page.getByRole("button", { name: "Disable account" }).click()
  await expect(created).toContainText("Disabled")

  // The one account that can still administer cannot take that away from
  // itself, because a console on the server would then be the only way back.
  await accounts.getByRole("button", { name: "e2e" }).click()
  await page.getByRole("button", { name: "Disable account" }).click()
  await expect(page.getByRole("status")).toContainText(
    "only enabled account that may administer",
  )
  await expect(
    page.getByRole("row", { name: /e2e/ }),
  ).toContainText("Enabled")
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

test("voids an invoice only after answering for its reason", async ({
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
  await page.getByRole("textbox", { name: "Quantity" }).fill("1")
  await page.getByRole("button", { name: "Apply Line" }).click()
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(createdRow(page)).toContainText("Draft")

  await page.getByRole("button", { name: "Open" }).click()

  // The required parameter turns the button into a question: clicking Void
  // opens the form instead of executing, and the popover's own Void waits
  // for the answer.
  await page.getByRole("button", { name: "Void" }).click()
  const ask = page.getByRole("dialog")
  await expect(ask.getByRole("textbox", { name: "Reason" })).toBeVisible()
  await expect(ask.getByRole("button", { name: "Void" })).toBeDisabled()

  await ask.getByRole("textbox", { name: "Reason" }).fill("Damaged in transit")
  await ask.getByRole("button", { name: "Void" }).click()

  await expect(page.getByText("Void completed successfully.")).toBeVisible()
  await expect(page.getByText("Cancelled", { exact: true })).toBeVisible()
  // The answer landed on the record: the now-locked Cancellation group
  // shows it as read-only text.
  await expect(page.getByText("Damaged in transit")).toBeVisible()
  // The action is spent: the transition guard no longer holds.
  await expect(page.getByRole("button", { name: "Void" })).toBeDisabled()
})

test("duplicates a posted invoice into a fresh editable draft", async ({
  page,
}) => {
  await signIn(page)

  await page.getByRole("row", { name: /INV-2026-0001/ }).click()
  await page.getByRole("button", { name: "Open" }).click()
  await expect(page.getByText("Invoice — INV-2026-0001")).toBeVisible()

  // The head start: the form reopens as a new record carrying what a
  // person could have typed on the original -- and nothing the system
  // owns, so there is no number yet and the state is the default.
  await page.getByRole("button", { name: "Duplicate" }).click()
  await expect(page.getByText("New Invoice")).toBeVisible()
  await expect(page.getByRole("textbox", { name: "Currency" })).toHaveValue(
    "EUR",
  )
  // The copied line is here; its stored total deliberately is not -- the
  // server finalizes calculated values when the record is saved.
  await expect(page.getByText("Demo invoice line").first()).toBeVisible()

  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(page.getByText("Invoice created successfully.")).toBeVisible()

  // A genuinely new record beside the original: same customer and total,
  // its own freshly allocated number, back at the start of the workflow.
  const copy = page
    .getByRole("row")
    .filter({ hasText: "Draft" })
    .filter({ hasText: "850.00" })
  await expect(copy).toHaveCount(1)
  await expect(copy).not.toContainText("INV-2026-0001")
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "Posted" })
      .filter({ hasText: "INV-2026-0001" }),
  ).toHaveCount(1)
})

test("attaches the signed document after posting and reads it back", async ({
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
  await page.getByRole("textbox", { name: "Quantity" }).fill("1")
  await page.getByRole("button", { name: "Apply Line" }).click()
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(createdRow(page)).toContainText("Draft")

  // Posted first, then the document: this is the order the carve-out
  // exists for, and doing it the other way round would prove nothing.
  await page.getByRole("button", { name: "Open" }).click()
  await page.getByRole("button", { name: "Post" }).click()
  await expect(page.getByText("Post completed successfully.")).toBeVisible()

  // Everything else on this record is frozen now.
  await expect(page.getByRole("textbox", { name: "Currency" })).toHaveCount(0)

  await page.getByLabel("Signed document").setInputFiles({
    name: "confirmation.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 countersigned by the customer"),
  })

  // The upload answered, and the draft is holding what it answered with.
  await expect(page.getByText("confirmation.pdf")).toBeVisible()
  await page.getByRole("button", { name: "Save", exact: true }).click()

  // Away and back, so what is on screen came from the database rather than
  // from the draft that put it there.
  await page.goBack()
  await page.getByRole("button", { name: "Open" }).click()
  await expect(page.getByText("confirmation.pdf")).toBeVisible()

  // The record now holds a document, so the lock covers it: it can be read
  // and it cannot become a different one.
  const link = page.getByRole("button", { name: "Download confirmation.pdf" })
  await expect(link).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Replace Signed document" }),
  ).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: "Delete Signed document" }),
  ).toHaveCount(0)

  // The name is the door: clicking it is what fetches the file.
  const download = page.waitForEvent("download")
  await link.click()
  expect((await download).suggestedFilename()).toBe("confirmation.pdf")
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
  // The last header control is now the Total column's funnel -- it stays
  // in the tab order because, unlike a row's reference doors, it has no
  // keyboard alternate. From it, one Tab still reaches the whole grid.
  await page.getByRole("button", { name: "Filter Total" }).focus()
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

test("takes the filtered grid away as a file the server built", async ({
  page,
}) => {
  // The grid is virtualised, so what the browser holds is a page rather than
  // the table. The file has to come from the server walking the same query,
  // which is why this asserts the request the control sends as well as the
  // download it gets back.
  await signIn(page)

  const exports: unknown[] = []
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/_export/csv")) {
      exports.push(request.postDataJSON())
    }
  })

  await page
    .getByRole("button", { name: "Named filter" })
    .or(page.getByRole("button", { name: /Draft invoices|All records/ }))
    .first()
    .click()
  await page.getByRole("menuitemradio", { name: "Draft invoices" }).click()

  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "Export records" }).click()
  await page.getByRole("menuitem", { name: "CSV" }).click()
  const file = await download

  expect(file.suggestedFilename()).toMatch(/^invoices-\d{4}-\d{2}-\d{2}\.csv$/)
  // The condition the reader chose reached the server, rather than the rows
  // the grid happened to be holding.
  expect(exports).toEqual([
    {
      view: "sales.Invoice.browse",
      filters: [{ field: "status", operator: "eq", value: "draft" }],
      sort: [],
    },
  ])
})

test("arranges the grid and the server remembers the arrangement", async ({
  page,
}) => {
  await signIn(page)
  // A journey that writes creates what it writes to; this one edits the
  // signed-in user's own arrangement, so it starts by resetting it and
  // leaves it reset -- running twice is safe, and the declared header is
  // a known starting point rather than an assumption. Through the UI, not
  // page.request: unsafe methods on a cookie session need the X-TIDE-CSRF
  // header, and a raw DELETE without it earns a 403.
  await page.getByRole("button", { name: "Choose columns" }).click()
  await page.getByRole("button", { name: "Reset to default" }).click()
  await expect(
    page.getByRole("button", { name: "Filter Number" }),
  ).toBeVisible()

  await page.getByRole("button", { name: "Choose columns" }).click()
  await page.getByRole("checkbox", { name: "Show Version" }).click()
  await page.getByRole("textbox", { name: "Rename Number" }).fill("No.")
  // Move the freshly shown column to the front: three swaps from the end
  // of a five-column arrangement. This is what proves the chooser's order
  // reaches the grid -- the drag-remembered client order stands aside
  // while an arrangement is active, and the header redraws on Apply.
  for (let step = 0; step < 5; step += 1) {
    await page.getByRole("button", { name: "Move Version up" }).click()
  }
  await page.getByRole("button", { name: "Apply" }).click()

  await expect(page.getByRole("button", { name: "Filter No." })).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Filter Version" }),
  ).toBeVisible()
  await expect(
    page.locator('button[aria-label^="Filter "]').first(),
  ).toHaveAttribute("aria-label", "Filter Version")

  // A fresh load rebuilds the grid from the server's answer, not from
  // anything this tab kept -- which is the difference between a stored
  // arrangement and a styling accident.
  await page.reload()
  await expect(page.getByRole("button", { name: "Filter No." })).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Filter Version" }),
  ).toBeVisible()
  await expect(
    page.locator('button[aria-label^="Filter "]').first(),
  ).toHaveAttribute("aria-label", "Filter Version")

  await page.getByRole("button", { name: "Choose columns" }).click()
  await page.getByRole("button", { name: "Reset to default" }).click()
  await expect(
    page.getByRole("button", { name: "Filter Number" }),
  ).toBeVisible()
  await expect(page.getByRole("button", { name: "Filter Version" })).toHaveCount(
    0,
  )
})

test("finds the work again from home", async ({ page }) => {
  // Home is the landing: the clean URL, the person's own work assembled.
  await signIn(page, "/")
  await expect(page.getByRole("heading", { name: "Home" })).toBeVisible()

  // A workspace tile wears the live numbers the browse itself would show.
  const workspaces = page.getByRole("region", { name: "Workspaces" })
  const invoicesTile = workspaces.getByRole("button", { name: /Invoices/ })
  await expect(invoicesTile.getByTestId("tile-numbers")).toContainText(
    "Count",
  )

  // Keep a view worth returning to, from inside the browse it belongs to.
  await invoicesTile.click()
  await expect(
    page.getByRole("button", { name: "Filter Number" }),
  ).toBeVisible()
  const savedName = unique("Return ")
  await page.getByRole("button", { name: /All records/ }).first().click()
  await page.getByRole("menuitemradio", { name: "Draft invoices" }).click()
  await page.getByRole("button", { name: "Save current view" }).click()
  await page.getByRole("textbox", { name: "View name" }).fill(savedName)
  await page.keyboard.press("Enter")
  await expect(page.getByRole("button", { name: savedName })).toBeVisible()

  // Back on Home it is a tile under My views, with its own live count.
  await page.getByRole("button", { name: "Home" }).click()
  const mine = page.getByRole("region", { name: "My views" })
  const savedTile = mine.getByRole("button", {
    name: new RegExp(savedName),
  })
  await expect(savedTile.getByTestId("tile-numbers")).toContainText("Count")

  // The tile opens the browse with the whole state relit: the trigger
  // wears the name, and the rows answer to the saved filter.
  await savedTile.click()
  await expect(page.getByRole("button", { name: savedName })).toBeVisible()
  await expect(page.getByRole("row", { name: /INV-2026-0001/ })).toHaveCount(
    0,
  )

  // A report shortcut opens the same preview the browse offers.
  await page.getByRole("button", { name: "Home" }).click()
  const reports = page.getByRole("region", { name: "Reports" })
  await reports
    .getByRole("button", { name: /Posted Sales Summary/ })
    .click()
  await expect(
    page.getByRole("dialog", { name: "Posted Sales Summary" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Close report preview" }).click()

  // Leave nothing behind: the unique name goes the way it came.
  await workspaces.getByRole("button", { name: /Invoices/ }).click()
  await page.getByRole("button", { name: "All records" }).click()
  await page
    .getByRole("button", { name: `Delete saved view ${savedName}` })
    .click()
  await expect(
    page.getByRole("menuitemradio", { name: savedName }),
  ).toHaveCount(0)
  await page.keyboard.press("Escape")
})

test("ranges a date, floors a total, and the saved view relights them", async ({
  page,
}) => {
  await signIn(page)

  // The invoice-date funnel is a From/To pair now -- a list of individual
  // dates was never the question.
  await page.getByRole("button", { name: "Filter Invoice Date" }).click()
  await page.getByLabel("From", { exact: true }).fill("2026-07-04")
  await page.getByLabel("To", { exact: true }).fill("2026-07-12")
  await page.getByRole("button", { name: "Apply" }).click()

  await page.getByRole("button", { name: "Filter Total" }).click()
  await page.getByLabel("Min", { exact: true }).fill("500")
  await page.getByRole("button", { name: "Apply" }).click()

  // Three seeded rows answer, drafts and posted alike -- the range asks
  // about dates and totals, not status -- and the footer answers for the
  // same filtered set the rows come from.
  await expect(page.getByTestId("grid-summary-number")).toHaveText(/Count3/)
  await expect(page.getByRole("row", { name: /INV-2026-0003/ })).toBeVisible()
  await expect(page.getByRole("row", { name: /INV-2026-0004/ })).toBeVisible()
  await expect(page.getByRole("row", { name: /INV-2026-0002/ })).toHaveCount(0)

  const savedName = unique("Window ")
  await page.getByRole("button", { name: "Save current view" }).click()
  await page.getByRole("textbox", { name: "View name" }).fill(savedName)
  await page.keyboard.press("Enter")
  await expect(page.getByRole("button", { name: savedName })).toBeVisible()

  // The Home tile answers with the same numbers the footer showed.
  await page.getByRole("button", { name: "Home" }).click()
  const tile = page
    .getByRole("region", { name: "My views" })
    .getByRole("button", { name: new RegExp(savedName) })
  await expect(tile.getByTestId("tile-numbers")).toContainText("3,112.50")

  // Opening the tile relights the controls, bounds included -- a grid
  // constrained by conditions its controls do not show would be lying.
  await tile.click()
  await expect(page.getByRole("button", { name: savedName })).toBeVisible()
  const dateFunnel = page.getByRole("button", {
    name: "Filter Invoice Date",
  })
  await expect(dateFunnel).toHaveAttribute("aria-pressed", "true")
  await dateFunnel.click()
  await expect(page.getByLabel("From", { exact: true })).toHaveValue("2026-07-04")
  await expect(page.getByLabel("To", { exact: true })).toHaveValue("2026-07-12")
  await page.keyboard.press("Escape")
  await expect(page.getByTestId("grid-summary-number")).toHaveText(/Count3/)

  // Leave nothing behind.
  await page.getByRole("button", { name: savedName }).click()
  await page
    .getByRole("button", { name: `Delete saved view ${savedName}` })
    .click()
  await expect(
    page.getByRole("menuitemradio", { name: savedName }),
  ).toHaveCount(0)
  await page.keyboard.press("Escape")
})

test("names a grid state and the server hands it back", async ({ page }) => {
  await signIn(page)
  const savedName = unique("Chase ")
  await expect(
    page.getByRole("button", { name: "Filter Number" }),
  ).toBeVisible()

  // Build a state worth naming: a standing arrangement with an extra
  // column, plus a declared filter.
  await page.getByRole("button", { name: "Choose columns" }).click()
  await page.getByRole("checkbox", { name: "Show Version" }).click()
  await page.getByRole("button", { name: "Apply" }).click()
  await expect(
    page.getByRole("button", { name: "Filter Version" }),
  ).toBeVisible()
  await page
    .getByRole("button", { name: /All records|Draft invoices/ })
    .first()
    .click()
  await page.getByRole("menuitemradio", { name: "Draft invoices" }).click()

  // Enter in the name box is the save: the form submits implicitly, the
  // way every data-entry surface here advances on Enter.
  await page.getByRole("button", { name: "Save current view" }).click()
  await page.getByRole("textbox", { name: "View name" }).fill(savedName)
  await page.keyboard.press("Enter")
  await expect(page.getByRole("button", { name: savedName })).toBeVisible()

  // The snapshot must outlive the standing arrangement it was taken
  // from: reset the arrangement, leave the saved view, come back.
  await page.getByRole("button", { name: "Choose columns" }).click()
  await page.getByRole("button", { name: "Reset to default" }).click()
  await page.getByRole("button", { name: savedName }).click()
  await page.getByRole("menuitemradio", { name: "All records" }).click()
  await expect(page.getByRole("button", { name: "Filter Version" })).toHaveCount(
    0,
  )
  await page.getByRole("button", { name: "All records" }).click()
  await page.getByRole("menuitemradio", { name: savedName }).click()
  await expect(
    page.getByRole("button", { name: "Filter Version" }),
  ).toBeVisible()
  await expect(page.getByRole("row", { name: /INV-2026-0002/ })).toBeVisible()
  await expect(page.getByRole("row", { name: /INV-2026-0001/ })).toHaveCount(0)

  // A fresh load rebuilds the offer from the server, not from this tab.
  await page.reload()
  await page.getByRole("button", { name: "All records" }).click()
  await page.getByRole("menuitemradio", { name: savedName }).click()
  await expect(
    page.getByRole("button", { name: "Filter Version" }),
  ).toBeVisible()

  // Leave nothing behind: delete the entry and watch the section forget
  // it. The journey's name is unique per run, so a failed run cannot
  // trip the next one either.
  await page.getByRole("button", { name: savedName }).click()
  await page
    .getByRole("button", { name: `Delete saved view ${savedName}` })
    .click()
  await expect(
    page.getByRole("menuitemradio", { name: savedName }),
  ).toHaveCount(0)
  await page.keyboard.press("Escape")
})

test("weighs a large-quantity warning and saves anyway", async ({ page }) => {
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
  await page.getByRole("textbox", { name: "Quantity" }).fill("500")
  await page.getByRole("button", { name: "Apply Line" }).click()
  await page.getByRole("button", { name: "Save", exact: true }).click()

  // The declared `severity: warning` rule on the line entity, evaluated at
  // the invoice commit and answered as a question rather than a failure:
  // amber, the rule's own words, and no red banner anywhere.
  await expect(
    page.getByText("The line quantity is unusually large."),
  ).toBeVisible()
  await expect(
    page.getByText("The record could not be saved."),
  ).not.toBeVisible()

  // Acknowledging resubmits with the rule id and the same draft; the
  // computed total proves the write went through the ordinary pipeline.
  await page.getByRole("button", { name: "Save anyway" }).click()
  const created = createdRow(page)
  await expect(created).toContainText(/INV-\d{4}-\d{4}/)
  await expect(created).toContainText("42,500.00")
  await expect(created).toContainText("Draft")
})
