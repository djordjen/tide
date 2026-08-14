import { expect, test, type Page } from "@playwright/test"
import { signIn } from "./session"

/**
 * A form is mostly controls, not the packaging around them.
 *
 * Every field used to be its own bordered card: 12px of padding above and
 * below, a 1px border, a 16px label and a 6px gap, wrapping a 36px input. That
 * is 84px spent to show 36px of control, and thirteen fields of it did not fit
 * on a 1440x900 screen — so the lines collection sat permanently below the
 * fold. jsdom computes no layout, which is why the unit suite cannot see this
 * and a real browser has to assert it.
 *
 * Both renderers are measured. A draft is editable and draws through
 * `RecordFormEditor`; a posted invoice is locked by its transition, so nothing
 * is editable, `editorActive` is false and `RecordDetailSections` draws it
 * instead. They share one field-cell rule and used to carry two copies of it,
 * so checking one would leave the other free to drift. The badge is asserted
 * to say which one actually rendered.
 *
 * Measured per row, not per cell: a grid row is as tall as its tallest cell
 * and stretches the rest to match, so a read-only field beside an input is
 * legitimately as tall as the input. Measured through `boundingBox` rather
 * than `page.evaluate`, because the e2e project has no DOM lib on purpose.
 */
const LABEL_AND_GAP_BUDGET = 24
const READ_ONLY_ROW_BUDGET = 48

type Measurement = {
  overweight: string[]
  bulky: string[]
  withControls: number
  readOnlyOnly: number
}

async function measureOpenRecord(page: Page): Promise<Measurement> {
  const rows = page.locator(".tide-form-row")
  const total = await rows.count()
  expect(total).toBeGreaterThan(3)

  const result: Measurement = {
    overweight: [],
    bulky: [],
    withControls: 0,
    readOnlyOnly: 0,
  }

  for (let index = 0; index < total; index += 1) {
    const row = rows.nth(index)
    const label = (await row.innerText()).split("\n")[0]
    const rowBox = await row.boundingBox()
    if (!rowBox) continue
    const height = Math.round(rowBox.height)

    const controls = row.locator("[data-tide-editor]")
    const controlCount = await controls.count()
    if (controlCount === 0) {
      result.readOnlyOnly += 1
      if (height > READ_ONLY_ROW_BUDGET) {
        result.bulky.push(`${label}: ${height}px`)
      }
      continue
    }

    result.withControls += 1
    let tallest = 0
    for (let control = 0; control < controlCount; control += 1) {
      const box = await controls.nth(control).boundingBox()
      tallest = Math.max(tallest, box?.height ?? 0)
    }
    const packaging = Math.round(height - tallest)
    if (packaging > LABEL_AND_GAP_BUDGET) {
      result.overweight.push(`${label}: ${packaging}px around a ${tallest}px control`)
    }
  }
  return result
}

/** Open a record from the grid. The caller is responsible for being on it. */
async function open(page: Page, invoice: RegExp): Promise<void> {
  await page.getByRole("row", { name: invoice }).click()
  await page.getByRole("button", { name: "Open" }).click()
  await page.getByRole("heading", { level: 1, name: invoice }).waitFor()
}

test("spends its vertical space on controls rather than on packaging", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page)

  await open(page, /INV-2026-0002/)
  await expect(page.getByText("Secured editor")).toBeVisible()
  const draft = await measureOpenRecord(page)

  // The open record is in the address bar, so history is the way back to the
  // grid; the sidebar entry for the view you are already in does not move.
  await page.goBack()
  await open(page, /INV-2026-0001/)
  await expect(page.getByText("Secured detail")).toBeVisible()
  const posted = await measureOpenRecord(page)

  expect(draft.withControls, "the draft is the editable renderer").toBeGreaterThan(0)
  expect(posted.withControls, "a posted invoice is locked").toBe(0)
  expect(posted.readOnlyOnly).toBeGreaterThan(0)

  expect(
    [...draft.overweight, ...posted.overweight],
    `a row may spend ${LABEL_AND_GAP_BUDGET}px on labels and gaps, no more`,
  ).toEqual([])
  expect(
    [...draft.bulky, ...posted.bulky],
    `a read-only row shows label and value within ${READ_ONLY_ROW_BUDGET}px`,
  ).toEqual([])
})

/**
 * The label beside its control, not above it.
 *
 * A label above doubles the rows a record needs, which is the same vertical
 * budget the field cards used to waste. This is a geometry claim and jsdom
 * computes none, so it can only be made here — and containment is half a
 * layout check, so the label is asserted to end before the control begins
 * rather than merely to be somewhere near it.
 *
 * Below 768px there are no two columns to have, and the same fields have to
 * stack; a phone is the one place this layout must not apply.
 */
async function firstLabelledField(page: Page): Promise<{
  label: { x: number; right: number; y: number; bottom: number }
  control: { x: number; y: number }
}> {
  const group = page.locator(".tide-field", { has: page.locator("[data-tide-editor]") })
  await group.first().waitFor()
  const labelBox = await group.first().locator("label").first().boundingBox()
  const controlBox = await group.first().locator("[data-tide-editor]").first().boundingBox()
  if (!labelBox || !controlBox) throw new Error("a field has no geometry")
  return {
    label: {
      x: labelBox.x,
      right: labelBox.x + labelBox.width,
      y: labelBox.y,
      bottom: labelBox.y + labelBox.height,
    },
    control: { x: controlBox.x, y: controlBox.y },
  }
}

test("puts the field label beside its control, and above it on a phone", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page)
  await open(page, /INV-2026-0002/)
  await expect(page.getByText("Secured editor")).toBeVisible()

  const wide = await firstLabelledField(page)
  expect(wide.label.right, "the label ends before the control starts").toBeLessThanOrEqual(
    wide.control.x,
  )
  expect(wide.control.y, "and shares its line").toBeLessThan(wide.label.bottom)

  await page.setViewportSize({ width: 375, height: 812 })
  const narrow = await firstLabelledField(page)
  expect(narrow.control.y, "on a phone the control is below the label").toBeGreaterThanOrEqual(
    narrow.label.bottom,
  )
  expect(narrow.label.x, "and both start at the same edge").toBeCloseTo(narrow.control.x, 0)
})
