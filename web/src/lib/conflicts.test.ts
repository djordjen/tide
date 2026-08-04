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

describe("comparing values that are not scalars", () => {
  it("does not invent a conflict when a server reorders keys", () => {
    // The comparison used `JSON.stringify`, which is key-order sensitive. A
    // server that serialises the same collection row with its keys in another
    // order -- a different ORM, a changed column order, a dict rebuilt -- made
    // every such row read as edited by both sides, and asked the person to
    // resolve a difference that does not exist.
    const conflict = compareRecordConflict(
      { lines: [{ line_number: 1, quantity: "1.00" }] },
      { lines: [{ quantity: "1.00", line_number: 1 }] },
      { lines: [{ line_number: 1, quantity: "1.00" }] },
      ["lines"],
    )

    expect(conflict.conflictingFields).toEqual([])
    expect(conflict.rebaseFields).toEqual([])
    // Nothing changed on either side, so there is nothing to review at all --
    // the reordered row is the same row.
    expect(conflict.fields).toEqual([])
  })

  it("does not invent a conflict against a reordered edit either", () => {
    const conflict = compareRecordConflict(
      { lines: [{ line_number: 1, quantity: "1.00" }] },
      { lines: [{ quantity: "2.00", line_number: 1 }] },
      { lines: [{ line_number: 1, quantity: "2.00" }] },
      ["lines"],
    )

    expect(conflict.conflictingFields).toEqual([])
    expect(conflict.fields[0].disposition).toBe("same_change")
  })

  it("still sees a genuine difference inside a collection", () => {
    const conflict = compareRecordConflict(
      { lines: [{ line_number: 1, quantity: "1.00" }] },
      { lines: [{ quantity: "2.00", line_number: 1 }] },
      { lines: [{ line_number: 1, quantity: "3.00" }] },
      ["lines"],
    )

    expect(conflict.conflictingFields).toEqual(["lines"])
  })

  it("tells a missing key from one that is present and null", () => {
    // `JSON.stringify` drops `undefined` in an object, so `{a: undefined}` and
    // `{}` compared equal. They are different records.
    const conflict = compareRecordConflict(
      { detail: {} },
      { detail: { note: null } },
      { detail: {} },
      ["detail"],
    )

    expect(conflict.fields[0].disposition).toBe("current_change")
  })

  it("compares arrays by order, because a collection has one", () => {
    const conflict = compareRecordConflict(
      { lines: [{ line_number: 1 }, { line_number: 2 }] },
      { lines: [{ line_number: 2 }, { line_number: 1 }] },
      { lines: [{ line_number: 1 }, { line_number: 2 }] },
      ["lines"],
    )

    expect(conflict.fields[0].disposition).toBe("current_change")
  })
})
