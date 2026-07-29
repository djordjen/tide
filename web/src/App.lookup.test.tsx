import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"

afterEach(() => {
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
        return jsonResponse({
          values: { ...body.values, customer: body.identity },
        })
      }
      const customerMatch = /\/customers\/(\d+)$/.exec(url)
      if (customerMatch && init?.method === "GET") {
        return jsonResponse(customers.get(Number(customerMatch[1])))
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

function invoice(id: number) {
  return {
    id,
    number: "INV-2026-0001",
    invoice_date: "2026-07-29",
    status: "draft",
    currency: "EUR",
    customer: 1,
    total: "100.00",
    lines: [
      {
        id: 10,
        description: "Demo line",
        total: "100.00",
      },
    ],
    _tide: {
      writable_fields: ["invoice_date", "currency", "customer", "lines"],
    },
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
  validations: [],
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
      entity: "sales.InvoiceLine",
      columns: [
        {
          ...plainColumn,
          name: "description",
          label: "Description",
        },
        {
          ...plainColumn,
          name: "total",
          label: "Total",
          field_type: "decimal",
          alignment: "right",
        },
      ],
      tab: null,
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
      validations: ["email"],
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

const session = {
  wire_version: "0.1",
  application: "TIDE Invoicing",
  application_version: "0.1.0",
  schema_version: "0.1",
  authentication: "development-bearer",
  principal: "development:api",
  roles: ["sales_clerk"],
  reports: [],
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
  },
}
