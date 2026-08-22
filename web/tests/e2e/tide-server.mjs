/**
 * Start the stack the documentation describes and leave a sign-in behind it.
 *
 * FastAPI hosts the built renderer at its own origin, authenticates against a
 * TIDE-owned identity store, and serves the application's demo data from
 * memory -- so a journey run against this exercises compilation, security,
 * persistence, the REST contract and the renderer together, rather than a
 * static copy of the bundle.
 *
 * Setup lives here rather than in `globalSetup` because Playwright starts a
 * web server *before* it runs global setup: by the time setup could create an
 * account, the server would already have opened the store without one. Doing
 * both here keeps the order honest -- the credentials are on disk before the
 * port accepts a connection, so a journey never has to wonder.
 */
import { randomBytes } from "node:crypto"
import { spawn, spawnSync } from "node:child_process"
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

// `session.ts` reads the same file. It is deliberately a plain constant in
// both places: a TypeScript spec cannot import this module, and one literal
// path is clearer than a build step that would let it.
const CREDENTIALS_FILE = ".tide/e2e-credentials.json"
const USERNAME = "e2e"
// Two roles: the application's own work, and administering who may sign in.
// The reference application grants the second nothing else, so a journey that
// wants both has to hold both -- which is also the arrangement a small
// deployment actually runs.
const ROLES = ["sales_clerk", "administrator"]

const repository = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
)
const port = process.argv[2]
const application = join(repository, "applications", "invoicing")
const webRoot = join(repository, "web", "dist")
const store = join(repository, ".tide", "e2e-auth.sqlite3")
const credentials = join(repository, CREDENTIALS_FILE)
// `spreadsheet` too, so the journeys meet the same export control a
// developer does: without it the manifest offers CSV alone and the
// toolbar renders a button where there would otherwise be a menu.
const tide = [
  "run",
  "--extra",
  "api",
  "--extra",
  "report",
  "--extra",
  "spreadsheet",
  "tide",
]

if (!existsSync(join(webRoot, "index.html"))) {
  console.error(
    "web/dist/index.html is missing. The journeys drive the built renderer, " +
      "so run `npm run build` before `npm run test:e2e`.",
  )
  process.exit(1)
}

// A password that only exists for this run, so nothing checked in or left
// behind can sign in to anything.
const password = randomBytes(24).toString("base64url")

mkdirSync(dirname(store), { recursive: true })
for (const stale of [store, `${store}-wal`, `${store}-shm`, credentials]) {
  rmSync(stale, { force: true })
}

const created = spawnSync(
  "uv",
  [
    ...tide,
    "auth",
    "create-user",
    application,
    "--store",
    store,
    "--username",
    USERNAME,
    "--display-name",
    "End to end",
    "--password-env",
    "TIDE_E2E_PASSWORD",
    ...ROLES.flatMap((role) => ["--role", role]),
  ],
  {
    cwd: repository,
    env: { ...process.env, TIDE_E2E_PASSWORD: password },
    stdio: ["ignore", "inherit", "inherit"],
  },
)
if (created.error) {
  console.error(
    `Could not run uv (${created.error.message}). The journeys need the ` +
      "Python side of TIDE; see docs/GETTING-STARTED.md.",
  )
  process.exit(1)
}
if (created.status !== 0) {
  process.exit(created.status ?? 1)
}

writeFileSync(
  credentials,
  `${JSON.stringify({ username: USERNAME, password }, null, 2)}\n`,
)

const server = spawn(
  "uv",
  [
    ...tide,
    "serve",
    application,
    "--demo",
    "--auth",
    "local",
    "--local-auth-store",
    store,
    "--web-root",
    webRoot,
    "--port",
    port,
    // Playwright forwards this to its own output. At `info` a passing run
    // buries the result under a line per request; at `warning` the only
    // things left are the ones worth reading when a journey fails.
    "--log-level",
    "warning",
  ],
  { cwd: repository, stdio: "inherit" },
)
server.on("error", (error) => {
  console.error(`Could not start the TIDE server: ${error.message}`)
  process.exit(1)
})
server.on("exit", (code) => process.exit(code ?? 1))
