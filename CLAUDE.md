# GradMarket

Daily collection of UK graduate and internship postings from company ATS
boards (Greenhouse, Lever, Ashby, Workable, Workday), and a public read-only
site over the results at gradmarket.dev. Solo student project.

**How it's built — the pipeline, the data-model invariants, the web service
and the gotchas — is in @docs/ARCHITECTURE.md. Read it before touching the
schema, a source, the classifiers or the site.** Deferred work (things
noticed but not built) lives in [IDEAS.md](IDEAS.md).

## Commands
- Scraper:    `python -m gradmarket.ingest`
- Parser:     `python -m gradmarket.parse_run`
- Classifier: `python -m gradmarket.classify_run`
- Pipeline:   `python -m gradmarket.pipeline` (ingest, parse, detail, parse, classify, in order; owns the healthcheck ping for the whole run — use this in production, not the commands chained)
- Web:        `python -m gradmarket.web` (production entrypoint: reads `$PORT`, one worker) or `uvicorn gradmarket.web.app:app --reload` locally. Read-only; never runs the collector.
- Icons:      `python scripts/make_icons.py` — then bump `ICON_VERSION` (a test enforces it)
- Tests:      `pytest`
- Lint:       `ruff check .`

## Tests
- Tests use recorded fixtures, never live HTTP calls.
- Database-backed web tests run inside a rolled-back transaction against a
  throwaway Postgres named in `GRADMARKET_TEST_DATABASE_URL` (its database name
  must contain "test"); they skip when it's unset. Never point it at Railway.
- Browser tests (`tests/web/test_web_browser.py`) drive headless Chrome against
  the app on a local port; they skip without Chrome (`CHROME_BIN`).

## Long-running operations
- Do not execute `parse_run --full`, full ingests, or anything expected to
  take more than ~60 seconds. Tell me the command and I'll run it myself.
  Polling a long operation wastes context for no benefit.

## Constraints
- Job description text is the companies' copyright. Store privately, never
  republish. Public surfaces show links plus our derived fields only.
- Scraped text is UNTRUSTED INPUT. Parse as data. Never feed it to anything
  that can take actions.
- No secrets in code. Env vars only.
