import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

/**
 * The browse grid is reachable and traversable from the keyboard alone.
 *
 * TIDE's terminal client has always been keyboard-first, and the Web UI is the
 * surface where that was only half true: every rendered row carried
 * `tabIndex={0}`, so a browse spent one tab stop per visible row and offered
 * no way to move between them, while the rows the virtualizer had not
 * rendered had no node to reach at all.
 *
 * The contract below is the ARIA grid one -- a single roving tab stop, arrows
 * to move it, Home and End to reach the ends -- and moving it selects, the
 * same way clicking a row does, so that `Open` and the record pane follow the
 * caret rather than the last mouse click.
 *
 * jsdom can see all of this: focus, `tabindex` and `aria-selected` are DOM
 * state rather than geometry. A Playwright journey covers the same contract
 * against the real bundle, and adds the one claim jsdom cannot make -- that
 * the grid costs exactly one tab stop, proven by tabbing straight back out of
 * it.
 *
 * Neither covers the scroll that `moveActiveRow` asks for when the target row
 * is outside the rendered window: the demo application has eight invoices and
 * a browser renders all of them, so there is nothing to scroll. Reaching that
 * needs a fixture larger than the viewport, which the e2e harness does not
 * have.
 */

afterEach(() => {
  // Explicit, because this file has two tests: without it the first grid stays
  // mounted and `screen` counts its tab stop alongside the second one's.
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("gives the grid one tab stop and moves it with the arrow keys", async () => {
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  const first = await screen.findByRole("row", { name: /INV-2026-0001/ })
  await screen.findByRole("row", { name: /INV-2026-0004/ })

  // One tab stop for the whole grid, not one per rendered row.
  const stops = screen
    .getAllByRole("row")
    .filter((row) => row.getAttribute("tabindex") === "0")
  expect(stops).toEqual([first])

  first.focus()
  await user.keyboard("{ArrowDown}")
  const second = screen.getByRole("row", { name: /INV-2026-0002/ })
  expect(second).toHaveFocus()
  expect(second).toHaveAttribute("aria-selected", "true")
  expect(second).toHaveAttribute("tabindex", "0")
  expect(first).toHaveAttribute("tabindex", "-1")
  expect(first).toHaveAttribute("aria-selected", "false")

  await user.keyboard("{ArrowUp}")
  expect(first).toHaveFocus()
  expect(first).toHaveAttribute("aria-selected", "true")

  // The ends hold rather than wrap: a browse is a window onto more rows, and
  // jumping to the far end is what End is for.
  await user.keyboard("{ArrowUp}")
  expect(first).toHaveFocus()

  await user.keyboard("{End}")
  const last = screen.getByRole("row", { name: /INV-2026-0004/ })
  expect(last).toHaveFocus()
  expect(last).toHaveAttribute("aria-selected", "true")

  await user.keyboard("{ArrowDown}")
  expect(last).toHaveFocus()

  await user.keyboard("{Home}")
  expect(first).toHaveFocus()
})

it("moves the tab stop to the row a click selected", async () => {
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  const third = await screen.findByRole("row", { name: /INV-2026-0003/ })
  await user.click(third)
  expect(third).toHaveAttribute("aria-selected", "true")

  // Tabbing out of the grid and back in returns to where the person was, not
  // to the top of the list.
  expect(third).toHaveAttribute("tabindex", "0")
  expect(
    screen
      .getAllByRole("row")
      .filter((row) => row.getAttribute("tabindex") === "0"),
  ).toEqual([third])

  third.focus()
  await user.keyboard("{ArrowDown}")
  expect(screen.getByRole("row", { name: /INV-2026-0004/ })).toHaveFocus()
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
          records: [
            summary(1, "INV-2026-0001"),
            summary(2, "INV-2026-0002"),
            summary(3, "INV-2026-0003"),
            summary(4, "INV-2026-0004"),
          ],
          next_cursor: null,
        })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )
}

function renderApp() {
  // The suite predates Home: land where the old default landed.
  window.history.replaceState(null, "", "/?view=sales.Invoice.browse")
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

function summary(id: number, number: string) {
  return { id, number, status: "draft", total: "100.00" }
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
      operations: ["list", "get"],
      draft_operations: [],
      readable_fields: ["id", "number", "status", "total"],
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
    plainColumn,
    { ...plainColumn, name: "status", label: "Status", field_type: "choice" },
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
  sortable_fields: ["number"],
  page_size: 25,
  operations: ["list", "get"],
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
  forms: {},
}
