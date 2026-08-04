export type TideConflictDisposition =
  | "your_change"
  | "current_change"
  | "same_change"
  | "conflict"

export type TideConflictChoice = "current" | "draft"

export interface TideConflictField {
  name: string
  original: unknown
  current: unknown
  draft: unknown
  disposition: TideConflictDisposition
}

export interface TideRecordConflict {
  fields: TideConflictField[]
  conflictingFields: string[]
  rebaseFields: string[]
}

export interface TideConflictResolution {
  draftFields: string[]
  currentFields: string[]
  unresolvedFields: string[]
  complete: boolean
}

export function compareRecordConflict(
  original: Record<string, unknown>,
  current: Record<string, unknown>,
  draft: Record<string, unknown>,
  fields: readonly string[],
): TideRecordConflict {
  const result: TideConflictField[] = []
  for (const name of fields) {
    const originalValue = original[name]
    const currentValue = current[name]
    const draftValue = draft[name]
    const yoursChanged = !conflictValueEqual(draftValue, originalValue)
    const currentChanged = !conflictValueEqual(currentValue, originalValue)
    if (!yoursChanged && !currentChanged) {
      continue
    }
    const disposition: TideConflictDisposition =
      yoursChanged && currentChanged
        ? conflictValueEqual(draftValue, currentValue)
          ? "same_change"
          : "conflict"
        : yoursChanged
          ? "your_change"
          : "current_change"
    result.push({
      name,
      original: structuredClone(originalValue),
      current: structuredClone(currentValue),
      draft: structuredClone(draftValue),
      disposition,
    })
  }
  return {
    fields: result,
    conflictingFields: result
      .filter((field) => field.disposition === "conflict")
      .map((field) => field.name),
    rebaseFields: result
      .filter((field) => field.disposition === "your_change")
      .map((field) => field.name),
  }
}

export function resolveRecordConflict(
  conflict: TideRecordConflict,
  choices: Readonly<Record<string, TideConflictChoice>>,
): TideConflictResolution {
  const conflicting = new Set(conflict.conflictingFields)
  const unknown = Object.keys(choices).filter(
    (name) => !conflicting.has(name),
  )
  if (unknown.length > 0) {
    throw new Error(
      `Conflict choices contain non-conflicting fields: ${unknown.sort().join(", ")}`,
    )
  }
  const draftFields: string[] = []
  const currentFields: string[] = []
  const unresolvedFields: string[] = []
  for (const field of conflict.fields) {
    if (field.disposition === "your_change") {
      draftFields.push(field.name)
    } else if (
      field.disposition === "current_change" ||
      field.disposition === "same_change"
    ) {
      currentFields.push(field.name)
    } else if (choices[field.name] === "draft") {
      draftFields.push(field.name)
    } else if (choices[field.name] === "current") {
      currentFields.push(field.name)
    } else {
      unresolvedFields.push(field.name)
    }
  }
  return {
    draftFields,
    currentFields,
    unresolvedFields,
    complete: unresolvedFields.length === 0,
  }
}

function conflictValueEqual(left: unknown, right: unknown): boolean {
  // Structural, not `JSON.stringify`. Serialising to compare makes key order
  // significant, so a server that emits the same collection row with its keys
  // in another order -- a different ORM, a reordered column, a rebuilt dict --
  // reads as having edited every one of those rows, and the person is asked to
  // resolve a difference that is not there. Arrays keep their order, because a
  // collection's order is part of its value.
  if (Object.is(left, right)) {
    return true
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) => conflictValueEqual(item, right[index]))
    )
  }
  if (isRecord(left) && isRecord(right)) {
    const keys = Object.keys(left)
    return (
      keys.length === Object.keys(right).length &&
      keys.every(
        (key) =>
          Object.hasOwn(right, key) && conflictValueEqual(left[key], right[key]),
      )
    )
  }
  return false
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
