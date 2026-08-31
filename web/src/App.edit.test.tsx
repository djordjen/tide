import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
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
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("creates and updates a flat metadata form through the secured API", async () => {
  const requests: Array<{
    method: string
    body: Record<string, unknown>
    ifMatch: string | null
  }> = []
  let stored = product(1, "P00001", "Support", "10.00")

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
      if (url.endsWith("/products/_query")) {
        return jsonResponse({ records: [stored], next_cursor: null })
      }
      if (url.endsWith("/products/1") && init?.method === "GET") {
        return jsonResponse(stored, {
          headers: { ETag: '"7"' },
        })
      }
      if (url.endsWith("/products") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        requests.push({ method: "POST", body, ifMatch: null })
        return jsonResponse(
          product(
            2,
            String(body.code),
            String(body.name),
            String(body.unit_price),
          ),
          { status: 201 },
        )
      }
      if (url.endsWith("/products/1") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        requests.push({
          method: "PATCH",
          body,
          ifMatch: new Headers(init.headers).get("If-Match"),
        })
        if (body.code === "DUP") {
          return jsonResponse(
            {
              code: "validation_failed",
              message: "code must be unique",
              issues: [
                {
                  rule: "unique",
                  message: "code must be unique",
                  fields: ["code"],
                  severity: "error",
                },
              ],
            },
            { status: 422 },
          )
        }
        stored = { ...stored, ...body }
        return jsonResponse(stored, {
          headers: { ETag: '"8"' },
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.click(await screen.findByRole("button", { name: "New" }))
  expect(
    await screen.findByRole("heading", { name: "New Product" }),
  ).toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: "Active" })).toBeChecked()

  await user.click(screen.getByRole("button", { name: "Save" }))
  expect(await screen.findByText("Code is required.")).toBeInTheDocument()
  expect(screen.getByText("Unit Price is required.")).toBeInTheDocument()
  expect(screen.getByText("Name is required.")).toBeInTheDocument()

  const newCode = screen.getByLabelText(/^Code/)
  const newPrice = screen.getByLabelText(/^Unit Price/)
  await user.type(newCode, "P00002")
  await user.keyboard("{Enter}")
  expect(newPrice).toHaveFocus()
  await user.type(newPrice, "19.999")
  await user.type(screen.getByLabelText(/^Name/), "Web support")
  expect(newPrice).toHaveValue("19.99")
  await user.click(screen.getByRole("button", { name: "Save" }))

  expect(
    await screen.findByText("Product created successfully."),
  ).toBeInTheDocument()
  expect(requests[0]).toEqual({
    method: "POST",
    body: {
      code: "P00002",
      unit_price: "19.99",
      name: "Web support",
      active: true,
    },
    ifMatch: null,
  })

  const row = await screen.findByRole("row", { name: /P00001/ })
  await user.dblClick(row)
  expect(
    await screen.findByRole("heading", {
      name: "Product — P00001 - Support",
    }),
  ).toBeInTheDocument()

  const code = screen.getByLabelText(/^Code/)
  await user.clear(code)
  await user.type(code, "DUP")
  const name = screen.getByLabelText(/^Name/)
  await user.clear(name)
  await user.type(name, "Updated support")
  await user.click(screen.getByRole("button", { name: "Save" }))
  expect(await screen.findByText("Code must be unique")).toBeInTheDocument()
  expect(requests[1].ifMatch).toBe('"7"')

  await user.clear(code)
  await user.type(code, "P00001")
  await user.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() =>
    expect(requests.at(-1)).toEqual({
      method: "PATCH",
      body: { name: "Updated support" },
      ifMatch: '"7"',
    }),
  )
  expect(
    await screen.findByRole("heading", {
      name: "Product — P00001 - Updated support",
    }),
  ).toBeInTheDocument()
})

it("reviews a stale draft and rebases explicit choices onto the fresh ETag", async () => {
  const requests: Array<{
    body: Record<string, unknown>
    ifMatch: string | null
  }> = []
  let stale = true
  let stored = product(1, "P00001", "Support", "10.00")
  let etag = '"7"'

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
      if (url.endsWith("/products/_query")) {
        return jsonResponse({ records: [stored], next_cursor: null })
      }
      if (url.endsWith("/products/1") && init?.method === "GET") {
        return jsonResponse(stored, { headers: { ETag: etag } })
      }
      if (url.endsWith("/products/1") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        requests.push({
          body,
          ifMatch: new Headers(init.headers).get("If-Match"),
        })
        if (stale) {
          stale = false
          stored = {
            ...stored,
            name: "Server support",
            active: false,
            _tide: {
              writable_fields: ["code", "name", "active"],
            },
          }
          etag = '"8"'
          return jsonResponse(
            {
              code: "stale_version",
              message: "expected version 7, found 8",
            },
            { status: 412 },
          )
        }
        stored = { ...stored, ...body }
        etag = '"9"'
        return jsonResponse(stored, { headers: { ETag: etag } })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(await screen.findByRole("row", { name: /P00001/ }))

  const name = await screen.findByLabelText(/^Name/)
  const price = screen.getByLabelText(/^Unit Price/)
  await user.clear(name)
  await user.type(name, "My support")
  await user.clear(price)
  await user.type(price, "11.00")
  await user.click(screen.getByRole("button", { name: "Save" }))

  const review = await screen.findByRole("dialog", {
    name: "Record changed elsewhere",
  })
  expect(within(review).getByText("Server support")).toBeInTheDocument()
  expect(within(review).getByText("My support")).toBeInTheDocument()
  expect(
    within(review).getByLabelText("Unit Price is now locked"),
  ).toBeInTheDocument()
  const apply = within(review).getByRole("button", {
    name: "Apply resolution",
  })
  expect(apply).toBeDisabled()
  await user.click(
    within(review).getByRole("button", { name: "Use my Name" }),
  )
  expect(apply).toBeEnabled()
  await user.click(apply)

  expect(
    await screen.findByText("Draft rebased onto current values"),
  ).toBeInTheDocument()
  expect(screen.getByLabelText(/^Name/)).toHaveValue("My support")
  expect(screen.queryByLabelText(/^Unit Price/)).not.toBeInTheDocument()
  expect(screen.getAllByText("Unit Price").length).toBeGreaterThan(0)
  expect(screen.getAllByText("10.00").length).toBeGreaterThan(0)
  expect(screen.getByRole("checkbox", { name: "Active" })).not.toBeChecked()

  await user.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() =>
    expect(requests).toEqual([
      {
        body: { unit_price: "11.00", name: "My support" },
        ifMatch: '"7"',
      },
      {
        body: { name: "My support" },
        ifMatch: '"8"',
      },
    ]),
  )
  expect(
    await screen.findByRole("heading", {
      name: "Product — P00001 - My support",
    }),
  ).toBeInTheDocument()
})

it("will not discard an unsaved edit to a keystroke", async () => {
  // Escape sits one key away from every field in the form, and closing the
  // editor throws the edit away with nothing to undo it. Previous and Next
  // already refuse while there are unsaved changes; this is the same rule.
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
      if (url.endsWith("/products/_query")) {
        return jsonResponse({
          records: [product(1, "P00001", "Support", "10.00")],
          next_cursor: null,
        })
      }
      if (url.endsWith("/products/1") && init?.method === "GET") {
        return jsonResponse(product(1, "P00001", "Support", "10.00"), {
          headers: { ETag: '"7"' },
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(await screen.findByRole("row", { name: /P00001/ }))
  const heading = await screen.findByRole("heading", {
    name: "Product — P00001 - Support",
  })
  expect(heading).toBeInTheDocument()

  const name = screen.getByLabelText(/^Name/)
  await user.clear(name)
  await user.type(name, "Edited support")

  await user.keyboard("{Escape}")
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "Product — P00001 - Support" }),
    ).toBeInTheDocument(),
  )
  expect(leaveIsGuarded()).toBe(true)

  // Put the value back: there is nothing to lose now, so neither guard should
  // still be in the way. Asserted as a transition within this test rather than
  // as a fact about the page, which would depend on every test before it.
  await user.clear(name)
  await user.type(name, "Support")
  await waitFor(() => expect(leaveIsGuarded()).toBe(false))

  await user.keyboard("{Escape}")
  await waitFor(() =>
    expect(
      screen.queryByRole("heading", { name: "Product — P00001 - Support" }),
    ).not.toBeInTheDocument(),
  )
})

it("hands back a fresh form after Save and New, and leaves after Save", async () => {
  // Data entry arrives in runs -- a stack of paper forms, one after the
  // other. `Save and New` is the run: the record is written, the form comes
  // back empty with its defaults, and the cursor is already in the first
  // field. Plain `Save` still ends the run at the grid.
  const created: Array<Record<string, unknown>> = []
  let rows = [product(1, "P00001", "Support", "10.00")]

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
      if (url.endsWith("/products/_query")) {
        return jsonResponse({ records: rows, next_cursor: null })
      }
      if (url.endsWith("/products") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        created.push(body)
        const saved = product(
          created.length + 1,
          String(body.code),
          String(body.name),
          String(body.unit_price),
        )
        rows = [...rows, saved]
        return jsonResponse(saved, { status: 201 })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.click(await screen.findByRole("button", { name: "New" }))
  await user.type(await screen.findByLabelText(/^Code/), "P00002")
  await user.type(screen.getByLabelText(/^Unit Price/), "19.99")
  await user.type(screen.getByLabelText(/^Name/), "Web support")
  await user.click(screen.getByRole("button", { name: "Save and New" }))

  expect(
    await screen.findByText("Product created. Continue with the next one."),
  ).toBeInTheDocument()
  expect(
    screen.getByRole("heading", { name: "New Product" }),
  ).toBeInTheDocument()
  const code = screen.getByLabelText(/^Code/)
  expect(code).toHaveValue("")
  expect(screen.getByLabelText(/^Name/)).toHaveValue("")
  // A fresh draft, defaults and all -- not a cleared copy of what was saved.
  expect(screen.getByRole("checkbox", { name: "Active" })).toBeChecked()
  await waitFor(() => expect(code).toHaveFocus())

  await user.type(code, "P00003")
  await user.type(screen.getByLabelText(/^Unit Price/), "5.00")
  await user.type(screen.getByLabelText(/^Name/), "Phone support")
  await user.click(screen.getByRole("button", { name: "Save" }))

  expect(
    await screen.findByText("Product created successfully."),
  ).toBeInTheDocument()
  expect(
    screen.queryByRole("heading", { name: "New Product" }),
  ).not.toBeInTheDocument()
  expect(created.map((body) => body.code)).toEqual(["P00002", "P00003"])
})

it("offers Save and New only where there is a next record to start", async () => {
  // An open record is one record. Saving it and starting another is a
  // different journey from the one this screen is on, and the button that
  // means it belongs to the screen that creates.
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
      if (url.endsWith("/products/_query")) {
        return jsonResponse({
          records: [product(1, "P00001", "Support", "10.00")],
          next_cursor: null,
        })
      }
      if (url.endsWith("/products/1") && init?.method === "GET") {
        return jsonResponse(product(1, "P00001", "Support", "10.00"), {
          headers: { ETag: '"7"' },
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(await screen.findByRole("row", { name: /P00001/ }))
  expect(
    await screen.findByRole("heading", {
      name: "Product — P00001 - Support",
    }),
  ).toBeInTheDocument()

  expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument()
  expect(
    screen.queryByRole("button", { name: "Save and New" }),
  ).not.toBeInTheDocument()
})

function leaveIsGuarded(): boolean {
  const leaving = new Event("beforeunload", { cancelable: true })
  window.dispatchEvent(leaving)
  return leaving.defaultPrevented
}

it("weighs a warning-only refusal and saves anyway with the acknowledgement", async () => {
  const patches: string[] = []
  let stored = product(1, "P00001", "Support", "10.00")

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
      if (url.endsWith("/products/_query")) {
        return jsonResponse({ records: [stored], next_cursor: null })
      }
      if (url.endsWith("/products/1") && init?.method === "GET") {
        return jsonResponse(stored, { headers: { ETag: '"7"' } })
      }
      if (url.includes("/products/1") && init?.method === "PATCH") {
        patches.push(url)
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        if (!url.includes("acknowledge_warnings=big_price")) {
          return jsonResponse(
            {
              code: "validation_failed",
              message: "The price is unusually high.",
              issues: [
                {
                  rule: "big_price",
                  message: "The price is unusually high.",
                  fields: ["unit_price"],
                  severity: "warning",
                },
              ],
            },
            { status: 422 },
          )
        }
        stored = { ...stored, ...body }
        return jsonResponse(stored, { headers: { ETag: '"8"' } })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  const row = await screen.findByRole("row", { name: /P00001/ })
  await user.dblClick(row)
  const price = await screen.findByLabelText(/^Unit Price/)
  await user.clear(price)
  await user.type(price, "9999.00")
  await user.click(screen.getByRole("button", { name: "Save" }))

  expect(
    await screen.findByText("The price is unusually high."),
  ).toBeInTheDocument()
  // A warning weighs; it does not fail. The refusal banner stays away.
  expect(
    screen.queryByText("The record could not be saved."),
  ).not.toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "Save anyway" }))
  await waitFor(() => expect(patches).toHaveLength(2))
  expect(patches[1]).toContain("acknowledge_warnings=big_price")
  expect(
    await screen.findByRole("heading", {
      name: "Product — P00001 - Support",
    }),
  ).toBeInTheDocument()
})

it("keeps info notices beside the created notice on save-and-new", async () => {
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
      if (url.endsWith("/products/_query")) {
        return jsonResponse({ records: [], next_cursor: null })
      }
      if (url.endsWith("/products") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        const created = product(
          2,
          String(body.code),
          String(body.name),
          String(body.unit_price),
        )
        return jsonResponse(
          {
            ...created,
            _tide: {
              ...created._tide,
              notices: [
                {
                  rule: "note_helps",
                  message: "A note helps the courier.",
                  fields: [],
                  severity: "info",
                },
              ],
            },
          },
          { status: 201 },
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.click(await screen.findByRole("button", { name: "New" }))
  await screen.findByRole("heading", { name: "New Product" })
  await user.type(screen.getByLabelText(/^Code/), "P00009")
  await user.type(screen.getByLabelText(/^Unit Price/), "5.00")
  await user.type(screen.getByLabelText(/^Name/), "Courier note")
  await user.click(screen.getByRole("button", { name: "Save and New" }))

  expect(
    await screen.findByText("Product created. Continue with the next one."),
  ).toBeInTheDocument()
  expect(
    await screen.findByText("A note helps the courier."),
  ).toBeInTheDocument()
})

function renderApp() {
  // The suite predates Home: land where the old default landed.
  window.history.replaceState(null, "", "/?view=catalog.Product.browse")
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

const session = {
  wire_version: "0.1",
  application: "TIDE Invoicing",
  application_version: "0.1.0",
  schema_version: "0.1",
  authentication: "development-bearer",
  principal: "development:api",
  roles: ["sales_clerk"],
  reports: [],
  entities: {
    "catalog.Product": {
      operations: ["list", "get", "create", "update"],
      draft_operations: [],
      readable_fields: ["id", "code", "name", "unit_price", "active"],
      writable_fields: ["code", "name", "unit_price", "active"],
      actions: [],
      audit: false,
    },
  },
}

const column = {
  name: "code",
  label: "Code",
  field_type: "string",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
}

const productView = {
  view: "catalog.Product.browse",
  entity: "catalog.Product",
  label: "Products",
  resource_path: "/api/v1/products",
  query_path: "/api/v1/products/_query",
  identity_field: "id",
  columns: [
    column,
    { ...column, name: "name", label: "Name" },
    {
      ...column,
      name: "unit_price",
      label: "Unit Price",
      field_type: "decimal",
      alignment: "right",
    },
    {
      ...column,
      name: "active",
      label: "Active",
      field_type: "boolean",
    },
  ],
  search_field: "code",
  search_label: "Code",
  named_filters: [],
  sortable_fields: ["code", "name", "unit_price", "active"],
  page_size: 25,
  operations: ["list", "get", "create", "update"],
  detail_view: "catalog.Product.edit",
}

const baseField = {
  ...column,
  writable: true,
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

const presentation = {
  wire_version: "0.1",
  application: "TIDE Invoicing",
  application_version: "0.1.0",
  schema_version: "0.1",
  principal: "development:api",
  navigation: [
    {
      label: "Master Data",
      items: [
        {
          view: "catalog.Product.browse",
          entity: "catalog.Product",
          label: "Products",
        },
      ],
    },
  ],
  views: {
    "catalog.Product.browse": productView,
  },
  forms: {
    "catalog.Product.edit": {
      view: "catalog.Product.edit",
      entity: "catalog.Product",
      label: "Product",
      display_template: "{code} - {name}",
      fields: {
        code: {
          ...baseField,
          required: true,
          max_length: 30,
          regex: "[A-Z][A-Z0-9-]{0,29}",
        },
        unit_price: {
          ...baseField,
          name: "unit_price",
          label: "Unit Price",
          field_type: "decimal",
          alignment: "right",
          required: true,
          numeric_mask: "0.00",
          precision: 12,
          scale: 2,
          minimum: "0",
        },
        name: {
          ...baseField,
          name: "name",
          label: "Name",
          required: true,
          max_length: 120,
        },
        active: {
          ...baseField,
          name: "active",
          label: "Active",
          field_type: "boolean",
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
    },
  },
}
