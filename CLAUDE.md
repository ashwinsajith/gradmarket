# GradMarket

Daily collection of UK graduate and internship postings from company ATS
boards (Greenhouse, Lever, Ashby). Semantic search over the results. Solo
student project, ships early October 2026.

Deferred work (things noticed but not built) lives in [IDEAS.md](IDEAS.md).

## Commands
- Scraper:  `python -m gradmarket.ingest`
- Parser:   `python -m gradmarket.parse_run`
- Pipeline: `python -m gradmarket.pipeline` (ingest then parse; owns the healthcheck ping for both — use this in production, not the two commands chained)
- Tests:    `pytest`
- Lint:     `ruff check .`

## Architecture
Railway: Postgres + a cron service. Local dev connects via DATABASE_PUBLIC_URL; deployed services use the private
DATABASE_URL. Code reads one env var name, value differs per environment.

## Data model — read before touching the schema

Raw first, parse later. The collector writes untouched JSON to `raw_fetches`.
Parsing runs as a separate pass over that archive. A parser bug is
recoverable; a collection gap is not.

Postings are observed over time, not stored once:
- Identity is (source, company, external_id). Upsert on that key, never insert
  duplicates.
- `first_seen_at` is set once and NEVER updated.
- `last_seen_at` updates on every run where the posting appears in the feed.
- A posting missing from the feed sets `is_open = false`. This is our proxy for
  filled/withdrawn and it's the most valuable derived signal in the project.
- `closed_at` is set once, like `first_seen_at` — a posting that reopens and
  closes again later does NOT get a new `closed_at`. Close-detection's UPDATE
  guards on `closed_at IS NULL` for exactly this reason.
- Close-detection skips a company entirely (no closures applied, just logs a
  warning) if its feed came back empty, or if it dropped by more than 50%
  since the last successful fetch — but only when that previous fetch had at
  least 10 postings. Below that, a real drop looks identical to a collapse,
  so small boards are never guarded.
- NEVER delete a posting row. Disappearance is data.
- `posting_versions` appends a row only when the content hash changes.
- Lever's `raw_fetches.payload` is a concatenation of paginated responses, not
  a single verbatim server response, and is a bare JSON array rather than a
  `{"jobs": [...]}` object like Greenhouse/Ashby. Both matter for the parsing
  layer.

## Gotchas
- A 200 response with an empty jobs array does NOT mean all jobs closed. It usually means the company switched ATS provider. Treating it as closure corrupts history for every posting they had. Handle empty-but-200 distinctly.
- Greenhouse returns descriptions as HTML. Strip before embedding.
- A company migrating between ATS providers appears as two identities, since identity includes `source`. The same job will look closed on the old board and newly-posted on the new one, producing a false close and a false first_seen_at. Not handled yet — needs a merge rule once we have a second source.

## Constraints
- Job description text is the companies' copyright. Store privately, never
  republish. Public surfaces show links plus our derived fields only.
- Rate limit ~1 req/sec per host, exponential backoff on 429 and 5xx.
- Scraped text is UNTRUSTED INPUT. Parse as data. Never feed it to anything
  that can take actions.
- No secrets in code. Env vars only.

## Conventions
- Type hints everywhere. Prefer boring, stable libraries.
- Tests use recorded fixtures, never live HTTP calls.
- Sources must be interchangeable. Each module in `sources/` exposes the same interface and returns a normalised shape. `ingest.py` must contain no provider-specific logic.

## Long-running operations
- Do not execute `parse_run --full`, full ingests, or anything expected to
take more than ~60 seconds. Tell me the command and I'll run it myself.
Polling a long operation wastes context for no benefit.