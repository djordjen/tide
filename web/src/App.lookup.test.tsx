import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("searches a metadata lookup and creates a related record with Save & Select", async () => {
  const selections: Array<Record<string, unknown>> = []
  const invoiceUpdates: Array<Record<string, unknown>> = []
  const customers = new Map<number, Record<string, unknown>>([
    [1, customer(1, "ADRIA", "Adria Consulting", "hello@adria.test")],
    [2, customer(2, "NORTH", "Northwind Trade", "sales@north.test")],
  ])
  let nextCustomerId = 3
  let storedInvoice = invoice(1)

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: false,
          mode: null,
          login_path: null,
          session_path: null,
          logout_path: null,
        })
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({
          records: [
            {
              id: 1,
              number: storedInvoice.number,
              customer: storedInvoice.customer,
              status: storedInvoice.status,
              total: storedInvoice.total,
            },
          ],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/1") && init?.method === "GET") {
        return jsonResponse(storedInvoice, {
          headers: { ETag: '"4"' },
        })
      }
      if (url.endsWith("/invoices/1") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        invoiceUpdates.push(body)
        storedInvoice = { ...storedInvoice, ...body }
        return jsonResponse(storedInvoice, {
          headers: { ETag: '"5"' },
        })
      }
      if (url.endsWith("/customers/_query")) {
        const body = JSON.parse(String(init?.body)) as {
          filters: Array<{ field: string; value: string }>
        }
        const candidate = body.filters[0]?.value.toLowerCase() ?? ""
        const records = [...customers.values()].filter((record) =>
          body.filters.length === 0
            ? true
            : String(record[body.filters[0].field])
                .toLowerCase()
                .includes(candidate),
        )
        return jsonResponse({ records, next_cursor: null })
      }
      if (url.endsWith("/customers") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        const created = customer(
          nextCustomerId,
          String(body.code),
          String(body.name),
          String(body.email),
        )
        customers.set(nextCustomerId, created)
        nextCustomerId += 1
        return jsonResponse(created, { status: 201 })
      }
      if (url.endsWith("/_tide/reference-selection")) {
        const body = JSON.parse(String(init?.body)) as {
          entity: string
          field: string
          values: Record<string, unknown>
          identity: number
        }
        selections.push(body)
        const refusal = selectionWireRefusal(body.entity, body.values)
        if (refusal) {
          return refusal
        }
        return jsonResponse({
          values: { ...body.values, customer: body.identity },
        })
      }
      const customerMatch = /\/customers\/(\d+)$/.exec(url)
      if (customerMatch && init?.method === "GET") {
        return jsonResponse(customers.get(Number(customerMatch[1])))
      }
      const productMatch = /\/products\/(\d+)$/.exec(url)
      if (productMatch && init?.method === "GET") {
        return jsonResponse(
          product(Number(productMatch[1]), "DEMO", "Demo product", "100.00"),
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0001/ }),
  )

  expect(await screen.findByText("Demo line")).toBeInTheDocument()
  expect(
    (await screen.findAllByText("ADRIA - Adria Consulting")).length,
  ).toBeGreaterThan(0)

  await user.click(screen.getByRole("button", { name: "Select Customer" }))
  const lookup = await screen.findByRole("dialog", {
    name: "Select Customer",
  })
  expect(within(lookup).getByRole("columnheader", { name: "Code" }))
    .toBeInTheDocument()
  expect(within(lookup).getByRole("columnheader", { name: "Name" }))
    .toBeInTheDocument()
  expect(within(lookup).getByRole("columnheader", { name: "Email" }))
    .toBeInTheDocument()

  await user.type(
    within(lookup).getByLabelText("Search lookup records"),
    "north",
  )
  const northwind = await within(lookup).findByRole("row", {
    name: /NORTH.*Northwind Trade/,
  })
  await user.click(northwind)
  await user.click(within(lookup).getByRole("button", { name: "Select" }))

  expect(
    (await screen.findAllByText("NORTH - Northwind Trade")).length,
  ).toBeGreaterThan(0)
  expect(selections[0]).toMatchObject({
    entity: "sales.Invoice",
    field: "customer",
    identity: 2,
    values: {
      invoice_date: "2026-07-29",
      currency: "EUR",
      customer: 1,
    },
  })
  await user.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(invoiceUpdates).toEqual([{ customer: 2 }]))

  await user.click(screen.getByRole("button", { name: "Select Customer" }))
  const secondLookup = await screen.findByRole("dialog", {
    name: "Select Customer",
  })
  await user.click(
    within(secondLookup).getByRole("button", { name: "New Customer" }),
  )
  expect(
    within(secondLookup).getByRole("heading", { name: "New Customer" }),
  ).toBeInTheDocument()

  await user.type(within(secondLookup).getByLabelText(/^Code/), "MORA")
  await user.type(
    within(secondLookup).getByLabelText(/^Email/),
    "hello@mora.test",
  )
  await user.type(
    within(secondLookup).getByLabelText(/^Name/),
    "Mora Trade",
  )
  await user.click(
    within(secondLookup).getByRole("button", {
      name: "Save & Select",
    }),
  )

  expect(
    (await screen.findAllByText("MORA - Mora Trade")).length,
  ).toBeGreaterThan(0)
  expect(screen.getByText("Demo line")).toBeInTheDocument()
  expect(selections.at(-1)).toMatchObject({
    entity: "sales.Invoice",
    field: "customer",
    identity: 3,
  })
  await user.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() =>
    expect(invoiceUpdates).toEqual([{ customer: 2 }, { customer: 3 }]),
  )
})

it("saves Invoice line drafts and Product Save & Select as one parent update", async () => {
  const selections: Array<Record<string, unknown>> = []
  const invoiceUpdates: Array<Record<string, unknown>> = []
  const products = new Map<number, Record<string, unknown>>([
    [1, product(1, "DEMO", "Demo product", "100.00")],
    [2, product(2, "SUP", "Support package", "25.00")],
  ])
  let nextProductId = 3
  let storedInvoice = invoice(1)

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: false,
          mode: null,
          login_path: null,
          session_path: null,
          logout_path: null,
        })
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({
          records: [
            {
              id: 1,
              number: storedInvoice.number,
              customer: storedInvoice.customer,
              status: storedInvoice.status,
              total: storedInvoice.total,
            },
          ],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/1") && init?.method === "GET") {
        return jsonResponse(storedInvoice, {
          headers: { ETag: '"4"' },
        })
      }
      if (url.endsWith("/invoices/1") && init?.method === "PATCH") {
        expect(new Headers(init.headers).get("If-Match")).toBe('"4"')
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        invoiceUpdates.push(body)
        storedInvoice = {
          ...storedInvoice,
          ...body,
          total: "50.00",
          version: 5,
        }
        return jsonResponse(storedInvoice, {
          headers: { ETag: '"5"' },
        })
      }
      if (url.endsWith("/products/_query")) {
        return jsonResponse({
          records: [...products.values()],
          next_cursor: null,
        })
      }
      if (url.endsWith("/products") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        const created = product(
          nextProductId,
          String(body.code),
          String(body.name),
          String(body.unit_price),
        )
        products.set(nextProductId, created)
        nextProductId += 1
        return jsonResponse(created, { status: 201 })
      }
      if (url.endsWith("/_tide/reference-selection")) {
        const body = JSON.parse(String(init?.body)) as {
          entity: string
          field: string
          values: Record<string, unknown>
          identity: number
        }
        selections.push(body)
        const refusal = selectionWireRefusal(body.entity, body.values)
        if (refusal) {
          return refusal
        }
        const selected = products.get(body.identity)
        return jsonResponse({
          values: {
            ...body.values,
            product: body.identity,
            description: selected?.name,
            unit_price: selected?.unit_price,
          },
        })
      }
      const productMatch = /\/products\/(\d+)$/.exec(url)
      if (productMatch && init?.method === "GET") {
        return jsonResponse(products.get(Number(productMatch[1])))
      }
      const customerMatch = /\/customers\/(\d+)$/.exec(url)
      if (customerMatch && init?.method === "GET") {
        return jsonResponse(
          customer(
            Number(customerMatch[1]),
            "ADRIA",
            "Adria Consulting",
            "hello@adria.test",
          ),
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0001/ }),
  )

  await user.click(
    await screen.findByRole("button", { name: "Select Product" }),
  )
  const lookupDialog = await screen.findByRole("dialog", {
    name: "Select Product",
  })
  expect(
    within(lookupDialog).getByRole("columnheader", {
      name: "Unit Price",
    }),
  ).toBeInTheDocument()
  await user.click(
    within(lookupDialog).getByRole("button", { name: "New Product" }),
  )
  await user.type(within(lookupDialog).getByLabelText(/^Code/), "CARE")
  await user.type(
    within(lookupDialog).getByLabelText(/^Unit Price/),
    "25.00",
  )
  await user.type(
    within(lookupDialog).getByLabelText(/^Name/),
    "Care package",
  )
  await user.click(
    within(lookupDialog).getByRole("button", {
      name: "Save & Select",
    }),
  )

  expect(selections.at(-1)).toMatchObject({
    entity: "sales.InvoiceLine",
    field: "product",
    identity: 3,
    values: {
      line_number: 1,
      product: 1,
      description: "Demo line",
      quantity: "1.000",
      unit_price: "100.00",
    },
  })
  expect(
    (await screen.findAllByDisplayValue("Care package")).length,
  ).toBeGreaterThan(0)

  const quantity = screen.getByLabelText(/^Quantity/)
  await user.clear(quantity)
  await user.type(quantity, "2.000")
  await user.click(screen.getByRole("button", { name: "Apply Line" }))

  await user.click(screen.getByRole("button", { name: "Add Line" }))
  expect(screen.getByText("2 draft rows")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Remove Line" }))
  expect(screen.getByText("1 draft row")).toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() =>
    expect(invoiceUpdates).toEqual([
      {
        lines: [
          {
            id: 10,
            line_number: 1,
            unit_price: "25.00",
            product: 3,
            quantity: "2.000",
            description: "Care package",
          },
        ],
      },
    ]),
  )
})

it("saves a dirty Invoice before posting it through the domain action", async () => {
  const requests: Array<{
    method: string
    url: string
    headers: Headers
    body: Record<string, unknown>
  }> = []
  let storedInvoice = invoice(1)
  storedInvoice._tide.actions.post.enabled = false

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: false,
          mode: null,
          login_path: null,
          session_path: null,
          logout_path: null,
        })
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({
          records: [
            {
              id: 1,
              number: storedInvoice.number,
              customer: storedInvoice.customer,
              status: storedInvoice.status,
              total: storedInvoice.total,
            },
          ],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/1") && init?.method === "GET") {
        return jsonResponse(storedInvoice, {
          headers: { ETag: '"4"' },
        })
      }
      if (url.endsWith("/invoices/1") && init?.method === "PATCH") {
        const headers = new Headers(init.headers)
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        requests.push({ method: "PATCH", url, headers, body })
        storedInvoice = {
          ...storedInvoice,
          ...body,
          version: 5,
          _tide: {
            ...storedInvoice._tide,
            actions: {
              post: { visible: true, enabled: true },
            },
          },
        }
        return jsonResponse(storedInvoice, {
          headers: { ETag: '"5"' },
        })
      }
      if (
        url.endsWith("/invoices/1/actions/post") &&
        init?.method === "POST"
      ) {
        const headers = new Headers(init.headers)
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        requests.push({ method: "POST", url, headers, body })
        storedInvoice = {
          ...storedInvoice,
          status: "posted",
          version: 6,
          _tide: {
            writable_fields: [],
            actions: {
              post: { visible: true, enabled: false },
            },
          },
        }
        return jsonResponse(storedInvoice, {
          headers: { ETag: '"6"' },
        })
      }
      const productMatch = /\/products\/(\d+)$/.exec(url)
      if (productMatch && init?.method === "GET") {
        return jsonResponse(
          product(
            Number(productMatch[1]),
            "DEMO",
            "Demo product",
            "100.00",
          ),
        )
      }
      const customerMatch = /\/customers\/(\d+)$/.exec(url)
      if (customerMatch && init?.method === "GET") {
        return jsonResponse(
          customer(
            Number(customerMatch[1]),
            "ADRIA",
            "Adria Consulting",
            "hello@adria.test",
          ),
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0001/ }),
  )

  const currency = await screen.findByLabelText(/^Currency/)
  await user.clear(currency)
  await user.type(currency, "USD")
  await user.click(
    screen.getByRole("button", { name: "Post invoice" }),
  )

  await waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[0]).toMatchObject({
    method: "PATCH",
    body: { currency: "USD" },
  })
  expect(requests[0].headers.get("If-Match")).toBe('"4"')
  expect(requests[1]).toMatchObject({
    method: "POST",
    body: {},
  })
  expect(requests[1].headers.get("If-Match")).toBe('"5"')
  expect(requests[1].headers.get("Idempotency-Key")).toMatch(
    /^web:[0-9a-f-]+$/,
  )
  expect(
    await screen.findByText("Post invoice completed successfully."),
  ).toBeInTheDocument()
  expect(screen.getAllByText("posted").length).toBeGreaterThan(0)
  expect(screen.getByRole("button", { name: "Post invoice" })).toBeDisabled()
})

it("previews secured record and summary reports and downloads controlled exports", async () => {
  const reportRequests: Array<{ method: string; url: string }> = []
  const downloadedNames: string[] = []
  const NativeURL = URL
  vi.stubGlobal(
    "URL",
    class extends NativeURL {
      static createObjectURL() {
        return "blob:tide-report"
      }

      static revokeObjectURL() {}
    },
  )
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function click(this: HTMLAnchorElement) {
      downloadedNames.push(this.download)
    },
  )

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: false,
          mode: null,
          login_path: null,
          session_path: null,
          logout_path: null,
        })
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        const stored = invoice(1)
        return jsonResponse({
          records: [
            {
              id: 1,
              number: stored.number,
              customer: stored.customer,
              status: stored.status,
              total: stored.total,
            },
          ],
          next_cursor: null,
        })
      }
      if (
        url.endsWith("/_tide/reports/sales.summary") &&
        init?.method === "POST"
      ) {
        reportRequests.push({ method: "POST", url })
        return jsonResponse(summaryReport())
      }
      if (
        url.endsWith("/_tide/reports/sales.summary/exports/csv") &&
        init?.method === "POST"
      ) {
        reportRequests.push({ method: "POST", url })
        return downloadResponse(
          "Customer,Currency,Invoices,Sales total\r\n",
          "posted-sales-summary.csv",
          "text/csv",
        )
      }
      if (
        url.endsWith("/_tide/reports/sales.invoice/records/1") &&
        init?.method === "GET"
      ) {
        reportRequests.push({ method: "GET", url })
        return jsonResponse(invoiceReport())
      }
      if (
        url.endsWith(
          "/_tide/reports/sales.invoice/records/1/exports/pdf",
        ) &&
        init?.method === "GET"
      ) {
        reportRequests.push({ method: "GET", url })
        return downloadResponse(
          "%PDF-test",
          "invoice-INV-2026-0001.pdf",
          "application/pdf",
        )
      }
      if (url.endsWith("/invoices/1") && init?.method === "GET") {
        return jsonResponse(invoice(1), {
          headers: { ETag: '"4"' },
        })
      }
      const productMatch = /\/products\/(\d+)$/.exec(url)
      if (productMatch && init?.method === "GET") {
        return jsonResponse(
          product(
            Number(productMatch[1]),
            "DEMO",
            "Demo product",
            "100.00",
          ),
        )
      }
      const customerMatch = /\/customers\/(\d+)$/.exec(url)
      if (customerMatch && init?.method === "GET") {
        return jsonResponse(
          customer(
            Number(customerMatch[1]),
            "ADRIA",
            "Adria Consulting",
            "hello@adria.test",
          ),
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.click(
    await screen.findByRole("button", {
      name: "Posted Sales Summary",
    }),
  )
  const summaryDialog = await screen.findByRole("dialog", {
    name: "Posted Sales Summary",
  })
  expect(
    await within(summaryDialog).findByText("4,610.00"),
  ).toBeInTheDocument()
  await user.click(
    within(summaryDialog).getByRole("button", {
      name: "Download CSV",
    }),
  )
  await waitFor(() =>
    expect(downloadedNames).toContain("posted-sales-summary.csv"),
  )
  await user.click(
    within(summaryDialog).getByRole("button", {
      name: "Close report preview",
    }),
  )

  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0001/ }),
  )
  // The button renders disabled while the record is still loading, and a
  // click on a disabled button is a silent no-op -- so waiting for it to
  // exist is not enough to know the click will do anything.
  const preview = await screen.findByRole("button", {
    name: "Preview",
  })
  await waitFor(() => expect(preview).toBeEnabled())
  await user.click(preview)
  const invoiceDialog = await screen.findByRole("dialog", {
    name: "Invoice",
  })
  expect(within(invoiceDialog).getByText("Demo line")).toBeInTheDocument()
  await user.click(
    within(invoiceDialog).getByRole("button", {
      name: "Download PDF",
    }),
  )
  await waitFor(() =>
    expect(downloadedNames).toContain("invoice-INV-2026-0001.pdf"),
  )
  expect(reportRequests.map(({ method, url }) => ({
    method,
    path: new NativeURL(url, "http://tide.test").pathname,
  }))).toEqual([
    {
      method: "POST",
      path: "/api/v1/_tide/reports/sales.summary",
    },
    {
      method: "POST",
      path: "/api/v1/_tide/reports/sales.summary/exports/csv",
    },
    {
      method: "GET",
      path: "/api/v1/_tide/reports/sales.invoice/records/1",
    },
    {
      method: "GET",
      path:
        "/api/v1/_tide/reports/sales.invoice/records/1/exports/pdf",
    },
  ])
})

it("sends collected summary parameters with builds and exports", async () => {
  // The manifest names what to ask for; what the person types must reach the
  // wire as the POST body -- for the preview and for the download alike.
  const bodies: string[] = []
  const downloadedNames: string[] = []
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL() {
        return "blob:tide-report"
      }

      static revokeObjectURL() {}
    },
  )
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function click(this: HTMLAnchorElement) {
      downloadedNames.push(this.download)
    },
  )

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: false,
          mode: null,
          login_path: null,
          session_path: null,
          logout_path: null,
        })
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse({
          ...presentation,
          reports: {
            "sales.summary": {
              ...presentation.reports["sales.summary"],
              parameters: [
                {
                  name: "from_date",
                  label: "From Date",
                  type: "date",
                  required: false,
                },
                {
                  name: "to_date",
                  label: "To Date",
                  type: "date",
                  required: false,
                },
              ],
            },
          },
        })
      }
      if (url.endsWith("/invoices/_query")) {
        const stored = invoice(1)
        return jsonResponse({
          records: [
            {
              id: 1,
              number: stored.number,
              customer: stored.customer,
              status: stored.status,
              total: stored.total,
            },
          ],
          next_cursor: null,
        })
      }
      if (
        url.endsWith("/_tide/reports/sales.summary") &&
        init?.method === "POST"
      ) {
        bodies.push(String(init.body))
        return jsonResponse(summaryReport())
      }
      if (
        url.endsWith("/_tide/reports/sales.summary/exports/csv") &&
        init?.method === "POST"
      ) {
        bodies.push(String(init.body))
        return downloadResponse(
          "Customer,Currency,Invoices,Sales total\r\n",
          "posted-sales-summary.csv",
          "text/csv",
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.click(
    await screen.findByRole("button", {
      name: "Posted Sales Summary",
    }),
  )
  const dialog = await screen.findByRole("dialog", {
    name: "Posted Sales Summary",
  })
  // All-optional parameters: the preview builds `{}` before anyone types.
  expect(await within(dialog).findByText("4,610.00")).toBeInTheDocument()

  fireEvent.change(within(dialog).getByLabelText(/From Date/), {
    target: { value: "2026-08-01" },
  })
  await user.click(
    within(dialog).getByRole("button", { name: "Build report" }),
  )
  await waitFor(() => expect(bodies).toHaveLength(2))

  await user.click(
    within(dialog).getByRole("button", { name: "Download CSV" }),
  )
  await waitFor(() =>
    expect(downloadedNames).toContain("posted-sales-summary.csv"),
  )

  expect(bodies.map((body) => JSON.parse(body))).toEqual([
    {},
    { from_date: "2026-08-01" },
    { from_date: "2026-08-01" },
  ])
})

it("keeps a chosen lookup row selectable while the search catches up", async () => {
  // The search term is part of the lookup query key, so the rows reload when
  // the debounce fires. A row chosen from the list as it stood -- the only
  // list the person could see -- has to survive that. Otherwise `selected` is
  // looked up in rows that no longer exist and the Select button goes dead
  // under the pointer, for a reason nothing on screen explains.
  //
  // The reload is held open rather than raced. Hoping to land inside that
  // window is what made the neighbouring test fail about one run in twenty.
  let releaseSearch = () => {}
  let searchStarted = false
  const searchHeld = new Promise<void>((resolve) => {
    releaseSearch = resolve
  })
  const storedInvoice = invoice(1)

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: false,
          mode: null,
          login_path: null,
          session_path: null,
          logout_path: null,
        })
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({
          records: [
            {
              id: 1,
              number: storedInvoice.number,
              customer: storedInvoice.customer,
              status: storedInvoice.status,
              total: storedInvoice.total,
            },
          ],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/1") && init?.method === "GET") {
        return jsonResponse(storedInvoice, { headers: { ETag: '"4"' } })
      }
      if (url.endsWith("/customers/_query")) {
        const body = JSON.parse(String(init?.body)) as {
          filters: Array<{ field: string; value: string }>
        }
        if (body.filters.length > 0) {
          searchStarted = true
          await searchHeld
          return jsonResponse({
            records: [customer(2, "NORTH", "Northwind Trade", "sales@north.test")],
            next_cursor: null,
          })
        }
        return jsonResponse({
          records: [
            customer(1, "ADRIA", "Adria Consulting", "hello@adria.test"),
            customer(2, "NORTH", "Northwind Trade", "sales@north.test"),
          ],
          next_cursor: null,
        })
      }
      const customerMatch = /\/customers\/(\d+)$/.exec(url)
      if (customerMatch && init?.method === "GET") {
        return jsonResponse(
          customerMatch[1] === "2"
            ? customer(2, "NORTH", "Northwind Trade", "sales@north.test")
            : customer(1, "ADRIA", "Adria Consulting", "hello@adria.test"),
        )
      }
      const productMatch = /\/products\/(\d+)$/.exec(url)
      if (productMatch && init?.method === "GET") {
        return jsonResponse(
          product(Number(productMatch[1]), "DEMO", "Demo product", "100.00"),
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0001/ }),
  )
  await user.click(screen.getByRole("button", { name: "Select Customer" }))
  const dialog = await screen.findByRole("dialog", { name: "Select Customer" })

  await within(dialog).findByRole("row", { name: /NORTH.*Northwind Trade/ })
  await user.type(
    within(dialog).getByLabelText("Search lookup records"),
    "north",
  )
  // The debounce has fired and the narrowed search is in flight, held open.
  // This is the window, entered deliberately rather than raced for.
  await waitFor(() => expect(searchStarted).toBe(true))

  // The list the person is looking at must still be there. Without carrying
  // the previous rows the table empties here, and a row chosen in this window
  // -- the only rows they can see -- stops resolving, which disables Select
  // under the pointer.
  const chosen = within(dialog).getByRole("row", {
    name: /NORTH.*Northwind Trade/,
  })
  await user.click(chosen)
  expect(within(dialog).getByRole("button", { name: "Select" })).toBeEnabled()

  releaseSearch()
  expect(
    await within(dialog).findByRole("row", {
      name: /NORTH.*Northwind Trade/,
    }),
  ).toBeInTheDocument()
})

function renderApp() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <App />
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

/**
 * The JSON type the server's selection decoder takes each field in.
 *
 * `tide.api.wire.decode_wire_value` accepts an integer only as a number and a
 * decimal only as a string, and refuses the whole request otherwise. A stub
 * that echoes back whatever it is handed cannot see a renderer serialising a
 * text input straight onto the wire -- which is how a Product selection came
 * to fail against a real server while passing here.
 */
const SELECTION_WIRE_TYPES: Record<
  string,
  Record<string, "string" | "number" | "boolean">
> = {
  "sales.Invoice": {
    invoice_date: "string",
    currency: "string",
    customer: "number",
  },
  "sales.InvoiceLine": {
    line_number: "number",
    description: "string",
    quantity: "string",
    unit_price: "string",
    product: "number",
  },
}

function selectionWireRefusal(
  entity: string,
  values: Record<string, unknown>,
): Response | null {
  const expected = SELECTION_WIRE_TYPES[entity]
  if (expected === undefined) {
    return badRequest(`no wire types are declared for ${entity}`)
  }
  for (const [name, value] of Object.entries(values)) {
    const wanted = expected[name]
    if (wanted === undefined) {
      return badRequest(`unknown draft field ${entity}.${name}`)
    }
    if (typeof value !== wanted) {
      return badRequest(
        `${entity}.${name} arrived as ${typeof value}, not ${wanted}`,
      )
    }
  }
  return null
}

function badRequest(detail: string): Response {
  return jsonResponse(
    {
      code: "invalid_request",
      message: `reference-selection payload is invalid: ${detail}`,
    },
    { status: 400 },
  )
}

function jsonResponse(
  value: unknown,
  init: ResponseInit = {},
): Response {
  return new Response(JSON.stringify(value), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...Object.fromEntries(new Headers(init.headers)),
    },
  })
}

function downloadResponse(
  value: string,
  filename: string,
  contentType: string,
): Response {
  return new Response(value, {
    headers: {
      "Content-Type": contentType,
      "Content-Disposition": (
        `attachment; filename="${filename}"; ` +
        `filename*=UTF-8''${encodeURIComponent(filename)}`
      ),
    },
  })
}

function customer(id: number, code: string, name: string, email: string) {
  return {
    id,
    code,
    name,
    email,
    active: true,
    _tide: {
      writable_fields: ["code", "name", "email", "active"],
    },
  }
}

function product(
  id: number,
  code: string,
  name: string,
  unitPrice: string,
) {
  return {
    id,
    code,
    name,
    unit_price: unitPrice,
    active: true,
    _tide: {
      writable_fields: ["code", "name", "unit_price", "active"],
    },
  }
}

function invoice(id: number) {
  return {
    id,
    number: "INV-2026-0001",
    invoice_date: "2026-07-29",
    status: "draft",
    currency: "EUR",
    customer: 1,
    total: "100.00",
    version: 4,
    lines: [
      {
        id: 10,
        line_number: 1,
        product: 1,
        description: "Demo line",
        quantity: "1.000",
        unit_price: "100.00",
        total: "100.00",
      },
    ],
    _tide: {
      writable_fields: ["invoice_date", "currency", "customer", "lines"],
      actions: {
        post: { visible: true, enabled: true },
      },
    },
  }
}

function invoiceReport() {
  return {
    wire_version: "0.1",
    report: "sales.invoice",
    title: "Invoice",
    application: "TIDE Invoicing",
    generated_at: "2026-07-30T12:00:00Z",
    header_text: ["Invoice"],
    record_values: [
      { label: "Invoice number", text: "INV-2026-0001", alignment: "left" },
    ],
    detail: {
      columns: [
        { name: "description", label: "Description", alignment: "left" },
        { name: "total", label: "Total", alignment: "right" },
      ],
      rows: [
        [
          { text: "Demo line", alignment: "left" },
          { text: "100.00", alignment: "right" },
        ],
      ],
    },
    footer_values: [
      { label: "Total", text: "100.00", alignment: "right" },
    ],
    page_footer_template: "Page {page_number} of {page_count}",
    suggested_filename: "invoice-INV-2026-0001",
  }
}

function summaryReport() {
  return {
    wire_version: "0.1",
    report: "sales.summary",
    title: "Posted Sales Summary",
    application: "TIDE Invoicing",
    generated_at: "2026-07-30T12:00:00Z",
    header_text: [],
    record_values: [],
    detail: {
      columns: [
        { name: "customer", label: "Customer", alignment: "left" },
        { name: "currency", label: "Currency", alignment: "left" },
        { name: "invoice_count", label: "Invoices", alignment: "right" },
        { name: "sales_total", label: "Sales total", alignment: "right" },
      ],
      rows: [
        [
          { text: "ADRIA - Adria Consulting", alignment: "left" },
          { text: "EUR", alignment: "left" },
          { text: "3", alignment: "right" },
          { text: "4,610.00", alignment: "right" },
        ],
      ],
    },
    footer_values: [
      { label: "Source records", text: "3", alignment: "right" },
    ],
    page_footer_template: "Page {page_number} of {page_count}",
    suggested_filename: "sales-summary-2026-07-30",
  }
}

const plainColumn = {
  name: "number",
  label: "Number",
  field_type: "string",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
}

const customerReference = {
  entity: "crm.Customer",
  resource_path: "/api/v1/customers",
  identity_field: "id",
  display_template: "{code} - {name}",
}

const productReference = {
  entity: "catalog.Product",
  resource_path: "/api/v1/products",
  identity_field: "id",
  display_template: "{code} - {name}",
}

const lookup = {
  view: "crm.Customer.lookup",
  title: "Select Customer",
  owner_entity: "sales.Invoice",
  field: "customer",
  target_entity: "crm.Customer",
  resource_path: "/api/v1/customers",
  query_path: "/api/v1/customers/_query",
  selection_path: "/api/v1/_tide/reference-selection",
  identity_field: "id",
  columns: [
    { ...plainColumn, name: "code", label: "Code" },
    { ...plainColumn, name: "name", label: "Name" },
    { ...plainColumn, name: "email", label: "Email" },
  ],
  search_fields: ["code", "name", "email"],
  page_size: 20,
  operations: ["list", "get", "create", "update"],
  create_view: "crm.Customer.edit",
}

const productLookup = {
  view: "catalog.Product.lookup",
  title: "Select Product",
  owner_entity: "sales.InvoiceLine",
  field: "product",
  target_entity: "catalog.Product",
  resource_path: "/api/v1/products",
  query_path: "/api/v1/products/_query",
  selection_path: "/api/v1/_tide/reference-selection",
  identity_field: "id",
  columns: [
    { ...plainColumn, name: "code", label: "Code" },
    { ...plainColumn, name: "name", label: "Name" },
    {
      ...plainColumn,
      name: "unit_price",
      label: "Unit Price",
      field_type: "decimal",
      alignment: "right",
    },
  ],
  search_fields: ["code", "name"],
  page_size: 20,
  operations: ["list", "get", "create", "update"],
  create_view: "catalog.Product.edit",
}

const baseField = {
  ...plainColumn,
  writable: false,
  lookup: null,
  required: false,
  help: null,
  max_length: null,
  choices: [],
  regex: null,
  numeric_mask: null,
  precision: null,
  scale: null,
  minimum: null,
  maximum: null,
  has_default: false,
  default_value: null,
}

const invoiceView = {
  view: "sales.Invoice.browse",
  entity: "sales.Invoice",
  label: "Invoices",
  resource_path: "/api/v1/invoices",
  query_path: "/api/v1/invoices/_query",
  identity_field: "id",
  columns: [
    plainColumn,
    {
      ...plainColumn,
      name: "customer",
      label: "Customer",
      field_type: "reference",
      target_entity: "crm.Customer",
      reference: customerReference,
    },
    { ...plainColumn, name: "status", label: "Status" },
    {
      ...plainColumn,
      name: "total",
      label: "Total",
      field_type: "decimal",
      alignment: "right",
    },
  ],
  search_field: "number",
  search_label: "Number",
  named_filters: [],
  sortable_fields: ["number", "status", "total"],
  page_size: 25,
  operations: ["list", "get", "create", "update"],
  detail_view: "sales.Invoice.edit",
}

const invoiceForm = {
  view: "sales.Invoice.edit",
  entity: "sales.Invoice",
  label: "Invoice",
  display_template: "number",
  fields: {
    number: baseField,
    invoice_date: {
      ...baseField,
      name: "invoice_date",
      label: "Invoice Date",
      field_type: "date",
      writable: true,
      required: true,
    },
    status: {
      ...baseField,
      name: "status",
      label: "Status",
      field_type: "choice",
      choices: ["draft", "posted"],
    },
    currency: {
      ...baseField,
      name: "currency",
      label: "Currency",
      writable: true,
      required: true,
    },
    customer: {
      ...baseField,
      name: "customer",
      label: "Customer",
      field_type: "reference",
      target_entity: "crm.Customer",
      reference: customerReference,
      writable: true,
      lookup,
      required: true,
    },
    total: {
      ...baseField,
      name: "total",
      label: "Total",
      field_type: "decimal",
      alignment: "right",
    },
  },
  sections: [
    {
      kind: "group",
      label: "Invoice",
      rows: [
        ["number", "invoice_date"],
        ["status", "currency"],
        ["customer", "total"],
      ],
      tab: null,
    },
    {
      kind: "collection",
      name: "lines",
      label: "Lines",
      record_label: "Line",
      entity: "sales.InvoiceLine",
      view: "sales.InvoiceLine.inline_edit",
      identity_field: "id",
      sequence_field: "line_number",
      columns: [
        {
          ...plainColumn,
          name: "line_number",
          label: "Line Number",
          field_type: "integer",
          alignment: "right",
        },
        {
          ...plainColumn,
          name: "product",
          label: "Product",
          field_type: "reference",
          target_entity: "catalog.Product",
          reference: productReference,
        },
        {
          ...plainColumn,
          name: "description",
          label: "Description",
        },
        {
          ...plainColumn,
          name: "quantity",
          label: "Quantity",
          field_type: "decimal",
          alignment: "right",
        },
        {
          ...plainColumn,
          name: "unit_price",
          label: "Unit Price",
          field_type: "decimal",
          alignment: "right",
        },
        {
          ...plainColumn,
          name: "total",
          label: "Total",
          field_type: "decimal",
          alignment: "right",
        },
      ],
      fields: {
        line_number: {
          ...baseField,
          name: "line_number",
          label: "Line Number",
          field_type: "integer",
          alignment: "right",
          writable: true,
          required: true,
          numeric_mask: "0",
        },
        unit_price: {
          ...baseField,
          name: "unit_price",
          label: "Unit Price",
          field_type: "decimal",
          alignment: "right",
          writable: true,
          required: true,
          numeric_mask: "0.00",
          precision: 12,
          scale: 2,
        },
        product: {
          ...baseField,
          name: "product",
          label: "Product",
          field_type: "reference",
          target_entity: "catalog.Product",
          reference: productReference,
          writable: true,
          lookup: productLookup,
          required: true,
        },
        quantity: {
          ...baseField,
          name: "quantity",
          label: "Quantity",
          field_type: "decimal",
          alignment: "right",
          writable: true,
          required: true,
          numeric_mask: "0.000",
          precision: 12,
          scale: 3,
          minimum: "0.001",
        },
        description: {
          ...baseField,
          name: "description",
          label: "Description",
          writable: true,
          required: true,
          max_length: 200,
        },
      },
      groups: [
        {
          kind: "group",
          label: "Line details",
          rows: [
            ["line_number", "unit_price"],
            ["product", "quantity"],
            ["description"],
          ],
          tab: null,
        },
      ],
      actions: ["add", "apply", "remove"],
      draft_operations: ["create", "update"],
      writable: true,
      tab: null,
    },
  ],
  actions: [
    {
      name: "post",
      label: "Post invoice",
      idempotent: true,
    },
  ],
}

const customerForm = {
  view: "crm.Customer.edit",
  entity: "crm.Customer",
  label: "Customer",
  display_template: "{code} - {name}",
  fields: {
    code: {
      ...baseField,
      name: "code",
      label: "Code",
      writable: true,
      required: true,
      regex: "[A-Z][A-Z0-9-]{0,19}",
    },
    email: {
      ...baseField,
      name: "email",
      label: "Email",
      writable: true,
    },
    name: {
      ...baseField,
      name: "name",
      label: "Name",
      writable: true,
      required: true,
    },
    active: {
      ...baseField,
      name: "active",
      label: "Active",
      field_type: "boolean",
      writable: true,
      has_default: true,
      default_value: true,
    },
  },
  sections: [
    {
      kind: "group",
      label: "Customer",
      rows: [
        ["code", "email"],
        ["name", "active"],
      ],
      tab: null,
    },
  ],
}

const productForm = {
  view: "catalog.Product.edit",
  entity: "catalog.Product",
  label: "Product",
  display_template: "{code} - {name}",
  fields: {
    code: {
      ...baseField,
      name: "code",
      label: "Code",
      writable: true,
      required: true,
    },
    unit_price: {
      ...baseField,
      name: "unit_price",
      label: "Unit Price",
      field_type: "decimal",
      alignment: "right",
      writable: true,
      required: true,
      numeric_mask: "0.00",
      precision: 12,
      scale: 2,
    },
    name: {
      ...baseField,
      name: "name",
      label: "Name",
      writable: true,
      required: true,
    },
    active: {
      ...baseField,
      name: "active",
      label: "Active",
      field_type: "boolean",
      writable: true,
      has_default: true,
      default_value: true,
    },
  },
  sections: [
    {
      kind: "group",
      label: "Product",
      rows: [
        ["code", "unit_price"],
        ["name", "active"],
      ],
      tab: null,
    },
  ],
}

const session = {
  wire_version: "0.1",
  application: "TIDE Invoicing",
  application_version: "0.1.0",
  schema_version: "0.1",
  authentication: "development-bearer",
  principal: "development:api",
  roles: ["sales_clerk"],
  reports: ["sales.invoice", "sales.summary"],
  entities: {},
}

const presentation = {
  wire_version: "0.1",
  application: "TIDE Invoicing",
  application_version: "0.1.0",
  schema_version: "0.1",
  principal: "development:api",
  navigation: [
    {
      label: "Sales",
      items: [
        {
          view: "sales.Invoice.browse",
          entity: "sales.Invoice",
          label: "Invoices",
        },
      ],
    },
  ],
  views: {
    "sales.Invoice.browse": invoiceView,
  },
  forms: {
    "sales.Invoice.edit": invoiceForm,
    "crm.Customer.edit": customerForm,
    "catalog.Product.edit": productForm,
  },
  reports: {
    "sales.invoice": {
      name: "sales.invoice",
      title: "Invoice",
      kind: "record",
      entity: "sales.Invoice",
      resource_path: "/api/v1/_tide/reports/sales.invoice",
      export_formats: ["csv", "html", "pdf"],
    },
    "sales.summary": {
      name: "sales.summary",
      title: "Posted Sales Summary",
      kind: "summary",
      entity: "sales.Invoice",
      resource_path: "/api/v1/_tide/reports/sales.summary",
      export_formats: ["csv", "html", "pdf"],
    },
  },
}
