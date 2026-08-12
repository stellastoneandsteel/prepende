# Evaluation regime: morning-paiper-bias-link-coverage-2026-08-16 (v1)

Question: Will at least 60% of non-video Morning Paiper feed items yield a
bias-ratable article body on a single first-attempt server-side fetch?

## Why this is the load-bearing number

Morning Paiper is replacing its standalone paste-in Bias Meter page with a
per-article bias button that rates outside articles on demand. The feature is
only worth shipping if pressing the button usually produces a reading. Publisher
bot-blocking, paywalls, consent interstitials, and JS-only rendering are the
failure modes, and their combined rate is unknown before measurement.

## Population

A single live harvest of the public feed via `GET /v1/newsroom` on the
morning-paiper production deployment, restricted to items of kind
`reported_news`, EXCLUDING any item whose final host is a YouTube host
(`youtube.com`, `www.youtube.com`, `youtu.be`, `m.youtube.com`).

The YouTube exclusion is a DISCLOSED GENEROSITY toward the claim: YouTube watch
pages carry no article prose and can never yield 40 extractable words, and
YouTube feeds are roughly a quarter of the configured source list. Including
them would drag the measured rate down mechanically without saying anything
about publisher fetchability. The excluded count is reported alongside the
result and is not netted out of any headline figure.

If one harvest yields fewer than 30 qualifying items, take consecutive harvests
at least 60 minutes apart, deduplicated by canonical URL, until N >= 30. N is
frozen at the first harvest that reaches 30 or more.

## Attempt definition

Exactly one fetch per item, matching what a reader's button press does:

- User-Agent `MorningPaiperBot/1.0 (+https://morningpaiper.com)`
- 6000 ms timeout, at most 4 redirect hops, SSRF/DNS-pin guards active
- No retries, no backoff, no second attempt with a different User-Agent
- No JavaScript execution, no cookie jar, no consent-banner dismissal
- No cached reading may satisfy an item; every item is fetched live

An item that fails for ANY reason — HTTP error, block, timeout, paywall,
consent wall, non-HTML content type, or successful fetch whose extracted body
is under 40 words — counts as a failure. There is no partial credit and no
"would have worked with a retry" category.

## Success definition

An item SUCCEEDS if and only if, on that single attempt,
`extractArticleBody(html)` returns text of at least 40 words AND
`rateBias(text)` returns `ok: true`.

Coverage = successes / N, over the frozen population.

## Resolution rule

Resolve YES (y=1) if coverage >= 0.60. Resolve NO (y=0) if coverage < 0.60.

Measured by `scripts/measure_bias_link_coverage.mjs` in the morning-paiper
repository, run once against the frozen population. The script's JSON output is
hash-pinned at resolution. No other script, population, threshold, or attempt
definition may be substituted; a changed regime refuses resolution rather than
bending the rule. If the script is edited after this contract is locked, the
edit is a regime change and resolution is refused.

## Evidence document ("coverage-measurement")

JSON: { "harvested_at": <iso8601>, "n": <int>, "excluded_youtube": <int>,
"successes": <int>, "coverage": <float>, "failures_by_reason":
{ <reason>: <int> }, "items": [ { "url": <string>, "host": <string>,
"ok": <bool>, "words": <int>, "reason": <string|null> } ],
"outcome": { "y": 0|1 } } — hash-pinned at resolution together with the
script output and the frozen population list.

Due: 2026-08-16T23:59:00Z. Nonresolution: forfeit at Brier penalty 1.
