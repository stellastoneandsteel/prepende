# Evaluation regime: x-trends-us-2026-08-10-iran-hormuz (v1)

Question: Will the Iran–Hormuz story return to the X trend list in the United
States on Monday 2026-08-10?

## Observation source

Primary: https://trends24.in/united-states/ hourly snapshots (top-50 list per
snapshot). Fallback if trends24.in is unreachable on resolution day:
https://getdaytrends.com/united-states/ same-day listing.

## Window

Snapshots timestamped between 08:00 and 23:59 America/New_York on 2026-08-10.

## Resolution rule

Resolve YES (y=1) if any snapshot in the window lists, case-insensitive, any
of: "Iran", "#Iran", "Hormuz", "#Hormuz", "Strait of Hormuz", "Zolghadr",
"Araghchi". Resolve NO (y=0) otherwise. No other source, window, or term list
may be substituted; a changed regime refuses resolution rather than bending
the rule.

## Evidence document ("trend-observation")

JSON: { "source": <uri>, "window": <string>, "snapshots_checked": <int>,
"matched_terms": [<string>], "outcome": { "y": 0|1 } } — hash-pinned at
resolution alongside a saved copy (screenshot or archive URI) of the snapshot.

Due: 2026-08-11T16:00:00Z. Nonresolution: forfeit at Brier penalty 1.
