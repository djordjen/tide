import { createReadStream, existsSync, statSync } from "node:fs"
import { createServer, type Server } from "node:http"
import { extname, join, normalize, resolve } from "node:path"
import { expect, test } from "@playwright/test"

const root = resolve("dist")
const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".svg", "image/svg+xml"],
])
let server: Server

test.beforeAll(
  () =>
    new Promise<void>((ready, reject) => {
      server = createServer((request, response) => {
        const pathname = new URL(
          request.url ?? "/",
          "http://127.0.0.1",
        ).pathname
        const relative = normalize(pathname).replace(/^[/\\]+/, "")
        let file = join(root, relative || "index.html")
        if (
          !file.startsWith(root) ||
          !existsSync(file) ||
          !statSync(file).isFile()
        ) {
          file = join(root, "index.html")
        }
        response.setHeader(
          "Content-Type",
          contentTypes.get(extname(file)) ?? "application/octet-stream",
        )
        createReadStream(file).pipe(response)
      })
      server.once("error", reject)
      server.listen(4173, "127.0.0.1", ready)
    }),
)

test.afterAll(
  () =>
    new Promise<void>((closed, reject) => {
      server.close((error) => (error ? reject(error) : closed()))
    }),
)

test("shows the secure TIDE application connection surface", async ({
  page,
}) => {
  await page.goto("/")

  await expect(
    page.getByRole("heading", { name: "Connect to an application" }),
  ).toBeVisible()
  await expect(page.getByLabel("Application token")).toHaveAttribute(
    "type",
    "password",
  )
  await expect(
    page.getByText("The browser receives no database URL"),
  ).toBeVisible()
})
