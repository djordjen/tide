import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

/**
 * The browse footer: what a view's `summaries:` declaration turns into.
 *
 * The renderer never aggregates -- the manifest says what to ask, the query
 * carries the request, and the server answers for the whole filtered set.
 * jsdom can see the band, its per-column cells and the formatted text, which
 * is the whole claim here; where the band sits is Playwright's business.
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("asks for the declared summaries and lays the answers under their columns", async () => {
  const requests = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  const footer = screen.getByTestId("grid-summary-row")
  const count = within(footer).getByTestId("grid-summary-number")
  const sum = within(footer).getByTestId("grid-summary-total")

  // The word is the renderer's; the value speaks the column's format --
  // except a count, which is a number of values, never money. Anchored
  // matches, because "9" is also a substring of a wrongly-dressed "9.00";
  // the gap between word and value is CSS, so the text has none.
  expect(count).toHaveTextContent(/^Count9$/)
  expect(sum).toHaveTextContent(/^Sum18,397\.15$/)

  const queried = requests.find((item) => item.url.endsWith("/_query"))
  expect(queried?.body.summaries).toEqual([
    { field: "number", function: "count" },
    { field: "total", function: "sum" },
  ])
})

it("shows an aggregate over nothing as a dash, and a zero count as zero", async () => {
  // The count rides the decimal column here on purpose: a count is a number
  // of values, and this is the one place the column's money format would
  // visibly dress 0 up as 0.00.
  stubServer({
    records: [],
    summaries: [
      { field: "number", function: "min", value: null },
      { field: "total", function: "count", value: 0 },
    ],
  })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByText(/No matching records/)

  const footer = screen.getByTestId("grid-summary-row")
  expect(within(footer).getByTestId("grid-summary-number")).toHaveTextContent(
    /^Min—$/,
  )
  expect(within(footer).getByTestId("grid-summary-total")).toHaveTextContent(
    /^Count0$/,
  )
})

it("draws no band and asks for nothing when the view declares none", async () => {
  const requests = stubServer({ declared: [] })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  expect(screen.queryByTestId("grid-summary-row")).not.toBeInTheDocument()
  const queried = requests.find((item) => item.url.endsWith("/_query"))
  expect(queried?.body.summaries).toBeUndefined()
})

interface CapturedRequest {
  url: string
  body: Record<string, unknown>
}

function stubServer(options?: {
  declared?: { field: string; function: string }[]
  records?: Record<string, unknown>[]
  summaries?: { field: string; function: string; value: unknown }[] | null
}): CapturedRequest[] {
  const declared = options?.declared ?? [
    { field: "number", function: "count" },
    { field: "total", function: "sum" },
  ]
  const records = options?.records ?? [invoice]
  const summaries =
    options?.summaries !== undefined
      ? options.summaries
      : declared.length
        ? [
            { field: "number", function: "count", value: 9 },
            { field: "total", function: "sum", value: "18397.15" },
          ]
        : null
  const requests: CapturedRequest[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.body && typeof init.body === "string") {
        requests.push({ url, body: JSON.parse(init.body) })
      }
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
        return jsonResponse(presentationFor(declared))
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({
          records,
          next_cursor: null,
          summaries,
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )
  return requests
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

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

const invoice = {
  id: 1,
  number: "INV-2026-0001",
  total: "850.00",
  _tide: { writable_fields: ["total"] },
}

const numberColumn = {
  name: "number",
  label: "Number",
  field_type: "string",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
}

const totalColumn = {
  ...numberColumn,
  name: "total",
  label: "Total",
  field_type: "decimal",
  alignment: "right",
  format: "money",
  format_options: {
    decimal_places: 2,
    thousands_separator: true,
    display: null,
  },
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
      readable_fields: ["id", "number", "total"],
      writable_fields: ["total"],
      actions: [],
      audit: false,
    },
  },
}

function presentationFor(
  declared: { field: string; function: string }[],
) {
  return {
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
      "sales.Invoice.browse": {
        view: "sales.Invoice.browse",
        entity: "sales.Invoice",
        label: "Invoices",
        resource_path: "/api/v1/invoices",
        query_path: "/api/v1/invoices/_query",
        identity_field: "id",
        columns: [numberColumn, totalColumn],
        search_field: null,
        search_label: null,
        named_filters: [],
        sortable_fields: [],
        summaries: declared,
        page_size: 25,
        operations: ["list", "get"],
        detail_view: null,
      },
    },
    forms: {},
  }
}
