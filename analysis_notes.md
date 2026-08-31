# Analysis - Notes

- Board list expanded 12 Aug (92→143) and 25 Aug (143→179); arrival counts on those dates are inflated by newly-observed rather than newly-posted roles.
- Need to decide how to handle the fact that the board list keeps changing – the cleanest approach for the eventual writeup is restricting the time series to the 92 boards existing since 10 August, so the denominator is constant
b
- **Of 44 companies with 20+ open UK roles, 28 have zero early-careers postings. The 16 that do are led by quantitative trading firms: Jump Trading (50% of its UK roles), Squarepoint (29%), then Palantir (26%) and Faculty (25%). Companies students most target – Monzo, Anthropic, OpenAI, Stripe, Databricks, Graphcore (103 UK roles), Wayve (51), TCS (110) – post none.**

- Of 110 UK early-careers postings inherited at collection start (first seen 10–12 Aug), 95 remain open and 15 have closed after ~3 weeks – roughly 14% attrition. Slow, consistent with a market running to fixed autumn deadlines rather than filling continuously.

- UK graduate roles do close within days in some cases – Scott Logic's graduate roles lasted 2 days, GSA's early talent event 1 day. That is a useful warning for students.

- Sampling bias: the original candidate list deliberately over-sampled quant firms, so they are over-represented relative to the true UK market. The finding is about what is visible on Greenhouse/Lever/Ashby/Workable, not UK graduate hiring overall.

- days_open is measured from first_seen_at, not true posting date. Anything inherited on 10–12 Aug has unknown real age, so observed durations are biased short. Scott Logic's 2-day graduate roles may have been live for weeks beforehand.

- Classifier recall on early-careers is 81%, so roughly one in five graduate roles is likely missed. Manual review of Monzo, Graphcore, Wayve and Anthropic's open UK titles found no misclassifications – those zeros are real.

- Anthropic's Fellows Program is early-careers-adjacent but research/PhD-level, excluded by rule 12 (early-careers = reachable for an undergraduate). A scoping decision, not an error.

- Fast closures skew toward events and internships rather than full graduate schemes – GSA's early talent evening closed in 1 day, SumUp's Revenue Ops intern in 1. Plausible that capacity-limited events fill immediately while schemes run to deadline. Worth retesting with more data.




