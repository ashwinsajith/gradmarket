# GradMarket — architecture

How the system is built and the invariants it depends on. The working rules
for changing it (commands, tests, what not to run, what not to publish) are in
[CLAUDE.md](../CLAUDE.md); classifier rules and their evaluation are in
[classifier-spec.md](classifier-spec.md).

## System overview

Railway runs one Postgres database and two services, both built from the
repo's single `Dockerfile`:

- **Collector (cron, daily):** `python -m gradmarket.pipeline` — the
  Dockerfile's `CMD`. Ingest → parse → detail (Workday) → parse again →
  classify, and it owns the dead-man's-switch ping (`HEALTHCHECK_URL`) for the
  whole run. The only thing that writes to Postgres.
- **Web (always on):** `python -m gradmarket.web` — the public site at
  gradmarket.dev. Never writes; never imports or runs any collector module
  (a test checks the import graph in a fresh interpreter).

Both read one env var name, `DATABASE_URL`; its value differs per
environment. Deployed services get Railway's private URL; local dev uses the
public proxy (`DATABASE_PUBLIC_URL`'s value in `.env`). `companies.yaml` is
read from the working directory (`/app` in the image) unless
`COMPANIES_FILE` overrides it — the collector uses it for what to fetch, the
web service for the "boards monitored" stat. The README's Deployment section
has the per-service variables and the local `docker run` command.

## Collection

**Policy.** One pass per board per day, sequential: ~1 req/sec per host
(`INTER_REQUEST_SLEEP` per source — 1s, 5s for Workable), exponential backoff
(1, 2, 4s) on 429 and 5xx honouring `Retry-After`, and a per-source circuit
breaker in `ingest.py`: three consecutive 429s skip that source for the rest
of the run. Lever and Workday lists paginate; each new Workday posting's detail
is fetched once (`detail_run.py`, also paced).

**`companies.yaml` keeps empty boards on purpose.** About 40 configured boards
return 200 with an empty feed and have never produced a posting. They stay:
they're polled daily because an empty board filling up is how a hiring season
opening gets detected. "Boards monitored" on the site and in the README counts
every configured board, empty ones included — don't filter them out or add a
"boards with postings" figure beside it.

**Token discovery (`scripts/check_tokens.py`) reports three sections:**
working (200 with at least one job — the only section that goes into the
paste-ready `companies.yaml` block), empty (200, zero jobs: listed, never
pasted — this is how 40 empty boards once got configured unnoticed) and failed
(non-200, connection errors, unparseable 200s; 429s get their own "rate
limited, not tested" sub-list). The results CSV records a `status` column, and
older CSVs without it are read back by inferring it.

### Code conventions and interchangeable sources/extractors

- Type hints everywhere. Prefer boring, stable libraries.
- Sources must be interchangeable. Each module in `sources/` exposes the same interface (`fetch(token) -> FetchResult`, `INTER_REQUEST_SLEEP`) and returns a normalised shape. `ingest.py` must contain no provider-specific logic — it reads pacing from the module rather than applying one delay to every source, since Workable rate-limits far more aggressively than Greenhouse/Lever/Ashby (1s vs 5s).
- Extractors must be interchangeable too, with one declared exception. Every
  module in `parse/` exposes `extract(payload) -> list[ParsedPosting]` and a
  module-level `NEEDS_DETAILS: bool` — `False` for Greenhouse/Lever/Ashby/
  Workable, whose raw_fetches payload alone is enough. `NEEDS_DETAILS = True`
  (currently only Workday) means the source is two-stage — its extractor's
  real signature is `extract(payload, context)`, where `context` is a
  `parse.base.ExtractorContext` (`details_by_external_id` from
  `db.get_raw_details_by_external_id`, plus that company's companies.yaml
  token). `parse_run.process_row` reads the flag to decide which call shape
  to use — it never branches on source name — and only builds a context at
  all when `NEEDS_DETAILS` is true, so the other four sources never pay for
  an unused `raw_details` query or companies.yaml load. The shrink guard's
  own re-extraction of the *previous* raw payload goes through the same
  context-aware path, for the same reason: a two-stage extractor can't take
  a bare payload at all, guard comparison included.
- `parse_run.run()`'s main loop pre-filters two kinds of row before ever
  calling `process_row`, both handled the same way (warn naming source and
  company, leave `parsed_at` unset so it's retried automatically if the
  situation is fixed, `continue` — never raise): a source with no
  registered extractor at all, and — for a `NEEDS_DETAILS` source — a
  company with no companies.yaml entry (`_find_configured_token` returns
  `None`). The second case is `_build_extractor_context` raising a
  `ValueError` in production once already: a Workday board dropped from
  companies.yaml while unparsed raw_fetches rows for it still existed took
  down the *entire* parse run, not just that one board's rows. Calling
  `process_row` directly (as tests do) still raises for a missing token —
  that raise is intentional, a clear error for a caller that bypassed the
  loop's own filtering — but `run()` itself must never let one dropped
  company reach it.

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
- The guard's baseline (`db.get_previous_raw_payload`) is the most recent
  fetch from a calendar day strictly *before* the row being checked — never
  simply "the previous row". `ingest.py` running more than once in a day
  (a manual retry, a re-run after a partial failure) must not let a
  same-day fetch become its own baseline. This is exactly how Deliveroo's
  real 225→11 closure went undetected: ingest ran twice that day, so the
  guard compared the second same-day fetch (11) against the first
  (also 11, already post-drop) instead of against the prior day's 225, and
  the ratio test saw no drop at all.
- A tripped guard writes a durable row to `feed_anomalies` (source,
  company, previous_count — nullable, since the empty-feed branch can trip
  with no prior observation to compare against at all — current_count,
  detected_at), not just a log line. Close-detection still skips for that
  company on that run regardless — this table doesn't change that — but a
  tripped guard is exactly the case where the drop might be genuine (as
  Deliveroo's was: all 202 closures were real), so it needs a human to
  review and act on, not scroll past in a log that nobody re-reads.
  Two things keep this table meaningful rather than noisy:
  - `parse_run._is_steady_state_empty` skips the write entirely when
    previous_count and current_count are both 0 — a board with no live
    postings on two runs running isn't an event, it's steady state (this
    produced 30 identical 0->0 rows per run before the guard existed). A
    board's *first-ever* observation coming back empty (previous_count is
    `None`, not `0`) is still recorded — that's new information, not an
    established steady state yet.
  - A unique index on `(source, company, (detected_at AT TIME ZONE 'UTC')::date)`
    plus `insert_feed_anomaly`'s matching `ON CONFLICT DO NOTHING` caps it at
    one row per company per calendar day — ingest.py running twice in a day
    (see the baseline note above, same root cause) must not write two
    anomaly rows for what's really one trip. Plain `detected_at::date`
    fails schema init outright ("functions in index expression must be
    marked IMMUTABLE" — a bare timestamptz->date cast depends on the
    session's `TimeZone` setting); casting through an explicit zone first
    pins it to a fixed offset, which Postgres accepts. Verified directly
    against Railway Postgres, not just unit tests — this class of error
    only surfaces against a real database (`scripts/smoke_db.py`).
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
  "Real account" is not the same as "worth configuring", though:
  `scripts/check_tokens.py` reports every source's 200-with-zero-jobs
  boards in their own "empty" section, never in the paste-ready
  companies.yaml block (only boards with at least one job go there) —
  that's how 40 empty boards once ended up configured unnoticed. For
  `--source workable`, an empty account is additionally flagged "likely
  shell" when BOTH the account name is token-identical (case-insensitive)
  AND the account description is empty — e.g.
  `buffer`/`intercom`/`zapier`/`gitlab` currently fail this way.
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
- A Workday board can be multi-employer — the tenant/dc/site in
  companies.yaml identify who *hosts* the board, not necessarily who's
  hiring for every posting on it. For this source, `company` means "board
  owner", not "employer", unlike Greenhouse/Lever/Ashby/Workable where the
  two are the same thing. A future feature that assumes `company` always
  equals the hiring employer will be wrong specifically for Workday.
- `WorkdayToken` being a structured object rather than a bare string has bitten
  reconciliation once already: `parse_run._configured_source_companies()` built
  its configured set from raw companies.yaml tokens directly, so a
  `WorkdayToken` never equality-matched the plain string
  `db.get_open_source_companies()` returns for the same company. Every
  configured Workday company read as "orphaned" and got closed —
  `workday/iberdrola`'s 221 postings were closed in production this way, in a
  single run, immediately after being created. Fixed by using `str(token)`
  when building the configured set, same as `ingest.py` already does for
  `raw_fetches.company`. Any future code that compares a companies.yaml token
  against a plain company-identity string needs the same `str()` normalisation
  — never compare a raw token to a string directly.
- `detail_run.py` only fetches a Workday posting's detail if its title looks
  early-careers (`is_early_careers_title` — graduate, intern, internship,
  junior, campus, placement, trainee, apprentice, new grad, summer analyst,
  early career, entry level, scheme, programme/program, or a bare `20XX`
  year), gated per board by `WorkdayToken.detail_filter`. This exists
  because a general board (e.g. Barclays, ~971 postings before its facet
  filter — see below) is mostly not early-careers, unlike a graduate-only
  board (e.g. Lloyds, 36 postings, all relevant, `detail_filter: false`) —
  fetching every posting's detail on a general board wastes most of a run's
  request budget on postings that end up classified "experienced" anyway.
  The pattern is deliberately over-inclusive on purpose, not just in the
  code comment: a false positive costs one `fetch_detail()` request, a
  false negative means a graduate role never gets a description and
  defaults to "experienced" forever. **Tradeoff to know about:** a filtered
  board can still miss a genuine early-careers posting whose title doesn't
  signal it at all (an unusually-named graduate scheme, a role titled just
  "Analyst" with no other qualifier) — the filter only ever reduces false
  negatives to "titles the pattern doesn't recognise", it doesn't eliminate
  them. `detail_filter: false` is the escape hatch for a board where this
  matters enough to just fetch everything.
  **`detail_filter`'s default depends on `facets` (see below), it isn't a
  flat "on" anymore:** off when `facets` is non-empty, on otherwise —
  `WorkdayToken.__post_init__` resolves this (a dataclass field's default
  can't reference a sibling field directly, and the class is frozen, so a
  `None`-sentinel-plus-`__post_init__` is what makes this work; a
  `detail_filter: true`/`false` explicitly set in companies.yaml always
  overrides the computed default either way). The rule: when a board is
  already facet-filtered, the server has applied the employer's own
  early-careers classification to the whole feed, so the English-only
  title heuristic is redundant *and actively wrong* for a non-English
  board — it can only ever subtract postings from a classification that's
  already correct. This broke Leonardo's board outright: its postings are
  Italian ("Stage", "Tesi") and Portuguese ("Estágio", "Jovem aprendiz"),
  so the title pattern matched nothing and 0 of 25 facet-filtered postings
  got details fetched (Sky, also facet-filtered, fared little better: 2 of
  25). Leonardo was removed from companies.yaml entirely once its postings
  turned out to be Italian/Brazilian internships, not UK roles — not
  something this default-flip alone would have fixed.
- Separately, a Workday board itself can be filtered *server-side*, at the
  list-fetch level, via `WorkdayToken.facets` — a mapping of facet
  parameter (`workerSubType`, occasionally `jobFamilyGroup` or
  `jobFamily`) to a list of ids, sent as the request body's
  `appliedFacets`. Verified on Barclays: its 973-posting board returns
  exactly 153 when filtered to Graduate/Intern/Apprentice `workerSubType`
  ids, and every result is a genuine graduate programme — this is the
  employer's own classification, not a title guess. Facet ids are opaque
  and tenant-specific; they must only ever come from companies.yaml,
  discovered per board with `scripts/probe_facets.py` — never inferred or
  guessed, never reused across boards. Defaults to `{}` (unfiltered), so
  this is purely additive — a board with no `facets:` entry behaves
  exactly as before this existed.
  **Tradeoff to know about:** this is server-side *pre*-filtering of the
  whole feed, not a display filter — a facet-filtered board is no longer a
  complete feed at all, so close-detection is weaker for it. A posting
  whose employer reclassifies its `workerSubType` (e.g. a scheme
  regraded from "Graduate" to "Experienced Hire" mid-posting) drops out of
  every future filtered fetch and looks exactly like a closure, even
  though the requisition may still be open under a different subtype.
  This is rarer than an ordinary title edit, so a facet-filtered feed is
  more stable than a `searchText`-based filter would be — but it's a real,
  standing caveat on lifecycle data (`is_open`/`closed_at`) for any board
  with `facets:` configured, distinct from the title-filter tradeoff above
  (which only affects whether a description gets fetched, never identity
  or closure).
- `location_class`/`seniority_class`/`classified_at` tag a posting; they
  never cause one to be closed or deleted. Classification is a separate pass
  over `postings` (`classify_run.py`), same shape as parsing over
  `raw_fetches` — idempotent via `classified_at IS NULL`, `--full` to
  reclassify everything. The classifiers themselves (`gradmarket/classify/`)
  are pure functions with no DB access, so tuning the rules never needs a
  rebuild, just a re-run.
- `raw_fetches.payload` retains description text only for rows that haven't
  been parsed yet. `parse_run.process_row` strips it (via
  `gradmarket.raw_fetches_pruning.strip_descriptions`) as the last thing it
  does for a row, immediately after that row's own postings/versions have
  been upserted and committed — job ids, titles, locations, and the
  payload's array/object structure are all preserved, only description
  fields are set to null. `posting_versions.description_raw` is the durable
  copy from then on, appending a new version whenever the text changes;
  keeping a second full copy in `raw_fetches` past that point was pure
  duplication and most of why the table grew the way it did. Stripping is
  skipped under `--dry-run` (nothing dry-run does is meant to persist) and
  never happens if that row's own parse raised — it's the literal last
  statement in `process_row`, so a failure anywhere above it is never
  reached. `scripts/prune_raw_fetches.py` still exists for one-time backlog
  cleanup of rows parsed *before* this stripping existed; it isn't the
  primary mechanism going forward.
- This makes `parse_run --full` destructive, not just incomplete, for
  essentially every row in a normally-operating database: `--full`
  `TRUNCATE`s `posting_versions` *before* rebuilding it from `raw_fetches`,
  and by the time `--full` runs, most rows have already had their
  descriptions stripped by the point above — so their only surviving
  full-description copy is deleted by the truncate, and nothing in the now
  description-less `raw_fetches` can rebuild it. The text is gone,
  permanently, not just "not reconstructed". `parse_run.py`'s `--full`
  refuses to run when any row shows this signature
  (`db.count_stripped_raw_fetches`) unless passed
  `--i-know-this-destroys-history` — never pass that flag without exporting
  anything description-dependent (e.g. embeddings) first.
- Beyond stripping descriptions, `raw_fetches` rows are deleted outright
  once they're both parsed and older than `RAW_FETCHES_RETENTION_DAYS`
  (default 30) — `parse_run.run()` does this itself after its main loop and
  reconciliation (`db.delete_old_parsed_raw_fetches`), then runs a plain
  `VACUUM` (never `VACUUM FULL`, which needs room for a full table copy and
  has failed here under exactly the disk pressure this exists to relieve)
  so the freed space is actually reusable. raw_fetches filled a 5GB Railway
  volume once already and needed emergency manual deletion; this is the
  automatic backstop. 30 days is sized for the debugging window, not
  storage — stripped rows are small (~11MB/day of collection), so even 30
  days is only ~330MB against the 5GB volume; the point of the window is
  giving a bad extraction bug time to be caught and fixed retroactively
  from raw payloads before they're gone. Only ever deletes parsed rows (an
  unparsed row is never a candidate, regardless of age — a collection gap
  is unrecoverable) and is skipped entirely under `--dry-run`, since
  `VACUUM` can't participate in a transaction and so has no rollback-safe
  way to run speculatively.
  **Consequence:** raw history now effectively only goes back about 30
  days. `--full`'s rebuild-from-`raw_fetches` can only ever reconstruct
  descriptions for postings whose raw row is still within that window —
  everything older has already had both its description stripped *and*
  its row deleted — so the destructive-rebuild guard above now applies to
  essentially all history in a normally-operating database, not just rows
  someone manually pruned.

## Per-board floors

A posting's real age is unknown if it was already live when its board was
first observed — on collection's first day, and on every day a board was added
since. The rule, defined once in `web/queries.py` (`BOARD_START_CTE`) and used
by both the role rows and the arrivals chart, so they can never disagree:

- A board's start is the earliest of `min(raw_fetches.fetched_at)` and
  `min(postings.first_seen_at)` for that `(source, company)` — over **all**
  its postings, closed ones included. Open rows alone would give a board whose
  early postings have all closed a late start and wrongly floor ages known
  exactly. `raw_fetches` alone isn't enough either: it's pruned at 30 days.
  The `raw_fetches` term is what keeps an empty board filling up (a season
  opening) from flooring its first genuine arrivals. Either term can only move
  a start earlier.
- A posting is a **floor** if `first_seen_at < board start + 1 day`.
- A floor shows its age as `"N+ days"` (`age_is_floor`, `age_label`), is
  never `is_new`, and **never appears in "This week"** — unknown age can't be
  presented as fresh, so a floor that would land there goes to "This month".
  Otherwise floors group by their floor value (a lower bound, so "This month"
  and "Earlier" stay true). `is_long_running` (> 60 days) stays valid for a
  floor.
- The **arrivals chart excludes floors entirely**: they weren't arrivals.
  That's what removes the spikes on days the board list grew, with no list of
  special dates to maintain (an earlier hardcoded `ARTEFACT_DATES` list was
  replaced by this).
- `collecting_since` is `min(first_seen_at)` over all postings — never
  `min(raw_fetches.fetched_at)`, which drifts forward with pruning.

## Classification

Two pure-function classifiers in `gradmarket/classify/`, re-run by
`classify_run.py`; full rules and evaluation in
[classifier-spec.md](classifier-spec.md).

**Location: explicit non-UK markers beat UK city names.** A location string
splits into entries (`;`, `|`, `•`) and each entry into comma fragments; the
posting is UK if any entry is. Within an entry: an explicit UK marker wins
(united kingdom, england, scotland, wales, northern ireland, `UK`, `GB-`);
otherwise a non-UK qualifier overrules the UK city it *qualifies* — the next
fragment being a US state/Canadian province code or name, or a non-UK country
(`Cambridge, MA`, `Manchester, New Hampshire`, `London, ON`), or the entry
starting with a non-UK country (Workday's country-first form). A marker never
overrules the whole string: commas also separate whole cities
(`London, Montreal, Singapore`), and a string-wide rule hid real London roles.
Singapore is deliberately not a qualifier (a city-state listed as a city).

**Corroboration exception.** Five state/province names are also UK places —
Washington (Tyne and Wear), New York (North Tyneside), Quebec (County Durham),
Maryland (Newham), California (Norfolk) — so they only qualify a city when the
string has another North American signal: a ZIP code, United States/USA/US/
Canada, or a state/province code. `Newcastle, Washington` stays UK.

### Evaluation data

- `data/eval/labels.csv` — union of all labelling batches (250 rows). Rules
  were developed while reading these, so accuracy measured on it is
  optimistic.
- `data/eval/holdout.csv` — 50 rows labelled after the classifier was
  written. This is the honest number; don't tune against it. One exception
  on record: the UK-location holdout was consulted once, as a check, after
  the Cambridge/non-UK-marker rule was written, so for the location
  classifier it's no longer strictly untouched. The early-careers holdout is
  unaffected.

## The web service (`gradmarket/web/`)

- `app.py` — FastAPI app: `/` (server-rendered page), `/api/roles`,
  `/api/arrivals`, `/healthz`; `/static`. FastAPI's `/docs`, `/redoc` and
  `/openapi.json` are off — every route is public surface.
- `queries.py` — the only SQL. Names every column explicitly
  (`PUBLIC_COLUMNS`); never selects or joins description text. A test seeds
  description text into every table that holds it and asserts no GET route's
  response contains it.
- `store.py` — connection pool and snapshot cache. `arrivals.py`,
  `display.py` — pure functions (arrivals chart, location display, city
  filter). `__main__.py` — the production entrypoint.

**Snapshot cache.** All the page's data is one in-memory `Snapshot`,
refreshed on read when older than 5 minutes — Postgres is never hit per
request. Everything derived from more than one row (arrivals, city counts) is
computed at refresh. A failed refresh keeps serving the previous snapshot and
waits 30s before retrying (so a database outage doesn't make every request sit
through a connect timeout); `/healthz` reports the snapshot's age. Before the
first successful load every route returns 503, and the service still boots
with Postgres unreachable (`pool.open(wait=False)`).

**Read-only.** Every pooled connection sets `read_only = True`, so every
transaction is `BEGIN READ ONLY`. Pool: 1–4 connections, idle ones recycled
after 60s and all after 10 minutes, each checked before use — Railway Postgres
has dropped long-lived connections before.

**One worker.** The cache is per-process; more workers would mean more caches
and more queries. `__main__.py` pins `workers=1` and no reload, and reads
`$PORT` itself — Railway runs a Dockerfile service's start command without a
shell, so `--port $PORT` would pass the literal text.

**Page derivations.**
- Freshness groups: "This week" (0–7 days), "This month" (8–30), "Earlier"
  (31+) — floors never "This week" (see Per-board floors). Empty groups are
  omitted. Sorting a column collapses groups into one flat list.
- City filter (`display.derive_cities`): a posting belongs to every UK city it
  lists. Whether a town mention is the UK one is decided by asking the
  location classifier about the segment with the town swapped for London — so
  `Plymouth, MA` is out and an unlisted UK town like Milton Keynes is in. A city
  name followed by a street word (`New Oxford Street`, `London Road`) is an
  address, not the city.
- Location display (`display.display_location`): **first**, fold every dash
  variant (en dash, em dash, non-breaking hyphen, figure dash) to `-` and
  non-breaking spaces to spaces — GSK's `UK – London – New Oxford Street` uses
  en dashes, and every later pattern only knows `-`. Then strip a leading/
  trailing "United Kingdom" and `UK - `/`GB - ` prefixes per segment, turn
  ` - ` into `, `, and show the first **UK** segment (by the classifier, on
  the raw segment) with the rest as "+N more". Display-only: the API and
  search keep the raw string.
- Icons: one 5×7 dot-matrix mark at every size, from
  `scripts/make_icons.py`. Every icon URL carries `?v=ICON_VERSION`
  (favicons are cached aggressively); a test fails if the files change
  without `ICON_VERSION` and `ICON_SET_SHA256` being updated.

## Gotchas
- A 200 response with an empty jobs array does NOT mean all jobs closed. It usually means the company switched ATS provider. Treating it as closure corrupts history for every posting they had. Handle empty-but-200 distinctly.
- Greenhouse returns descriptions as HTML. Strip before embedding.
- A company migrating between ATS providers appears as two identities, since identity includes `source`. The same job will look closed on the old board and newly-posted on the new one, producing a false close and a false first_seen_at. Not handled yet — needs a merge rule once we have a second source.
- `pipeline.py` runs `parse_run` TWICE — once before `detail_run`, once
  right after it, before `classify_run`. The first pass creates a two-stage
  source's postings (currently just Workday) with location/description
  still missing, since detail hasn't been fetched yet at that point.
  `detail_run.run()` then fetches what's missing and, for every company it
  stored at least one new detail for, flags that company's most recent
  `raw_fetches` row unparsed again (`db.mark_latest_raw_fetch_unparsed`,
  once per company per run, only touching an already-parsed row). The
  second `parse_run` pass reparses exactly those flagged rows, joining the
  new details into `postings`/`posting_versions` — and since this all
  happens before `classify_run` runs, a brand-new Workday posting's very
  first classification now sees its real content in the *same* cycle,
  not a day or more later. This closes what used to be a real gap: a new
  posting's first-ever classification happening with `description_raw =
  None`, permanently, since classification only ran once
  (`classified_at IS NULL`).
  `get_postings_to_classify` also picking up any posting whose
  `postings.updated_at` is newer than its `classified_at` (a stale
  classification, not just a missing one) is what makes even a *later*
  cycle's join-in still trigger reclassification reliably — this second
  parse pass is what makes that usually not even necessary. Not
  Workday-specific: the same content-changed-after-classification gap
  applies to any source where a company edits a posting's title or
  location after it was first seen. `--full` still reclassifies everything
  unconditionally, `updated_at` freshness included. When `detail_run`
  stores nothing new, no company gets flagged, so the second parse pass is
  a no-op — parse_run's own idempotency (`get_unparsed_raw_fetches`, gated
  on `parsed_at`) handles that without any special-casing.
- `postings.updated_at` is set to `now()` by `bulk_upsert_postings`, but
  ONLY when this posting's content actually changed (an insert, or an
  update whose incoming content_hash differs from what's stored) — real
  wall-clock write time, never `posting_versions.observed_at` (the raw
  row's own `fetched_at`, i.e. when the data was *collected*). The
  staleness check above deliberately compares against `updated_at`, not
  `observed_at`: an earlier version compared against `observed_at`, and
  re-parsing an *old* raw_fetches row (a `--full` rebuild, or any backfill)
  produced a version whose `observed_at` was old even though the write was
  happening right now — that looked already-up-to-date next to an existing
  `classified_at` and got silently skipped, leaving a stale classification
  no future run would ever correct. `updated_at` can't have this problem:
  it's always the actual time of the write, regardless of how old the
  source data being written is.
  The content-changed guard is itself a fix for a second incident: an
  earlier version of this column bumped `updated_at` on *every* upsert,
  including a plain re-observation that only refreshes `last_seen_at` —
  which meant every open posting looked freshly updated on every single
  run, turning "reclassify what changed" into "reclassify all ~21k
  postings every run" in production. The guard evaluates
  `postings.content_hash IS DISTINCT FROM EXCLUDED.content_hash` directly
  in the `ON CONFLICT DO UPDATE` clause — the same content_hash-equality
  signal `process_row` already uses to decide whether to append a
  `posting_versions` row, just checked against the table's own pre-update
  value instead of being threaded through as a second parameter, so it
  can't drift out of sync with what's actually stored.
- **Mixed content behind Railway's proxy — does not reproduce locally.**
  Railway terminates TLS at its edge and forwards plain HTTP, so the app sees
  every request as `http`. Two rules, both needed: static assets are
  referenced with root-relative paths (`/static/site.css`), never `url_for()`
  — `url_for` built `http://` URLs, the https page blocked them as mixed
  content, and the deployed page went unstyled; and uvicorn runs with
  `proxy_headers=True, forwarded_allow_ips="*"` (in `web/__main__.py`) so the
  app sees `X-Forwarded-Proto`. `"*"` is safe only because the container is
  reachable solely through Railway's proxy. Local dev is plain http end to end,
  so neither failure shows up there; a test asserts the rendered page has no
  `http://` asset URL.
- `data-*` attribute names on the page are shared namespace for
  `site.js`'s lookups. Chart bars once carried `data-count`, so a
  `document.querySelector("[data-count]")` meant for the filter bar's count
  matched the first bar and wrote "257 roles" into the chart. List lookups are
  now scoped to the list/form, and a test asserts `data-count` appears once.
