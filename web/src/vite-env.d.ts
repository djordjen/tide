/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Where this deployment's API lives, relative to the origin serving the page.
   *
   * Matches the server's `build_fastapi_app(base_path=...)`. Defaults to
   * `/api/v1`, which is also the server's default, so an ordinary deployment
   * sets nothing.
   */
  readonly VITE_TIDE_BASE_PATH?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
