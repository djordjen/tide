import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { connectWithToken } from "@/test/connect"

/**
 * Identity administration in the browser.
 *
 * Roles are compiled and this screen never offers to change them; what it
 * changes is who holds them, and whether an account may sign in. The whole
 * screen exists only where the session says both halves hold, so the first
 * test here is that it is absent when they do not.
 *
 * Passwords in this file are fixture strings. Nothing reads one back: the
 * server answers a reset with 204 and an account carries only when its
 * password last changed.
 */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  // The screen lives in the address bar like every other destination, so a
  // test that opened it leaves `?view=_tide.administration` behind for the
  // next one -- which then never sees the browse it starts from.
  window.history.replaceState(null, "", "/")
})

it("offers no identity administration where the session does not", async () => {
  stubServer({ administration: false })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })

  expect(
    screen.queryByRole("button", { name: "Identities" }),
  ).not.toBeInTheDocument()
})

it("lists the accounts with their roles and whether they may sign in", async () => {
  stubServer()
  const user = userEvent.setup()
  await openAdministration(user)

  const accounts = screen.getByRole("table", { name: "Accounts" })
  const admin = within(accounts).getByRole("row", { name: /admin/ })
  const clerk = within(accounts).getByRole("row", { name: /clerk/ })

  expect(admin).toHaveTextContent("administrator")
  expect(clerk).toHaveTextContent("sales_clerk")
  expect(clerk).toHaveTextContent("Disabled")
  expect(admin).toHaveTextContent("Enabled")
})

it("shows what each compiled role grants, and never offers to change it", async () => {
  stubServer()
  const user = userEvent.setup()
  await openAdministration(user)

  const roles = screen.getByRole("region", { name: "Roles" })

  expect(roles).toHaveTextContent("administrator")
  expect(roles).toHaveTextContent("tide.users.administer")
  expect(roles).toHaveTextContent("sales.invoice.post")
  // Nothing in this panel is a control: a compiled role is reported, not
  // offered. A tick box here would be a promise the compiler cannot keep.
  expect(within(roles).queryAllByRole("checkbox")).toHaveLength(0)
  expect(within(roles).queryAllByRole("button")).toHaveLength(0)
})

it("replaces an account's roles with exactly what is checked", async () => {
  const requests = stubServer()
  const user = userEvent.setup()
  await openAdministration(user)

  await user.click(screen.getByRole("button", { name: "clerk" }))
  await user.click(await screen.findByRole("checkbox", { name: "auditor" }))
  await user.click(screen.getByRole("checkbox", { name: "sales_clerk" }))
  await user.click(screen.getByRole("button", { name: "Save roles" }))

  await screen.findByRole("status")
  const patch = requests.find((item) => item.method === "PATCH")
  expect(patch?.url).toMatch(/administration\/users\/clerk$/)
  expect(patch?.body).toEqual({ roles: ["auditor"] })
})

it("says why the last enabled administrator cannot be disabled", async () => {
  stubServer({
    refuse: {
      status: 409,
      code: "conflict",
      message:
        "'admin' is the only enabled account that may administer identities; " +
        "grant another account the administering role first",
    },
  })
  const user = userEvent.setup()
  await openAdministration(user)

  await user.click(screen.getByRole("button", { name: "admin" }))
  await user.click(await screen.findByRole("button", { name: "Disable account" }))

  expect(await screen.findByRole("status")).toHaveTextContent(
    /only enabled account that may administer/,
  )
})

it("creates an account with a password that never comes back", async () => {
  const requests = stubServer()
  const user = userEvent.setup()
  await openAdministration(user)

  await user.click(screen.getByRole("button", { name: "New account" }))
  await user.type(await screen.findByLabelText("Username"), "new.clerk")
  await user.type(screen.getByLabelText("Display name"), "New Clerk")
  await user.type(screen.getByLabelText("Password"), "a fixture passphrase")
  await user.click(screen.getByRole("checkbox", { name: "sales_clerk" }))
  await user.click(screen.getByRole("button", { name: "Create account" }))

  await screen.findByRole("status")
  const created = requests.find((item) =>
    item.url.endsWith("/_tide/administration/users"),
  )
  expect(created?.body).toEqual({
    username: "new.clerk",
    display_name: "New Clerk",
    password: "a fixture passphrase",
    roles: ["sales_clerk"],
  })
  // The form is gone, so the fixture password is not sitting in the DOM.
  expect(screen.queryByLabelText("Password")).not.toBeInTheDocument()
})

it("resets a password and clears the field it was typed into", async () => {
  const requests = stubServer()
  const user = userEvent.setup()
  await openAdministration(user)

  await user.click(screen.getByRole("button", { name: "clerk" }))
  const field = await screen.findByLabelText("New password")
  await user.type(field, "another fixture passphrase")
  await user.click(screen.getByRole("button", { name: "Reset password" }))

  await screen.findByRole("status")
  const reset = requests.find((item) => item.url.endsWith("/password"))
  expect(reset?.body).toEqual({ password: "another fixture passphrase" })
  expect(screen.getByLabelText("New password")).toHaveValue("")
})

it("says when a password last changed without printing a machine instant", async () => {
  // Framework data has no author and therefore no declared format, so the
  // renderer owns the default -- the same neutral one a datetime column falls
  // back to. What it must not do is put a wire value on the screen.
  stubServer()
  const user = userEvent.setup()
  await openAdministration(user)

  await user.click(screen.getByRole("button", { name: "clerk" }))

  const panel = await screen.findByText(/Last changed/)
  expect(panel).toHaveTextContent("2026-08-21 09:05")
  expect(panel.textContent ?? "").not.toMatch(/\d{4}-\d{2}-\d{2}T/)
})

it("is reachable by a principal with no browse views at all", async () => {
  // The administrator role grants `tide.users.administer` and nothing else,
  // so this identity has no navigation. The shell used to answer that with
  // "No available workspaces" and a disconnect button.
  stubServer({ navigation: [], views: {} })
  const user = userEvent.setup()
  renderApp()
  await connectWithToken(user)

  await user.click(await screen.findByRole("button", { name: "Identities" }))

  expect(await screen.findByRole("table", { name: "Accounts" })).toBeVisible()
})

async function openAdministration(user: ReturnType<typeof userEvent.setup>) {
  renderApp()
  await connectWithToken(user)
  await screen.findByRole("row", { name: /INV-2026-0001/ })
  await user.click(screen.getByRole("button", { name: "Identities" }))
  await screen.findByRole("table", { name: "Accounts" })
  // The table exists before its rows arrive; every test here is about them.
  await screen.findByRole("button", { name: "admin" })
}

interface CapturedRequest {
  url: string
  method: string
  body: Record<string, unknown>
}

function stubServer(options?: {
  administration?: boolean
  navigation?: unknown[]
  views?: Record<string, unknown>
  refuse?: { status: number; code: string; message: string }
}): CapturedRequest[] {
  const administration = options?.administration ?? true
  const requests: CapturedRequest[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? "GET"
      if (init?.body && typeof init.body === "string") {
        requests.push({ url, method, body: JSON.parse(init.body) })
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
        return jsonResponse({ ...session, administration })
      }
      if (url.endsWith("/_tide/presentation")) {
        return jsonResponse({
          ...presentation,
          navigation: options?.navigation ?? presentation.navigation,
          views: options?.views ?? presentation.views,
        })
      }
      if (url.endsWith("/_tide/administration/roles")) {
        return jsonResponse({ roles })
      }
      if (url.endsWith("/_tide/administration/users") && method === "GET") {
        return jsonResponse({ users, truncated: false })
      }
      if (
        options?.refuse &&
        method !== "GET" &&
        url.includes("/_tide/administration/")
      ) {
        return jsonResponse(
          { code: options.refuse.code, message: options.refuse.message },
          options.refuse.status,
        )
      }
      if (url.endsWith("/_tide/administration/users") && method === "POST") {
        return jsonResponse({
          ...users[1],
          username: "new.clerk",
          display_name: "New Clerk",
        })
      }
      if (url.endsWith("/password")) {
        return new Response(null, { status: 204 })
      }
      if (url.includes("/_tide/administration/users/")) {
        return jsonResponse({ ...users[1], roles: ["auditor"] })
      }
      if (url.endsWith("/invoices/_query")) {
        return jsonResponse({ records: [invoice], next_cursor: null })
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

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

const invoice = {
  id: 1,
  number: "INV-2026-0001",
  total: "850.00",
}

const roles = [
  { name: "administrator", grants: ["tide.users.administer"] },
  { name: "auditor", grants: ["sales.invoice.read"] },
  {
    name: "sales_clerk",
    grants: ["sales.invoice.read", "sales.invoice.post"],
  },
]

const users = [
  {
    username: "admin",
    display_name: "admin",
    enabled: true,
    roles: ["administrator"],
    created_at: "2026-08-21T09:00:00Z",
    password_changed_at: "2026-08-21T09:00:00Z",
  },
  {
    username: "clerk",
    display_name: "Sales Clerk",
    enabled: false,
    roles: ["sales_clerk"],
    created_at: "2026-08-21T09:05:00Z",
    password_changed_at: "2026-08-21T09:05:00Z",
  },
]

const session = {
  wire_version: "0.1",
  application: "TIDE Invoicing",
  application_version: "0.1.0",
  schema_version: "0.1",
  authentication: "development-bearer",
  principal: "development:api",
  roles: ["administrator"],
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
      columns: [numberColumn],
      search_field: null,
      search_label: null,
      named_filters: [],
      sortable_fields: [],
      summaries: [],
      page_size: 25,
      operations: ["list", "get"],
      detail_view: null,
    },
  },
  forms: {},
}
