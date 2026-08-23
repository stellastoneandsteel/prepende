# Teardown: how @muellerberndt publishes, and what we do about it

Date: 2026-08-10. Internal note, not published to the Pages site.
Subject: Bernhard Mueller (X: `@muellerberndt`, sites: floatingpragma.io, Medium,
GitHub `muellerberndt` / `FloatingPragma` / `scabench-org`).

---

## 0. Evidence grade (read this before quoting anything below)

This session's network policy blocks direct fetches of `x.com`, `twitter.com`,
`medium.com`, `floatingpragma.io`, `arxiv.org`, and `linkedin.com`. GitHub was
directly readable. So:

| Claim | How obtained | Confidence |
|---|---|---|
| GitHub repo names, stars, README structure | first-hand page read | high |
| Medium article titles and dates | search-index snippets | high |
| X follower count (26.2K), join date (Jun 2011), following (1,851) | search-index snippet of profile | medium |
| Individual X post text and dates | search-index snippets of post pages | medium-high |
| The 78.4M-view promoted post | two independent search snippets agreeing | medium-high |
| Ad spend, targeting, campaign dates | **not obtainable** | X publishes no ad transparency library |
| Posting frequency per week | **not measured** | would need the timeline |

Anything below marked *inferred* is my read, not a verified fact.

---

## 1. What he actually is

Two audiences fused into one account, and that is the whole trick.

**Audience 1, earned over twenty years (security).** Mythril (4.3k stars, symbolic
execution for EVM), OWASP MASTG (13.1k), OWASP MASVS (2.4k), Hound (804, AI code
auditor), ScaBench. Pwnie Award for Best Research. This audience does not need to be
convinced he is serious.

**Audience 2, new and enormous (simulation theory / physics).** Observer Patch
Holography, the `FloatingPragma/observer-patch-holography` repo (654 stars), a
20-chapter book *Reverse Engineering Reality*, an interactive simulation site, a
YouTube playlist, and a stream of "we're in a simulation" X posts.

He spends Audience 1's credibility to buy Audience 2's attention. A random simulation
theorist gets dismissed. A guy with 13k stars on the OWASP mobile testing standard
saying the same thing gets read. That transfer is the asset, and it is the thing we
are actually competing with, not his prose.

## 2. The stack: six layers, each with exactly one job

This is the part worth copying, and it is the part we currently do not have at all.

| Layer | Surface | Its only job |
|---|---|---|
| 0 | GitHub repo (the hub) | Be the canonical, auditable thing everything points back to |
| 1 | arXiv paper + 4,300 Lean theorems | Buy institutional legitimacy |
| 2 | Book / long-form site (`oph-book.floatingpragma.io`) | Convert the interested into invested |
| 3 | Medium articles | Translate the formal artifact into English |
| 4 | X posts | Hook, and move you down one layer |
| 5 | Paid ads to `learn.floatingpragma.io` | Buy top-of-funnel for the mass layer only |

The discipline: **no layer tries to do two jobs.** The X post does not explain the
theory, it hooks. The Medium piece does not carry the proof, it links down to the
repo. The repo does not sell, it proves.

Our `docs/index.html` is 94KB and currently tries to be layers 0, 2, 3, and 4
simultaneously. That is the single biggest structural problem in our publishing.

## 3. Anatomy of his articles

**Titles are two-part, colon-separated, claim then mechanism.** Verified examples:

- *Observers are All You Need: How Observer-Synchronization Creates All of Physics*
- *Unleashing the Hound: How AI Agents Find Deep Logic Bugs in Any Codebase*
- *Hunting for Security Bugs with AI Agents: A Full Walkthrough*
- *STARK Lab: An Interactive Deep Dive into Zero-Knowledge Proofs* (Dec 2025)
- *The Security Researcher's Guide to Mathematics* (Nov 2025)
- *How Observer Patch Holography Improves on the Standard Model and General Relativity*
- *Is Tether a black swan? Risk assessment by a DeFi security guy* (Jun 2021)

Note the borrowed formats. "Observers are All You Need" rides *Attention Is All You
Need*. "Is Tether a black swan?" is a question the reader already had. He is not
inventing curiosity, he is intercepting it.

**He leads with a receipt when he has one.** Hound shipped with ScaBench numbers:
31.2% micro recall against an 8.3% baseline, 3.7x improvement, evaluated on real
findings from Code4rena, Cantina, and Sherlock. Not "powerful AI auditor." A number,
a baseline, and a named public dataset.

**Every major piece ships with something you can run.** STARK Lab is interactive.
The OPH program has a simulation site, a Telegram bot, an X bot, reproducible
notebooks, and a "Choose A Reading Path" guide. The article is never the terminal
artifact.

**The falsification section is the move.** The OPH README contains an explicit
falsification program: a massive graviton, gauge-mediated proton decay, a fourth
light generation, a charge-lattice outlier, or the wrong neutrino branch each kill
the theory as stated. He separates exact results from open interfaces, and
target-informed **postdictions** from forward predictions, and he runs a
"frozen-prediction ladder" using cryptographic custody.

Read that last sentence again. A man with 26K followers is using a
cryptographically-frozen prediction ladder to defend a physics claim. That is our
product's thesis, deployed in the wild, by someone with distribution. It is
simultaneously the strongest validation our category has and the clearest warning
about who gets there first.

## 4. Posting mechanics

**Link in the first reply.** Verified, in his own words: *"A HTML version of 'Hound:
Relation-First Knowledge Graphs...' is now available on arXiv. Link in 1st
response."* X suppresses posts carrying external links. He posts the hook clean and
drops the URL into reply 1. Free reach, costs nothing to copy.

**Numbered threads for research, one-liners for reach.** Research announcements open
`1/ I'm happy to announce that it's *feasible* to...`. The mass-audience posts are
single provocations:

- *"Observer Patch Holography (OPH) is the true Theory-of-Everything."* (Mar 2)
- *"What if all of physics emerges from consistency between local observers?"* (Mar 31)
- *"Learn how Observer Patch Holography unifies the Standard Model and gravity. Spoiler: We're in a simulation."* (Apr 14)
- *"Once you 'glitch' outside the matrix for the first time... Start the process now. It's time."* (Jan 26)

**Two registers, never blended in one post.** Rigorous and hedged for peers.
Mythic, almost devotional, for the mass audience. He keeps them on the same account
because the peer register is what makes the mythic register land, but he never puts
both in one post.

**One asset, many hooks, spread over months.** The same 20-chapter book was pushed
on Mar 31 ("What if all of physics emerges from..."), Apr 5 (paid, "Lean what our
Universe actually is"), and Apr 14 ("Spoiler: We're in a simulation"). Three
different emotional angles, one artifact. He is not writing more, he is re-aiming.

**Format spread:** GitHub, arXiv, Medium, X, YouTube playlist, Telegram bot, X bot,
interactive sim site, a Vercel summary site, plus a print PDF. Same content, many
doors.

## 4.5 Why it actually spreads

First, a correction to the framing. **His papers do not go viral.** No Hacker News
thread, no physics-forum debate, no peer critique surfaced in any search. The
papers and the 4,300 Lean theorems are ballast, not payload. What spreads is a
one-line reframe, and the largest number attached to any of it (78.4M) was bought,
not earned. The honest organic proof points are much smaller: 654 repo stars, a
YouTube playlist, and diffusion into non-physics corners (a fine-art drawing blog
posted about Observer Patch Holography in April 2026).

So the spread is consumer-side, not peer-side. Six devices do the work.

**1. The status-inversion sentence.** Verbatim from the README:

> "Physics has revised its idea of what is fundamental before. Space was absolute
> until it was relative; matter was continuous until it was quantized. Each revision
> looked outrageous from inside the previous picture and obvious from inside the
> next one. OPH makes the next revision."

This is pre-emptive immunization. It tells the reader in advance that finding the
claim absurd is the *expected* reaction of someone standing on the wrong side of
history. Skepticism gets converted into evidence for the claim. It is the single
highest-leverage rhetorical structure in his entire corpus, and it costs nothing.

**2. He wrote the theory of his own distribution.** In 2022 he published *The
Selfish Meme: A Simulation Theory of Everything*, taking Dawkins' replicators to
the extreme: culture as variation and selection, ideas as things that survive by
being copyable. His actual research subject is how ideas self-replicate. That does
not prove his publishing is engineered, but it explains why it looks engineered.
He is running his own theory on his own output.

**3. Format arbitrage: dorm-room idea plus institutional armor.** "We're in a
simulation" is among the most-shared ideas on the internet and carries zero status.
Lean-checked theorems carry status and zero reach. Neither half spreads on its own.
Bolting them together produces a claim a general audience can repeat at a party and
cannot easily dismiss. **The seam is the product.** That is the transferable
insight, not the physics.

**4. Falsifiability used offensively, not defensively.** "40 hard OPH-killing
outcomes." From the README: *"A mismatch with the Standard Model is an allowed
outcome rather than something the protocol may tune away."* This does three jobs
simultaneously. It reads as maximally scientific. It makes attacking him expensive,
since a critic now has to engage his list rather than wave him off. And it
manufactures argument, which is distribution. Worth noting coldly: none of the 40
killers will be observed this quarter, so the posture is rhetorically potent and
practically unfalsifiable on any timescale that matters.

**5. Initiation framing on the consumer layer.** Verbatim:

> "Once you 'glitch' outside the matrix for the first time, and understand the
> fundamental thinking error everyone makes, a process starts. Every day, you see
> the true reality a little clearer. Little steps. Start the process now. It's time."

That is not science communication. It is a conversion sequence: a promise of ongoing
revelation, staged progress, and personal transformation. It turns a reader into a
returning reader. This is the device that most explains the mass-audience numbers,
and it is also the one we must not touch (see below).

**6. Zero friction, zero paywall, many doors.** An 800-page book free on the web,
a print PDF, a public repo, a simulation site, a YouTube playlist, Telegram and X
bots. Nothing is for sale, which removes the obvious motive attack, and every format
is an independent entry point.

### What transfers to us, and what does not

**Take:** the status-inversion sentence (we have a real one available: every field
that got serious made its practitioners record predictions before the outcome, and
AI has not yet). The falsification list as an offensive asset, where ours is
strictly stronger because our killers resolve in weeks rather than never. The format
arbitrage, where our seam is "stated AI confidence is a marketing number" bolted to
a hash any reader can verify in 30 seconds. Zero friction.

**Do not take:** the initiation register. "Exit the matrix" language would falsify
our product. Our entire claim is that we do not overclaim, and the honesty floor is
the asset. One post in that voice costs more than it earns.

**The strategic fact underneath all of it:** he has built reach without
adjudication. Nobody scores him, and by design nobody can for decades. We are
building adjudication without reach. Those two meet in exactly one place, which is
franchise C: pointing a working scoreboard at claims that currently enjoy reach and
no score. That is why the teardown series is the growth engine and not a side quest.

## 5. Does he pay for ads: yes, and here is the evidence

Post `x.com/muellerberndt/status/2040766214193250750`, dated 2026-04-05:

> "Lean what our Universe actually is: A computation (a.k.a. simulation) on a
> holographic screen. Here's exactly how it works, and the math to prove it."

Three things about it:

1. **It reportedly carries 78.4M views.** On a 26.2K-follower account. Organic reach
   at 3,000x follower count does not happen. This is bought.
2. **It points to `learn.floatingpragma.io`,** a dedicated subdomain that appears
   nowhere in his organic layer stack. A separate landing page for ad traffic is a
   paid-acquisition tell.
3. **Replies discuss it as "the ad" and criticise a typo:** the first word reads
   "Lean" instead of "Learn". The top reply is about the typo, not the physics.

Caveats, stated plainly: X operates no public ad transparency library, so spend,
flight dates, targeting, and total campaign count are unknowable. The 78.4M figure
comes from search-index snippets of the post page, not from a first-hand read.

**The strategic read (inferred).** He pays only at layer 5, only for the
mass-market simulation-theory content, and only into a purpose-built landing page.
There is no sign he pays to promote the security work, which already has organic
distribution. Paid is bolted onto a dense organic corpus, not used as a substitute
for one. And the typo episode is the lesson in miniature: 78.4M paid impressions
bought attention, and the most-visible response was that the ad looked careless.
Paid reach does not buy trust. It only rents eyeballs for whatever the corpus
already earned.

## 6. Where he is beatable

1. **His predictions do not resolve.** He has a frozen-prediction ladder, which is
   admirable, but a graviton mass bound is not settling this decade. Ours resolve in
   weeks. We can show a scoreboard that actually moves. He cannot.
2. **He admits postdiction.** He explicitly labels target-informed postdictions.
   Every one of our claims is pre-registered and hashed before the run. That is a
   strictly stronger position and it is cheap for us to hold.
3. **He lists falsifiers; he does not headline misses.** We publish MISSES as
   headlines with the lock hash attached. That is a harder, rarer signal, and as far
   as the prior-art sweep found, nobody else ships it.
4. **His claim surface is unverifiable by his readers.** 4,300 Lean theorems are not
   checkable by an interested reader on a lunch break. `pip install`, lock a
   prediction, get a hash: 30 seconds. Verifiability by a normal person is our lane.
5. **The paid layer is sloppy.** A typo in the first word of a campaign that reached
   tens of millions. Speed over polish at the exact moment polish mattered.

## 7. Our corpus, audited

**What we have (verified in-repo, 2026-08-10):**

- `docs/index.html`, 94KB. Four experiments, five build cards, concept art, the
  honesty floor, an embedded video. Doing the work of hub, book, article, and
  landing page at once.
- `docs/log/index.html`, 35KB, 17 logbook entries dated 2026-06-15 through
  2026-08-03. **This is our best asset and it is buried one click down.** Entry
  titles already read like his headlines: *"Scale buys capacity, not
  noise-robustness"*, *"The real circuit is weaker than its own model"*,
  *"Reliability has a lever, and a low ceiling"*.
- `docs/reservoir-arc/index.html`, 15KB. Our one true standalone article, and it is
  the right shape: why pre-register, three batches, a scorecard, what it means.
- `docs/build/index.html`, 7KB.
- Five V2 protocol documents (`PROTOCOL_V2`, `THREAT_MODEL_V2`,
  `PRODUCTION_PILOT_V2`, `ANCHOR_PROVIDER_SPIKE_V2`, `PUBLISHING`). **Zero of these
  have an article.** This is unpublished authority sitting on disk.
- Ledger: 26 contracts, 14 resolutions, 12 unresolved. Below our own stated n>=30
  validity floor.

**Cadence, from git history:** a 30-commit burst June 15 to June 21, then six weeks
of nothing but the weekly auto-rebuild, then a V2 burst August 8 to August 9. Two
bursts and a silence. The auto-rebuild kept the numbers fresh while the writing
stopped, which is exactly the failure mode a weekly job hides.

**What we do not have, measured against his stack:**

| Layer | Him | Us |
|---|---|---|
| 0 hub | repo, 654 stars, receipts index | repo, no receipts index |
| 1 formal | arXiv + Lean | none |
| 2 long-form | 800-page book, own domain | one 94KB page |
| 3 translation | Medium, years of it | none |
| 4 hook | X, 26.2K followers | **no account** |
| 5 paid | promoted, dedicated LP | none (correctly, for now) |
| run-it-yourself | STARK Lab, sim site, bots | CLI and an SVG |

The missing browser demo is the cheapest high-leverage item on this list. "Type a
prediction, watch it get hashed, try to change it and watch it get rejected" is a
one-day static page and it is the single most persuasive thing we could hand
someone.

## 8. What we write, when, and how

**Governing principle.** We do not have his twenty-year audience and we will not
manufacture one. We have the thing he does not: a ledger that resolves on a human
timescale. So we lead with the scoreboard, not the thesis. His opening move is "here
is my theory of everything." Ours is "here is what we got wrong, timestamped before
we knew."

### Three franchises, rotated

**A. The MISS series (flagship, unique to us).**
One article per resolved miss. Title pattern: claim, colon, correction.
*"Scale should have rescued the reservoir. It did not. Here is the hash we locked
before we ran it."* We have at least three ready to write from the logbook: the
XOR noise-fragility miss, the scaling miss, and the averaging-recovery miss.

**B. The protocol series (technical authority).**
One article per V2 document. Start with the hardest one: what `integrity()` actually
covers, including the fact that the README overstated it and we fixed it. Publishing
your own overclaim, with the commit that corrected it, is worth more than three
feature announcements.

**C. The teardown series (reach).**
Point prepende at other people's public, dated, resolvable claims. Score a public
forecaster. Score a lab's stated confidence against outcomes. This is the franchise
that travels beyond our niche, and it is the only one that would ever justify paid
promotion later.

### Article template

Steal his shape, keep our floor. No em dashes.

1. Two-part title: claim, colon, mechanism.
2. Open on the receipt. The number, the lock hash, the date locked. First screen.
3. What we pre-registered, quoted verbatim, with hash and timestamp.
4. What happened.
5. What this does not prove. His "open interfaces" section, our honesty floor. Name
   the retrospective rows, name the n, name the floor we are under.
6. What would falsify the next claim, locked before you finish reading.
7. Run it yourself. Three lines, copy-pasteable, working.

### Cadence (the "when")

- **One logbook entry per week, minimum.** Non-negotiable. The auto-rebuild already
  refreshes the numbers; the entries are the missing half. Six silent weeks is the
  bug we are fixing.
- **One article every two weeks**, rotating A, B, C.
- **One X post per weekday** once the account exists. Three of five carry a number,
  not an opinion.
- **Re-aim, do not re-write.** Ship the article, post the hook thread next morning,
  post a *different* hook for the same article ten days later. He proved this works
  across three months on one book.

### Posting mechanics to copy verbatim

- Link in the first reply, always.
- The hook carries the number, never the noun. *"14 resolved. Brier 0.19. Four
  public misses, all with hashes."* beats *"Introducing Prepende Protocol v2."*
- One image per post, and we already have it: the dependency-free reliability
  diagram SVG. Ship it with every scoreboard post.
- Cross-post to Medium three days after the site version, canonical link back to the
  site.
- **arXiv the protocol spec.** `PROTOCOL_V2.md` is most of a paper already. This is
  the cheapest institutional legitimacy available and he uses it constantly. It also
  timestamps the mechanism publicly, which matters given section 6's warning.

### Paid: not yet

His paid layer works because there is a decade of organic corpus behind it. We have
four pages and no social account. Money spent now buys a bounce off a page with
nothing to click next. Revisit when we have roughly 20 articles and 30+ resolutions,
and then only behind the teardown series.

### Sequencing, next 90 days

| Weeks | Work |
|---|---|
| 1 to 2 | Structural fix. Split `docs/index.html` into a hub plus per-article pages. Stand up the X account. Ship the browser "lock a prediction" demo. |
| 3 to 4 | Article B1: what `integrity()` covers, what the README overstated, and the commit that fixed it. |
| 5 to 6 | Article A1: the reservoir miss, rewritten standalone. Cross-post to Medium. |
| 7 to 8 | arXiv the protocol spec. Article B2: anchoring, and why caller-supplied `created_at` was a hole we shipped. |
| 9 to 12 | Article C1: first teardown of an outside public claim. This is the growth article. |

**Do this first, before any of it:** close the n>=30 gap. Twelve contracts are
unresolved. Every article in every franchise leans on a headline number that is
currently below our own stated validity floor, and the site is already showing it.
Resolving those honestly is a prerequisite, not a parallel track.

## 9. Open decision: pre-register this plan?

On brand and defensible: lock two or three contracts about this content plan itself.
For example, "by 2026-11-10 the site carries at least six standalone article pages,"
or "the first teardown article draws more unique visitors than the median of the
first five articles."

One condition if we do it: a separate predictor label such as `prepende:ops`, so
operations predictions never pool into the calibration headline, same rule that
already governs `prepende:dev-selftest`. Not appending anything without your call,
since the ledger is append-only and a wrong row cannot be taken back.

---

## Sources

- [Bernhard Mueller (@muellerberndt) on X](https://x.com/muellerberndt)
- [muellerberndt on GitHub](https://github.com/muellerberndt)
- [FloatingPragma/observer-patch-holography](https://github.com/muellerberndt/observer-patch-holography)
- [FloatingPragma organization repositories](https://github.com/orgs/FloatingPragma/repositories)
- [scabench-org/hound](https://github.com/scabench-org/hound)
- [Bernhard Mueller on Medium](https://muellerberndt.medium.com/)
- [Observers are All You Need](https://muellerberndt.medium.com/observers-are-all-you-need-how-observer-synchronization-creates-all-of-physics-8ebb7e9783e7)
- [How Observer Patch Holography Improves on the Standard Model and General Relativity](https://muellerberndt.medium.com/how-observer-path-holography-improves-on-the-standard-model-and-general-relativity-c971c376027e)
- [Unleashing the Hound](https://muellerberndt.medium.com/unleashing-the-hound-how-ai-agents-find-deep-logic-bugs-in-any-codebase-64c2110e3a6f)
- [Hunting for Security Bugs with AI Agents: A Full Walkthrough](https://muellerberndt.medium.com/hunting-for-security-bugs-in-code-with-ai-agents-a-full-walkthrough-a0dc24e1adf0)
- [STARK Lab](https://muellerberndt.medium.com/stark-lab-an-interactive-deep-dive-into-zero-knowledge-proofs-d5894121b22e)
- [The Security Researcher's Guide to Mathematics](https://muellerberndt.medium.com/the-security-researchers-guide-to-mathematics-000dc0c98a0f)
- [Floating Pragma](https://floatingpragma.io/) and [Selected Works](https://floatingpragma.io/selected-works/)
- [Reverse Engineering Reality (web edition)](https://oph-book.floatingpragma.io/)
- [Hound paper, arXiv 2510.09633](https://arxiv.org/abs/2510.09633)
- ["Link in 1st response" post](https://x.com/muellerberndt/status/1977971608561348880)
- [The promoted post, 2026-04-05](https://x.com/muellerberndt/status/2040766214193250750)
- [OPH book promo, 2026-03-31](https://x.com/muellerberndt/status/2038896409915896119)
- [OPH "true Theory-of-Everything", 2026-03-02](https://x.com/muellerberndt/status/2028470843177832735)
- [OPH unification post, 2026-04-14](https://x.com/muellerberndt/status/2043960734993002632)
- [OPH YouTube playlist](https://www.youtube.com/playlist?list=PLff0tYtg64Egc2sTtKgThcPRNRdR6i83O)
