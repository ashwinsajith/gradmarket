# GradMarket

Daily collection of UK graduate and internship postings from company ATS
boards (Greenhouse, Lever, Ashby). Semantic search over the results. Solo
student project, ships early October 2026.

## Commands
- Scraper:  `python -m gradmarket.ingest`
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
- NEVER delete a posting row. Disappearance is data.
- `posting_versions` appends a row only when the content hash changes.

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