import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

beforeEach(() => {
  window.history.replaceState(null, "", "/")
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  window.history.replaceState(null, "", "/")
})

it("puts the open view in the address bar and takes it back", async () => {
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  expect(
    await screen.findByRole("heading", { name: "Products" }),
  ).toBeInTheDocument()
  expect(window.location.search).toBe("")

  await user.click(screen.getByRole("button", { name: "Customers" }))

  expect(
    await screen.findByRole("heading", { name: "Customers" }),
  ).toBeInTheDocument()
  expect(new URLSearchParams(window.location.search).get("view")).toBe(
    "crm.Customer.browse",
  )

  window.history.back()

  expect(
    await screen.findByRole("heading", { name: "Products" }),
  ).toBeInTheDocument()
})

it("opens the view a link names", async () => {
  window.history.replaceState(null, "", "/?view=crm.Customer.browse")
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  expect(
    await screen.findByRole("heading", { name: "Customers" }),
  ).toBeInTheDocument()
})

it("falls back when a link names a view this principal cannot see", async () => {
  // The address bar is a caller like any other. A link shared by someone with
  // more permission must not leave the recipient looking at an empty shell.
  window.history.replaceState(null, "", "/?view=secret.Ledger.browse")
  stubServer()
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  expect(
    await screen.findByRole("heading", { name: "Products" }),
  ).toBeInTheDocument()
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
      if (url.endsWith("/_query")) {
        return jsonResponse({ records: [], next_cursor: null })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )
}

function renderApp() {
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

const capability = {
  operations: ["list", "get"],
  draft_operations: [],
  readable_fields: ["id", "code"],
  writable_fields: [],
  actions: [],
  audit: false,
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
    "catalog.Product": capability,
    "crm.Customer": capability,
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

function browseView(view: string, entity: string, label: string) {
  return {
    view,
    entity,
    label,
    resource_path: `/api/v1/${label.toLowerCase()}`,
    query_path: `/api/v1/${label.toLowerCase()}/_query`,
    identity_field: "id",
    columns: [column],
    search_field: "code",
    search_label: "Code",
    named_filters: [],
    sortable_fields: ["code"],
    page_size: 25,
    operations: ["list", "get"],
    detail_view: null,
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
      label: "Master Data",
      items: [
        {
          view: "catalog.Product.browse",
          entity: "catalog.Product",
          label: "Products",
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
    "catalog.Product.browse": browseView(
      "catalog.Product.browse",
      "catalog.Product",
      "Products",
    ),
    "crm.Customer.browse": browseView(
      "crm.Customer.browse",
      "crm.Customer",
      "Customers",
    ),
  },
  forms: {},
}
