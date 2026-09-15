# Analysis - Notes

- Board list expanded 12 Aug (92→143) and 25 Aug (143→179); arrival counts on those dates are inflated by newly-observed rather than newly-posted roles.
- Need to decide how to handle the fact that the board list keeps changing – the cleanest approach for the eventual writeup is restricting the time series to the 92 boards existing since 10 August, so the denominator is constant.
- **Of 43 companies with 20+ open UK roles, 26 have zero early-careers postings. The 17 that do are led by quantitative trading firms: Jump Trading (53% of its UK roles), Squarepoint (30%), Palantir (28%), then Man Group (14%), Point72 (13%) and DRW (12%). Companies students most target — Monzo, Anthropic, OpenAI, Stripe (2 of 43), Databricks, Graphcore (94 UK roles), Wayve (60), TCS (108) — post almost none.**

- 96 UK early-careers postings open, 122 seen since collection began on 10 Aug — roughly 21% closed over four weeks.

- UK graduate roles do close within days in some cases – Scott Logic's graduate roles lasted 2 days, GSA's early talent event 1 day. That is a useful warning for students.

- Sampling bias: the original candidate list deliberately over-sampled quant firms, so they are over-represented relative to the true UK market. The finding is about what is visible on Greenhouse/Lever/Ashby/Workable, not UK graduate hiring overall.

- days_open is measured from first_seen_at, not true posting date. Anything inherited on 10–12 Aug has unknown real age, so observed durations are biased short. Scott Logic's 2-day graduate roles may have been live for weeks beforehand.

- Classifier recall on early-careers is 81%, so roughly one in five graduate roles is likely missed. Manual review of Monzo, Graphcore, Wayve and Anthropic's open UK titles found no misclassifications – those zeros are real.

- Anthropic's Fellows Program is early-careers-adjacent but research/PhD-level, excluded by rule 12 (early-careers = reachable for an undergraduate). A scoping decision, not an error.

- Fast closures skew toward events and internships rather than full graduate schemes – GSA's early talent evening closed in 1 day, SumUp's Revenue Ops intern in 1. Plausible that capacity-limited events fill immediately while schemes run to deadline. Worth retesting with more data.

- 9 Sep – skyscanner and sophos migrated ATS; ~120 postings appear as closures under the old source and new arrivals under the new one on this date. Artefact, not market movement.

- Classifier false-positive found in production, 9 Sep. The CPT/OPT rule matched the ordinary English word "opt", so any description containing "opt out of marketing communications" boilerplate was classified early-careers. This misclassified Faculty's entire board — 18 open UK roles including senior positions — and inflated the reported UK early-careers count by ~19% (119 → 96). Fixed by requiring uppercase CPT/OPT with context. Two similar risks logged but not fixed without evidence: "final year" can match contract timelines, "students eligible" can match benefits copy.

- The 50-posting holdout reported 100% precision on early-careers. It missed this error entirely because no board in the sample carried opt-out boilerplate. A held-out sample validates against the distribution it was drawn from; systematic errors concentrated in one employer's template can be invisible to it. Production monitoring found what evaluation didn't.

- Three ATS migrations in five weeks (Skyscanner, Sophos, Sage), two of which left the four platforms entirely. That's roughly 2% of the sampling frame churning per month, and it's a real constraint on any longitudinal claim.

- Raw archive retained 30 days from 14 Sep; snapshots from 10 Aug to 11 Sep were deleted under disk pressure, so lifecycle data for that period cannot be re-derived from source. postings and posting_versions are complete and unaffected.




