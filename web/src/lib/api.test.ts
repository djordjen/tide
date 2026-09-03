import { afterEach, describe, expect, it, vi } from "vitest"

import { TideApi } from "@/lib/api"
import type {
  TideBrowsePresentation,
  TideQueryInput,
} from "@/lib/contracts"

const emptyQuery: TideQueryInput = {
  filters: [],
  sort: [],
  limit: 50,
  cursor: null,
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.resetModules()
})

describe("the API path a build talks to", () => {
  it("is the server's own default when nothing says otherwise", () => {
    expect(new TideApi("token").basePath).toBe("/api/v1")
  })

  it("follows the deployment that moved it", async () => {
    // `build_fastapi_app(base_path=...)` is a server-side choice and the
    // manifest builds every path from it, so a renderer nailed to `/api/v1`
    // cannot talk to a server hosted under `https://host/tide/` at all: the
    // first request 404s and every manifest path is refused as unsafe.
    const requested = await withBasePath("/tide/api/v1", async (api) => {
      const paths: string[] = []
      vi.stubGlobal(
        "fetch",
        vi.fn((url: string) => {
          paths.push(url)
          return Promise.resolve(
            new Response("{}", {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          )
        }),
      )
      await api.connect().catch(() => undefined)
      return paths
    })

    expect(requested.sort()).toEqual([
      "/tide/api/v1/_tide/presentation",
      "/tide/api/v1/_tide/session",
    ])
  })

  it("will not load a build whose configured path is not a path", async () => {
    vi.stubEnv("VITE_TIDE_BASE_PATH", "https://elsewhere.test/api")
    vi.resetModules()

    await expect(import("@/lib/api")).rejects.toThrow(
      /VITE_TIDE_BASE_PATH/,
    )
  })
})

describe("a path the server supplied", () => {
  // Both of these stub a `fetch` that answers happily. Without it the request
  // fails at the transport instead, and the test passes whether the path was
  // refused or merely unreachable -- which is how the `..` case first read as
  // covered while the check that refuses it was deleted.

  it("is refused when it leaves the configured API", async () => {
    // The manifest names every path the renderer will call, so this is where
    // a compromised or confused server could aim the browser somewhere else
    // with the session's credentials attached.
    const fetched = stubHappyFetch()

    await expect(
      new TideApi("token").query(
        viewWithQueryPath("/admin/users/_query"),
        emptyQuery,
      ),
    ).rejects.toThrow("unsafe API resource path")
    expect(fetched).not.toHaveBeenCalled()
  })

  it("is refused when it walks back out with ..", async () => {
    // `fetch` resolves the path before sending it, so `/api/v1/../admin`
    // reaches `/admin` and a prefix test alone reads a path that is never
    // requested.
    const fetched = stubHappyFetch()

    await expect(
      new TideApi("token").query(
        viewWithQueryPath("/api/v1/../../admin/users"),
        emptyQuery,
      ),
    ).rejects.toThrow("unsafe API resource path")
    expect(fetched).not.toHaveBeenCalled()
  })

  it("is allowed inside the configured API of a moved deployment", async () => {
    const requested = await withBasePath("/tide/api/v1", async (api) => {
      const paths: string[] = []
      vi.stubGlobal(
        "fetch",
        vi.fn((url: string) => {
          paths.push(url)
          return Promise.resolve(
            new Response('{"records":[],"next_cursor":null}', {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          )
        }),
      )
      await api.query(
        viewWithQueryPath("/tide/api/v1/customers/_query"),
        emptyQuery,
      )
      return paths
    })

    expect(requested).toEqual(["/tide/api/v1/customers/_query"])
  })
})

/** A `fetch` that answers every request, so only a refusal can fail a call. */
function stubHappyFetch() {
  const fetched = vi.fn(() =>
    Promise.resolve(
      new Response('{"records":[],"next_cursor":null}', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )
  vi.stubGlobal("fetch", fetched)
  return fetched
}

/** Load a second copy of the client as a build configured for `basePath`. */
async function withBasePath<T>(
  basePath: string,
  run: (api: TideApi) => Promise<T>,
): Promise<T> {
  vi.stubEnv("VITE_TIDE_BASE_PATH", basePath)
  vi.resetModules()
  const module = await import("@/lib/api")
  const api = new module.TideApi("token")
  expect(api.basePath).toBe(basePath)
  return await run(api as unknown as TideApi)
}

function viewWithQueryPath(queryPath: string): TideBrowsePresentation {
  return { query_path: queryPath } as unknown as TideBrowsePresentation
}

describe("a lookup dialog's query", () => {
  // The manifest's `source` is the marching order: echo it verbatim so the
  // server narrows the rows by the edge's declared lookup filter. Its
  // absence is equally load-bearing -- an older server forbids unknown
  // keys, so a contract without a source must produce the body the client
  // always sent.
  const lookupContract = (
    source: { entity: string; field: string } | null,
  ) => ({
    view: "catalog.Product.lookup",
    title: "Select Product",
    owner_entity: "sales.InvoiceLine",
    field: "product",
    target_entity: "catalog.Product",
    resource_path: "/api/v1/products",
    query_path: "/api/v1/products/_query",
    selection_path: "/api/v1/_tide/reference-selection",
    identity_field: "id",
    columns: [
      {
        name: "code",
        label: "Code",
        field_type: "string",
        alignment: "left" as const,
        format: null,
        format_options: null,
        target_entity: null,
        reference: null,
        values: [],
      },
    ],
    search_fields: ["code"],
    page_size: 20,
    operations: ["list" as const, "get" as const],
    create_view: null,
    source,
  })

  const captureQueryBodies = () => {
    const bodies: Record<string, unknown>[] = []
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>)
        return Promise.resolve(
          new Response('{"records":[],"next_cursor":null}', {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
      }),
    )
    return bodies
  }

  it("echoes the manifest's source on every query it issues", async () => {
    const bodies = captureQueryBodies()

    await new TideApi("token").searchLookup(
      lookupContract({ entity: "sales.InvoiceLine", field: "product" }),
      "",
    )

    expect(bodies).toHaveLength(1)
    expect(bodies[0].lookup_source).toEqual({
      entity: "sales.InvoiceLine",
      field: "product",
    })
  })

  it("sends no lookup_source key when the manifest names none", async () => {
    const bodies = captureQueryBodies()

    await new TideApi("token").searchLookup(lookupContract(null), "")

    expect(bodies).toHaveLength(1)
    expect("lookup_source" in bodies[0]).toBe(false)
  })
})

describe("a mass update request", () => {
  // One door for the whole selection: typed changes beside identity and
  // version assertions spelled the way If-Match spells them, and the
  // acknowledgement as the repeatable query parameter it is everywhere.
  const massUpdateContract = {
    path: "/api/v1/jobs/_mass-update",
    version_field: "version",
    limit: 1000,
  }

  const captureMassUpdate = (payload: string) => {
    const calls: { url: string; body: Record<string, unknown> }[] = []
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string | URL, init?: RequestInit) => {
        calls.push({
          url: String(url),
          body: JSON.parse(String(init?.body)) as Record<string, unknown>,
        })
        return Promise.resolve(
          new Response(payload, {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
      }),
    )
    return calls
  }

  it("sends changes, targets and acknowledgements the wire way", async () => {
    const calls = captureMassUpdate(
      '{"outcomes":[{"identity":1,"status":"updated","code":null,' +
        '"message":null,"issues":[],"notices":[],"version":2}],' +
        '"updated":1,"refused":0}',
    )

    const result = await new TideApi("token").massUpdate(
      massUpdateContract,
      { priority: "high" },
      [
        { identity: 1, version: 3 },
        { identity: 9, version: "null" },
      ],
      ["heavy_hours"],
    )

    expect(calls).toHaveLength(1)
    expect(calls[0].url).toContain(
      "/api/v1/jobs/_mass-update?acknowledge_warnings=heavy_hours",
    )
    expect(calls[0].body).toEqual({
      changes: { priority: "high" },
      targets: [
        { identity: 1, version: 3 },
        { identity: 9, version: "null" },
      ],
    })
    expect(result.updated).toBe(1)
    expect(result.outcomes[0].version).toBe(2)
  })

  it("sends no acknowledgement parameter when there is nothing to say", async () => {
    const calls = captureMassUpdate(
      '{"outcomes":[],"updated":0,"refused":0}',
    )

    await new TideApi("token").massUpdate(massUpdateContract, { a: 1 }, [
      { identity: 1 },
    ])

    expect(calls[0].url).not.toContain("acknowledge_warnings")
    expect(calls[0].body).toEqual({
      changes: { a: 1 },
      targets: [{ identity: 1 }],
    })
  })
})
