import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"

afterEach(() => {
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
  await user.type(
    screen.getByLabelText("Application token"),
    "a-development-token-that-is-long-enough",
  )
  await user.click(
    screen.getByRole("button", { name: "Connect securely" }),
  )

  await user.click(await screen.findByRole("button", { name: "New" }))
  expect(
    screen.getByRole("heading", { name: "New Product" }),
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
  validations: [],
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
