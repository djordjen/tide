import type {
  TideBrowserAuthenticationInfo,
  TideBrowserSessionInfo,
  TideBrowsePresentation,
  TideConnection,
  TideDistinctResult,
  TideFilterInput,
  TideLocalUser,
  TideLocalUserList,
  TidePresentationLookup,
  TidePresentationManifest,
  TidePresentationFormAction,
  TidePresentationReference,
  TideQueryInput,
  TideRecord,
  TideRecordPage,
  TideRecordSnapshot,
  TideReferenceSelectionResult,
  TideReportDocument,
  TideBrowseDownload,
  TideBrowseExportFormat,
  TideReportDownload,
  TideSortInput,
  TideReportExportFormat,
  TidePresentationReport,
  TideRoleCatalogue,
  TideSessionInfo,
  TideAuditHistory,
} from "@/lib/contracts"

const WIRE_VERSION = "0.1"

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

/**
 * Where this deployment's API lives, relative to the origin serving the page.
 *
 * `build_fastapi_app(base_path=...)` is a server-side choice, and the manifest
 * builds every resource, query, selection, report and browser-authentication
 * path from it. A renderer that assumes `/api/v1` therefore cannot talk to a
 * server configured any other way: its own first request 404s, and any path
 * the manifest supplies from outside `/api/` is refused as unsafe. Hosting the
 * whole application under `https://host/tide/` is exactly that case.
 *
 * Read from the build environment because the bundle is static -- Vite inlines
 * the value, so one build belongs to one deployment. That is the bargain the
 * development proxy already makes through `VITE_TIDE_API_TARGET`. It sits
 * below `TideApiError` because a misconfigured build is reported with one.
 */
const API_BASE_PATH = configuredBasePath(
  import.meta.env.VITE_TIDE_BASE_PATH ?? "/api/v1",
)

export class TideApi {
  readonly basePath: string
  readonly token: string | null
  readonly browserAuthentication: TideBrowserAuthenticationInfo | null
  private readonly csrfToken: string | null
  private readonly expiryHandlers = new Set<() => void>()

  constructor(
    token: string,
    basePath = API_BASE_PATH,
    browserSession: {
      authentication: TideBrowserAuthenticationInfo
      csrfToken: string
    } | null = null,
  ) {
    const normalized = token.trim()
    if (!normalized && browserSession === null) {
      throw new TideApiError("Enter the development API token.")
    }
    this.token = browserSession === null ? normalized : null
    this.browserAuthentication = browserSession?.authentication ?? null
    this.csrfToken = browserSession?.csrfToken ?? null
    this.basePath = safeApiPath(basePath)
  }

  /** Be told when the server stops accepting this session, from anywhere.
   *
   * A session used to be checked once, when the application mounted, so a
   * cookie that expired while someone was working surfaced as whatever error
   * box the screen they happened to be on shows. Every authenticated request
   * goes through one method, which makes that the only place worth watching.
   *
   * Returns a function that stops listening, so a component can subscribe for
   * as long as it is mounted and no longer.
   */
  onSessionExpired(handler: () => void): () => void {
    this.expiryHandlers.add(handler)
    return () => {
      this.expiryHandlers.delete(handler)
    }
  }

  static async discoverBrowserAuthentication(
    basePath = API_BASE_PATH,
    signal?: AbortSignal,
  ): Promise<TideBrowserAuthenticationInfo> {
    const normalizedBasePath = safeApiPath(basePath)
    const response = await publicResponse(
      `${normalizedBasePath}/_tide/browser-auth`,
      signal,
    )
    const value = (await safeJson(response)) as Partial<TideBrowserAuthenticationInfo>
    const enabled = value.enabled === true
    const paths = [value.login_path, value.session_path, value.logout_path]
    if (
      typeof value.enabled !== "boolean" ||
      (enabled &&
        !["oidc", "password", "development"].includes(value.mode ?? "")) ||
      (!enabled && value.mode !== null) ||
      (enabled && paths.some((path) => typeof path !== "string")) ||
      (!enabled && paths.some((path) => path !== null))
    ) {
      throw new TideApiError(
        "The server returned an invalid browser authentication contract.",
        { code: "contract_mismatch" },
      )
    }
    for (const path of paths) {
      if (typeof path === "string") {
        safeApiPath(path)
      }
    }
    return value as TideBrowserAuthenticationInfo
  }

  static async loginWithPassword(
    authentication: TideBrowserAuthenticationInfo,
    username: string,
    password: string,
    basePath = API_BASE_PATH,
    signal?: AbortSignal,
  ): Promise<TideApi> {
    if (
      authentication.mode !== "password" ||
      authentication.login_path === null
    ) {
      throw new TideApiError("Local password sign-in is unavailable.", {
        code: "authentication_unavailable",
      })
    }
    let response: Response
    try {
      response = await fetch(safeApiPath(authentication.login_path), {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-TIDE-LOGIN": "password",
        },
        body: JSON.stringify({ username, password }),
        redirect: "error",
        signal,
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw error
      }
      throw new TideApiError("The TIDE application server could not be reached.")
    }
    if (!response.ok) {
      await throwResponseError(response)
    }
    const csrfToken = browserCsrfToken(await safeJson(response))
    return new TideApi("", basePath, {
      authentication,
      csrfToken,
    })
  }

  /**
   * Start a session on a development server, which asks for no credential.
   *
   * Deliberately its own method rather than a branch inside
   * `loginWithPassword`: nothing is being sent, so there is no place for a
   * username or password to be read from, and a reader of either method can
   * see at a glance which one carries a secret.
   */
  static async startDevelopmentSession(
    authentication: TideBrowserAuthenticationInfo,
    basePath = API_BASE_PATH,
    signal?: AbortSignal,
  ): Promise<TideApi> {
    if (
      authentication.mode !== "development" ||
      authentication.login_path === null
    ) {
      throw new TideApiError("Development sign-in is unavailable.", {
        code: "authentication_unavailable",
      })
    }
    let response: Response
    try {
      response = await fetch(safeApiPath(authentication.login_path), {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-TIDE-LOGIN": "development",
        },
        redirect: "error",
        signal,
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw error
      }
      throw new TideApiError("The TIDE application server could not be reached.")
    }
    if (!response.ok) {
      await throwResponseError(response)
    }
    const csrfToken = browserCsrfToken(await safeJson(response))
    return new TideApi("", basePath, {
      authentication,
      csrfToken,
    })
  }

  static async restoreBrowserSession(
    authentication: TideBrowserAuthenticationInfo,
    basePath = API_BASE_PATH,
    signal?: AbortSignal,
  ): Promise<TideApi | null> {
    if (
      !authentication.enabled ||
      authentication.session_path === null ||
      authentication.logout_path === null
    ) {
      return null
    }
    let response: Response
    try {
      response = await fetch(safeApiPath(authentication.session_path), {
        method: "GET",
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
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
    // 204 is "this browser has no session", 401 is "the cookie it sent was
    // rejected". Both mean sign in again; they are separated so that a cold
    // load is not reported as a failure by the console, the server log and
    // anything watching either.
    if (response.status === 204 || response.status === 401) {
      return null
    }
    if (!response.ok) {
      await throwResponseError(response)
    }
    const csrfToken = browserCsrfToken(await safeJson(response))
    return new TideApi("", basePath, {
      authentication,
      csrfToken,
    })
  }

  get usesBrowserSession(): boolean {
    return this.browserAuthentication !== null
  }

  async logout(signal?: AbortSignal): Promise<void> {
    const logoutPath = this.browserAuthentication?.logout_path
    if (logoutPath === null || logoutPath === undefined) {
      return
    }
    await this.authorizedResponse(
      logoutPath,
      { method: "POST" },
      signal,
    )
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

  /**
   * Identity administration.
   *
   * Registered by the server only where TIDE owns the identities, so these
   * are called only when `session.administration` says so -- otherwise the
   * paths are not merely denied, they are not there.
   */
  administrationRoles(signal?: AbortSignal): Promise<TideRoleCatalogue> {
    return this.request<TideRoleCatalogue>(
      `${this.basePath}/_tide/administration/roles`,
      { method: "GET" },
      signal,
    )
  }

  administrationUsers(signal?: AbortSignal): Promise<TideLocalUserList> {
    return this.request<TideLocalUserList>(
      `${this.basePath}/_tide/administration/users`,
      { method: "GET" },
      signal,
    )
  }

  createLocalUser(
    input: {
      username: string
      password: string
      roles: string[]
      display_name?: string
    },
    signal?: AbortSignal,
  ): Promise<TideLocalUser> {
    return this.request<TideLocalUser>(
      `${this.basePath}/_tide/administration/users`,
      { method: "POST", body: JSON.stringify(input) },
      signal,
    )
  }

  updateLocalUser(
    username: string,
    changes: { roles?: string[]; enabled?: boolean },
    signal?: AbortSignal,
  ): Promise<TideLocalUser> {
    return this.request<TideLocalUser>(
      `${this.basePath}/_tide/administration/users/${encodeURIComponent(username)}`,
      { method: "PATCH", body: JSON.stringify(changes) },
      signal,
    )
  }

  /**
   * Replace an account's password. Answers 204, so there is nothing to parse
   * -- and nothing about the credential is ever read back.
   */
  async resetLocalPassword(
    username: string,
    password: string,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.authorizedResponse(
      `${this.basePath}/_tide/administration/users/${encodeURIComponent(username)}/password`,
      { method: "POST", body: JSON.stringify({ password }) },
      signal,
    )
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

  distinct(
    view: TideBrowsePresentation,
    field: string,
    filters: TideFilterInput[],
    signal?: AbortSignal,
  ): Promise<TideDistinctResult> {
    return this.request<TideDistinctResult>(
      `${view.resource_path}/_distinct`,
      {
        method: "POST",
        body: JSON.stringify({ field, filters }),
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

  /**
   * The record's bounded newest-first history. Registered by the server only
   * where the entity declares audit, and called only when the session's
   * capabilities say this principal may view it.
   */
  recordHistory(
    view: TideBrowsePresentation,
    identity: unknown,
    signal?: AbortSignal,
  ): Promise<TideAuditHistory> {
    const segment = encodeURIComponent(String(identity))
    return this.request<TideAuditHistory>(
      `${view.resource_path}/${segment}/_audit`,
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

  buildReport(
    report: TidePresentationReport,
    identity: unknown | null,
    parameters: Record<string, string>,
    signal?: AbortSignal,
  ): Promise<TideReportDocument> {
    const path = reportRequestPath(report, identity)
    return this.request<TideReportDocument>(
      path,
      report.kind === "summary"
        ? { method: "POST", body: JSON.stringify(parameters) }
        : { method: "GET" },
      signal,
    )
  }

  /**
   * Take one browse view away as a file.
   *
   * The same conditions and sort the grid is showing; the server owns how
   * far the walk goes. The two count headers say whether the cap stopped it,
   * so a caller can warn before handing the file over.
   */
  async exportBrowse(
    view: TideBrowsePresentation,
    exportFormat: TideBrowseExportFormat,
    filters: TideFilterInput[],
    sort: TideSortInput[],
    signal?: AbortSignal,
  ): Promise<TideBrowseDownload> {
    if (!(view.export_formats ?? []).includes(exportFormat)) {
      throw new TideApiError(
        `${exportFormat.toUpperCase()} export is unavailable for this view.`,
        { code: "export_format_unavailable" },
      )
    }
    const response = await this.authorizedResponse(
      `${view.resource_path}/_export/${exportFormat}`,
      {
        method: "POST",
        body: JSON.stringify({ view: view.view, filters, sort }),
      },
      signal,
      "*/*",
    )
    const rows = Number(response.headers.get("X-Tide-Export-Rows") ?? 0)
    return {
      blob: await response.blob(),
      filename:
        responseFilename(response.headers.get("Content-Disposition")) ??
        `export.${exportFormat}`,
      rows,
      total: Number(response.headers.get("X-Tide-Export-Total") ?? rows),
    }
  }

  async exportReport(
    report: TidePresentationReport,
    exportFormat: TideReportExportFormat,
    identity: unknown | null,
    parameters: Record<string, string>,
    signal?: AbortSignal,
  ): Promise<TideReportDownload> {
    if (!report.export_formats.includes(exportFormat)) {
      throw new TideApiError(
        `${exportFormat.toUpperCase()} export is unavailable for this report.`,
        { code: "report_format_unavailable" },
      )
    }
    const path = `${reportRequestPath(report, identity)}/exports/${exportFormat}`
    const response = await this.authorizedResponse(
      path,
      report.kind === "summary"
        ? { method: "POST", body: JSON.stringify(parameters) }
        : { method: "GET" },
      signal,
      "*/*",
    )
    return {
      blob: await response.blob(),
      filename:
        responseFilename(response.headers.get("Content-Disposition")) ??
        `report.${exportFormat}`,
    }
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
    const response = await this.authorizedResponse(path, init, signal)
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

  private async authorizedResponse(
    path: string,
    init: RequestInit,
    signal?: AbortSignal,
    accept = "application/json",
  ): Promise<Response> {
    const safePath = safeApiPath(path)
    let response: Response
    try {
      response = await fetch(safePath, {
        ...init,
        cache: "no-store",
        credentials: this.token === null ? "same-origin" : "omit",
        headers: {
          Accept: accept,
          ...(this.token !== null
            ? { Authorization: `Bearer ${this.token}` }
            : {}),
          ...(this.token === null && !safeMethod(init.method)
            ? { "X-TIDE-CSRF": this.csrfToken ?? "" }
            : {}),
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

    if (response.status === 401) {
      // Announced before it is thrown, so the shell can ask for a sign-in
      // rather than leaving whichever screen raised it to explain a session
      // that no longer exists. The throw still happens: the caller that asked
      // for this did not get its answer.
      for (const handler of [...this.expiryHandlers]) {
        handler()
      }
    }
    if (!response.ok) {
      await throwResponseError(response)
    }
    return response
  }
}

function browserCsrfToken(value: unknown): string {
  const session = value as Partial<TideBrowserSessionInfo>
  if (
    typeof session.csrf_token !== "string" ||
    session.csrf_token.length < 32
  ) {
    throw new TideApiError(
      "The server returned an invalid browser session contract.",
      { code: "contract_mismatch" },
    )
  }
  return session.csrf_token
}

async function publicResponse(
  path: string,
  signal?: AbortSignal,
): Promise<Response> {
  let response: Response
  try {
    response = await fetch(safeApiPath(path), {
      method: "GET",
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      redirect: "error",
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error
    }
    throw new TideApiError("The TIDE application server could not be reached.")
  }
  if (!response.ok) {
    await throwResponseError(response)
  }
  return response
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    throw new TideApiError("The server returned an invalid JSON response.", {
      status: response.status,
      code: "invalid_response",
      correlationId: response.headers.get("X-Correlation-ID"),
    })
  }
}

async function throwResponseError(response: Response): Promise<never> {
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

function safeMethod(method: string | undefined): boolean {
  return ["GET", "HEAD", "OPTIONS"].includes((method ?? "GET").toUpperCase())
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

/** Validate this build's own API base path, and say so if it is unusable. */
function configuredBasePath(value: string): string {
  const normalized = value.replace(/\/+$/, "")
  if (!wellFormedPath(normalized)) {
    throw new TideApiError(
      `VITE_TIDE_BASE_PATH must be an absolute path, not ${value.trim() || "an empty value"}.`,
      { code: "configuration_error" },
    )
  }
  return normalized
}

/**
 * Refuse a path the server supplied that is not this deployment's API.
 *
 * The manifest names every path the renderer will call, so this is the one
 * place a compromised or confused server could aim the browser somewhere else
 * with the session's credentials attached. Anchoring on the configured base
 * path rather than a literal `/api/` keeps that narrow whatever the deployment
 * chose, and refusing a `..` segment stops the anchor being walked back out of
 * -- `fetch` resolves the path before sending it, so a prefix test alone reads
 * a path the server never gets.
 */
function safeApiPath(path: string): string {
  const normalized = path.replace(/\/+$/, "")
  if (
    !wellFormedPath(normalized) ||
    (normalized !== API_BASE_PATH &&
      !normalized.startsWith(`${API_BASE_PATH}/`))
  ) {
    throw new TideApiError(
      "The server supplied an unsafe API resource path.",
      { code: "contract_mismatch" },
    )
  }
  return normalized
}

function wellFormedPath(path: string): boolean {
  return (
    path.startsWith("/") &&
    !path.startsWith("//") &&
    !path.includes("\\") &&
    !path.split("/").includes("..")
  )
}

function reportRequestPath(
  report: TidePresentationReport,
  identity: unknown | null,
): string {
  if (report.kind === "summary") {
    return report.resource_path
  }
  if (identity === null || identity === undefined) {
    throw new TideApiError("Select a record before previewing this report.", {
      code: "report_record_required",
    })
  }
  return `${report.resource_path}/records/${encodeURIComponent(String(identity))}`
}

function responseFilename(disposition: string | null): string | null {
  if (!disposition) {
    return null
  }
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
  if (encoded) {
    try {
      return decodeURIComponent(encoded)
    } catch {
      return null
    }
  }
  return /filename="([^"]+)"/i.exec(disposition)?.[1] ?? null
}


/**
 * Present an unknown failure as something the surface can render.
 *
 * A rejected mutation may carry anything at all; every caller wants a
 * `TideApiError` with a message worth showing.
 */
export function actionApiError(
  error: unknown,
  fallback: string,
): TideApiError {
  return error instanceof TideApiError
    ? error
    : new TideApiError(fallback)
}
