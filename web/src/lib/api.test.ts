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
