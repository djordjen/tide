import type { TideValidationIssue } from "@/lib/api"
import type {
  TideFormPresentation,
  TidePresentationFormCollection,
  TidePresentationFormField,
  TidePresentationFormSection,
  TideRecord,
} from "@/lib/contracts"

export type TideFormDraft = Record<string, unknown>
export type TideFormErrors = Record<string, string>

// Every scalar type the form can put on screen. A type missing here does not
// degrade one field -- `isEditableForm` requires all of them, so one unknown
// type makes the whole record uneditable in the browser.
export const EDITABLE_SCALAR_TYPES = new Set([
  "boolean",
  "choice",
  "date",
  "datetime",
  "decimal",
  "file",
  "integer",
  "reference",
  "string",
  "uuid",
])

export function isEditableForm(form: TideFormPresentation): boolean {
  return Object.values(form.fields).every((field) =>
    EDITABLE_SCALAR_TYPES.has(field.field_type),
  )
}

export function formDraft(
  form: TideFormPresentation,
  record?: TideRecord,
): TideFormDraft {
  return Object.fromEntries(
    Object.values(form.fields).map((field) => [
      field.name,
      draftValue(
        field,
        record
          ? record[field.name]
          : field.has_default
            ? field.default_value
            : null,
      ),
    ]),
  )
}

// A duplicate opens as a new record: defaults first, then what the
// original could offer. Only fields the seed actually carries overwrite,
// so an absent value never drowns a declared default with nothing.
export function seededFormDraft(
  form: TideFormPresentation,
  seed: TideRecord,
): TideFormDraft {
  const base = formDraft(form)
  const fromSeed = formDraft(form, seed)
  for (const name of Object.keys(seed)) {
    if (name in base) {
      base[name] = fromSeed[name]
    }
  }
  return base
}

export function validateFormDraft(
  form: TideFormPresentation,
  draft: TideFormDraft,
  writableFields: ReadonlySet<string>,
): TideFormErrors {
  const errors: TideFormErrors = {}
  for (const field of Object.values(form.fields)) {
    if (!writableFields.has(field.name)) {
      continue
    }
    const value = draft[field.name]
    const raw = typeof value === "string" ? value.trim() : value
    if (field.required && isEmpty(raw)) {
      errors[field.name] = `${field.label} is required.`
      continue
    }
    if (isEmpty(raw)) {
      continue
    }
    if (
      field.max_length !== null &&
      typeof raw === "string" &&
      raw.length > field.max_length
    ) {
      errors[field.name] =
        `${field.label} cannot exceed ${field.max_length} characters.`
      continue
    }
    if (
      field.regex &&
      typeof raw === "string" &&
      !new RegExp(`^(?:${field.regex})$`).test(raw)
    ) {
      errors[field.name] = `${field.label} has an invalid format.`
      continue
    }
    if (field.field_type === "integer" && !/^-?\d+$/.test(String(raw))) {
      errors[field.name] = `${field.label} must be a whole number.`
      continue
    }
    if (field.field_type === "decimal") {
      const normalized = normalizedDecimal(String(raw))
      if (!normalized || !decimalFits(field, normalized)) {
        errors[field.name] = `${field.label} has an invalid numeric format.`
        continue
      }
      if (
        field.minimum !== null &&
        compareDecimals(normalized, String(field.minimum)) < 0
      ) {
        errors[field.name] =
          `${field.label} must be at least ${field.minimum}.`
        continue
      }
      if (
        field.maximum !== null &&
        compareDecimals(normalized, String(field.maximum)) > 0
      ) {
        errors[field.name] =
          `${field.label} must be at most ${field.maximum}.`
        continue
      }
    }
    if (
      field.field_type === "choice" &&
      !field.choices.includes(String(raw))
    ) {
      errors[field.name] = `${field.label} has an invalid choice.`
      continue
    }
    if (
      field.field_type === "date" &&
      !/^\d{4}-\d{2}-\d{2}$/.test(String(raw))
    ) {
      errors[field.name] = `${field.label} must be a date.`
    }
  }
  return errors
}

export function mutationPayload(
  form: TideFormPresentation,
  draft: TideFormDraft,
  writableFields: ReadonlySet<string>,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.values(form.fields)
      .filter((field) => writableFields.has(field.name))
      .map((field) => [
        field.name,
        payloadValue(field, draft[field.name]),
      ]),
  )
}

/**
 * Say what a draft holds so far, in the wire types the server insists on.
 *
 * This goes through the same `payloadValue` conversion as a save, because the
 * server decodes both with one strict decoder: an integer field must arrive as
 * a JSON number, a boolean as a JSON boolean. A text input only ever hands
 * back a string, so sending the draft raw made the server refuse the whole
 * selection -- and with it the assignments the selection exists to perform.
 *
 * It differs from `mutationPayload` in what it leaves out. A save replaces the
 * record, so every writable field is sent, empty ones included. A selection
 * only describes work in progress, so a field nobody has filled is omitted
 * rather than asserted as null.
 */
export function referenceSelectionDraft(
  form: TideFormPresentation,
  draft: TideFormDraft,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.values(form.fields)
      .filter(
        (field) => field.writable && draft[field.name] !== undefined,
      )
      .map((field) => [field.name, payloadValue(field, draft[field.name])])
      .filter(([, value]) => value !== null),
  )
}

export function changedMutationPayload(
  form: TideFormPresentation,
  draft: TideFormDraft,
  writableFields: ReadonlySet<string>,
  record: TideRecord,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(mutationPayload(form, draft, writableFields)).filter(
      ([name, value]) =>
        comparableValue(form.fields[name], value) !==
        comparableValue(form.fields[name], record[name]),
    ),
  )
}

export function collectionEditorForm(
  collection: TidePresentationFormCollection,
): TideFormPresentation | null {
  const fields = collection.fields ?? {}
  const groups = collection.groups ?? []
  if (
    !collection.view ||
    Object.keys(fields).length === 0 ||
    groups.length === 0
  ) {
    return null
  }
  return {
    view: collection.view,
    entity: collection.entity,
    label: collection.record_label,
    display_template: null,
    fields,
    sections: groups,
  }
}

export function collectionDraftRows(
  collection: TidePresentationFormCollection,
  records: unknown,
): TideRecord[] {
  const form = collectionEditorForm(collection)
  if (!form || !Array.isArray(records)) {
    return []
  }
  return records.map((record) => {
    const source =
      record && typeof record === "object"
        ? (record as TideRecord)
        : ({} as TideRecord)
    return { ...source, ...draftMetadata(source), ...formDraft(form, source) }
  })
}

function draftMetadata(source: TideRecord): Partial<TideRecord> {
  const metadata = source._tide
  if (!metadata?.references) {
    return {}
  }
  // A draft exists to change values, and a resolved name belongs to the
  // value it named. The rest of the envelope describes the field or the
  // record, so it survives editing; this does not.
  const { references, ...rest } = metadata
  void references
  return { _tide: rest }
}

export function newCollectionDraft(
  collection: TidePresentationFormCollection,
  records: TideRecord[],
): TideRecord {
  const form = collectionEditorForm(collection)
  if (!form) {
    return {}
  }
  const draft = formDraft(form)
  const sequence = collection.sequence_field
  const field = sequence ? form.fields[sequence] : undefined
  if (sequence && field?.field_type === "integer") {
    draft[sequence] = String(
      Math.max(
        0,
        ...records.map((record) => Number(record[sequence] ?? 0)),
      ) + 1,
    )
  }
  return draft
}

export function collectionMutationPayload(
  collection: TidePresentationFormCollection,
  records: TideRecord[],
): Array<Record<string, unknown>> {
  const form = collectionEditorForm(collection)
  if (!form) {
    return []
  }
  const writable = new Set(
    Object.values(form.fields)
      .filter((field) => field.writable)
      .map((field) => field.name),
  )
  return records.map((record) => {
    const values = mutationPayload(form, record, writable)
    const identityField = collection.identity_field
    const identity = identityField ? record[identityField] : null
    return identityField &&
      identity !== null &&
      identity !== undefined &&
      identity !== ""
      ? { [identityField]: identity, ...values }
      : values
  })
}

export function validateCollectionDrafts(
  collection: TidePresentationFormCollection,
  records: TideRecord[],
): TideFormErrors[] {
  const form = collectionEditorForm(collection)
  if (!form) {
    return []
  }
  const writable = new Set(
    Object.values(form.fields)
      .filter((field) => field.writable)
      .map((field) => field.name),
  )
  return records.map((record) =>
    validateFormDraft(form, record, writable),
  )
}

export function collectionPayloadChanged(
  collection: TidePresentationFormCollection,
  draft: TideRecord[],
  original: unknown,
): boolean {
  const current = collectionMutationPayload(collection, draft)
  const baseline = collectionMutationPayload(
    collection,
    collectionDraftRows(collection, original),
  )
  return JSON.stringify(current) !== JSON.stringify(baseline)
}

export function acceptsNumericDraft(
  field: TidePresentationFormField,
  value: string,
): boolean {
  if (!value || value === "-" || value === "," || value === ".") {
    return true
  }
  const separator = field.numeric_mask?.includes(",") ? "," : "[.,]"
  const scale = field.scale ?? numericMaskScale(field.numeric_mask)
  const precision = field.precision
  const integerDigits =
    precision !== null && precision !== undefined && scale !== null
      ? Math.max(precision - scale, 1)
      : null
  const integer = integerDigits ? `\\d{0,${integerDigits}}` : "\\d*"
  const fraction =
    scale === null || scale === undefined || scale <= 0
      ? ""
      : `(?:${separator}\\d{0,${scale}})?`
  return new RegExp(`^-?${integer}${fraction}$`).test(value)
}

export function normalizeNumericDraft(
  field: TidePresentationFormField,
  value: string,
): string {
  const normalized = normalizedDecimal(value)
  if (!normalized) {
    return value
  }
  const scale = field.scale ?? numericMaskScale(field.numeric_mask)
  if (scale === null || scale === undefined) {
    return normalized
  }
  const [whole, fraction = ""] = normalized.split(".")
  return `${whole}${scale ? `.${fraction.padEnd(scale, "0").slice(0, scale)}` : ""}`
}

export function shiftIsoDate(value: string, days: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) {
    return value
  }
  const date = new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])),
  )
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

function draftValue(
  field: TidePresentationFormField,
  value: unknown,
): unknown {
  if (value === null || value === undefined) {
    return field.field_type === "boolean" ? false : ""
  }
  if (field.field_type === "boolean") {
    return Boolean(value)
  }
  if (field.field_type === "reference") {
    return value
  }
  if (field.field_type === "file") {
    // The whole projection, not the key: the control shows a name and a
    // size, and a draft holding only the key would have to fetch them back
    // for a file the person just chose. The key is taken out again on the
    // way to the server, where it is the only part that means anything.
    return value
  }
  if (
    field.field_type === "date" &&
    typeof value === "string"
  ) {
    return value.slice(0, 10)
  }
  return String(value)
}

function payloadValue(
  field: TidePresentationFormField,
  value: unknown,
): unknown {
  if (field.field_type === "boolean") {
    return Boolean(value)
  }
  if (field.field_type === "reference") {
    return value === "" || value === null || value === undefined
      ? null
      : value
  }
  if (field.field_type === "file") {
    return attachmentIdentity(value)
  }
  const raw = String(value ?? "").trim()
  if (!raw) {
    return null
  }
  if (field.field_type === "decimal") {
    return normalizedDecimal(raw) ?? raw
  }
  if (field.field_type === "integer") {
    const numeric = Number(raw)
    return Number.isSafeInteger(numeric) ? numeric : raw
  }
  return raw
}

function comparableValue(
  field: TidePresentationFormField,
  value: unknown,
): string {
  if (value === null || value === undefined || value === "") {
    return ""
  }
  if (field.field_type === "boolean") {
    return value ? "true" : "false"
  }
  if (field.field_type === "decimal") {
    return normalizedDecimal(String(value)) ?? String(value)
  }
  if (field.field_type === "file") {
    return attachmentIdentity(value) ?? ""
  }
  return String(value)
}

/** The key inside a file field's value, wherever the value came from. */
function attachmentIdentity(value: unknown): string | null {
  if (typeof value === "string") {
    return value.trim() || null
  }
  if (value && typeof value === "object") {
    const identity = (value as { identity?: unknown }).identity
    return typeof identity === "string" ? identity : null
  }
  return null
}

function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || value === ""
}

function normalizedDecimal(value: string): string | null {
  const normalized = value.trim().replace(",", ".")
  return /^-?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)
    ? normalized.startsWith(".")
      ? `0${normalized}`
      : normalized.startsWith("-.")
        ? normalized.replace("-.", "-0.")
        : normalized
    : null
}

function decimalFits(
  field: TidePresentationFormField,
  normalized: string,
): boolean {
  const unsigned = normalized.replace(/^-/, "")
  const [whole, fraction = ""] = unsigned.split(".")
  const significantWhole = whole.replace(/^0+(?=\d)/, "")
  if (field.scale !== null && fraction.length > field.scale) {
    return false
  }
  return (
    field.precision === null ||
    significantWhole.length + fraction.length <= field.precision
  )
}

function numericMaskScale(mask: string | null): number | null {
  const match = /^0(?:[.,](0+))?$/.exec(mask ?? "")
  return match ? (match[1]?.length ?? 0) : null
}

function compareDecimals(left: string, right: string): number {
  const normalizedLeft = normalizedDecimal(left) ?? left
  const normalizedRight = normalizedDecimal(right) ?? right
  const leftNegative = normalizedLeft.startsWith("-")
  const rightNegative = normalizedRight.startsWith("-")
  if (leftNegative !== rightNegative) {
    return leftNegative ? -1 : 1
  }
  const leftParts = normalizedLeft.replace(/^-/, "").split(".")
  const rightParts = normalizedRight.replace(/^-/, "").split(".")
  const scale = Math.max(
    leftParts[1]?.length ?? 0,
    rightParts[1]?.length ?? 0,
  )
  const leftValue = BigInt(
    `${leftParts[0]}${(leftParts[1] ?? "").padEnd(scale, "0")}`,
  )
  const rightValue = BigInt(
    `${rightParts[0]}${(rightParts[1] ?? "").padEnd(scale, "0")}`,
  )
  const comparison = leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : 0
  return leftNegative ? -comparison : comparison
}


export function formTabs(sections: TidePresentationFormSection[]): string[] {
  if (!sections.some((section) => section.tab)) {
    return []
  }
  return [
    ...new Set(sections.map((section) => section.tab ?? "General")),
  ]
}


export function collectionDraftState(
  form: TideFormPresentation,
  record?: TideRecord,
): Record<string, TideRecord[]> {
  return Object.fromEntries(
    form.sections
      .filter(
        (
          section,
        ): section is TidePresentationFormCollection =>
          section.kind === "collection" &&
          section.writable === true,
      )
      .map((section) => [
        section.name,
        collectionDraftRows(section, record?.[section.name]),
      ]),
  )
}


export function issueFieldErrors(
  form: TideFormPresentation,
  issues: TideValidationIssue[],
): TideFormErrors {
  const errors: TideFormErrors = {}
  for (const issue of issues) {
    for (const name of issue.fields) {
      const field = form.fields[name]
      if (!field || errors[name]) {
        continue
      }
      errors[name] = issue.message.replace(
        new RegExp(`^${escapeRegExp(name)}\\b`, "i"),
        field.label,
      )
    }
  }
  return errors
}


function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
