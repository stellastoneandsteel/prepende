# Evaluation regime: x-trends-us-2026-08-10-drake-carryover (v1)

Question: Will "Drake" still be trending on X in the United States on Monday
2026-08-10 — day 2 after the 2026-08-08 Stake-anniversary Kick livestream
(20-v-1 dating segment, the "bark" moment)?

## Observation source

Primary: https://trends24.in/united-states/ hourly snapshots (top-50 list per
snapshot). Fallback if trends24.in is unreachable on resolution day:
https://getdaytrends.com/united-states/ same-day listing.

## Window

Snapshots timestamped between 08:00 and 23:59 America/New_York on 2026-08-10.

## Resolution rule

Resolve YES (y=1) if any snapshot in the window lists "Drake" (case-insensitive,
standalone name or hashtag "#Drake"). Nothing else counts: adjacent stream
figures (Pinkchyu, Jordyn, Lena, $Bark, Stake) do NOT satisfy the rule — the
question is whether Drake himself holds the trend list a second full day.

Resolve NO (y=0) otherwise. No other source, window, or term list may be
substituted; a changed regime refuses resolution rather than bending the rule.

## Evidence document ("trend-observation")

JSON: { "source": <uri>, "window": <string>, "snapshots_checked": <int>,
"matched_terms": [<string>], "outcome": { "y": 0|1 } } — hash-pinned at
resolution alongside a saved copy (screenshot or archive URI) of the snapshot.

Due: 2026-08-11T16:00:00Z. Nonresolution: forfeit at Brier penalty 1.
