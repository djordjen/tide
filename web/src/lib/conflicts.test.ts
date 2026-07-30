import { describe, expect, it } from "vitest"

import {
  compareRecordConflict,
  resolveRecordConflict,
} from "@/lib/conflicts"

describe("record conflict review", () => {
  it("classifies safe, server-only, same, and overlapping changes", () => {
    const conflict = compareRecordConflict(
      {
        name: "Original",
        price: "10.00",
        active: true,
        lines: [{ line_number: 1, quantity: "1.00" }],
      },
      {
        name: "Server",
        price: "10.00",
        active: false,
        lines: [{ line_number: 1, quantity: "2.00" }],
      },
      {
        name: "Mine",
        price: "11.00",
        active: true,
        lines: [{ line_number: 1, quantity: "2.00" }],
      },
      ["name", "price", "active", "lines"],
    )

    expect(
      conflict.fields.map((field) => [field.name, field.disposition]),
    ).toEqual([
      ["name", "conflict"],
      ["price", "your_change"],
      ["active", "current_change"],
      ["lines", "same_change"],
    ])
    expect(conflict.conflictingFields).toEqual(["name"])
    expect(conflict.rebaseFields).toEqual(["price"])

    const unresolved = resolveRecordConflict(conflict, {})
    expect(unresolved.complete).toBe(false)
    expect(unresolved.unresolvedFields).toEqual(["name"])

    const resolved = resolveRecordConflict(conflict, { name: "draft" })
    expect(resolved).toEqual({
      draftFields: ["name", "price"],
      currentFields: ["active", "lines"],
      unresolvedFields: [],
      complete: true,
    })
  })

  it("rejects choices for non-conflicting fields", () => {
    const conflict = compareRecordConflict(
      { name: "Original" },
      { name: "Original" },
      { name: "Mine" },
      ["name"],
    )

    expect(() =>
      resolveRecordConflict(conflict, { name: "current" }),
    ).toThrow(/non-conflicting fields/i)
  })
})
