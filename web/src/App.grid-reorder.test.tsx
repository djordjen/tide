import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

/**
 * Reordering starts at the grip, and nowhere else.
 *
 * The header cell used to be draggable as a whole, so grabbing the resize
 * border started a native drag instead: the column appeared to shift, and
 * because the drag swallowed the mouseup, the resize tracker was left
 * running with the button up. The gesture contract now: the grip icon is
 * the only drag source, the whole header stays the drop target, and the
 * separator belongs to resizing alone.
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("reorders a column from its grip and from nowhere else", async () => {
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  const headerNames = () =>
    screen.getAllByRole("columnheader").map((header) => header.textContent)
  expect(headerNames()).toEqual(["Number", "Total"])

  const [numberHeader, totalHeader] = screen.getAllByRole("columnheader")

  // The gesture from the defect report: a drag that begins on the resize
  // border. It must not move the column.
  fireEvent.dragStart(
    screen.getByRole("separator", { name: "Resize Total" }),
  )
  fireEvent.dragOver(numberHeader)
  fireEvent.drop(numberHeader)
  expect(headerNames()).toEqual(["Number", "Total"])

  // Nor may a drag that begins on the header surface itself.
  fireEvent.dragStart(totalHeader)
  fireEvent.dragOver(numberHeader)
  fireEvent.drop(numberHeader)
  expect(headerNames()).toEqual(["Number", "Total"])

  // The grip is the one handle that means "move me".
  fireEvent.dragStart(screen.getByTitle("Drag Total to reorder"))
  fireEvent.dragOver(numberHeader)
  fireEvent.drop(numberHeader)
  expect(headerNames()).toEqual(["Total", "Number"])
})

function stubServer() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
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
          records: [invoice],
          next_cursor: null,
          summaries: null,
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )
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
  _tide: {},
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
  values: [],
}

const totalColumn = {
  ...numberColumn,
  name: "total",
  label: "Total",
  field_type: "decimal",
  alignment: "right",
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
      writable_fields: [],
      actions: [],
      audit: false,
    },
  },
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
      summaries: [],
      edit: "form",
      page_size: 25,
      operations: ["list", "get"],
      detail_view: null,
    },
  },
  forms: {},
}
