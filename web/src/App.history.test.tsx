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

it("shows the record's history as a tab beside its collections", async () => {
  // The audit trail reaches this surface the way collections already do: as
  // a tab on the panel below the record. The wire's redaction vocabulary is
  // rendered, not re-judged -- a withheld value stays withheld here.
  let auditCalls = 0
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse(noBrowserAuth)
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(auditedSession)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({
          records: [summary(2, "INV-2026-0002", "draft")],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/2/_audit")) {
        auditCalls += 1
        return jsonResponse(history)
      }
      if (url.endsWith("/invoices/2") && init?.method === "PATCH") {
        return jsonResponse(
          invoice(2, "INV-2026-0002", "draft", ["invoice_date", "currency"]),
        )
      }
      if (url.endsWith("/invoices/2")) {
        return jsonResponse(
          invoice(2, "INV-2026-0002", "draft", ["invoice_date", "currency"]),
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0002/ }),
  )
  await screen.findByRole("heading", { name: "Invoice — INV-2026-0002" })

  expect(screen.getByRole("tab", { name: "Lines" })).toBeInTheDocument()
  await user.click(screen.getByRole("tab", { name: "History" }))

  const panel = await screen.findByRole("tabpanel")
  await within(panel).findByText("Updated")
  expect(within(panel).getByText("3 events")).toBeInTheDocument()

  // Newest first, exactly as served.
  const rows = within(panel).getAllByRole("row")
  expect(rows[1]).toHaveTextContent("Updated")
  expect(rows[2]).toHaveTextContent("post · Succeeded")
  expect(rows[3]).toHaveTextContent("Created")

  // Changes speak the form's labels and the wire's redaction vocabulary.
  expect(
    within(panel).getByText("Status: draft → posted"),
  ).toBeInTheDocument()
  expect(within(panel).getByText("Total: [redacted]")).toBeInTheDocument()
  expect(within(panel).getByText("Currency")).toBeInTheDocument()
  expect(within(panel).getByText("via action")).toBeInTheDocument()
  expect(within(panel).getAllByText("local:mara").length).toBe(2)
  expect(within(panel).getByText("local:vera")).toBeInTheDocument()
  // 10:15 UTC keeps its calendar day in any test timezone; the clock time
  // would not.
  expect(within(panel).getAllByText(/28\.08\.2026/).length).toBe(3)
  expect(
    within(panel).getByText(
      "Newest first · Protected values stay redacted",
    ),
  ).toBeInTheDocument()

  // A save makes the history it just extended stale: the open panel asks
  // again rather than keep showing the record's past without its present.
  await user.click(screen.getByRole("tab", { name: "Lines" }))
  const currency = screen.getByLabelText(/^Currency/)
  await user.clear(currency)
  await user.type(currency, "USD")
  await user.click(screen.getByRole("button", { name: "Save" }))
  await user.click(screen.getByRole("tab", { name: "History" }))
  await waitFor(() => expect(auditCalls).toBe(2))
})

it("keeps history off the screen where the session does not grant it", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
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
        return jsonResponse({
          records: [summary(2, "INV-2026-0002", "draft")],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/2")) {
        return jsonResponse(invoice(2, "INV-2026-0002", "draft", []))
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0002/ }),
  )
  await screen.findByRole("heading", { name: "Invoice — INV-2026-0002" })

  expect(screen.getByRole("tab", { name: "Lines" })).toBeInTheDocument()
  expect(screen.queryByRole("tab", { name: "History" })).toBeNull()
})

it("says when history could not be loaded, and Try again recovers", async () => {
  let refused = true
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse(noBrowserAuth)
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse(auditedSession)
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse(presentation)
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({
          records: [summary(2, "INV-2026-0002", "draft")],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/2/_audit")) {
        if (refused) {
          return jsonResponse(
            {
              code: "forbidden",
              message: "History for this record is not available to you.",
            },
            403,
          )
        }
        return jsonResponse({
          wire_version: "0.1",
          entity: "sales.Invoice",
          identity: 2,
          events: [],
        })
      }
      if (url.endsWith("/invoices/2")) {
        return jsonResponse(invoice(2, "INV-2026-0002", "draft", []))
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0002/ }),
  )
  await screen.findByRole("heading", { name: "Invoice — INV-2026-0002" })
  await user.click(screen.getByRole("tab", { name: "History" }))

  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent(
    "History for this record is not available to you.",
  )

  refused = false
  await user.click(screen.getByRole("button", { name: "Try again" }))
  expect(
    await screen.findByText("No history recorded for this record."),
  ).toBeInTheDocument()
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

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function summary(id: number, number: string, status: string) {
  return { id, number, status, total: "100.00" }
}

function invoice(
  id: number,
  number: string,
  status: string,
  writableFields: string[],
) {
  return {
    id,
    number,
    invoice_date: "2026-07-01",
    status,
    currency: "EUR",
    total: "100.00",
    lines: [
      {
        id,
        description: `Demo line ${id}`,
        total: "100.00",
      },
    ],
    _tide: writableFields.length
      ? { writable_fields: writableFields }
      : undefined,
  }
}

const noBrowserAuth = {
  enabled: false,
  mode: null,
  login_path: null,
  session_path: null,
  logout_path: null,
}

const history = {
  wire_version: "0.1",
  entity: "sales.Invoice",
  identity: 2,
  events: [
    {
      event_id: "evt-3",
      entity: "sales.Invoice",
      kind: "record",
      action: null,
      operation: "update",
      identity: 2,
      principal: "local:mara",
      channel: "rest",
      correlation_id: "corr-3",
      started_at: "2026-08-28T10:15:00Z",
      outcome: null,
      finished_at: null,
      error_code: null,
      source: "action",
      changes: [
        {
          field: "status",
          before_present: true,
          after_present: true,
          value_mode: "recorded",
          before: "draft",
          after: "posted",
        },
        {
          field: "total",
          before_present: true,
          after_present: true,
          value_mode: "redacted",
          before: null,
          after: null,
        },
        {
          field: "currency",
          before_present: true,
          after_present: true,
          value_mode: "field_only",
          before: null,
          after: null,
        },
      ],
    },
    {
      event_id: "evt-2",
      entity: "sales.Invoice",
      kind: "action",
      action: "post",
      operation: null,
      identity: 2,
      principal: "local:mara",
      channel: "rest",
      correlation_id: "corr-3",
      started_at: "2026-08-28T10:14:59Z",
      outcome: "succeeded",
      finished_at: "2026-08-28T10:15:00Z",
      error_code: null,
      source: null,
      changes: [],
    },
    {
      event_id: "evt-1",
      entity: "sales.Invoice",
      kind: "record",
      action: null,
      operation: "create",
      identity: 2,
      principal: "local:vera",
      channel: "web",
      correlation_id: "corr-1",
      started_at: "2026-08-28T09:00:00Z",
      outcome: null,
      finished_at: null,
      error_code: null,
      source: "user",
      changes: [
        {
          field: "number",
          before_present: false,
          after_present: true,
          value_mode: "recorded",
          before: null,
          after: "INV-2026-0002",
        },
      ],
    },
  ],
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
      operations: ["list", "get", "update"],
      draft_operations: [],
      readable_fields: [
        "id",
        "number",
        "invoice_date",
        "status",
        "currency",
        "total",
        "lines",
      ],
      writable_fields: ["invoice_date", "currency", "lines"],
      actions: [],
      audit: false,
    },
  },
}

const auditedSession = {
  ...session,
  entities: {
    "sales.Invoice": {
      ...session.entities["sales.Invoice"],
      audit: true,
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
    plainColumn,
    {
      ...plainColumn,
      name: "status",
      label: "Status",
      field_type: "choice",
    },
  ],
  search_field: "number",
  search_label: "Number",
  named_filters: [],
  sortable_fields: ["number", "status"],
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
  views: {
    "sales.Invoice.browse": invoiceView,
  },
  forms: {
    "sales.Invoice.edit": {
      view: "sales.Invoice.edit",
      entity: "sales.Invoice",
      label: "Invoice",
      display_template: "number",
      fields: {
        number: readOnlyField,
        invoice_date: {
          ...readOnlyField,
          name: "invoice_date",
          label: "Invoice Date",
          field_type: "date",
          format_options: {
            decimal_places: null,
            thousands_separator: false,
            display: "%d.%m.%Y",
          },
          writable: true,
        },
        status: {
          ...readOnlyField,
          name: "status",
          label: "Status",
          field_type: "choice",
        },
        currency: {
          ...readOnlyField,
          name: "currency",
          label: "Currency",
          writable: true,
        },
        total: {
          ...readOnlyField,
          name: "total",
          label: "Total",
          field_type: "decimal",
          alignment: "right",
          format_options: {
            decimal_places: 2,
            thousands_separator: true,
            display: null,
          },
        },
      },
      sections: [
        {
          kind: "group",
          label: "Invoice",
          rows: [
            ["number", "invoice_date"],
            ["status", "currency"],
            ["total"],
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
          ],
          tab: null,
        },
      ],
    },
  },
}
