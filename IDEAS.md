# Ideas

Parking lot for things deliberately deferred. Capturing, not committing.

## Storage
- raw_fetches grows ~94 rows/day with full descriptions duplicated per snapshot — likely a few hundred MB by February. If Railway storage cost becomes an issue: retention, not restructuring — keep full payloads 30 days, strip description text from older rows, keep ids/metadata. Lifecycle data survives; only deep-history description re-parsing is lost.

## Monitoring
- Persistently-empty boards (optiver, marshallwace, mistral, labelbox, bumble, snyk, vercel) trip the feed guard every run — warnings become noise. Need to distinguish "always been empty" from "just collapsed"; only the latter is interesting.
- Token health: a scheduled check that existing tokens still resolve, flagging new 404s — catches ATS migrations. Separate from discovery, which stays manual.
- Healthcheck granularity: one ping can't distinguish a collection failure from a parse failure. Split into two checks if the pipeline log-message distinction proves insufficient.

## Coverage
- Insurance and consultancies are thin. Most UK-native fintech (Starling, Wise, Revolut, Thought Machine) is on Workday or bespoke ATSes — unreachable via Greenhouse/Lever/Ashby. State as a sampling limitation in the write-up rather than solving.
