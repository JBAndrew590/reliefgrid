# ADR 002 — Deterministic, explainable matching

**Status:** Accepted · 2026-11-05

## Eligibility and formula

A candidate lot must have the same resource ID, active source location and organization, positive available quantity, an expiry date at least as late as the request's required date (or no expiry), and be within the query radius. A source organization must be the requester or have `share_inventory=true`.

| Component | Max | Rule |
| --- | ---: | --- |
| Urgency | 25 | routine 8, high 16, critical 25 |
| Distance | 20 | `round(20 × exp(-distance_m / 100000))` |
| Coverage | 30 | `round(30 × min(available / required, 1))` |
| Expiry | 15 | 15 when usable through deadline and expiring within 30 days; 10 for no expiry or later expiry; 0 if near deadline (the hard gate normally excludes this case) |
| Deadline | 10 | 10 when due ≤1 day; 6 when due ≤3 days; otherwise 3 |

The result is 0–100. The SQL candidate query initially orders by earliest expiration, then ID; scoring sorts by total descending, expiration, distance and ID. Score components are stored in `match_candidates` for display, but are recommendation snapshots. Allocation always rechecks stock and eligibility under locks. Scores reflect urgency of the request, so they should be compared within a request rather than across unrelated requests.

## Trade-offs

Weights are currently code constants and reviewed changes require a versioned release; this avoids arbitrary production changes without an audit. A 500-candidate query cap bounds work but can exclude farther stock in a dense network. It is not a route optimizer and does not model road travel time, cold-chain requirements or supplier contracts. Resource compatibility is exact by resource ID and unit; conversion is deliberately not performed implicitly.
