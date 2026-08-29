export type TideOperation = "list" | "get" | "create" | "update" | "delete"
export type TideAlignment = "left" | "center" | "right"
export type TideReportKind = "record" | "summary"
export type TideReportExportFormat = "csv" | "html" | "pdf" | "xlsx"
export type TideBrowseExportFormat = "csv" | "xlsx"
export type TideFilterOperator =
  | "eq"
  | "ne"
  | "lt"
  | "lte"
  | "gt"
  | "gte"
  | "contains"
  | "icontains"
  | "in"

export interface TideFilterInput {
  field: string
  operator: TideFilterOperator
  value: unknown
}

export interface TideSortInput {
  field: string
  descending: boolean
}

export interface TideEntityCapabilities {
  operations: TideOperation[]
  draft_operations: Array<"create" | "update">
  readable_fields: string[]
  writable_fields: string[]
  actions: string[]
  audit: boolean
}

export type TideAuditKind = "action" | "record"
export type TideRecordAuditOperation = "create" | "update" | "delete"
export type TideAuditValueMode = "recorded" | "field_only" | "redacted"
export type TideAuditOutcome =
  | "started"
  | "succeeded"
  | "replayed"
  | "conflict"
  | "failed"

/**
 * One changed field in a record audit event. The wire already decided what
 * may be shown: values arrive only in `recorded` mode, and a renderer never
 * reconstructs what redaction withheld.
 */
export interface TideAuditFieldChange {
  field: string
  before_present: boolean
  after_present: boolean
  value_mode: TideAuditValueMode
  before?: unknown
  after?: unknown
}

export interface TideAuditEvent {
  event_id: string
  entity: string
  kind: TideAuditKind
  action: string | null
  operation: TideRecordAuditOperation | null
  identity: unknown
  principal: string
  channel: string
  correlation_id: string
  started_at: string
  outcome: TideAuditOutcome | null
  finished_at: string | null
  error_code: string | null
  source: "user" | "action" | "system" | null
  changes: TideAuditFieldChange[]
}

/** Bounded newest-first history for one authorized record. */
export interface TideAuditHistory {
  wire_version: "0.1"
  entity: string
  identity: unknown
  events: TideAuditEvent[]
}

/** One record a search found: its identity, and how it names itself. */
export interface TideSearchHit {
  identity: unknown
  display: string
}

/** Every hit one entity contributed, bounded and saying so. */
export interface TideSearchGroup {
  entity: string
  label: string
  records: TideSearchHit[]
  truncated: boolean
}

/** Grouped hits for one search, in the model's own entity order. */
export interface TideSearchResult {
  wire_version: "0.1"
  text: string
  groups: TideSearchGroup[]
}

export interface TideSessionInfo {
  wire_version: "0.1"
  application: string
  application_version: string
  schema_version: string
  authentication: string
  principal: string
  roles: string[]
  reports: string[]
  entities: Record<string, TideEntityCapabilities>
  /**
   * True only where this principal may administer identities *and* this
   * server owns some. A provider administers its own, and development
   * authentication has no accounts at all.
   */
  administration: boolean
}

/** One compiled role and the permissions it carries. Never editable here. */
export interface TideRoleGrants {
  name: string
  grants: string[]
}

export interface TideRoleCatalogue {
  roles: TideRoleGrants[]
}

/** One account TIDE owns. There is no password material on the wire. */
export interface TideLocalUser {
  username: string
  display_name: string
  enabled: boolean
  roles: string[]
  created_at: string
  password_changed_at: string
}

export interface TideLocalUserList {
  users: TideLocalUser[]
  truncated: boolean
}

export interface TideBrowserAuthenticationInfo {
  enabled: boolean
  mode: "oidc" | "password" | "development" | null
  login_path: string | null
  session_path: string | null
  logout_path: string | null
}

export interface TideBrowserSessionInfo {
  csrf_token: string
}

export interface TidePresentationFormat {
  decimal_places: number | null
  thousands_separator: boolean
  display: string | null
}

export interface TidePresentationReference {
  entity: string
  resource_path: string
  identity_field: string
  display_template: string
}

export interface TideValueLabel {
  value: boolean | number | string
  label: string
}

export interface TidePresentationColumn {
  name: string
  label: string
  field_type: string
  alignment: TideAlignment
  format: string | null
  format_options: TidePresentationFormat | null
  target_entity: string | null
  reference: TidePresentationReference | null
  values: readonly TideValueLabel[]
}

export interface TidePresentationLookup {
  view: string
  title: string
  owner_entity: string
  field: string
  target_entity: string
  resource_path: string
  query_path: string
  selection_path: string
  identity_field: string
  columns: TidePresentationColumn[]
  search_fields: string[]
  page_size: number
  operations: TideOperation[]
  create_view: string | null
}

export interface TidePresentationFormField
  extends TidePresentationColumn {
  writable: boolean
  lookup?: TidePresentationLookup | null
  required: boolean
  help: string | null
  max_length: number | null
  choices: string[]
  regex: string | null
  numeric_mask: string | null
  precision: number | null
  scale: number | null
  minimum: string | number | null
  maximum: string | number | null
  has_default: boolean
  default_value: unknown
  /** Extensions a file field's picker may offer; empty means any kind. */
  accept?: string[]
  max_size_bytes?: number | null
  /**
   * Where this file field's uploads go. Absent when there is nowhere to
   * send them -- the caller may not write the field, or the entity is not
   * exposed -- and a control with no upload path offers no upload.
   */
  upload_path?: string | null
}

/**
 * What a record says about the file one of its fields holds. Never where
 * the file is: the only way to the bytes is the record's download route.
 */
export interface TideAttachmentValue {
  identity: string
  filename: string
  size: number
  content_type: string
}

export interface TidePresentationNamedFilter {
  name: string
  label: string
  conditions: TideFilterInput[]
}

export interface TideSummaryRequest {
  field: string
  function: string
}

export interface TideDistinctValue {
  value: unknown
  display: string | null
}

export interface TideDistinctResult {
  field: string
  values: TideDistinctValue[]
  truncated: boolean
}

export interface TideSummaryValue extends TideSummaryRequest {
  value: unknown
}

export interface TideBrowsePresentation {
  view: string
  entity: string
  label: string
  resource_path: string
  query_path: string
  identity_field: string
  columns: TidePresentationColumn[]
  search_field: string | null
  search_label: string | null
  named_filters: TidePresentationNamedFilter[]
  sortable_fields: string[]
  /**
   * Shown columns a per-column value filter can constrain. Optional for
   * the same version-skew reason as `summaries`; absence means no funnels.
   */
  filterable_fields?: string[]
  /**
   * What the footer asks of every page query, already column-filtered.
   * Optional because a bundle can be newer than the server it is served
   * by, and a browse against such a server still has to draw.
   */
  summaries?: TideSummaryRequest[]
  /**
   * How this browse offers editing. Optional for the same version-skew
   * reason; an older server means form editing, which is what absence
   * defaults to everywhere this is read.
   */
  edit?: "form" | "inline"
  /**
   * Which files this principal may take this view away as. Empty or
   * absent means no control at all -- either the principal does not
   * hold the export capability, or the server has no writer for the
   * format. Optional for the same version-skew reason as `summaries`.
   */
  export_formats?: TideBrowseExportFormat[]
  page_size: number
  operations: TideOperation[]
  detail_view: string | null
}

export interface TidePresentationFormGroup {
  kind: "group"
  label: string
  rows: string[][]
  tab: string | null
}

export interface TidePresentationFormCollection {
  kind: "collection"
  name: string
  label: string
  record_label: string
  entity: string
  view?: string
  identity_field?: string | null
  sequence_field?: string | null
  columns: TidePresentationColumn[]
  fields?: Record<string, TidePresentationFormField>
  groups?: TidePresentationFormGroup[]
  actions?: Array<"add" | "apply" | "remove">
  draft_operations?: Array<"create" | "update">
  writable?: boolean
  tab: string | null
}

export type TidePresentationFormSection =
  | TidePresentationFormGroup
  | TidePresentationFormCollection

export interface TidePresentationFormAction {
  name: string
  label: string
  idempotent: boolean
}

export interface TideFormPresentation {
  view: string
  entity: string
  label: string
  display_template: string | null
  fields: Record<string, TidePresentationFormField>
  sections: TidePresentationFormSection[]
  actions?: TidePresentationFormAction[]
}

export interface TidePresentationNavigationItem {
  view: string
  entity: string
  label: string
}

export interface TidePresentationNavigationGroup {
  label: string
  items: TidePresentationNavigationItem[]
}

export type TideReportParameterType =
  | "string"
  | "integer"
  | "decimal"
  | "boolean"
  | "date"
  | "datetime"

// One value a renderer collects as text before building a summary. The wire
// `required` flag means the caller must supply it: a parameter with a
// server-side default arrives here as optional.
export interface TideReportParameter {
  name: string
  label: string
  type: TideReportParameterType
  required: boolean
}

export interface TidePresentationReport {
  name: string
  title: string
  kind: TideReportKind
  entity: string
  resource_path: string
  export_formats: TideReportExportFormat[]
  // Absent or empty for record reports: their identity is bound from the URL.
  parameters?: TideReportParameter[]
}

export interface TidePresentationManifest {
  wire_version: "0.1"
  application: string
  application_version: string
  schema_version: string
  principal: string
  navigation: TidePresentationNavigationGroup[]
  views: Record<string, TideBrowsePresentation>
  forms: Record<string, TideFormPresentation>
  reports?: Record<string, TidePresentationReport>
}

export interface TideReportValue {
  label: string
  text: string
  alignment: TideAlignment
}

export interface TideReportColumn {
  name: string
  label: string
  alignment: TideAlignment
}

export interface TideReportCell {
  text: string
  alignment: TideAlignment
}

export interface TideReportGroup {
  values: TideReportValue[]
  row_start: number
  row_count: number
  footer_values: TideReportValue[]
}

export interface TideReportDocument {
  wire_version: "0.1"
  report: string
  title: string
  application: string
  generated_at: string
  header_text: string[]
  record_values: TideReportValue[]
  detail: {
    columns: TideReportColumn[]
    rows: TideReportCell[][]
  }
  footer_values: TideReportValue[]
  page_footer_template: string
  suggested_filename: string
  groups?: TideReportGroup[]
}

/** One record's file, on its way to being saved by whoever asked for it. */
export interface TideAttachmentDownload {
  blob: Blob
  filename: string
}

export interface TideBrowseDownload {
  blob: Blob
  filename: string
  /** Rows in the file. */
  rows: number
  /** Rows the query admits. Fewer in the file means the cap stopped it. */
  total: number
}

export interface TideReportDownload {
  blob: Blob
  filename: string
}

export interface TideProtectionMetadata {
  protected_fields?: string[]
  writable_fields?: string[]
  actions?: Record<string, TideRecordActionState>
  /** How each reference on this record names its target, resolved server-side. */
  references?: Record<string, string>
  /**
   * What the entity's `appearance:` rules made of this record, evaluated
   * server-side. Absent when no rule matched -- and `string` rather than the
   * renderer's own union, because a server one version ahead may name an
   * emphasis this bundle has never heard of.
   */
  appearance?: {
    record?: string
    fields?: Record<string, string>
    /**
     * Fields a rule hides on this record. Presentation only: the value is
     * right here in the payload, and a field a principal may not read is
     * withheld by the server as `protected_fields` instead.
     */
    hidden?: string[]
  }
}

export interface TideRecordActionState {
  visible: boolean
  enabled: boolean
}

export type TideRecord = Record<string, unknown> & {
  _tide?: TideProtectionMetadata
}

export interface TideRecordSnapshot {
  record: TideRecord
  etag: string | null
}

export interface TideRecordPage {
  records: TideRecord[]
  next_cursor: string | null
  /** Whole-filtered-set answers; null or absent when the query asked for none. */
  summaries?: TideSummaryValue[] | null
}

export interface TideReferenceSelectionResult {
  values: Record<string, unknown>
}

export interface TideQueryInput {
  filters: TideFilterInput[]
  sort: TideSortInput[]
  limit: number
  cursor: string | null
  summaries?: TideSummaryRequest[]
}

export interface TideConnection {
  session: TideSessionInfo
  presentation: TidePresentationManifest
}
