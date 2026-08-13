import base from "./playwright.config"

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
 * It briefly took a second server, because `/docs` could not render under the
 * security headers this one sends. TIDE hosts Swagger UI itself now, so one
 * server draws every capture again.
 */
export default {
  ...base,
  testDir: "./tests/screenshots",
  reporter: "list" as const,
}
