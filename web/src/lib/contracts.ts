export type TideOperation = "list" | "get" | "create" | "update" | "delete"
export type TideAlignment = "left" | "center" | "right"
export type TideFilterOperator =
  | "eq"
  | "ne"
  | "lt"
  | "lte"
  | "gt"
  | "gte"
  | "contains"
  | "icontains"

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

export interface TidePresentationColumn {
  name: string
  label: string
  field_type: string
  alignment: TideAlignment
  format: string | null
  format_options: TidePresentationFormat | null
  target_entity: string | null
  reference: TidePresentationReference | null
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
  validations: string[]
  has_default: boolean
  default_value: unknown
}

export interface TidePresentationNamedFilter {
  name: string
  label: string
  conditions: TideFilterInput[]
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
  entity: string
  columns: TidePresentationColumn[]
  tab: string | null
}

export type TidePresentationFormSection =
  | TidePresentationFormGroup
  | TidePresentationFormCollection

export interface TideFormPresentation {
  view: string
  entity: string
  label: string
  display_template: string | null
  fields: Record<string, TidePresentationFormField>
  sections: TidePresentationFormSection[]
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

export interface TidePresentationManifest {
  wire_version: "0.1"
  application: string
  application_version: string
  schema_version: string
  principal: string
  navigation: TidePresentationNavigationGroup[]
  views: Record<string, TideBrowsePresentation>
  forms: Record<string, TideFormPresentation>
}

export interface TideProtectionMetadata {
  protected_fields?: string[]
  writable_fields?: string[]
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
}

export interface TideReferenceSelectionResult {
  values: Record<string, unknown>
}

export interface TideQueryInput {
  filters: TideFilterInput[]
  sort: TideSortInput[]
  limit: number
  cursor: string | null
}

export interface TideConnection {
  session: TideSessionInfo
  presentation: TidePresentationManifest
}
