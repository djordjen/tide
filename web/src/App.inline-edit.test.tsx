import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeAll, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

/**
 * Editing in the row: what `edit: inline` on a browse view turns into.
 *
 * The row rides the same rails as the form -- a fresh GET decides what is
 * writable, the shared draft/validate/diff helpers shape the save, and the
 * PATCH carries If-Match -- so nothing here is a second write path. jsdom
 * can see the editors, the request bodies and the refusals, which is the
 * whole claim; how a 43px row wears an input is a screenshot's business.
 */

beforeAll(() => {
  // Radix's portalled listbox needs these three in jsdom.
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.setPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("puts the writable cells into editors and saves only what changed", async () => {
  const server = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.dblClick(await screen.findByRole("row", { name: /CONS/ }))

  const name = await screen.findByRole("textbox", { name: "Name" })
  expect(name).toHaveValue("Consulting hour")
  // `code` is not among this record's writable fields, so its cell stays
  // text -- presentation follows the record's own verdict, not the column.
  expect(
    screen.queryByRole("textbox", { name: "Code" }),
  ).not.toBeInTheDocument()
  expect(screen.getByRole("row", { name: /CONS/ })).toHaveTextContent("CONS")

  await user.clear(name)
  await user.type(name, "Consulting day")
  await user.keyboard("{Enter}")

  await waitFor(() => expect(server.patches).toHaveLength(1))
  expect(server.patches[0].body).toEqual({ name: "Consulting day" })
  expect(server.patches[0].headers["if-match"]).toBe('"7"')
  // The editors leave with the save, and the grid shows what came back.
  await screen.findByText("Consulting day")
  expect(
    screen.queryByRole("textbox", { name: "Name" }),
  ).not.toBeInTheDocument()
})

it("escape leaves the row exactly as it was", async () => {
  const server = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.dblClick(await screen.findByRole("row", { name: /CONS/ }))
  const name = await screen.findByRole("textbox", { name: "Name" })
  await user.clear(name)
  await user.type(name, "Scrapped idea")
  await user.keyboard("{Escape}")

  expect(server.patches).toHaveLength(0)
  expect(
    screen.queryByRole("textbox", { name: "Name" }),
  ).not.toBeInTheDocument()
  expect(screen.getByRole("row", { name: /CONS/ })).toHaveTextContent(
    "Consulting hour",
  )
})

it("a refused save keeps the row editing and names the field", async () => {
  const server = stubServer({
    refusal: {
      status: 422,
      body: {
        code: "validation_failed",
        message: "The record did not validate.",
        issues: [
          {
            rule: "minimum",
            message: "unit_price must be at least 0.",
            fields: ["unit_price"],
            severity: "error",
          },
        ],
      },
    },
  })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.dblClick(await screen.findByRole("row", { name: /CONS/ }))
  const price = await screen.findByRole("textbox", { name: "Unit Price" })
  await user.clear(price)
  await user.type(price, "12.34")
  await user.keyboard("{Enter}")

  await waitFor(() => expect(server.patches).toHaveLength(1))
  // Still editing, with the server's words on the field that earned them.
  expect(screen.getByRole("textbox", { name: "Unit Price" })).toHaveAttribute(
    "aria-invalid",
    "true",
  )
  await screen.findByText("Unit Price must be at least 0.")
})

it("a stale row stops editing and says who moved it", async () => {
  const server = stubServer({
    refusal: {
      status: 412,
      body: {
        code: "stale_version",
        message: "The record changed since it was read.",
      },
    },
  })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.dblClick(await screen.findByRole("row", { name: /CONS/ }))
  const name = await screen.findByRole("textbox", { name: "Name" })
  await user.clear(name)
  await user.type(name, "Too late")
  await user.keyboard("{Enter}")

  await waitFor(() => expect(server.patches).toHaveLength(1))
  await waitFor(() =>
    expect(
      screen.queryByRole("textbox", { name: "Name" }),
    ).not.toBeInTheDocument(),
  )
  await screen.findByText(/changed since it was read/)
})

it("leaving a dirty row saves it, the way the reference application does", async () => {
  const server = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.dblClick(await screen.findByRole("row", { name: /CONS/ }))
  const name = await screen.findByRole("textbox", { name: "Name" })
  await user.clear(name)
  await user.type(name, "Consulting day")
  await user.click(screen.getByRole("row", { name: /SUP/ }))

  await waitFor(() => expect(server.patches).toHaveLength(1))
  expect(server.patches[0].body).toEqual({ name: "Consulting day" })
})

it("boolean and captioned cells edit as their own controls", async () => {
  const server = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.dblClick(await screen.findByRole("row", { name: /CONS/ }))
  const active = await screen.findByRole("checkbox", { name: "Active" })
  expect(active).toBeChecked()
  await user.click(active)
  await user.keyboard("{Enter}")

  await waitFor(() => expect(server.patches).toHaveLength(1))
  expect(server.patches[0].body).toEqual({ active: false })
})

it("form mode keeps double-click opening the record", async () => {
  stubServer({ editMode: "form" })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.dblClick(await screen.findByRole("row", { name: /CONS/ }))

  await screen.findByRole("heading", { name: /Consulting hour/ })
  expect(
    screen.queryByRole("textbox", { name: "Name" }),
  ).toBeInTheDocument()
})

interface CapturedPatch {
  body: Record<string, unknown>
  headers: Record<string, string>
}

function stubServer(options?: {
  editMode?: "form" | "inline"
  refusal?: { status: number; body: Record<string, unknown> }
}): { patches: CapturedPatch[] } {
  const editMode = options?.editMode ?? "inline"
  const patches: CapturedPatch[] = []
  const stored = { ...consulting }
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? "GET"
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
        return jsonResponse(presentationFor(editMode))
      }
      if (url.endsWith("/products/_query")) {
        return jsonResponse({
          records: [stored, supplies],
          next_cursor: null,
          summaries: null,
        })
      }
      if (url.endsWith("/products/7") && method === "GET") {
        return jsonResponse(stored, { ETag: '"7"' })
      }
      if (url.endsWith("/products/7") && method === "PATCH") {
        const body = JSON.parse(String(init?.body ?? "{}"))
        const headers = Object.fromEntries(
          Object.entries((init?.headers ?? {}) as Record<string, string>).map(
            ([key, value]) => [key.toLowerCase(), value],
          ),
        )
        patches.push({ body, headers })
        if (options?.refusal) {
          return jsonResponse(options.refusal.body, {}, options.refusal.status)
        }
        Object.assign(stored, body)
        return jsonResponse(stored, { ETag: '"8"' })
      }
      throw new Error(`Unexpected ${method} ${url}`)
    }),
  )
  return { patches }
}

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
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
  headers: Record<string, string> = {},
  status = 200,
): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  })
}

const consulting = {
  id: 7,
  code: "CONS",
  name: "Consulting hour",
  unit_price: "85.00",
  active: true,
  _tide: { writable_fields: ["name", "unit_price", "active"] },
}

const supplies = {
  id: 8,
  code: "SUP",
  name: "Supplies",
  unit_price: "12.00",
  active: true,
  _tide: { writable_fields: ["name", "unit_price", "active"] },
}

const codeColumn = {
  name: "code",
  label: "Code",
  field_type: "string",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
  values: [],
}

const nameColumn = { ...codeColumn, name: "name", label: "Name" }

const priceColumn = {
  ...codeColumn,
  name: "unit_price",
  label: "Unit Price",
  field_type: "decimal",
  alignment: "right",
  format: "money",
  format_options: {
    decimal_places: 2,
    thousands_separator: true,
    display: null,
  },
}

const activeColumn = {
  ...codeColumn,
  name: "active",
  label: "Active",
  field_type: "boolean",
  alignment: "center",
}

const formField = {
  values: [],
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
  lookup: null,
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
      operations: ["list", "get", "update"],
      draft_operations: [],
      readable_fields: ["id", "code", "name", "unit_price", "active"],
      writable_fields: ["name", "unit_price", "active"],
      actions: [],
      audit: false,
    },
  },
}

function presentationFor(editMode: "form" | "inline") {
  return {
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
      "catalog.Product.browse": {
        view: "catalog.Product.browse",
        entity: "catalog.Product",
        label: "Products",
        resource_path: "/api/v1/products",
        query_path: "/api/v1/products/_query",
        identity_field: "id",
        columns: [codeColumn, nameColumn, priceColumn, activeColumn],
        search_field: null,
        search_label: null,
        named_filters: [],
        sortable_fields: [],
        summaries: [],
        edit: editMode,
        page_size: 25,
        operations: ["list", "get", "update"],
        detail_view: "catalog.Product.edit",
      },
    },
    forms: {
      "catalog.Product.edit": {
        view: "catalog.Product.edit",
        entity: "catalog.Product",
        label: "Product",
        display_template: "{name}",
        fields: {
          code: {
            ...codeColumn,
            ...formField,
            max_length: 30,
          },
          name: {
            ...nameColumn,
            ...formField,
            required: true,
            max_length: 120,
          },
          unit_price: {
            ...priceColumn,
            ...formField,
            numeric_mask: "0.00",
            precision: 12,
            scale: 2,
          },
          active: { ...activeColumn, ...formField },
        },
        sections: [
          {
            kind: "group",
            label: "Product",
            rows: [["code", "name"], ["unit_price", "active"]],
            tab: null,
          },
        ],
      },
    },
  }
}
