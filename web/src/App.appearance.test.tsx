import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

/**
 * What an application's `appearance:` rules look like once they arrive.
 *
 * The renderer never sees a rule -- the server evaluates them and sends a
 * verdict per record -- so everything here is about reading `_tide.appearance`
 * and putting it where a person will see it: the row in a list of four
 * hundred, and the field on the record it belongs to.
 *
 * jsdom can see the marker and the class list, which is the whole claim. What
 * shade of amber that turns into is a screenshot's business.
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("marks the row an application's rules judged, and leaves the others alone", async () => {
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  const painted = await screen.findByRole("row", { name: /INV-2026-0002/ })
  const plain = await screen.findByRole("row", { name: /INV-2026-0001/ })

  expect(painted).toHaveAttribute("data-emphasis", "warning")
  expect(plain).not.toHaveAttribute("data-emphasis")
  // The verdict is a class the theme owns, not a colour the row carries.
  expect(painted.className).toContain("amber")

  // A rule may speak for one field rather than the whole record, and in a
  // grid that field is a cell.
  const total = within(painted).getByText("1,200.00")
  expect(total).toHaveAttribute("data-emphasis", "danger")
})

it("leaves an emphasis it has never heard of undrawn", async () => {
  // A server may be a version ahead of the bundle it is serving. An unknown
  // emphasis is dropped rather than turned into a class that does not exist,
  // because an unpainted row reads better than a broken one.
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  const future = await screen.findByRole("row", { name: /INV-2026-0003/ })

  expect(future).not.toHaveAttribute("data-emphasis")
  expect(future.className).not.toContain("chartreuse")
  expect(within(future).getByText("300.00")).not.toHaveAttribute(
    "data-emphasis",
  )
})

it("carries the same verdict onto the record the row opens", async () => {
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.dblClick(await screen.findByRole("row", { name: /INV-2026-0002/ }))
  expect(
    await screen.findByRole("heading", { name: /INV-2026-0002/ }),
  ).toBeInTheDocument()

  // Scoped to the card: the browse behind it is hidden rather than
  // unmounted, and its column header is also called Total.
  const card = document.querySelector<HTMLElement>("[data-tide-record-card]")!
  expect(card).toHaveAttribute("data-emphasis", "warning")
  // The field is writable here, so its value is an input and the label is
  // what can carry the mark.
  expect(within(card).getByText(/^Total/)).toHaveAttribute(
    "data-emphasis",
    "danger",
  )
})

function stubServer() {
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
          records: [plainInvoice, paintedInvoice, futureInvoice],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/2") && (init?.method ?? "GET") === "GET") {
        return jsonResponse(paintedInvoice)
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

const plainInvoice = {
  id: 1,
  number: "INV-2026-0001",
  total: "100.00",
  _tide: { writable_fields: ["total"] },
}

const paintedInvoice = {
  id: 2,
  number: "INV-2026-0002",
  total: "1200.00",
  _tide: {
    writable_fields: ["total"],
    appearance: { record: "warning", fields: { total: "danger" } },
  },
}

/** A verdict from a server that knows an emphasis this bundle does not. */
const futureInvoice = {
  id: 3,
  number: "INV-2026-0003",
  total: "300.00",
  _tide: {
    writable_fields: ["total"],
    appearance: {
      record: "chartreuse",
      fields: { total: "neon" },
    },
  },
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

const totalColumn = {
  ...plainColumn,
  name: "total",
  label: "Total",
  field_type: "decimal",
  alignment: "right",
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
      operations: ["list", "get", "update"],
      draft_operations: [],
      readable_fields: ["id", "number", "total"],
      writable_fields: ["total"],
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
  columns: [plainColumn, totalColumn],
  search_field: "number",
  search_label: "Number",
  named_filters: [],
  sortable_fields: ["number"],
  page_size: 25,
  operations: ["list", "get", "update"],
  detail_view: "sales.Invoice.edit",
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
  views: { "sales.Invoice.browse": invoiceView },
  forms: {
    "sales.Invoice.edit": {
      view: "sales.Invoice.edit",
      entity: "sales.Invoice",
      label: "Invoice",
      display_template: "{number}",
      fields: {
        total: {
          ...totalColumn,
          values: [],
          writable: true,
          required: false,
          help: null,
          max_length: null,
          choices: [],
          regex: null,
          numeric_mask: "0.00",
          precision: 12,
          scale: 2,
          minimum: null,
          maximum: null,
          has_default: false,
          default_value: null,
        },
      },
      sections: [
        { kind: "group", label: "Invoice", rows: [["total"]], tab: null },
      ],
    },
  },
}
