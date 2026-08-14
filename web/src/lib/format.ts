import type {
  TidePresentationColumn,
  TidePresentationReference,
  TideRecord,
} from "@/lib/contracts"

export function formatCellValue(
  column: TidePresentationColumn,
  value: unknown,
  protectedFields: readonly string[] = [],
): string {
  if (protectedFields.includes(column.name)) {
    return "Protected"
  }
  if (value === null || value === undefined) {
    return ""
  }
  // A captioned code is shown as what it stands for. An uncaptioned one
  // is shown as itself: a legacy column holds values nobody wrote down,
  // and blanking one hides the only evidence that it is there.
  const caption = (column.values ?? []).find((item) => item.value === value)
  if (caption) {
    return caption.label
  }
  if (column.field_type === "boolean") {
    return value ? "Yes" : "No"
  }
  if (column.field_type === "choice") {
    return humanize(String(value))
  }
  if (
    (column.field_type === "date" || column.field_type === "datetime") &&
    typeof value === "string"
  ) {
    return formatIsoDate(
      value,
      column.format_options?.display ??
        (column.field_type === "date" ? "%Y-%m-%d" : "%Y-%m-%d %H:%M"),
    )
  }
  if (
    column.field_type === "decimal" ||
    column.field_type === "integer"
  ) {
    return formatExactNumber(
      String(value),
      column.format_options?.decimal_places,
      column.format_options?.thousands_separator ?? false,
    )
  }
  return String(value)
}

/**
 * Resolve one `display:` declaration against a record.
 *
 * A declaration is either the name of the field to show -- `display: number`,
 * `display: uniqueid` -- or a template of `{placeholders}`. Both formatters
 * below take the same declaration from the same manifest, and only one of
 * them used to know about the first form: a reference to an entity whose
 * display is a bare field name rendered the literal word `uniqueid` in every
 * picker. Both reference applications write `{braces}` or resolve through the
 * embedded envelope, so nothing exercised the other branch.
 */
function applyDisplayTemplate(template: string, record: TideRecord): string {
  if (!template.includes("{")) {
    return safeDisplayValue(record[template])
  }
  return template.replace(
    /\{([A-Za-z_][A-Za-z0-9_]*)\}/g,
    (_, field: string) => safeDisplayValue(record[field]),
  )
}

export function formatReferenceDisplay(
  reference: TidePresentationReference,
  record: TideRecord,
): string {
  return applyDisplayTemplate(reference.display_template, record)
}

export function formatRecordDisplay(
  template: string | null,
  record: TideRecord,
  identityField: string,
): string {
  if (!template) {
    return safeDisplayValue(record[identityField])
  }
  return applyDisplayTemplate(template, record)
}

export function humanize(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

function formatExactNumber(
  raw: string,
  decimalPlaces: number | null | undefined,
  grouping: boolean,
): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(raw)
  if (!match) {
    return raw
  }
  const [, sign, wholeRaw, fractionRaw = ""] = match
  const whole = grouping
    ? wholeRaw.replace(/\B(?=(\d{3})+(?!\d))/g, ",")
    : wholeRaw
  if (decimalPlaces === null || decimalPlaces === undefined) {
    return `${sign}${whole}${fractionRaw ? `.${fractionRaw}` : ""}`
  }
  const fraction = fractionRaw
    .padEnd(decimalPlaces, "0")
    .slice(0, decimalPlaces)
  return `${sign}${whole}${decimalPlaces ? `.${fraction}` : ""}`
}

function formatIsoDate(value: string, pattern: string): string {
  const match =
    /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(
      value,
    )
  if (!match) {
    return value
  }
  const [, year, month, day, hour = "00", minute = "00", second = "00"] =
    match
  return pattern.replace(
    /%[YmdHMS]/g,
    (token) =>
      ({
        "%Y": year,
        "%m": month,
        "%d": day,
        "%H": hour,
        "%M": minute,
        "%S": second,
      })[token] ?? token,
  )
}

function safeDisplayValue(value: unknown): string {
  if (value === null || value === undefined) {
    return ""
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No"
  }
  return String(value)
}
