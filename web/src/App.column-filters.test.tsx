import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeAll, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

/**
 * Per-column value filters: the funnel, the checkbox list, and the `in`
 * condition they become.
 *
 * The list is the server's answer -- distinct values under the *other*
 * active conditions, never the column's own, which is what lets a person
 * widen a filter they already applied. jsdom sees the requests, the
 * checkboxes and the funnel's state; where the popover hangs is a
 * screenshot's business.
 */

beforeAll(() => {
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

it("filters the browse by the checked values of one column", async () => {
  const server = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  const popover = await screen.findByRole("dialog", { name: "Status values" })
  // The list came from the server, captioned the way cells are, and
  // arrives fully checked -- an unfiltered column admits everything. The
  // gesture is the reference application's: clear, then choose.
  expect(server.distincts).toHaveLength(1)
  expect(server.distincts[0]).toEqual({ field: "status", filters: [] })
  await user.click(within(popover).getByRole("checkbox", { name: "Select all" }))
  await user.click(within(popover).getByRole("checkbox", { name: "Draft" }))
  await user.click(within(popover).getByRole("button", { name: "Apply" }))

  await waitFor(() => {
    const asked = server.queries.at(-1)
    expect(asked?.filters).toEqual([
      { field: "status", operator: "in", value: ["draft"] },
    ])
  })
  // The funnel says the column is constraining the view.
  expect(
    screen.getByRole("button", { name: "Filter Status" }),
  ).toHaveAttribute("aria-pressed", "true")
})

it("the blank entry chooses rows where the column is empty", async () => {
  const server = stubServer({
    distinctValues: [
      { value: null, display: null },
      { value: "draft", display: null },
    ],
  })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  const popover = await screen.findByRole("dialog", { name: "Status values" })
  await user.click(within(popover).getByRole("checkbox", { name: "Select all" }))
  await user.click(within(popover).getByRole("checkbox", { name: "(Blank)" }))
  await user.click(within(popover).getByRole("button", { name: "Apply" }))

  await waitFor(() => {
    expect(server.queries.at(-1)?.filters).toEqual([
      { field: "status", operator: "in", value: [null] },
    ])
  })
})

it("cancel discards the staged checks", async () => {
  const server = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })
  const before = server.queries.length

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  const popover = await screen.findByRole("dialog", { name: "Status values" })
  await user.click(within(popover).getByRole("checkbox", { name: "Draft" }))
  await user.click(within(popover).getByRole("button", { name: "Cancel" }))

  expect(server.queries.length).toBe(before)
  expect(
    screen.getByRole("button", { name: "Filter Status" }),
  ).toHaveAttribute("aria-pressed", "false")
})

it("checking every value releases the column", async () => {
  const server = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  let popover = await screen.findByRole("dialog", { name: "Status values" })
  await user.click(within(popover).getByRole("checkbox", { name: "Select all" }))
  await user.click(within(popover).getByRole("checkbox", { name: "Draft" }))
  await user.click(within(popover).getByRole("button", { name: "Apply" }))
  await waitFor(() =>
    expect(server.queries.at(-1)?.filters).toEqual([
      { field: "status", operator: "in", value: ["draft"] },
    ]),
  )

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  popover = await screen.findByRole("dialog", { name: "Status values" })
  await user.click(
    within(popover).getByRole("checkbox", { name: "Select all" }),
  )
  await user.click(within(popover).getByRole("button", { name: "Apply" }))

  // Releasing restores the unfiltered query, whose page is still cached --
  // so the proof is the funnel's state and that no query ever asked with
  // an in-condition again, not a fresh network call.
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Filter Status" }),
    ).toHaveAttribute("aria-pressed", "false"),
  )
  expect(server.queries.at(-1)?.filters).toEqual([
    { field: "status", operator: "in", value: ["draft"] },
  ])
})

it("a column's list reflects the other filters and not its own", async () => {
  const server = stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  let popover = await screen.findByRole("dialog", { name: "Status values" })
  await user.click(within(popover).getByRole("checkbox", { name: "Select all" }))
  await user.click(within(popover).getByRole("checkbox", { name: "Draft" }))
  await user.click(within(popover).getByRole("button", { name: "Apply" }))
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Filter Status" }),
    ).toHaveAttribute("aria-pressed", "true"),
  )

  await user.click(screen.getByRole("button", { name: "Filter Number" }))
  await screen.findByRole("dialog", { name: "Number values" })
  await waitFor(() => {
    expect(server.distincts.at(-1)).toEqual({
      field: "number",
      filters: [{ field: "status", operator: "in", value: ["draft"] }],
    })
  })
  await user.keyboard("{Escape}")

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  popover = await screen.findByRole("dialog", { name: "Status values" })
  await waitFor(() => {
    expect(server.distincts.at(-1)).toEqual({ field: "status", filters: [] })
  })
  // Its own values stay checkable to widen the filter, and the applied
  // ones arrive already checked.
  expect(
    within(popover).getByRole("checkbox", { name: "Draft" }),
  ).toBeChecked()
})

it("an apply that changed nothing keeps a filter the list cannot show", async () => {
  // Reopened under another funnel, the status list may lack values the
  // applied filter still holds. Apply used to read "everything visible is
  // checked" as "release the column", silently discarding the rest -- and
  // dropping the other funnel later widened the view past what the user
  // had chosen.
  const server = stubServer({
    distinctFor: (field, filters) =>
      field === "status" && filters.length > 0
        ? [{ value: "draft", display: null }]
        : undefined,
  })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  let popover = await screen.findByRole("dialog", { name: "Status values" })
  await user.click(
    within(popover).getByRole("checkbox", { name: "Cancelled" }),
  )
  await user.click(within(popover).getByRole("button", { name: "Apply" }))
  await waitFor(() =>
    expect(server.queries.at(-1)?.filters).toEqual([
      { field: "status", operator: "in", value: ["draft", "posted"] },
    ]),
  )

  await user.click(screen.getByRole("button", { name: "Filter Number" }))
  popover = await screen.findByRole("dialog", { name: "Number values" })
  await user.click(
    within(popover).getByRole("checkbox", { name: "Select all" }),
  )
  // The number column is a string, so its values render as stored.
  await user.click(within(popover).getByRole("checkbox", { name: "draft" }))
  await user.click(within(popover).getByRole("button", { name: "Apply" }))
  await waitFor(() =>
    expect(server.queries.at(-1)?.filters).toContainEqual({
      field: "number",
      operator: "in",
      value: ["draft"],
    }),
  )

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  popover = await screen.findByRole("dialog", { name: "Status values" })
  await within(popover).findByRole("checkbox", { name: "Draft" })
  await user.click(within(popover).getByRole("button", { name: "Apply" }))

  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Filter Status" }),
    ).toHaveAttribute("aria-pressed", "true"),
  )
  expect(server.queries.at(-1)?.filters).toContainEqual({
    field: "status",
    operator: "in",
    value: ["draft", "posted"],
  })
})

it("a cut list says so, and the search narrows it in place", async () => {
  stubServer({
    distinctValues: [
      { value: "cancelled", display: null },
      { value: "draft", display: null },
      { value: "posted", display: null },
    ],
    truncated: true,
  })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  await user.click(screen.getByRole("button", { name: "Filter Status" }))
  const popover = await screen.findByRole("dialog", { name: "Status values" })
  expect(within(popover).getByText(/first 200/i)).toBeInTheDocument()

  await user.type(
    within(popover).getByRole("searchbox", { name: "Search values" }),
    "dra",
  )
  expect(
    within(popover).getByRole("checkbox", { name: "Draft" }),
  ).toBeInTheDocument()
  expect(
    within(popover).queryByRole("checkbox", { name: "Posted" }),
  ).not.toBeInTheDocument()
})

interface CapturedQuery {
  filters: unknown[]
}

function stubServer(options?: {
  distinctValues?: { value: unknown; display: string | null }[]
  truncated?: boolean
  distinctFor?: (
    field: string,
    filters: unknown[],
  ) => { value: unknown; display: string | null }[] | undefined
}): {
  queries: CapturedQuery[]
  distincts: { field: string; filters: unknown[] }[]
} {
  const queries: CapturedQuery[] = []
  const distincts: { field: string; filters: unknown[] }[] = []
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
        const body = JSON.parse(String(init?.body ?? "{}"))
        queries.push({ filters: body.filters ?? [] })
        return jsonResponse({
          records: [invoice],
          next_cursor: null,
          summaries: null,
        })
      }
      if (url.endsWith("/invoices/_distinct")) {
        const body = JSON.parse(String(init?.body ?? "{}"))
        distincts.push({ field: body.field, filters: body.filters ?? [] })
        return jsonResponse({
          field: body.field,
          values: options?.distinctFor?.(body.field, body.filters ?? []) ??
            options?.distinctValues ?? [
              { value: "cancelled", display: null },
              { value: "draft", display: null },
              { value: "posted", display: null },
            ],
          truncated: options?.truncated ?? false,
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )
  return { queries, distincts }
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
  status: "draft",
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

const statusColumn = {
  ...numberColumn,
  name: "status",
  label: "Status",
  field_type: "choice",
  values: [
    { value: "draft", label: "Draft" },
    { value: "posted", label: "Posted" },
    { value: "cancelled", label: "Cancelled" },
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
  entities: {
    "sales.Invoice": {
      operations: ["list", "get"],
      draft_operations: [],
      readable_fields: ["id", "number", "status"],
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
      columns: [numberColumn, statusColumn],
      search_field: null,
      search_label: null,
      named_filters: [],
      sortable_fields: [],
      filterable_fields: ["number", "status"],
      summaries: [],
      edit: "form",
      page_size: 25,
      operations: ["list", "get"],
      detail_view: null,
    },
  },
  forms: {},
}
