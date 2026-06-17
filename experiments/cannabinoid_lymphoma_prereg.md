# Pre-registration — Cannabinoids and lymphoma: what the evidence actually supports

> **STATUS: LOCKED 2026-06-17.** Contracts H1–H5 are locked into `predictions.jsonl`
> via `cannabinoid_lymphoma_predictions.py --lock` (cids: H1 `a805f2314ca1`,
> H2 `99959fa3080f`, H3 `d74a05b6f6dc`, H4 `6c3e61b3a118`, H5 `5eaa9c60b91e`).
> Resolution is pending (step 3). No video. This makes no treatment claim.

## Why this study

This study began from a single striking observation: a chest mass that resolved during
a period of full-spectrum cannabis-extract use, later characterised as an aggressive
lymphoma, found to be gone on follow-up. That is an **n=1 anecdote** — hypothesis-
generating, not evidence of causation. (No personal identifying details are recorded
here or in any output.)

The point of running it through prepende is the same discipline as every other study
here: **lock the predictions before testing, then let the resolution demote even the
hypothesis we most want to be true.** A favourable anecdote is exactly the case where
self-deception is most likely, so it is the best possible test of the method's honesty.

This is **not medical advice** and makes **no treatment claim**. Its only output is an
honest map of what the peer-reviewed evidence does and does not support.

## What the scouting batch already found (2026-06-17, PubMed + Consensus + ClinicalTrials.gov)

- **Preclinical signal is real**: cannabinoids induce apoptosis / cell-cycle arrest
  selectively in tumour cells (incl. lymphoma/leukemia) in vitro and in mouse models;
  active mechanisms in the literature run mostly through **CB2 / TRPV1 / integrated
  stress response**, not CB1. (Lal 2020; Besser 2024, eLife.)
- **The one human lymphoma trial cuts the other way**: Sativex in 23 indolent B-cell
  lymphoma patients dropped circulating lymphoma cells — but **without apoptosis**
  (no caspase-3); the effect looked like **redistribution**, and the malignant clone
  had **significantly increased at 1 week**. Authors urged **caution**. (Melén 2019, Blood.)
- **No interventional trial** tests cannabis as a lymphoma cure; the one active cancer
  study (COSMIC, NCT06418204) is observational and assesses **benefits AND harms**.
- **Spontaneous regression** is documented in **10–20% of indolent non-Hodgkin lymphoma**
  and recorded even in aggressive subtypes (DLBCL, mantle cell) — a sufficient
  alternative explanation for an unattributed single remission. (Drobyski 1989; Grem
  1986; Ye 2018.)

## Locked predictions (CONTRACTS — ready to lock, not yet locked)

Each resolves against a **dated, fixed literature search** (the eval_regime). `claim.p`
is the pre-registered probability the claim is TRUE.

| id | question | claim.p | resolution_rule | eval_regime |
|----|----------|---------|-----------------|-------------|
| H1 | No interventional human trial demonstrates cannabis/cannabinoids *cause* remission of lymphoma | 0.93 | y=1 if a fixed search returns 0 interventional trials with remission as a demonstrated cannabinoid effect | ClinicalTrials.gov + PubMed[Publication Type: Clinical Trial], queries fixed, run-once on lock date |
| H2 | Preclinical models show cannabinoid-induced apoptosis/arrest in lymphoma/leukemia lines | 0.95 | y=1 if ≥3 independent preclinical papers report it | PubMed + Consensus, fixed queries, lock date |
| H3 | The lone human lymphoma trial shows redistribution (not apoptotic killing) with ≥1-week rebound | 0.90 | y=1 if the trial's own results report no caspase-3 activation and a significant clonal increase at follow-up | Melén 2019 (Blood) as the fixed primary source |
| H4 | Spontaneous regression of NHL is documented at ≥10% in indolent subtypes | 0.92 | y=1 if ≥2 independent series report ≥10% SR in indolent NHL | PubMed + Consensus, fixed queries, lock date |
| H5 | A specific **CB1→DNA-replication** axis drives lymphoma cell death | 0.15 | y=1 if ≥2 independent papers establish a direct CB1→DNA-replication-machinery mechanism causing lymphoma death | PubMed, fixed queries, lock date |

**Pre-committed reading of the likely outcome:** preclinical promise (H2 ✓), no clinical
cure evidence (H1 ✓), the human trial pointing the *other* way (H3 ✓), spontaneous
regression as a live confound (H4 ✓), and the proposed CB1→DNA mechanism **failing to
hold** (H5 ✗) — the interesting, publishable failure.

## Confounds named up front
- biopsy/stage confirmation of the index case (unknown → cannot be assumed)
- any concurrent or prior conventional treatment (must be assumed possible)
- imaging/timeline gaps
- publication bias toward positive preclinical results

## What would change the conclusion
A registered interventional trial showing a causal anticancer effect, or a replicated
direct CB1→DNA-replication mechanism, would flip H1/H5 and warrant a stronger statement.
Until then, the honest output is: real preclinical signal, no human cure evidence, and a
better-supported explanation (spontaneous regression / immune response) for any single case.
