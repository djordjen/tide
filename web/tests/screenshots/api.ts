/** Where `api-server.mjs` listens. Read by the config that starts it and by
 * the capture that navigates to it, so the two cannot drift apart. */
export const API_PORT = 4174
export const API_ORIGIN = `http://127.0.0.1:${API_PORT}`
