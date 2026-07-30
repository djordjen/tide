import type {
  TideBrowsePresentation,
  TideConnection,
  TidePresentationLookup,
  TidePresentationManifest,
  TidePresentationFormAction,
  TidePresentationReference,
  TideQueryInput,
  TideRecord,
  TideRecordPage,
  TideRecordSnapshot,
  TideReferenceSelectionResult,
  TideSessionInfo,
} from "@/lib/contracts"

const WIRE_VERSION = "0.1"
const DEFAULT_BASE_PATH = "/api/v1"

interface TideErrorEnvelope {
  code?: unknown
  message?: unknown
  issues?: unknown
}

export interface TideValidationIssue {
  rule: string
  message: string
  fields: string[]
  severity: string
}

export class TideApiError extends Error {
  readonly status: number
  readonly code: string
  readonly correlationId: string | null
  readonly issues: TideValidationIssue[]

  constructor(
    message: string,
    {
      status = 0,
      code = "transport_error",
      correlationId = null,
      issues = [],
    }: {
      status?: number
      code?: string
      correlationId?: string | null
      issues?: TideValidationIssue[]
    } = {},
  ) {
    super(message)
    this.name = "TideApiError"
    this.status = status
    this.code = code
    this.correlationId = correlationId
    this.issues = issues
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

  getRecord(
    view: TideBrowsePresentation,
    identity: unknown,
    signal?: AbortSignal,
  ): Promise<TideRecordSnapshot> {
    const segment = encodeURIComponent(String(identity))
    return this.recordRequest(
      `${view.resource_path}/${segment}`,
      { method: "GET" },
      signal,
    )
  }

  createRecord(
    view: TideBrowsePresentation,
    values: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<TideRecordSnapshot> {
    return this.recordRequest(
      view.resource_path,
      {
        method: "POST",
        body: JSON.stringify(values),
      },
      signal,
    )
  }

  updateRecord(
    view: TideBrowsePresentation,
    identity: unknown,
    values: Record<string, unknown>,
    etag: string | null,
    signal?: AbortSignal,
  ): Promise<TideRecordSnapshot> {
    const segment = encodeURIComponent(String(identity))
    return this.recordRequest(
      `${view.resource_path}/${segment}`,
      {
        method: "PATCH",
        body: JSON.stringify(values),
        headers: etag ? { "If-Match": etag } : undefined,
      },
      signal,
    )
  }

  executeAction(
    view: TideBrowsePresentation,
    identity: unknown,
    action: TidePresentationFormAction,
    etag: string | null,
    idempotencyKey: string | null,
    signal?: AbortSignal,
  ): Promise<TideRecordSnapshot> {
    const identitySegment = encodeURIComponent(String(identity))
    const actionSegment = encodeURIComponent(action.name)
    return this.recordRequest(
      `${view.resource_path}/${identitySegment}/actions/${actionSegment}`,
      {
        method: "POST",
        body: JSON.stringify({}),
        headers: {
          ...(etag ? { "If-Match": etag } : {}),
          ...(idempotencyKey
            ? { "Idempotency-Key": idempotencyKey }
            : {}),
        },
      },
      signal,
    )
  }

  async searchLookup(
    lookup: TidePresentationLookup,
    searchText: string,
    signal?: AbortSignal,
  ): Promise<TideRecord[]> {
    const candidate = searchText.trim()
    const fields = candidate ? lookup.search_fields : [null]
    const records = new Map<string, TideRecord>()
    for (const field of fields) {
      const page = await this.request<TideRecordPage>(
        lookup.query_path,
        {
          method: "POST",
          body: JSON.stringify({
            filters:
              field === null
                ? []
                : [
                    {
                      field,
                      operator: "icontains",
                      value: candidate,
                    },
                  ],
            sort: [
              {
                field: lookup.search_fields[0],
                descending: false,
              },
            ],
            limit: lookup.page_size,
            cursor: null,
          } satisfies TideQueryInput),
        },
        signal,
      )
      for (const record of page.records) {
        const identity = record[lookup.identity_field]
        if (identity === null || identity === undefined) {
          continue
        }
        records.set(String(identity), record)
        if (records.size >= lookup.page_size) {
          return [...records.values()]
        }
      }
    }
    return [...records.values()]
  }

  createLookupRecord(
    lookup: TidePresentationLookup,
    values: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<TideRecordSnapshot> {
    return this.recordRequest(
      lookup.resource_path,
      {
        method: "POST",
        body: JSON.stringify(values),
      },
      signal,
    )
  }

  async applyReferenceSelection(
    lookup: TidePresentationLookup,
    values: Record<string, unknown>,
    identity: unknown,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    const result = await this.request<TideReferenceSelectionResult>(
      lookup.selection_path,
      {
        method: "POST",
        body: JSON.stringify({
          entity: lookup.owner_entity,
          field: lookup.field,
          values,
          identity,
        }),
      },
      signal,
    )
    return result.values
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
    return (await this.requestWithResponse<T>(path, init, signal)).data
  }

  private async recordRequest(
    path: string,
    init: RequestInit,
    signal?: AbortSignal,
  ): Promise<TideRecordSnapshot> {
    const result = await this.requestWithResponse<TideRecord>(
      path,
      init,
      signal,
    )
    return {
      record: result.data,
      etag: result.response.headers.get("ETag"),
    }
  }

  private async requestWithResponse<T>(
    path: string,
    init: RequestInit,
    signal?: AbortSignal,
  ): Promise<{ data: T; response: Response }> {
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
        issues: safeValidationIssues(envelope.issues),
      })
    }

    try {
      return {
        data: (await response.json()) as T,
        response,
      }
    } catch {
      throw new TideApiError("The server returned an invalid JSON response.", {
        status: response.status,
        code: "invalid_response",
        correlationId: response.headers.get("X-Correlation-ID"),
      })
    }
  }
}

function safeValidationIssues(value: unknown): TideValidationIssue[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    if (
      typeof item !== "object" ||
      item === null ||
      typeof Reflect.get(item, "rule") !== "string" ||
      typeof Reflect.get(item, "message") !== "string"
    ) {
      return []
    }
    const fields = Reflect.get(item, "fields")
    const severity = Reflect.get(item, "severity")
    return [
      {
        rule: Reflect.get(item, "rule") as string,
        message: Reflect.get(item, "message") as string,
        fields: Array.isArray(fields)
          ? fields.filter((field): field is string => typeof field === "string")
          : [],
        severity: typeof severity === "string" ? severity : "error",
      },
    ]
  })
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
