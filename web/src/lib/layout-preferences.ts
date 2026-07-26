export interface TideColumnLayout {
  version: 1
  order: string[]
  sizes: Record<string, number>
}

const STORAGE_PREFIX = "tide.web.column-layout.v1"
const MINIMUM_WIDTH = 72
const MAXIMUM_WIDTH = 640

export function layoutStorageKey(
  application: string,
  principal: string,
  view: string,
): string {
  return `${STORAGE_PREFIX}:${application}:${principal}:${view}`
}

export function loadColumnLayout(
  key: string,
  availableColumns: readonly string[],
): TideColumnLayout | null {
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) {
      return null
    }
    const candidate = JSON.parse(raw) as Partial<TideColumnLayout>
    if (
      candidate.version !== 1 ||
      !Array.isArray(candidate.order) ||
      !isSizeMap(candidate.sizes)
    ) {
      return null
    }
    const available = new Set(availableColumns)
    const known = candidate.order.filter(
      (name): name is string =>
        typeof name === "string" && available.has(name),
    )
    const order = [
      ...new Set([...known, ...availableColumns]),
    ]
    const sizes = Object.fromEntries(
      Object.entries(candidate.sizes)
        .filter(([name]) => available.has(name))
        .map(([name, size]) => [
          name,
          clampColumnWidth(size),
        ]),
    )
    return { version: 1, order, sizes }
  } catch {
    return null
  }
}

export function saveColumnLayout(
  key: string,
  layout: TideColumnLayout,
): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(layout))
  } catch {
    // Personal layout persistence is best effort and never blocks the view.
  }
}

export function clearColumnLayout(key: string): void {
  try {
    window.localStorage.removeItem(key)
  } catch {
    // Reset still applies to the current session when storage is unavailable.
  }
}

export function clampColumnWidth(value: number): number {
  return Math.max(
    MINIMUM_WIDTH,
    Math.min(MAXIMUM_WIDTH, Math.round(value)),
  )
}

function isSizeMap(
  value: Partial<TideColumnLayout>["sizes"],
): value is Record<string, number> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.values(value).every(
      (size) => typeof size === "number" && Number.isFinite(size),
    )
  )
}
