import type {
  TideBrowsePresentation,
  TideConnection,
  TidePresentationManifest,
  TidePresentationReference,
  TideQueryInput,
  TideRecord,
  TideRecordPage,
  TideSessionInfo,
} from "@/lib/contracts"

const WIRE_VERSION = "0.1"
const DEFAULT_BASE_PATH = "/api/v1"

interface TideErrorEnvelope {
  code?: unknown
  message?: unknown
}

export class TideApiError extends Error {
  readonly status: number
  readonly code: string
  readonly correlationId: string | null

  constructor(
    message: string,
    {
      status = 0,
      code = "transport_error",
      correlationId = null,
    }: {
      status?: number
      code?: string
      correlationId?: string | null
    } = {},
  ) {
    super(message)
    this.name = "TideApiError"
    this.status = status
    this.code = code
    this.correlationId = correlationId
  }
}

export class TideApi {
  readonly basePath: string
  readonly token: string

  constructor(token: string, basePath = DEFAULT_BASE_PATH) {
    const normalized = token.trim()
    if (!normalized) {
      throw new TideApiError("Enter the development API token.")
    }
    this.token = normalized
    this.basePath = safeApiPath(basePath)
  }

  async connect(signal?: AbortSignal): Promise<TideConnection> {
    const [session, presentation] = await Promise.all([
      this.request<TideSessionInfo>(
        `${this.basePath}/_tide/session`,
        { method: "GET" },
        signal,
      ),
      this.request<TidePresentationManifest>(
        `${this.basePath}/_tide/presentation`,
        { method: "GET" },
        signal,
      ),
    ])
    if (
      session.wire_version !== WIRE_VERSION ||
      presentation.wire_version !== WIRE_VERSION
    ) {
      throw new TideApiError(
        `This Web renderer requires TIDE wire version ${WIRE_VERSION}.`,
        { code: "contract_mismatch" },
      )
    }
    const sessionContract = [
      session.application,
      session.application_version,
      session.schema_version,
      session.principal,
    ]
    const presentationContract = [
      presentation.application,
      presentation.application_version,
      presentation.schema_version,
      presentation.principal,
    ]
    if (
      sessionContract.some(
        (value, index) => value !== presentationContract[index],
      )
    ) {
      throw new TideApiError(
        "The presentation contract does not match the authenticated session.",
        { code: "contract_mismatch" },
      )
    }
    return { session, presentation }
  }

  query(
    view: TideBrowsePresentation,
    query: TideQueryInput,
    signal?: AbortSignal,
  ): Promise<TideRecordPage> {
    return this.request<TideRecordPage>(
      view.query_path,
      {
        method: "POST",
        body: JSON.stringify(query),
      },
      signal,
    )
  }

  getReference(
    reference: TidePresentationReference,
    identity: unknown,
    signal?: AbortSignal,
  ): Promise<TideRecord> {
    const segment = encodeURIComponent(String(identity))
    return this.request<TideRecord>(
      `${reference.resource_path}/${segment}`,
      { method: "GET" },
      signal,
    )
  }

  private async request<T>(
    path: string,
    init: RequestInit,
    signal?: AbortSignal,
  ): Promise<T> {
    const safePath = safeApiPath(path)
    let response: Response
    try {
      response = await fetch(safePath, {
        ...init,
        cache: "no-store",
        credentials: "omit",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${this.token}`,
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
        redirect: "error",
        signal,
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw error
      }
      throw new TideApiError(
        "The TIDE application server could not be reached.",
      )
    }

    if (!response.ok) {
      const correlationId = response.headers.get("X-Correlation-ID")
      let envelope: TideErrorEnvelope = {}
      try {
        envelope = (await response.json()) as TideErrorEnvelope
      } catch {
        // The framework deliberately does not copy arbitrary response text.
      }
      const code =
        typeof envelope.code === "string" ? envelope.code : "request_failed"
      const message =
        typeof envelope.message === "string"
          ? envelope.message
          : `The server rejected the request (HTTP ${response.status}).`
      throw new TideApiError(message, {
        status: response.status,
        code,
        correlationId,
      })
    }

    try {
      return (await response.json()) as T
    } catch {
      throw new TideApiError("The server returned an invalid JSON response.", {
        status: response.status,
        code: "invalid_response",
        correlationId: response.headers.get("X-Correlation-ID"),
      })
    }
  }
}

function safeApiPath(path: string): string {
  if (
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path.includes("\\") ||
    !path.startsWith("/api/")
  ) {
    throw new TideApiError(
      "The server supplied an unsafe API resource path.",
      { code: "contract_mismatch" },
    )
  }
  return path.replace(/\/+$/, "")
}
