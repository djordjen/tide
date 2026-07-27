import type {
  TideFormPresentation,
  TidePresentationFormField,
  TideRecord,
} from "@/lib/contracts"

export type TideFormDraft = Record<string, unknown>
export type TideFormErrors = Record<string, string>

const EDITABLE_SCALAR_TYPES = new Set([
  "boolean",
  "choice",
  "date",
  "datetime",
  "decimal",
  "integer",
  "string",
])

export function isFlatEditableForm(form: TideFormPresentation): boolean {
  return (
    form.sections.every((section) => section.kind === "group") &&
    Object.values(form.fields).every((field) =>
      EDITABLE_SCALAR_TYPES.has(field.field_type),
    )
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
    if (
      field.validations.includes("email") &&
      typeof raw === "string" &&
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw)
    ) {
      errors[field.name] = `${field.label} must be a valid email address.`
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
  return String(value)
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
