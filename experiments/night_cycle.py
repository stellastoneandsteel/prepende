#!/usr/bin/env python3
"""
night_cycle.py — the undirected-interval / "night cycle" prototype.

Essay No.1's most buildable claim: instruction-free recombination over a fixed
corpus, NO objective, surfacing surprising junctions as candidates for morning
review (the arise-and-gate pattern: arise freely, gate what you keep).

This script does only the UNDIRECTED half (the mechanism): it loads the corpus,
extracts concepts, and pairs them at random with no goal. The recombination
(cognition) and the salience gate (review) happen on top — by a model, then a
human — exactly as the architecture prescribes. Pure stdlib; deterministic by seed.
"""
import glob
import random
import re

CURATED = [  # prepende physics + tool concepts, to cross-pollinate with the essays
    "the coupling gap (compute in between)", "reservoir computing", "self-sustaining attractor",
    "time crystal", "Casimir effect / vacuum gap", "oscillator Ising machine",
    "perturbational complexity (PCI)", "gated hands / per-action approval",
    "echo-state property", "edge of chaos", "Wilson calibration / Brier score",
    "hash-locked prediction", "granular / cymatics medium", "evanescent near-field",
]


def harvest_concepts():
    pool = set(CURATED)
    for f in glob.glob("corpus/*.md"):
        txt = open(f, encoding="utf-8").read()
        m = re.search(r"tags:\s*\[([^\]]*)\]", txt)
        if m:
            for tag in m.group(1).split(","):
                t = tag.strip().strip('"').strip()
                if t and t not in ("prepende", "philosophy", "series-search-for-understanding"):
                    pool.add(t.replace("-", " "))
        m2 = re.search(r"^title:\s*\"?(.+?)\"?$", txt, re.M)
        if m2:
            pool.add(m2.group(1).split("—")[0].strip())
    return sorted(pool)


def pairs(pool, n, seed):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        a, b = rng.sample(pool, 2)
        out.append((a, b))
    return out


if __name__ == "__main__":
    import sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    pool = harvest_concepts()
    print("night cycle — undirected pass (seed=%d)" % seed)
    print("corpus concepts harvested: %d" % len(pool))
    print("=" * 70)
    for i, (a, b) in enumerate(pairs(pool, n, seed), 1):
        print("J%-2d  %s   ×   %s" % (i, a, b))
    print("=" * 70)
    print("next: recombine each pair with NO objective; salience-gate; keep only")
    print("the surprising-AND-valid junctions as candidates for review.")
