import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("connects through the safe manifest and renders capability navigation", async () => {
  const requests: Array<{ url: string; authorization: string | null }> = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const headers = new Headers(init?.headers)
      requests.push({
        url,
        authorization: headers.get("Authorization"),
      })
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(session)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({ records: [], next_cursor: null })
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

  expect(
    await screen.findByRole("heading", { name: "Invoices" }),
  ).toBeInTheDocument()
  expect(
    screen.getByRole("navigation", { name: "Application navigation" }),
  ).toHaveTextContent("Customers")
  expect(
    await screen.findByText("No matching records"),
  ).toBeInTheDocument()
  expect(
    requests.every(
      (request) =>
        request.authorization ===
        "Bearer a-development-token-that-is-long-enough",
    ),
  ).toBe(true)
  expect(
    [...Array(window.localStorage.length)].some((_, index) =>
      window.localStorage
        .getItem(window.localStorage.key(index) ?? "")
        ?.includes("development-token"),
    ),
  ).toBe(false)
})

function renderApp() {
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

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
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
  },
}

const invoiceView = {
  view: "sales.Invoice.browse",
  entity: "sales.Invoice",
  label: "Invoices",
  resource_path: "/api/v1/invoices",
  query_path: "/api/v1/invoices/_query",
  identity_field: "id",
  columns: [
    {
      name: "number",
      label: "Number",
      field_type: "string",
      alignment: "left",
      format: null,
      format_options: null,
      target_entity: null,
      reference: null,
    },
  ],
  search_field: "number",
  search_label: "Number",
  named_filters: [],
  sortable_fields: ["number"],
  page_size: 25,
  operations: ["list", "get"],
  detail_view: null,
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
    {
      label: "Master Data",
      items: [
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
    "crm.Customer.browse": {
      ...invoiceView,
      view: "crm.Customer.browse",
      entity: "crm.Customer",
      label: "Customers",
      resource_path: "/api/v1/customers",
      query_path: "/api/v1/customers/_query",
    },
  },
  forms: {},
}
