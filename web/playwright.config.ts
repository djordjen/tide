import { defineConfig, devices } from "@playwright/test"

const port = 4173

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  // A retry re-runs a journey against a server the first attempt has already
  // written to, which is not the same test. Every journey creates what it
  // writes to so that is survivable, but a green retry would still be a
  // weaker claim than a green first run -- so a failure here is a failure.
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: process.env.TIDE_PLAYWRIGHT_CHANNEL,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `node tests/e2e/tide-server.mjs ${port}`,
    url: `http://127.0.0.1:${port}/health/ready`,
    reuseExistingServer: !process.env.CI,
    // Compiling the application and seeding demo data takes longer than
    // Playwright's default on a cold uv cache.
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
})
