import { expect, test, type Locator, type Page } from "@playwright/test"
import { signIn } from "./session"

/**
 * The Web UI has to work on a phone, because it is the only surface that does.
 * The terminal client cannot run on one, and REST and MCP are not interfaces a
 * person uses. "Terminal-first" says where the keyboard effort goes; it is not
 * licence to ship a layout that only holds at desk widths.
 *
 * What this catches is a control placed outside the viewport. The record
 * action bar did exactly that: `Cancel`, `Save`, `Preview Invoice` and `Post
 * invoice` were laid out from 249px to 416px inside a 375px viewport, and
 * because the overflow was clipped rather than scrolled, `scrollWidth` matched
 * `clientWidth` and the page looked fine to every check that asks the document
 * how wide it is. Four actions that could not be reached by any means, on the
 * only surface a phone can use.
 *
 * So the measurement is per control, through `boundingBox`, and not
 * `page.evaluate`: the e2e project compiles without the DOM library on
 * purpose. Vertical position is not asserted -- a long form is meant to
 * scroll -- only that nothing sits to the left or the right of the screen.
 */
const PHONE = { width: 375, height: 812 }

type Placed = { label: string; box: NonNullable<Awaited<ReturnType<Locator["boundingBox"]>>> }

async function placedControls(scope: Locator): Promise<Placed[]> {
  const controls = scope.locator("button, input, select, textarea")
  const total = await controls.count()
  expect(total).toBeGreaterThan(0)

  const placed: Placed[] = []
  for (let index = 0; index < total; index += 1) {
    const control = controls.nth(index)
    if (!(await control.isVisible())) continue
    const box = await control.boundingBox()
    if (!box) continue
    const label =
      (await control.getAttribute("aria-label")) ||
      (await control.innerText()).trim() ||
      "(unlabelled)"
    placed.push({ label, box })
  }
  return placed
}

/** Controls in `scope` whose box leaves the viewport horizontally. */
async function escaping(page: Page, scope: Locator): Promise<string[]> {
  const width = page.viewportSize()?.width ?? 0
  expect(width).toBeGreaterThan(0)

  return (await placedControls(scope))
    .filter(({ box }) => box.x < -0.5 || box.x + box.width > width + 0.5)
    .map(
      ({ label, box }) =>
        `${label}: ${Math.round(box.x)}..${Math.round(box.x + box.width)} of ${width}`,
    )
}

/**
 * Pairs of controls in `scope` that sit on top of each other.
 *
 * Containment alone is not enough, and finding that out cost a round: making
 * the action group shrink brought every button back inside 375px and left
 * `Preview Invoice` printed across `Next`. A control that is on screen and
 * unclickable is no better than one that is off it, and only a second property
 * separates the two.
 */
async function colliding(scope: Locator): Promise<string[]> {
  const placed = await placedControls(scope)
  const collisions: string[] = []
  for (let a = 0; a < placed.length; a += 1) {
    for (let b = a + 1; b < placed.length; b += 1) {
      const one = placed[a].box
      const two = placed[b].box
      const overlapX =
        Math.min(one.x + one.width, two.x + two.width) - Math.max(one.x, two.x)
      const overlapY =
        Math.min(one.y + one.height, two.y + two.height) -
        Math.max(one.y, two.y)
      if (overlapX > 0.5 && overlapY > 0.5) {
        collisions.push(`${placed[a].label} over ${placed[b].label}`)
      }
    }
  }
  return collisions
}

async function open(page: Page, invoice: RegExp): Promise<void> {
  await page.getByRole("row", { name: invoice }).click()
  await page.getByRole("button", { name: "Open" }).click()
  await page.getByRole("heading", { level: 1, name: invoice }).waitFor()
}

test("keeps every control on screen at phone width", async ({ page }) => {
  await page.setViewportSize(PHONE)

  await page.goto("/")
  await expect(
    page.getByRole("heading", { name: "Sign in to your application" }),
  ).toBeVisible()
  expect(await escaping(page, page.locator("body")), "sign-in").toEqual([])

  await signIn(page)
  // The grid and the header strip above it scroll horizontally together, by
  // design and on every renderer, so the browse is measured by its chrome
  // rather than by its columns.
  await expect(page.getByRole("row", { name: /INV-2026-0001/ })).toBeVisible()
  expect(await escaping(page, page.locator("header")), "app chrome").toEqual([])
  expect(
    await escaping(page, page.locator("[data-tide-toolbar]")),
    "browse toolbar",
  ).toEqual([])

  // Identity administration is framework chrome rather than an application
  // view, so nothing above reaches it -- and below the sidebar's breakpoint
  // the workspace select is the only way in, which is the route being
  // measured here as much as the screen is.
  await page
    .getByRole("combobox", { name: "Current workspace" })
    .selectOption("_tide.administration")
  await expect(page.getByRole("table", { name: "Accounts" })).toBeVisible()
  await page.getByRole("button", { name: "e2e" }).click()
  await expect(page.getByRole("button", { name: "Save roles" })).toBeVisible()
  expect(await escaping(page, page.locator("main")), "identities").toEqual([])
  expect(
    await colliding(page.locator("main")),
    "identities overlaps",
  ).toEqual([])
  await page
    .getByRole("combobox", { name: "Current workspace" })
    .selectOption("sales.Invoice.browse")
  await expect(page.getByRole("row", { name: /INV-2026-0001/ })).toBeVisible()

  // A draft: the editable renderer, with Save and the domain actions.
  await open(page, /INV-2026-0002/)
  await expect(page.getByText("Secured editor")).toBeVisible()
  expect(await escaping(page, page.locator("footer")), "draft action bar").toEqual(
    [],
  )
  expect(
    await colliding(page.locator("footer")),
    "draft action bar overlaps",
  ).toEqual([])
  expect(await escaping(page, page.locator("main")), "draft form").toEqual([])

  const select = page.getByRole("button", { name: "Select Customer" })
  await select.click()
  const lookup = page.getByRole("dialog", { name: "Select Customer" })
  await expect(lookup).toBeVisible()
  expect(await escaping(page, lookup), "lookup dialog").toEqual([])
  await page.keyboard.press("Escape")

  // A posted invoice is locked by its transition, so the read-only renderer
  // draws it and the action bar is a different set of buttons.
  await page.goBack()
  await open(page, /INV-2026-0001/)
  await expect(page.getByText("Secured detail")).toBeVisible()
  expect(
    await escaping(page, page.locator("footer")),
    "posted action bar",
  ).toEqual([])
  expect(
    await colliding(page.locator("footer")),
    "posted action bar overlaps",
  ).toEqual([])
})
