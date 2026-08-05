import path from "node:path"
import { fileURLToPath } from "node:url"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

const root = path.dirname(fileURLToPath(import.meta.url))

// The renderer reads the same variable to build its request paths, so a
// development server that proxied a fixed `/api` would forward nothing once a
// deployment moved its API elsewhere.
const basePath = process.env.VITE_TIDE_BASE_PATH ?? "/api/v1"
const apiTarget = process.env.VITE_TIDE_API_TARGET ?? "http://127.0.0.1:8000"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(root, "src"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      [basePath]: {
        target: apiTarget,
        changeOrigin: false,
      },
      "/health": {
        target: apiTarget,
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: true,
    // The App suites drive whole journeys -- connect, browse, open, edit,
    // save, resolve a conflict -- through a real React tree in jsdom. The
    // longest genuinely takes a bit over two seconds on an idle machine, and
    // around four when the other suites are competing for a core. Vitest's
    // 5s default is a unit-test budget; against that, an ordinary run was
    // finishing with a couple of hundred milliseconds to spare and failing
    // outright whenever it did not.
    testTimeout: 15_000,
    // Five of these files drive whole applications through jsdom, and each one
    // is CPU-bound while it does. Letting vitest use every core runs them all
    // at once on a machine that cannot, so each takes several times longer in
    // wall-clock and the marginal ones start missing their waits -- adding a
    // single test file was enough to make the suite fail most runs. Capping
    // the pool costs a little total time and buys back the headroom.
    maxWorkers: 4,
    minWorkers: 1,
  },
})
