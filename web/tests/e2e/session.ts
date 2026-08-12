import { readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { expect, type Page } from "@playwright/test"

// Written by `tide-server.mjs` before the port opens, so it is always there by
// the time a journey runs. Keep the path in step with that file.
const CREDENTIALS_FILE = ".tide/e2e-credentials.json"

const repository = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
)

/**
 * Sign in the way a person does: through the server's own password form.
 *
 * The heading is asserted rather than assumed. The renderer shows a token box
 * when nothing told it the server owns identities, so a manifest that failed
 * to reach the browser would otherwise look like a typing mistake ten lines
 * later.
 */
export async function signIn(page: Page): Promise<void> {
  const { username, password } = JSON.parse(
    readFileSync(join(repository, CREDENTIALS_FILE), "utf8"),
  ) as { username: string; password: string }

  await page.goto("/")
  await expect(
    page.getByRole("heading", { name: "Sign in to your application" }),
  ).toBeVisible()
  await page.getByLabel("Username").fill(username)
  await page.getByLabel("Password").fill(password)
  await page.getByRole("button", { name: "Sign in" }).click()
  // Either shell counts as arrival. Below the sidebar's breakpoint the same
  // navigation collapses into one workspace select, so a journey run at phone
  // width would otherwise wait for a landmark that is deliberately absent.
  await expect(
    page
      .getByRole("navigation", { name: "Application navigation" })
      .or(page.getByRole("combobox", { name: "Current workspace" })),
  ).toBeVisible()
}

/**
 * A value no other run has used, so a journey that writes can be run again --
 * including by Playwright's own retry -- without tripping over last time.
 */
export function unique(prefix: string): string {
  return `${prefix}${Date.now().toString(36).toUpperCase()}`
}

/** The row a save just created: the grid selects it and nothing else. */
export function createdRow(page: Page) {
  return page.getByRole("row", { selected: true })
}
