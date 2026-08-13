import { mkdirSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { expect, test } from "@playwright/test"

import { signIn } from "../e2e/session"

/**
 * Capture the Web UI and the generated API description for the documentation.
 *
 * Run with `npm run screenshots`, never as part of `test:e2e`: these write
 * into the repository, and a suite whose success is measured by files it left
 * behind is not a suite. What they do share with the journeys is the stack --
 * same server, same sign-in, same compiled application and demo data -- so
 * what a reader sees here is what `tide serve applications/invoicing --demo`
 * puts on screen.
 */

const images = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "docs",
  "images",
)

const VIEWPORT = { width: 1280, height: 820 }

test.beforeAll(() => {
  mkdirSync(images, { recursive: true })
})

test("invoice browse and the generated record editor", async ({ page }) => {
  await page.setViewportSize(VIEWPORT)
  await signIn(page)

  await expect(page.getByRole("row", { name: /INV-2026-0001/ })).toBeVisible()
  await page.screenshot({ path: join(images, "tide-web-invoices.png") })

  // `Open` lands on the editor, not on a read-only detail: TIDE renders one
  // record screen and lets the model decide which fields it may write.
  await page.getByRole("row", { name: /INV-2026-0002/ }).click()
  await page.getByRole("button", { name: "Open" }).click()
  await expect(
    page.getByRole("heading", { level: 1, name: /INV-2026-0002/ }),
  ).toBeVisible()
  await expect(page.getByRole("row", { name: /Priority support/ })).toBeVisible()
  await page.screenshot({ path: join(images, "tide-web-invoice.png") })
})

test("generated OpenAPI description", async ({ page }) => {
  // Shorter than the others: one entity's generated operations are the
  // subject, and the schema catalogue below them is not.
  await page.setViewportSize({ width: VIEWPORT.width, height: 540 })

  // The journeys' server. It used to take one of its own, because TIDE's
  // browser security headers refused FastAPI's CDN-hosted Swagger UI and this
  // page came out blank; the assets are TIDE's own now.
  await page.goto("/docs")
  await expect(page.locator(".opblock").first()).toBeVisible()

  // Open on the operations the model generated, not on the health checks and
  // the `_tide` routes that happen to sort first. `boundingBox` rather than
  // `evaluate`: this project is compiled without the DOM library, on purpose.
  const invoices = page.locator('.opblock-tag[data-tag="Invoices"]')
  const heading = await invoices.boundingBox()
  await page.mouse.wheel(0, (heading?.y ?? 0) - 16)
  await expect(page.getByText("/api/v1/invoices").first()).toBeVisible()
  await page.screenshot({ path: join(images, "tide-api-docs.png") })
})
