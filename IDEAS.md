# Ideas

Parking lot for things deliberately deferred. Capturing, not committing.

## Storage
- raw_fetches grows ~94 rows/day with full descriptions duplicated per snapshot — likely a few hundred MB by February. If Railway storage cost becomes an issue: retention, not restructuring — keep full payloads 30 days, strip description text from older rows, keep ids/metadata. Lifecycle data survives; only deep-history description re-parsing is lost.

## Monitoring
- Persistently-empty boards (optiver, marshallwace, mistral, labelbox, bumble, snyk, vercel) trip the feed guard every run — warnings become noise. Need to distinguish "always been empty" from "just collapsed"; only the latter is interesting.
- Token health: a scheduled check that existing tokens still resolve, flagging new 404s — catches ATS migrations. Separate from discovery, which stays manual.
- Dropping a company from config orphans its postings; now handled by reconciliation.

## Coverage
- Insurance and consultancies are thin. Most UK-native fintech (Starling, Wise, Revolut, Thought Machine) is on Workday or bespoke ATSes — unreachable via Greenhouse/Lever/Ashby. State as a sampling limitation in the write-up rather than solving.

## Classification
- Two more rule-6 early-evidence phrases in `classify/seniority.py` can match
  ordinary prose unrelated to the candidate's own status, same class of bug
  as the CPT/OPT fix: "final year" matches "the final year of this
  fixed-term contract/grant/lease" (describing the role's or business's
  timeline, not a student's degree); "students eligible" matches benefits
  copy like "students eligible for this scholarship benefit" (an employee's
  dependents, not the applicant). Both need a real false-positive posting to
  calibrate a context requirement against — unlike CPT/OPT, there's no
  known misclassified board yet, so tightening blind risks new false
  negatives. "no prior experience" has a milder version of the same risk
  ("no prior experience with our internal tools is assumed") but reads far
  less risky in practice.
