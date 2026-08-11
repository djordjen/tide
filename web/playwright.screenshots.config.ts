import base from "./playwright.config"
import { API_ORIGIN, API_PORT } from "./tests/screenshots/api"

/**
 * The same stack the journeys run against, pointed at a different directory.
 *
 * Captures are not tests: they write into `docs/images/`, they have no
 * assertions worth failing a build over, and a CI run that produced them would
 * leave a dirty tree behind. Sharing `webServer` and `baseURL` with
 * `playwright.config.ts` is the point -- a screenshot of a stack assembled
 * differently from the one the journeys certify would be a picture of
 * something this repository does not test.
 *
 * The second server is the quick start's: `tide serve --demo` behind a bearer
 * token, which is the configuration whose `/docs` a browser can render. See
 * `tests/screenshots/api-server.mjs`.
 */
export default {
  ...base,
  testDir: "./tests/screenshots",
  reporter: "list" as const,
  webServer: [
    base.webServer!,
    {
      command: `node tests/screenshots/api-server.mjs ${API_PORT}`,
      url: `${API_ORIGIN}/health/ready`,
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: "pipe" as const,
      stderr: "pipe" as const,
    },
  ],
}
