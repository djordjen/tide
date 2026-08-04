import path from "node:path"
import { fileURLToPath } from "node:url"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

const root = path.dirname(fileURLToPath(import.meta.url))

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
      "/api": {
        target: process.env.VITE_TIDE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: false,
      },
      "/health": {
        target: process.env.VITE_TIDE_API_TARGET ?? "http://127.0.0.1:8000",
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
  },
})
