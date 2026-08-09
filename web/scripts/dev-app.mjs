/**
 * Run one TIDE application's API and the Vite dev server side by side.
 *
 * This exists so `package.json` does not grow an entry per application. The
 * flags that have to track the `tide serve` contract -- `--auth local`, the
 * identity store, the port -- are written once here; a second copy is a second
 * place to forget when the CLI changes, which is exactly how the Windows
 * shortcut ended up passing `--customers` to a command that had replaced it.
 *
 * Usage from the `web` directory:
 *
 *   npm run dev:app -- --app contacts
 *   npm run dev:app -- --app invoicing --extra report --database-env
 */
import { spawn } from "node:child_process"

const DEFAULT_PORT = "8000"
// Invoicing's store predates the `<app>-local-auth.sqlite3` convention below.
// Renaming it would strand the administrator account developers have already
// created, so the one exception is recorded here rather than everywhere.
const STORE_OVERRIDES = { invoicing: ".tide/local-auth.sqlite3" }
// The command is handed to a shell, so a value that reached it unchecked could
// carry its own. Everything accepted here is a name or a path -- a leading dot
// included, because every store lives under `.tide`; a leading dash is not,
// because it would be read as a flag.
const SAFE = /^[A-Za-z0-9.][A-Za-z0-9._/\\-]*$/

function fail(message) {
  console.error(message)
  process.exit(2)
}

function parse(argv) {
  const options = {
    app: "invoicing",
    extras: [],
    store: "",
    port: DEFAULT_PORT,
    demo: true,
    print: false,
  }
  let index = 0
  while (index < argv.length) {
    const flag = argv[index]
    const next = argv[index + 1]
    if (flag === "--database-env") {
      options.demo = false
      index += 1
      continue
    }
    if (flag === "--print") {
      options.print = true
      index += 1
      continue
    }
    if (next === undefined || next.startsWith("--")) {
      fail(`${flag} needs a value.`)
    }
    if (flag === "--app") options.app = next
    else if (flag === "--extra") options.extras.push(next)
    else if (flag === "--store") options.store = next
    else if (flag === "--port") options.port = next
    else fail(`Unknown option: ${flag}`)
    index += 2
  }
  return options
}

const options = parse(process.argv.slice(2))
const store =
  options.store ||
  STORE_OVERRIDES[options.app] ||
  `.tide/${options.app}-local-auth.sqlite3`

for (const [name, value] of [
  ["--app", options.app],
  ["--store", store],
  ...options.extras.map((extra) => ["--extra", extra]),
]) {
  if (!SAFE.test(value)) fail(`${name} ${value} is not a name or a path.`)
}
if (!/^[0-9]+$/.test(options.port)) fail(`--port ${options.port} is not a port.`)

const extras = ["api", "client", ...options.extras].flatMap((name) => [
  "--extra",
  name,
])
// Relative to the repository root, which is where `cd ..` lands: an absolute
// path here would need quoting the moment a checkout lives under a directory
// with a space in its name, and this one does.
const server = [
  "cd",
  "..",
  "&&",
  "uv",
  "run",
  ...extras,
  "tide",
  "serve",
  `applications/${options.app}`,
  options.demo ? "--demo" : "--database-env",
  "--auth",
  "local",
  "--local-auth-store",
  store,
  "--port",
  options.port,
].join(" ")

// `--print` is how the command is checked without starting anything, and how a
// developer sees what a shortcut is about to run.
if (options.print) {
  console.log(server)
  process.exit(0)
}

const runner = spawn(
  [
    "concurrently",
    "--kill-others",
    "--names",
    "api,web",
    "--prefix-colors",
    "blue,green",
    `"${server}"`,
    '"vite --open"',
  ].join(" "),
  { stdio: "inherit", shell: true },
)
runner.on("error", (error) => {
  console.error(`Could not start the dev servers: ${error.message}`)
  process.exit(1)
})
runner.on("exit", (code) => process.exit(code ?? 1))
