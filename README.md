# GradMarket

**Live: [gradmarket.dev](https://gradmarket.dev)**

**A daily record of UK graduate and internship postings, collected from company
applicant tracking systems since August 2026.**

Job aggregators show you what's open today. They don't tell you when a posting
appeared, how long it's been up, or whether it ever closed. GradMarket checks
195 company job boards every morning and keeps every observation, so those
questions have answers.

| | |
|---|---|
| **Collecting since** | 10 August 2026, daily, unattended |
| **Boards monitored** | 195 across 5 ATS platforms |
| **Postings collected** | 30,599 (15,787 currently open) |
| **UK early-careers roles tracked** | 448 (257 currently open) |
| **Content revisions recorded** | 53,332 |
| **Tests** | 521 |

Figures as of 1 November 2026. Boards monitored counts every board in
`companies.yaml`, including 40 that return 200 with an empty feed and have
never produced a posting — deliberately: an empty board filling up is how a
hiring season opening gets detected.

---

## Why it exists

Applying to UK graduate schemes, three things are hard to find out:

- **When did this appear?** Aggregators show a posting without saying whether
  it went up yesterday or in May.
- **Is it actually live?** Many listings stay up permanently to collect CVs.
- **What does the market look like?** Nobody publishes how many graduate roles
  exist, who posts them, or when the season opens.

All three need a longitudinal record — the same boards checked repeatedly, with
every snapshot kept. That's the thing this builds.

---

## How it works

```
   Greenhouse   Lever   Ashby   Workable   Workday
        │         │       │        │          │
        └─────────┴───────┴────────┴──────────┘
                          │  one module per source,
                          │  common fetch() interface
                          ▼
                   ┌─────────────┐
                   │ raw_fetches │   untouched API responses,
                   └──────┬──────┘   one row per board per day
                          │
                          ▼
          ┌───────────────────────────────┐
          │  postings · posting_versions  │   identity, lifecycle,
          └───────────────┬───────────────┘   content history
                          │
                          ▼
              location_class · seniority_class
```

**Raw first, parse later.** The collector writes API responses untouched. Parsing
is a separate pass over that archive. A parsing bug is recoverable — fix the
code, re-run over stored payloads. A collection gap is not.

This paid off concretely: an extractor was storing only the first location of
multi-location postings. Once fixed, three weeks of history were rebuilt from
the archive, recovering **195 UK postings** that had been invisible.

**Postings are observed over time, not stored once.** Identity is
`(source, company, external_id)`. `first_seen_at` is written once and never
updated; `last_seen_at` moves on every observation; a posting absent from a feed
is marked closed rather than deleted. Disappearance is data.

**Close-detection has a guard.** If a board returns an empty feed, or collapses
by more than half, close-detection is skipped and the event is recorded in
`feed_anomalies` for review. A company migrating ATS looks exactly like every
job closing at once, and guessing wrong corrupts history irreversibly.

---

## The site

[gradmarket.dev](https://gradmarket.dev) is a FastAPI service on the same
database: server-rendered, with every query in a read-only transaction and the
result cached in memory for five minutes, so Postgres sees a handful of
queries an hour however busy the page is.

It shows what an aggregator can't: how long each role has been open, what
arrived this week, and a daily arrivals chart running from the first day of
collection. Roles already live when their board was first observed are
excluded from the arrivals chart and labelled "N+ days" in the list — their
real age is unknown, so they're never presented as new.

---

## The five sources

Each is a module behind the same `fetch(token) -> FetchResult` interface. The
orchestrator contains no provider-specific logic; adding a source is a module
plus a registry entry.

| Source | Boards monitored | Postings | Notes |
|---|---:|---:|---|
| Greenhouse | 66 | 16,140 | Single request, full descriptions |
| Ashby | 60 | 9,456 | Secondary locations in a separate field |
| Workday | 18 | 3,568 | Two-stage fetch; see below |
| Lever | 15 | 1,336 | Offset pagination, EU host fallback |
| Workable | 36 | 99 | Daily request quota, no `id` field |

**Workday is the interesting one.** Its list endpoint returns titles and
locations but no descriptions — those need a separate request per posting. So
collection is two-stage: list daily to catch arrivals and closures, fetch each
posting's detail once and store it separately.

It also exposes a `workerSubType` facet carrying the employer's *own*
early-careers classification. Passing those IDs server-side turns Barclays'
973-posting board into exactly the 153 graduate, intern and apprentice roles —
better precision than any title heuristic, because it's the company's own
taxonomy rather than an inference from wording.

Facet IDs are tenant-specific and discovered with `scripts/probe_facets.py`.

---

## Classification

Two rule-based classifiers tag every posting: UK or not, early-careers or not.
Both are pure functions with no database access, so rules can be changed and
re-run without re-collecting anything.

**Evaluation.** 250 postings were hand-labelled across three batches. Rules were
developed against the first 200; a separate 50-posting holdout was labelled
*after* the classifier was written and never used for tuning.

| | Precision | Recall |
|---|---|---|
| UK location | 1.00 | 1.00 |
| Early-careers | 1.00 | 0.81 |

Precision of 1.00 with recall of 0.81 means the classifier under-shows rather
than mis-shows: a student sees fewer results, not wrong ones. For a job search
tool that's the right direction to fail in.

---

## What the data shows

**Graduate hiring is concentrated, and not where students look.** Of 55
companies with 20+ open UK roles, 31 post no early-careers positions at all.
Among those that do, quantitative trading firms lead by proportion — Jump
Trading at 47% of its UK roles, Squarepoint at 32%. (Lloyds and Barclays rank
higher, but their Workday boards are filtered server-side to the employer's
own graduate categories, so the proportion says nothing there.)

**The season has a measurable shape.** UK early-careers arrivals were
near-flat through August and early September — about one a day, averaging
0.97 from 10 August to 15 September — then climbed: 2.0 a day across
September, 3.8 across October, with the single busiest day on 21 October at 30
arrivals. These count genuine arrivals only; roles already live when their
board was first observed are excluded.

**The autumn season is largely invisible on startup-oriented platforms.**
Through August and early September, new UK graduate roles arrived at 1–2 per day
across 179 boards. The large schemes weren't missing from the market — they were
on enterprise employers' Workday boards, which the first four sources can't
reach. Adding Workday roughly doubled UK graduate coverage.

**The sampling frame itself churns.** Three companies migrated ATS within five
weeks of collection starting; two left all four original platforms entirely.
Roughly 2% of boards move per month, which is a real constraint on any
longitudinal claim.

---

## Running it

```bash
pip install -e ".[dev]"

python -m gradmarket.pipeline      # ingest → parse → detail → parse → classify
python -m gradmarket.ingest        # collection only
python -m gradmarket.parse_run     # parse only  (--full to rebuild)
python -m gradmarket.classify_run  # classify only  (--full to re-tag)

pytest
```

Monitoring is a dead-man's switch — the pipeline pings an external service on
success, and silence is the alert. A platform can't tell you a scheduled job
stopped firing; only the absence of a ping can.

Configuration is `companies.yaml`. Secrets are environment variables.

### The public page

```bash
uvicorn gradmarket.web.app:app --reload   # http://127.0.0.1:8000
```

Reads `DATABASE_URL` from `.env` like everything else, opens every transaction
read-only, and queries Postgres at most once per 5 minutes (an in-memory
snapshot cache). Routes: `/` (server-rendered page), `/api/roles` (the same
rows as JSON), `/api/arrivals` (the chart's daily counts), `/healthz`.

---

## Deployment

Railway: one Postgres database and two services, both built from this repo's
`Dockerfile`.

| | Collector (cron) | Web |
|---|---|---|
| Runs | `python -m gradmarket.pipeline` (the Dockerfile's `CMD`), daily on a cron schedule | `python -m gradmarket.web`, always on |
| Writes to Postgres | yes | never — read-only transactions |
| `DATABASE_URL` | Railway's **private** URL | Railway's **private** URL |
| `HEALTHCHECK_URL` | dead-man's-switch ping URL | not used |
| `COMPANIES_FILE` | optional; defaults to `companies.yaml` in the working directory (`/app` in the image) | same — the "boards monitored" stat reads it |
| `PORT` | not used | set by Railway |
| Healthcheck | the outbound ping above | Railway healthcheck on `/healthz` |

`DATABASE_PUBLIC_URL` (Railway's TCP proxy) is only for running things from a
laptop; deployed services use the private network.

**Why the web service starts with `python -m gradmarket.web` and not a
`uvicorn` command:** Railway runs a Dockerfile service's custom start command
without a shell, so `--port $PORT` would pass the literal text `$PORT`.
`gradmarket/web/__main__.py` reads `PORT` itself and pins one worker (the
snapshot cache is per-process — more workers means more caches and more
queries) and no reload.

To run the image exactly as the web service runs it, from a laptop (so with
the public URL from `.env`):

```bash
docker build -t gradmarket .
docker run --rm -p 8000:8000 -e PORT=8000 \
  -e DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' .env)" \
  gradmarket python -m gradmarket.web
```

`.dockerignore` keeps a local build's context the same as Railway's (which
builds from git): above all, `.env` never ends up inside an image.

---

## Collection policy

- One pass per board per day, sequential, with a delay between boards (1s;
  5s for Workable). Lever and Workday lists are paginated, and each new Workday
  posting's detail is fetched once, also paced.
- 429 and 5xx are retried with exponential backoff that honours `Retry-After`;
  three consecutive 429s from one source trip a circuit breaker that skips that
  source for the rest of the run.
- Only the public endpoints the companies' own career pages use.
- Job description text is stored privately for classification and never
  republished; the public site shows links and derived fields only.

---

## Limitations

Stated plainly, because an analysis without them isn't worth much.

**The sampling frame is not the UK job market.** It covers employers on five
modern ATS platforms. Most large UK employers run Oracle, SuccessFactors, or
bespoke systems and are unreachable — including most of the FTSE 100. The
candidate list also over-sampled quantitative trading firms early on, so they
are over-represented relative to reality.

**Held-out evaluation missed three systematic errors that production caught
immediately.** Classification was validated on 250 hand-labelled postings with
a 50-posting holdout labelled after the rules were written. Reported precision
was 1.00 for UK location and 1.00 for early-careers, at 0.81 recall. In
production, three distinct failures appeared that neither set contained:

- A `Specialist` keyword that preempted early-careers markers.
- A `CPT/OPT` visa rule that matched the ordinary word "opt", so any
  description containing *"opt out of marketing communications"* boilerplate
  was classified early-careers. It misclassified an entire company's board and
  inflated the reported count by 19%.
- A UK city rule that matched Cambridge, Massachusetts. A full-corpus review
  after fixing it reclassified 58 of 5,791 postings — roughly 1% — including
  two roles visible on the public page.

Each was found by looking at live output, not by evaluation. A held-out set
validates against the distribution it was drawn from; errors concentrated in
one employer's template or one ambiguous place name are invisible to it at
n=50. The practical conclusion is that for a system like this, monitoring the
output beats enlarging the test set.

**The UK-location holdout is no longer strictly untouched.** It was consulted
once, as a check, after the Cambridge rule was written, so for the location
classifier it's no longer a clean held-out measure. The early-careers holdout
is unaffected.

**Posting durations are biased short.** Age is measured from first observation,
not true posting date, so anything already live at collection start has unknown
real age.

**Raw retention is 30 days.** Descriptions are stripped once parsed, and raw
rows are deleted after a month. Content history lives in `posting_versions` and
is complete; but postings can only be re-derived from source within the
retention window.

**Three dates are collection artefacts, not market movement** — 12 August, 25
August and 9 September, when the board list expanded or companies migrated ATS.
Any time series must exclude or mark them.

---

## Repository

```
src/gradmarket/
  sources/     one module per ATS, common fetch interface
  parse/       one extractor per source, common output shape
  classify/    location and seniority rules, pure functions
  ingest.py    collection
  parse_run.py parsing, close-detection, retention
  detail_run.py Workday's second-stage fetches
  pipeline.py  orchestration and health reporting
  web/         the public site: FastAPI app, read-only queries, snapshot cache
    templates/ index.html, server-rendered
    static/    site.css, site.js, icons
scripts/       discovery and operational utilities
tests/         521 tests, fixtures only, no live HTTP
docs/          classifier specification and evaluation
```

MIT licensed. Job description text is the companies' copyright and is stored
privately, never republished — public output is links and derived fields only.
