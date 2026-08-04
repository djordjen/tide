import "@testing-library/jest-dom/vitest"

import { configure } from "@testing-library/react"
import { vi } from "vitest"

// `findBy*` and `waitFor` keep their own budget, separate from the suite's
// `testTimeout`. It defaults to one second, which is a unit-test figure: these
// journey tests wait on a debounced search and a mocked round trip while the
// other suites compete for a core, and a query that needs 1.1s of that fails
// its own wait long before the test runs out of time.
configure({ asyncUtilTimeout: 5_000 })

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

class TestResizeObserver implements ResizeObserver {
  readonly root = null
  readonly rootMargin = ""
  readonly thresholds = []
  private readonly callback: ResizeObserverCallback

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
  }

  observe(target: Element) {
    this.callback(
      [
        {
          target,
          contentRect: target.getBoundingClientRect(),
          borderBoxSize: [],
          contentBoxSize: [],
          devicePixelContentBoxSize: [],
        },
      ],
      this,
    )
  }
  unobserve() {}
  disconnect() {}
  takeRecords(): ResizeObserverEntry[] {
    return []
  }
}

vi.stubGlobal("ResizeObserver", TestResizeObserver)

Object.defineProperty(HTMLElement.prototype, "getBoundingClientRect", {
  configurable: true,
  value: () => ({
    bottom: 640,
    height: 640,
    left: 0,
    right: 1024,
    top: 0,
    width: 1024,
    x: 0,
    y: 0,
    toJSON: () => undefined,
  }),
})

Object.defineProperties(HTMLElement.prototype, {
  offsetHeight: {
    configurable: true,
    get: () => 640,
  },
  offsetWidth: {
    configurable: true,
    get: () => 1024,
  },
})

Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  configurable: true,
  value: () => ({
    font: "",
    measureText: (value: string) => ({ width: value.length * 7 }),
  }),
})
