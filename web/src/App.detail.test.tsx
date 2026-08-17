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
})

it("opens a shared-layout detail and navigates without replacing the shell", async () => {
  const details = {
    1: invoice(1, "INV-2026-0001", "posted", []),
    2: invoice(
      2,
      "INV-2026-0002",
      "draft",
      ["invoice_date", "currency", "lines"],
    ),
  }
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
        const body = JSON.parse(String(init?.body)) as {
          cursor: string | null
        }
        if (body.cursor === "cursor-2") {
          return jsonResponse({
            records: [summary(2, "INV-2026-0002", "draft")],
            next_cursor: null,
          })
        }
        return jsonResponse({
          records: [summary(1, "INV-2026-0001", "posted")],
          next_cursor: "cursor-2",
        })
      }
      if (url.endsWith("/invoices/1")) {
        return jsonResponse(details[1])
      }
      if (url.endsWith("/invoices/2")) {
        return jsonResponse(details[2])
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  const firstRow = await screen.findByRole("row", {
    name: /INV-2026-0001/,
  })
  await user.dblClick(firstRow)

  expect(
    await screen.findByRole("heading", {
      name: "Invoice — INV-2026-0001",
    }),
  ).toBeInTheDocument()
  expect(
    screen.getByLabelText("Number is read-only"),
  ).toBeInTheDocument()
  // Collections live in their own tabbed panel below the record now, so the
  // collection's name is a tab, not a heading inside the record card.
  expect(screen.getByRole("tab", { name: "Lines" })).toBeInTheDocument()
  expect(screen.getByText("Demo line 1")).toBeInTheDocument()
  const totalHeader = screen.getByRole("columnheader", { name: "Total" })
  expect(totalHeader).toHaveClass("text-right")
  const linesTable = totalHeader.closest("table")
  expect(linesTable).not.toBeNull()
  expect(
    within(linesTable as HTMLTableElement).getByRole("cell", {
      name: "100.00",
    }),
  ).toHaveClass(
    "text-right",
    "tabular-nums",
  )
  expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()

  await user.click(screen.getByRole("button", { name: "Next" }))
  expect(
    await screen.findByRole("heading", {
      name: "Invoice — INV-2026-0002",
    }),
  ).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()
  expect(screen.queryByLabelText("Invoice Date is read-only")).toBeNull()

  await user.keyboard("{PageUp}")
  expect(
    await screen.findByRole("heading", {
      name: "Invoice — INV-2026-0001",
    }),
  ).toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "Close" }))
  expect(
    screen.getByRole("heading", { name: "Invoices" }),
  ).toBeInTheDocument()
})

it("gathers every collection into one tabbed panel below the record", async () => {
  // A second collection must not be a silent omission on this surface any
  // more than it was on the terminal: each one is a tab, the first is open,
  // and switching shows the other's rows in the same panel.
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
        return jsonResponse(twoCollectionPresentation())
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({
          records: [summary(1, "INV-2026-0001", "posted")],
          next_cursor: null,
        })
      }
      if (url.endsWith("/invoices/1")) {
        return jsonResponse({
          ...invoice(1, "INV-2026-0001", "posted", []),
          approvals: [{ id: 9, approver: "Mira Novak" }],
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await user.dblClick(
    await screen.findByRole("row", { name: /INV-2026-0001/ }),
  )
  await screen.findByRole("heading", { name: "Invoice — INV-2026-0001" })

  const lines = screen.getByRole("tab", { name: "Lines" })
  const approvals = screen.getByRole("tab", { name: "Approvals" })
  expect(lines).toHaveAttribute("aria-selected", "true")
  expect(approvals).toHaveAttribute("aria-selected", "false")
  expect(screen.getByText("Demo line 1")).toBeInTheDocument()
  expect(screen.queryByText("Mira Novak")).toBeNull()

  await user.click(approvals)
  expect(await screen.findByText("Mira Novak")).toBeInTheDocument()
  expect(screen.queryByText("Demo line 1")).toBeNull()
  expect(approvals).toHaveAttribute("aria-selected", "true")
  // One row is one row on the read-only side too.
  expect(screen.getByText("1 row")).toBeInTheDocument()
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
    {
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
    },
  ],
  search_field: "number",
  search_label: "Number",
  named_filters: [],
  sortable_fields: ["number", "status", "total"],
  page_size: 25,
  operations: ["list", "get", "update"],
  detail_view: "sales.Invoice.edit",
}

function twoCollectionPresentation() {
  const form = presentation.forms["sales.Invoice.edit"]
  return {
    ...presentation,
    forms: {
      "sales.Invoice.edit": {
        ...form,
        sections: [
          ...form.sections,
          {
            kind: "collection",
            name: "approvals",
            label: "Approvals",
            entity: "sales.InvoiceApproval",
            columns: [
              {
                ...plainColumn,
                name: "approver",
                label: "Approver",
              },
            ],
            tab: null,
          },
        ],
      },
    },
  }
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
        number: plainColumn,
        invoice_date: {
          ...plainColumn,
          name: "invoice_date",
          label: "Invoice Date",
          field_type: "date",
          format_options: {
            decimal_places: null,
            thousands_separator: false,
            display: "%d.%m.%Y",
          },
        },
        status: {
          ...plainColumn,
          name: "status",
          label: "Status",
          field_type: "choice",
        },
        currency: {
          ...plainColumn,
          name: "currency",
          label: "Currency",
        },
        total: {
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
            {
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
            },
          ],
          tab: null,
        },
      ],
    },
  },
}
