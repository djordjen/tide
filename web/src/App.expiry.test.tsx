import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
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

it("asks for a new sign-in when the session ends mid-session", async () => {
  // A session that expires while someone is working used to surface as
  // whatever error box the screen they were on happens to show, with the
  // application shell still up around it.
  let sessionValid = true

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
      if (url.endsWith("/products/_query")) {
        if (!sessionValid) {
          return jsonResponse(
            { code: "unauthorized", message: "session has expired" },
            { status: 401 },
          )
        }
        return jsonResponse({ records: [product], next_cursor: null })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  expect(await screen.findByRole("heading", { name: "Products" })).toBeInTheDocument()

  sessionValid = false
  await user.click(screen.getByRole("button", { name: "Refresh records" }))

  expect(
    await screen.findByText(/session has ended/i),
  ).toBeInTheDocument()
  await waitFor(() =>
    expect(
      screen.queryByRole("heading", { name: "Products" }),
    ).not.toBeInTheDocument(),
  )
  expect(
    screen.getByLabelText("Application token"),
  ).toBeInTheDocument()
})

function renderApp() {
  // The suite predates Home: land where the old default landed.
  window.history.replaceState(null, "", "/?view=catalog.Product.browse")
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

function jsonResponse(value: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(value), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...Object.fromEntries(new Headers(init.headers)),
    },
  })
}

const product = {
  id: 1,
  code: "P00001",
  name: "Support",
  unit_price: "10.00",
  active: true,
  _tide: { writable_fields: ["code", "name", "unit_price", "active"] },
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
    "catalog.Product": {
      operations: ["list", "get"],
      draft_operations: [],
      readable_fields: ["id", "code", "name", "unit_price", "active"],
      writable_fields: [],
      actions: [],
      audit: false,
    },
  },
}

const column = {
  name: "code",
  label: "Code",
  field_type: "string",
  alignment: "left",
  format: null,
  format_options: null,
  target_entity: null,
  reference: null,
}

const presentation = {
  wire_version: "0.1",
  application: "TIDE Invoicing",
  application_version: "0.1.0",
  schema_version: "0.1",
  principal: "development:api",
  navigation: [
    {
      label: "Master Data",
      items: [
        {
          view: "catalog.Product.browse",
          entity: "catalog.Product",
          label: "Products",
        },
      ],
    },
  ],
  views: {
    "catalog.Product.browse": {
      view: "catalog.Product.browse",
      entity: "catalog.Product",
      label: "Products",
      resource_path: "/api/v1/products",
      query_path: "/api/v1/products/_query",
      identity_field: "id",
      columns: [column, { ...column, name: "name", label: "Name" }],
      search_field: "code",
      search_label: "Code",
      named_filters: [],
      sortable_fields: ["code", "name"],
      page_size: 25,
      operations: ["list", "get"],
      detail_view: null,
    },
  },
  forms: {},
}
