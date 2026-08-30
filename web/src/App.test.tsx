import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { TideApi } from "@/lib/api"
import { connectWithToken, TOKEN } from "@/test/connect"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

it("connects through the safe manifest and renders capability navigation", async () => {
  const requests: Array<{ url: string; authorization: string | null }> = []
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
  await connectWithToken(user)

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
        `Bearer ${TOKEN}`,
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

it("restores and ends a server-held browser session without exposing tokens", async () => {
  const requests: Array<{
    url: string
    method: string
    authorization: string | null
    csrf: string | null
    credentials: RequestCredentials | undefined
  }> = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const headers = new Headers(init?.headers)
      requests.push({
        url,
        method: init?.method ?? "GET",
        authorization: headers.get("Authorization"),
        csrf: headers.get("X-TIDE-CSRF"),
        credentials: init?.credentials,
      })
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: true,
          mode: "oidc",
          login_path: "/api/v1/_tide/browser-auth/login",
          session_path: "/api/v1/_tide/browser-auth/session",
          logout_path: "/api/v1/_tide/browser-auth/logout",
        })
      }
      if (url.endsWith("/_tide/browser-auth/session")) {
        return jsonResponse({
          csrf_token: "browser-csrf-token-that-is-long-enough",
        })
      }
      if (url.endsWith("/_tide/browser-auth/logout")) {
        return new Response(null, { status: 204 })
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse({
          ...session,
          authentication: "oidc-jwt",
          principal: "oidc:user-123",
        })
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse({ ...presentation, principal: "oidc:user-123" })
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({ records: [], next_cursor: null })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  renderApp()
  expect(
    await screen.findByRole("heading", { name: "Invoices" }),
  ).toBeInTheDocument()
  const authentication = await TideApi.discoverBrowserAuthentication()
  const restoredApi = await TideApi.restoreBrowserSession(authentication)
  expect(restoredApi).not.toBeNull()
  await restoredApi?.logout()
  await waitFor(() => {
    expect(
      requests.find((request) => request.url.endsWith("/browser-auth/logout")),
    ).toMatchObject({
      method: "POST",
      csrf: "browser-csrf-token-that-is-long-enough",
    })
  })

  const protectedRequests = requests.filter(
    (request) => !request.url.endsWith("/_tide/browser-auth"),
  )
  expect(
    protectedRequests.every(
      (request) =>
        request.authorization === null && request.credentials === "same-origin",
    ),
  ).toBe(true)
})

it("offers identity-provider sign-in when no browser session exists", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: true,
          mode: "oidc",
          login_path: "/api/v1/_tide/browser-auth/login",
          session_path: "/api/v1/_tide/browser-auth/session",
          logout_path: "/api/v1/_tide/browser-auth/logout",
        })
      }
      if (url.endsWith("/_tide/browser-auth/session")) {
        return new Response(
          JSON.stringify({
            code: "unauthorized",
            message: "authentication required",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  renderApp()
  expect(
    await screen.findByRole("heading", {
      name: "Sign in to your application",
    }),
  ).toBeInTheDocument()
  expect(
    screen.getByRole("link", { name: "Sign in securely" }),
  ).toHaveAttribute("href", "/api/v1/_tide/browser-auth/login")
})

it("signs in with a local username and password without retaining credentials", async () => {
  const requests: Array<{
    url: string
    method: string
    loginProof: string | null
    body: string | null
  }> = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const headers = new Headers(init?.headers)
      requests.push({
        url,
        method: init?.method ?? "GET",
        loginProof: headers.get("X-TIDE-LOGIN"),
        body: typeof init?.body === "string" ? init.body : null,
      })
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: true,
          mode: "password",
          login_path: "/api/v1/_tide/browser-auth/login",
          session_path: "/api/v1/_tide/browser-auth/session",
          logout_path: "/api/v1/_tide/browser-auth/logout",
        })
      }
      if (url.endsWith("/_tide/browser-auth/session")) {
        return new Response(
          JSON.stringify({
            code: "unauthorized",
            message: "authentication required",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/_tide/browser-auth/login")) {
        return jsonResponse({
          csrf_token: "local-csrf-token-that-is-long-enough",
        })
      }
      if (url.endsWith("/_tide/session")) {
        return jsonResponse({
          ...session,
          authentication: "local-password",
          principal: "local:alice",
        })
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse({ ...presentation, principal: "local:alice" })
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({ records: [], next_cursor: null })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )

  const user = userEvent.setup()
  renderApp()
  await user.type(await screen.findByLabelText("Username"), "alice")
  await user.type(screen.getByLabelText("Password"), "secret passphrase")
  await user.click(screen.getByRole("button", { name: "Sign in" }))

  expect(
    await screen.findByRole("heading", { name: "Invoices" }),
  ).toBeInTheDocument()
  const login = requests.find((request) =>
    request.url.endsWith("/_tide/browser-auth/login"),
  )
  expect(login).toMatchObject({
    method: "POST",
    loginProof: "password",
  })
  expect(login?.body).toBe(
    JSON.stringify({ username: "alice", password: "secret passphrase" }),
  )
  expect(window.localStorage.length).toBe(0)
})

it("opens a development server with no credential to type", async () => {
  const requests: Array<{
    url: string
    method: string
    loginProof: string | null
    body: string | null
  }> = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const headers = new Headers(init?.headers)
      requests.push({
        url,
        method: init?.method ?? "GET",
        loginProof: headers.get("X-TIDE-LOGIN"),
        body: typeof init?.body === "string" ? init.body : null,
      })
      if (url.endsWith("/_tide/browser-auth")) {
        return jsonResponse({
          enabled: true,
          mode: "development",
          login_path: "/api/v1/_tide/browser-auth/login",
          session_path: "/api/v1/_tide/browser-auth/session",
          logout_path: "/api/v1/_tide/browser-auth/logout",
        })
      }
      if (url.endsWith("/_tide/browser-auth/session")) {
        return new Response(
          JSON.stringify({
            code: "unauthorized",
            message: "authentication required",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/_tide/browser-auth/login")) {
        return jsonResponse({
          csrf_token: "browser-csrf-token-that-is-long-enough",
        })
      }
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

  renderApp()
  // The screen says what it is. A server that asks for nothing looks exactly
  // like a broken one otherwise, and this is the mode where a reader most
  // needs to know which they are looking at.
  expect(
    await screen.findByRole("heading", { name: "Open a development server" }),
  ).toBeInTheDocument()
  expect(screen.queryByLabelText("Application token")).not.toBeInTheDocument()

  await userEvent.click(
    screen.getByRole("button", { name: "Open without signing in" }),
  )
  expect(
    await screen.findByRole("heading", { name: "Invoices" }),
  ).toBeInTheDocument()

  const login = requests.find((request) =>
    request.url.endsWith("/_tide/browser-auth/login"),
  )
  // Nothing is sent, and the custom header still is: a cross-site form can
  // post to this path but cannot set that.
  expect(login).toMatchObject({
    method: "POST",
    loginProof: "development",
    body: null,
  })
})

it("says which build of the application is on the screen", async () => {
  // A support conversation starts with which version you are looking at, and
  // the manifest already carries it -- so the sidebar can answer without
  // anyone opening a terminal.
  stubManifest(presentation)

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  expect(
    await screen.findByText("TIDE Invoicing · v0.1.0"),
  ).toBeInTheDocument()
})

it("offers a filter once the navigation outgrows the sidebar", async () => {
  // Two views are a list you read. Eleven are a list you hunt through.
  stubManifest(wideNavigation)

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  const navigation = await screen.findByRole("navigation", {
    name: "Application navigation",
  })
  expect(navigation).toHaveTextContent("Price Lists")

  await user.type(screen.getByLabelText("Filter navigation"), "cust")
  expect(navigation).toHaveTextContent("Customers")
  expect(navigation).not.toHaveTextContent("Price Lists")
  // A group whose every item was filtered out leaves with them: a heading
  // over nothing is a promise the list is not keeping.
  expect(navigation).not.toHaveTextContent("Sales")

  await user.clear(screen.getByLabelText("Filter navigation"))
  await user.type(screen.getByLabelText("Filter navigation"), "zzz")
  expect(navigation).toHaveTextContent("No matching views")
})

it("keeps the filter away from a navigation you can read at a glance", async () => {
  stubManifest(presentation)

  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("navigation", { name: "Application navigation" })

  expect(
    screen.queryByLabelText("Filter navigation"),
  ).not.toBeInTheDocument()
})

/** The three calls every screen needs before it can render anything. */
function stubManifest(manifest: unknown) {
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
        return jsonResponse(manifest)
      }
      if (url.includes("/_query")) {
        return jsonResponse({ records: [], next_cursor: null })
      }
      throw new Error(`Unexpected URL ${url}`)
    }),
  )
}

function renderApp() {
  // The suite predates Home: land where the old default landed.
  window.history.replaceState(null, "", "/?view=sales.Invoice.browse")
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

/** The reference application's shape: one working view, then the reference
 *  data behind it -- more names than a sidebar shows without scrolling. */
const MASTER_DATA = [
  "Customers",
  "Products",
  "Suppliers",
  "Warehouses",
  "Currencies",
  "Tax Codes",
  "Payment Terms",
  "Countries",
  "Regions",
  "Price Lists",
]

const wideNavigation = {
  ...presentation,
  navigation: [
    presentation.navigation[0],
    {
      label: "Master Data",
      items: MASTER_DATA.map((label) => ({
        view: `master.${label.replace(/ /g, "")}.browse`,
        entity: `master.${label.replace(/ /g, "")}`,
        label,
      })),
    },
  ],
  views: {
    "sales.Invoice.browse": invoiceView,
    ...Object.fromEntries(
      MASTER_DATA.map((label) => {
        const name = label.replace(/ /g, "")
        return [
          `master.${name}.browse`,
          {
            ...invoiceView,
            view: `master.${name}.browse`,
            entity: `master.${name}`,
            label,
            resource_path: `/api/v1/${name.toLowerCase()}`,
            query_path: `/api/v1/${name.toLowerCase()}/_query`,
          },
        ]
      }),
    ),
  },
}
