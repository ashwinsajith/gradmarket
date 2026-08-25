# GradMarket

Daily collection of UK graduate and internship postings from company ATS
boards (Greenhouse, Lever, Ashby, Workable). Semantic search over the
results. Solo student project, ships early October 2026.

Deferred work (things noticed but not built) lives in [IDEAS.md](IDEAS.md).

## Commands
- Scraper:    `python -m gradmarket.ingest`
- Parser:     `python -m gradmarket.parse_run`
- Classifier: `python -m gradmarket.classify_run`
- Pipeline:   `python -m gradmarket.pipeline` (ingest, parse, classify, in order; owns the healthcheck ping for all three — use this in production, not the commands chained)
- Tests:      `pytest`
- Lint:       `ruff check .`

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
- Workable jobs have no `id` field at all — `shortcode` is the unique
  identifier and is what `external_id` maps to for this source. Every other
  source has an `id` field; a future refactor that assumes all sources do
  will silently break Workable specifically.
- Workable account slugs are NOT namespaced to real company identity, unlike
  Greenhouse/Lever/Ashby tokens. `apply.workable.com/api/v1/widget/accounts/
  notion` resolves to an unrelated small London agency, not the software
  company. Verifying a Workable token can't lean on "the name matches the
  company" the way it can for the other three sources.
- A 200-with-zero-jobs Workable response is a real, resolved account, same
  as the other three sources — a nonsense token 404s, it doesn't 200 with
  zero jobs. (Earlier note here claimed the opposite — that zero jobs meant
  a wrong/squatted account — which was wrong and has been corrected.)
  `scripts/check_tokens.py`'s `--source workable` handling still separates
  out a small "likely shell" bucket from zero-job accounts, but only when
  BOTH the account name is token-identical (case-insensitive) AND the
  account description is empty — e.g. `buffer`/`intercom`/`zapier`/`gitlab`
  currently fail this way. A zero-job account with a distinct name or a
  real description is treated as working, same as any other source's empty
  board.
- Workable enforces a daily request quota, not a short rate-limit window —
  observed a 429 with `Retry-After: 82392` (~22.9h). `scripts/check_tokens.py`
  runs against the same quota as the daily collection ingest; a discovery run
  against Workable costs a day of Workable data. `sources/workable.py`'s
  `fetch()` gives up immediately on a 429 whose `Retry-After` exceeds
  `MAX_RETRY_AFTER_SECONDS` rather than retrying — backoff can't outlast a
  day-long block.
- Workable payloads can contain the same `shortcode` twice within a single
  response — observed in production (`instanda` returned `ED3D202D57` twice,
  `universalquantum` returned `290D8C2160` twice). Not a pagination
  artefact, since Workable doesn't paginate. This broke the parse layer's
  multi-row upsert with `CardinalityViolation: ON CONFLICT DO UPDATE command
  cannot affect row a second time`. Deduplication now happens in two layers:
  `sources/workable.py` on fetch (keyed on `shortcode`), and
  `parse_run.process_row` defensively for any source (keyed on
  `external_id`) — both keep the last occurrence. Lever's `fetch()` also
  deduplicates (its skip/limit pagination can in principle re-read an item
  that shifted across a page boundary), but that one is precautionary — it's
  never actually fired the way Workable's has.
- `location_class`/`seniority_class`/`classified_at` tag a posting; they
  never cause one to be closed or deleted. Classification is a separate pass
  over `postings` (`classify_run.py`), same shape as parsing over
  `raw_fetches` — idempotent via `classified_at IS NULL`, `--full` to
  reclassify everything. The classifiers themselves (`gradmarket/classify/`)
  are pure functions with no DB access, so tuning the rules never needs a
  rebuild, just a re-run.

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
- Sources must be interchangeable. Each module in `sources/` exposes the same interface (`fetch(token) -> FetchResult`, `INTER_REQUEST_SLEEP`) and returns a normalised shape. `ingest.py` must contain no provider-specific logic — it reads pacing from the module rather than applying one delay to every source, since Workable rate-limits far more aggressively than Greenhouse/Lever/Ashby (1s vs 5s).

## Long-running operations
- Do not execute `parse_run --full`, full ingests, or anything expected to
take more than ~60 seconds. Tell me the command and I'll run it myself.
Polling a long operation wastes context for no benefit.

## Evaluation data
- `data/eval/labels.csv` — union of all labelling batches (250 rows). Rules were developed while reading these, so accuracy measured on it is optimistic.
- `data/eval/holdout.csv` — 50 rows labelled after the classifier was written, never used for rule development. This is the honest number. Do not tune against it.