# GradMarket

**A daily record of UK graduate and internship postings, collected from company
applicant tracking systems since August 2026.**

Job aggregators show you what's open today. They don't tell you when a posting
appeared, how long it's been up, or whether it ever closed. GradMarket checks
157 company job boards every morning and keeps every observation, so those
questions have answers.

| | |
|---|---|
| **Collecting since** | 10 August 2026, daily, unattended |
| **Company boards** | 157 across 5 ATS platforms |
| **Postings collected** | 25,019 (14,948 currently open) |
| **UK early-careers roles tracked** | 343 (248 currently open) |
| **Content revisions recorded** | 39,615 |
| **Tests** | 345 |

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

## The five sources

Each is a module behind the same `fetch(token) -> FetchResult` interface. The
orchestrator contains no provider-specific logic; adding a source is a module
plus a registry entry.

| Source | Boards | Postings | Notes |
|---|---:|---:|---|
| Greenhouse | 69 | 13,562 | Single request, full descriptions |
| Ashby | 60 | 7,859 | Secondary locations in a separate field |
| Workday | 25 | 2,397 | Two-stage fetch; see below |
| Lever | 10 | 1,126 | Offset pagination, EU host fallback |
| Workable | 8 | 75 | Daily request quota, no `id` field |

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

**Graduate hiring is concentrated, and not where students look.** Of 48
companies with 20+ open UK roles, 28 post no early-careers positions at all.
Among those that do, quantitative trading firms lead by proportion — Jump
Trading at 53% of its UK roles, Squarepoint at 30%.

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

Deployed on Railway: Postgres plus a daily cron, built from the Dockerfile.
Monitoring is a dead-man's switch — the pipeline pings an external service on
success, and silence is the alert. A platform can't tell you a scheduled job
stopped firing; only the absence of a ping can.

Configuration is `companies.yaml`. Secrets are environment variables.

---

## Limitations

Stated plainly, because an analysis without them isn't worth much.

**The sampling frame is not the UK job market.** It covers employers on five
modern ATS platforms. Most large UK employers run Oracle, SuccessFactors, or
bespoke systems and are unreachable — including most of the FTSE 100. The
candidate list also over-sampled quantitative trading firms early on, so they
are over-represented relative to reality.

**Early-careers recall is 81%**, so roughly one in five graduate roles is
missed. A production bug illustrated why that figure is optimistic: a rule
matching `CPT/OPT` visa status also matched the ordinary word "opt", so any
description containing *"opt out of marketing communications"* boilerplate was
classified early-careers. It misclassified an entire company's board and
inflated the reported count by 19%. The 50-posting holdout missed it completely,
because no employer in that sample used the phrase.

A held-out set validates against the distribution it was drawn from. Systematic
errors concentrated in one employer's template are invisible to it.

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
scripts/       discovery and operational utilities
tests/         345 tests, fixtures only, no live HTTP
docs/          classifier specification and evaluation
```

MIT licensed. Job description text is the companies' copyright and is stored
privately, never republished — public output is links and derived fields only.
