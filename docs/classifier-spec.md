# GradMarket — classifier specification and evaluation

Two independent classifiers tag every posting. Nothing is ever deleted based on
classification; the columns `location_class` and `seniority_class` are added to
`postings` and queried as needed.

---

## Location classifier

`classify_location(location) -> 'uk' | 'non_uk' | 'unknown'`

**Procedure**

1. Normalise: lowercase, strip whitespace.
2. Split on `|`, `•`, `;`, `,` — multi-location postings are common and the
   separator is inconsistent across sources.
3. A posting is `uk` if **any** fragment matches a UK pattern.

**UK patterns**

- Cities: London, Manchester, Bristol, Edinburgh, Birmingham, Leeds, Glasgow,
  Cardiff, Belfast, Sheffield, Liverpool, Newcastle, Nottingham, Cambridge,
  Oxford, Reading
- Nations/country: united kingdom, england, scotland, wales
- Prefix form: `GB-` (e.g. `GB-London`)
- `UK` as a whole word

**Known false-positive risk:** Cambridge (MA), Oxford (MS/OH), Reading (PA) are
also US place names, and "Reading" is a common English word. Matched on word
boundaries only. Not observed to fire incorrectly in 250 labelled rows, but the
risk is real and documented in code.

**`unknown`** — empty string, bare "Remote", bare "Hybrid", or region codes with
no country ("AMER", "EMEA"). These carry no geographic information and forcing
a binary would invent data.

---

## Seniority classifier

`classify_seniority(title, description) -> 'early' | 'experienced' | 'unknown'`

**Core principle: `early` requires positive evidence.** Absence of a stated
experience requirement is *not* evidence of a graduate role — most senior
postings simply don't state years. Default is `experienced`.

**Precedence, in order — first match wins**

| # | Rule | Result |
|---|------|--------|
| 0 | Title contains both an experienced marker and an early keyword | `early` (early wins) |
| 1 | Title contains recruiter / recruiting / talent acquisition | `experienced` |
| 2 | Title contains senior, staff, principal, lead, head of, director, manager, VP, chief, sr., executive, owner | `experienced` |
| 3 | Title contains graduate, intern, internship, junior, campus, placement, trainee, apprentice, new grad, summer analyst, early career, entry level, working student | `early` |
| 4 | Description states an experience floor above zero ("3+ years", "5+ years", or a bare "N+ years in a … role") | `experienced` |
| 5 | Description gives positive early evidence: "0–N years", "new grads", "no prior experience", "final year", "penultimate year", "graduating in 20XX", "CPT/OPT", "students eligible" | `early` |
| 6 | Otherwise | `experienced` |

**Rule 0 exists because of a bug found during holdout evaluation** — see below.

### Notes on specific signals

- **"Specialist" is not a seniority marker.** It's a role descriptor appearing
  at all levels. Removed from rule 2 after it caused 4 of 5 holdout errors.
- **Recruiter titles are traps.** "Campus Recruiter" and "Head of Early Career
  Recruiting" are experienced roles that *hire* early-careers candidates. Hence
  rule 1 sitting above everything else.
- **Encouragement-to-apply boilerplate is not evidence.** "We understand not
  everyone will meet all the qualifications" appears on postings at every level
  and means nothing about seniority.
- **"Requires experience" without a number still means experience.** Only
  *silence* about experience is uninformative.
- **Compensation bands** well above graduate level are strong evidence of
  experienced. Useful on US postings; UK postings usually omit salary.
- **PhD-level roles are out of scope**, even when titled "Internship".
  Early-careers is defined as *reachable for an undergraduate finishing their
  degree*.
- **When a company runs separate new-grad postings** (Palantir, Snowflake),
  their general engineering roles default to experienced — the company has
  drawn the line itself.

---

## Evaluation

**Method.** 250 postings hand-labelled across three batches. Rules were
developed while reading the first 200, so accuracy on those is optimistic and
reported only as a consistency check. A separate 50-row holdout was labelled
*after* the classifier was written and never used for rule development — that
is the honest number.

### Holdout results (50 unseen postings)

**Location — confusion matrix** (rows = true, columns = predicted)

| true \ pred | uk | non_uk | unknown |
|---|---|---|---|
| **uk** | 26 | 0 | 0 |
| **non_uk** | 0 | 24 | 0 |
| **unknown** | 0 | 0 | 0 |

| class | precision | recall | F1 | support |
|---|---|---|---|---|
| uk | 1.00 | 1.00 | 1.00 | 26 |
| non_uk | 1.00 | 1.00 | 1.00 | 24 |

**Seniority — confusion matrix**

| true \ pred | early | experienced | unknown |
|---|---|---|---|
| **early** | 21 | 5 | 0 |
| **experienced** | 0 | 24 | 0 |
| **unknown** | 0 | 0 | 0 |

| class | precision | recall | F1 | support |
|---|---|---|---|---|
| early | 1.00 | 0.81 | 0.89 | 26 |
| experienced | 0.83 | 1.00 | 0.91 | 24 |

**Reading of the result.** The classifier never labels something early that
isn't — precision 1.00 — but misses roughly a fifth of genuine early-careers
roles. For a job search tool that's the right direction to fail in: a student
sees fewer results, not wrong ones.

### What the evaluation caught

All five seniority errors were false negatives. Four shared a cause:

| Title | Predicted | True |
|---|---|---|
| Junior Platform Specialist | experienced | early |
| Trading Infrastructure Specialist – Graduate Programme | experienced | early |
| Junior Early Growth Specialist CR German | experienced | early |
| Junior Data Acquisition Specialist | experienced | early |
| New Partner Experience Advisor – French Speaking | experienced | early |

"Specialist" sat in the experienced-marker list at precedence 2, firing before
the early keywords at precedence 3 — so "Junior … Specialist" and "… Specialist
– Graduate Programme" never reached the rule that would have caught them.

Fixed post-evaluation by removing "Specialist" from rule 2 and adding rule 0
(early wins on conflict). The reported 0.81 recall is the pre-fix figure, since
re-running the same holdout after tuning would no longer be a held-out
measurement.

The fifth error (Deliveroo) is a genuine judgement case with no keyword
conflict — a £27,700 graduate-level role whose title carries no early signal.

---

## Data files

- `data/eval/labels.csv` — union of all batches (250 rows). Rules were
  developed against these; accuracy here is optimistic.
- `data/eval/holdout.csv` — 50 rows labelled after the classifier existed.
  The honest number. **Do not tune against it.**

---

## Sampling frame limitation

Coverage is 94 company boards across Greenhouse, Lever and Ashby. This excludes
most large UK employers, who use Workday, SuccessFactors or Oracle — including
the Big Four, high street banks, and most of the FTSE 100. The dataset therefore
skews toward technology-forward employers and scaleups. Approximately 2% of
resolved tokens turned out to be a different company sharing the name, found by
manual inspection and removed.
