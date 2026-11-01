# GradMarket — classifier specification and evaluation

Two independent classifiers tag every posting. Nothing is ever deleted based on
classification; the columns `location_class` and `seniority_class` are added to
`postings` and queried as needed.

---

## Location classifier

`classify_location(location) -> 'uk' | 'non_uk' | 'unknown'`

**Procedure**

1. Normalise whitespace. Case is kept — the state-code and bare-`US` checks
   are case-sensitive.
2. Split into **location entries** on `;`, `|`, `•` (every parser joins a
   multi-location posting's locations with `"; "`), and each entry into
   **fragments** on `,`. Commas are ambiguous: sometimes parts of one address
   (`Cambridge, MA`), sometimes separate cities (`London, Montreal`).
3. Classify each entry with the precedence below. The posting is `uk` if
   **any** entry is `uk`.

**Precedence within one entry — first match wins**

| # | Rule | Entry result |
|---|------|--------------|
| 1 | Any fragment has an explicit UK marker: united kingdom, england, scotland, wales, northern ireland, `UK` as a whole word, `GB-` prefix | `uk` |
| 2a | The entry's **first** fragment is a non-UK country (Workday's country-first form) | not `uk` — cities not checked |
| 2b | A UK city name whose **next** fragment is a US state / Canadian province code or name, or a non-UK country | that city is skipped |
| 2c | …except a state/province name that is also a UK place (Washington, New York, Quebec, Maryland, California): it counts for 2b only if the string has another North American signal | city skipped only if corroborated |
| 3 | Any other fragment containing a UK city name | `uk` |
| 4 | Otherwise | not `uk` |

**Rule 2 — an explicit country beats a city heuristic.** It runs before any
city matching. It exists because "Operations Summer 2027 Internship –
Cambridge, MA" at JLL, location `United States of America, Cambridge, MA`, was
classified `uk`: "Cambridge" matched the UK city list and nothing outranked it.

- **Non-UK countries** (the whole fragment, case-insensitive): United States,
  United States of America, USA, U.S., U.S.A., Canada, Australia, New Zealand,
  Ireland, Republic of Ireland, India; plus bare `US` in uppercase only
  (lowercase "us" is a word). Whole-fragment matching means "Indiana" isn't
  India and "Northern Ireland" isn't Ireland.
- **State/province codes** (the whole fragment, uppercase, optionally with a
  ZIP): the 50 US states plus DC (`Cambridge, MA`, `Boston, MA 02110`), and
  the 13 Canadian provinces/territories (`London, ON` is London, Ontario).
- **State/province names** (the whole fragment, case-insensitive, optionally
  with a ZIP): the 50 US states plus District of Columbia
  (`Manchester, New Hampshire`, `Cambridge, Massachusetts, United States`,
  `Cambridge, Massachusetts 02139`), and the 13 Canadian provinces/territories
  (`London, Ontario`). Whole-fragment matching keeps `Virginia Water` (Surrey)
  from reading as Virginia, and `Hampshire` / `Jersey` from reading as New
  Hampshire / New Jersey.

**Rule 2c — state names that are also UK places.** These five match a UK place
name exactly, so on their own they're not evidence of North America:

| Name | UK place |
|---|---|
| Washington | town in Tyne and Wear, next to Newcastle (also a village in West Sussex) |
| New York | village in North Tyneside, a few miles from Newcastle |
| Quebec | former mining village in County Durham |
| Maryland | area of Newham, east London |
| California | village in Norfolk |

Each counts as a qualifier only when the **whole string** also has a North
American signal: a ZIP code (5 digits, optionally `-NNNN`), United States /
USA / U.S. / U.S.A. / `US` / Canada, or a state/province code.
`Newcastle, Washington` → `uk`; `Newcastle, Washington, USA`,
`Newcastle, Washington 98056` and `Newcastle, Washington; Seattle, WA` →
`non_uk`. The collision list was checked against UK place names by search and
general knowledge, not a full gazetteer; the Maryland and California entries
rest on thinner sources than Washington, New York and Quebec.
- **Singapore is not a rule-2 marker** — it's a city-state that appears as
  one city among several in comma-separated lists, and no place in it is
  named after a UK city, so it never needs to overrule one.

**Why a marker only overrules the city it qualifies, not the whole string.**
The first version of this rule was string-wide ("contains a non-UK country →
`non_uk`"). It misclassified 8 of the 250 labelled rows, all real London roles
listed in comma-separated city lists — `London, Montreal, Singapore`,
`Montreal, London, Singapore, Warsaw, Houston`, `London and Singapore`. The same
three strings appear in the holdout, so the holdout has now been looked at
for this rule; the fix was driven by the identical failures in `labels.csv`.
The qualifier version changes no prediction on either labelled set.

**Why rule 2a is per entry.** Workday prefixes only the *primary* location's
country to the whole `"; "`-joined list — `United States of America, Cambridge,
MA; London`. Rule 2a governs the first entry only, so London is still found.

**Why rule 1 outranks rule 2.** Rule 2 only overrides *city-name guesses*,
never another explicit country: `London, UK / New York, USA` is a genuinely
multi-country posting, and any UK location is enough.

**UK patterns**

- Cities: London, Manchester, Bristol, Edinburgh, Birmingham, Leeds, Glasgow,
  Cardiff, Belfast, Sheffield, Liverpool, Newcastle, Nottingham, Cambridge,
  Oxford, Reading
- Nations/country: united kingdom, england, scotland, wales, northern ireland —
  except inside "New South Wales" (Australia) and "New England" (US)
- Prefix form: `GB-` (e.g. `GB-London`)
- `UK` as a whole word (not inside "Ukraine")

**Remaining false-positive risk.**
- A UK city name with no qualifier right after it still reads as UK: a bare
  "Cambridge" or "Reading" is treated as the UK one.
- A qualifier must be the *next* fragment: `Cambridge, Middlesex County,
  Massachusetts` is still `uk`, because the fragment after Cambridge is a
  county.
- The five rule-2c names without a corroborating signal still read as UK:
  `Newcastle, Washington` is treated as the Tyne and Wear town even when it's
  the one in Washington state.
- "Reading" is also an ordinary English word; it's matched on word boundaries
  only.

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

*Location numbers below predate rule 2 of the location precedence. The rule
changes no location prediction on these 50 rows (or on the 250 labelled rows),
so they still hold.*

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
