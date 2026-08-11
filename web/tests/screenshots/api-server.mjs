/**
 * The quick start's server, for one screenshot: `tide serve --demo` with a
 * bearer token and no TIDE-owned sign-in.
 *
 * Deliberately a second server rather than a mode on `tide-server.mjs`. TIDE
 * sends its browser security headers only when it owns identities, and one of
 * them is a `script-src 'self'` policy that FastAPI's Swagger UI -- a CDN
 * script tag plus an inline initialiser -- cannot satisfy. So `/docs` renders
 * under this configuration and is blank under the journeys' one, and a
 * capture that quietly reused their server would be a picture of an empty
 * page. Which configuration a reader is looking at is part of what the
 * screenshot says.
 */
import { randomBytes } from "node:crypto"
import { spawn } from "node:child_process"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const repository = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
)
const port = process.argv[2]

const server = spawn(
  "uv",
  [
    "run",
    "--extra",
    "api",
    "--extra",
    "report",
    "tide",
    "serve",
    join(repository, "applications", "invoicing"),
    "--demo",
    "--port",
    port,
    "--log-level",
    "warning",
  ],
  {
    cwd: repository,
    // A token that exists for this run only, so nothing checked in or left
    // behind can call the API. `tide serve` requires at least 32 characters.
    env: { ...process.env, TIDE_API_TOKEN: randomBytes(32).toString("base64url") },
    stdio: "inherit",
  },
)
server.on("error", (error) => {
  console.error(`Could not start the TIDE server: ${error.message}`)
  process.exit(1)
})
server.on("exit", (code) => process.exit(code ?? 1))
