# Evaluation regime: x-trends-us-2026-08-10-wweraw (v1)

Question: Will WWE Raw trend on X in the United States on Monday 2026-08-10?

## Observation source

Primary: https://trends24.in/united-states/ hourly snapshots (top-50 list per
snapshot). Fallback if trends24.in is unreachable on resolution day:
https://getdaytrends.com/united-states/ same-day listing.

## Window

Snapshots timestamped between 20:00 and 23:59 America/New_York on 2026-08-10
(WWE Raw airs live on Netflix 20:00–23:00 ET from Scope Arena, Norfolk, VA).

## Resolution rule

Resolve YES (y=1) if any snapshot in the window lists, case-insensitive:

- "#WWERaw", "WWERaw", "WWE Raw", or standalone "Raw"; OR
- at least TWO of these on-card names: "Penta", "Laredo Kid", "Seth Rollins",
  "Rollins", "Liv Morgan", "Dominik Mysterio", "Oba Femi", "Sol Ruca".

Resolve NO (y=0) otherwise. No other source, window, or term list may be
substituted; a changed regime refuses resolution rather than bending the rule.

## Evidence document ("trend-observation")

JSON: { "source": <uri>, "window": <string>, "snapshots_checked": <int>,
"matched_terms": [<string>], "outcome": { "y": 0|1 } } — hash-pinned at
resolution alongside a saved copy (screenshot or archive URI) of the snapshot.

Due: 2026-08-11T16:00:00Z. Nonresolution: forfeit at Brier penalty 1.
