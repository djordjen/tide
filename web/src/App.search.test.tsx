import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  // The first journey navigates by pushState; the address bar survives into
  // the next test unless it is walked back to the shell's own front door.
  window.history.replaceState(null, "", "/")
})

it("searches everywhere and a hit opens its record in place", async () => {
  const searchBodies: unknown[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse(noBrowserAuth)
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({ records: [], next_cursor: null })
      }
      if (url.endsWith("/_tide/search")) {
        searchBodies.push(JSON.parse(String(init?.body)))
        return jsonResponse({
          wire_version: "0.1",
          text: "adria",
          groups: [
            {
              entity: "crm.Customer",
              label: "Customers",
              records: [
                { identity: 1, display: "ADRIA - Adria Consulting" },
              ],
              truncated: false,
            },
            {
              // An entity the manifest offers no view for: a hit without a
              // door must not be offered as one.
              entity: "warehouse.Bin",
              label: "Bins",
              records: [{ identity: 9, display: "B-09" }],
              truncated: false,
            },
          ],
        })
      }
      if (url.endsWith("/customers/_query")) {
        return jsonResponse({
          records: [{ id: 1, code: "ADRIA", name: "Adria Consulting" }],
          next_cursor: null,
        })
      }
      if (url.endsWith("/customers/1")) {
        return jsonResponse({
          id: 1,
          code: "ADRIA",
          name: "Adria Consulting",
          _tide: { writable_fields: [], protected_fields: [], actions: {} },
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("heading", { name: "Invoices" })

  await user.click(
    screen.getByRole("button", { name: "Search everywhere" }),
  )
  await user.type(
    screen.getByRole("searchbox", { name: "Search everywhere" }),
    "adria",
  )

  const hit = await screen.findByRole("link", {
    name: "ADRIA - Adria Consulting",
  })
  const panel = screen.getByRole("dialog")
  expect(
    within(panel).getByRole("heading", { name: "Customers" }),
  ).toBeInTheDocument()
  // One debounced request, carrying the text and the fixed page bound.
  expect(searchBodies).toEqual([{ text: "adria", limit: 5 }])
  // The doorless group is withheld entirely.
  expect(screen.queryByText("Bins")).toBeNull()
  expect(screen.queryByText("B-09")).toBeNull()

  await user.click(hit)
  expect(
    await screen.findByRole("heading", { name: "Customer — ADRIA" }),
  ).toBeInTheDocument()
})

it("says when a search fails, and when nothing matches", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse(noBrowserAuth)
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({ records: [], next_cursor: null })
      }
      if (url.endsWith("/_tide/search")) {
        const body = JSON.parse(String(init?.body)) as { text: string }
        if (body.text === "boom") {
          return jsonResponse(
            { code: "internal", message: "The search could not be run." },
            500,
          )
        }
        return jsonResponse({
          wire_version: "0.1",
          text: body.text,
          groups: [],
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("heading", { name: "Invoices" })

  await user.click(
    screen.getByRole("button", { name: "Search everywhere" }),
  )
  const box = screen.getByRole("searchbox", { name: "Search everywhere" })
  await user.type(box, "boom")
  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("The search could not be run.")

  await user.clear(box)
  await user.type(box, "zzz")
  expect(
    await screen.findByText("Nothing matches this search."),
  ).toBeInTheDocument()
})

function renderApp() {
  // The suite predates Home: land where the old default landed.
  window.history.replaceState(null, "", "/?view=sales.Invoice.browse")
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
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

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

const noBrowserAuth = {
  enabled: false,
  mode: null,
  login_path: null,
  session_path: null,
  logout_path: null,
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

const readOnlyField = {
  ...plainColumn,
  writable: false,
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
    "sales.Invoice": {
      operations: ["list", "get"],
      draft_operations: [],
      readable_fields: ["id", "number"],
      writable_fields: [],
      actions: [],
      audit: false,
    },
    "crm.Customer": {
      operations: ["list", "get"],
      draft_operations: [],
      readable_fields: ["id", "code", "name"],
      writable_fields: [],
      actions: [],
      audit: false,
    },
  },
}

const invoiceView = {
  view: "sales.Invoice.browse",
  entity: "sales.Invoice",
  label: "Invoices",
  resource_path: "/api/v1/invoices",
  query_path: "/api/v1/invoices/_query",
  identity_field: "id",
  columns: [plainColumn],
  search_field: "number",
  search_label: "Number",
  named_filters: [],
  sortable_fields: ["number"],
  page_size: 25,
  operations: ["list", "get"],
  detail_view: "sales.Invoice.edit",
}

const customerView = {
  view: "crm.Customer.browse",
  entity: "crm.Customer",
  label: "Customers",
  resource_path: "/api/v1/customers",
  query_path: "/api/v1/customers/_query",
  identity_field: "id",
  columns: [
    { ...plainColumn, name: "code", label: "Code" },
    { ...plainColumn, name: "name", label: "Name" },
  ],
  search_field: "name",
  search_label: "Name",
  named_filters: [],
  sortable_fields: ["code", "name"],
  page_size: 25,
  operations: ["list", "get"],
  detail_view: "crm.Customer.edit",
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
        {
          view: "crm.Customer.browse",
          entity: "crm.Customer",
          label: "Customers",
        },
      ],
    },
  ],
  views: {
    "sales.Invoice.browse": invoiceView,
    "crm.Customer.browse": customerView,
  },
  forms: {
    "sales.Invoice.edit": {
      view: "sales.Invoice.edit",
      entity: "sales.Invoice",
      label: "Invoice",
      display_template: "number",
      fields: { number: readOnlyField },
      sections: [
        {
          kind: "group",
          label: "Invoice",
          rows: [["number"]],
          tab: null,
        },
      ],
    },
    "crm.Customer.edit": {
      view: "crm.Customer.edit",
      entity: "crm.Customer",
      label: "Customer",
      display_template: "code",
      fields: {
        code: { ...readOnlyField, name: "code", label: "Code" },
        name: { ...readOnlyField, name: "name", label: "Name" },
      },
      sections: [
        {
          kind: "group",
          label: "Customer",
          rows: [["code", "name"]],
          tab: null,
        },
      ],
    },
  },
}
