# Vendored Swagger UI

`swagger-ui-bundle.js` and `swagger-ui.css` are **swagger-ui-dist 5.32.13**,
Apache-2.0, unmodified. `LICENSE` is the package's own.

```text
swagger-ui-bundle.js  1,556,354 bytes  sha256 5f3be5d9cf40cdd60dca0dafeaf8743fd858d1b3bb717bbdaebf7201303f63d7
swagger-ui.css          185,733 bytes  sha256 9e617d9ac0afb0e430c11a17366de8624db7ce34c99ebd297443f0048ce30899
LICENSE                  11,358 bytes  sha256 cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30
```

Fetched from `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.13/<file>`,
which mirrors the npm package of the same name and version
(`sha512-qQobzb3DeC2LeK0j3E8812Ef4aIq1y9flJxvZkimkqUC/w4u7wS+yCc+VakqGJLweUUBrI24effhwo8OsAvNAw==`).

## Why these are in the repository

TIDE sends `Content-Security-Policy: … script-src 'self'` on every response
whenever it owns identities, which is the configuration `docs/WEB-UI.md`
describes. FastAPI's `/docs` is a CDN script tag, a CDN stylesheet, a CDN
favicon and an inline initialiser, so every part of it was refused and the page
rendered blank while still answering 200 — which is all the exposure tests had
ever asked of it.

The alternative to hosting these was adding a third-party origin to
`script-src` on a page that carries a session cookie. Hosting them also means
`/docs` needs no network: it was taking 14.6s to load from the CDN on the
machine where this was found, and does not load at all on an isolated one.

`/redoc` is still FastAPI's, and is still CDN-dependent and therefore still
blank under those headers. A second vendored megabyte for a second view of the
same document was not judged worth it; see `docs/DECISIONS.md`.

## Replacing them

Download the same three files at a pinned version, update the sizes and hashes
above, and run the suite — `tests/test_api_docs_exposure.py` asserts the page
references nothing external and that the assets are served and withheld with
the description. There is no build step: the files are served as they are.

```bash
curl -sSfL https://cdn.jsdelivr.net/npm/swagger-ui-dist@<version>/swagger-ui-bundle.js -o swagger-ui-bundle.js
```
