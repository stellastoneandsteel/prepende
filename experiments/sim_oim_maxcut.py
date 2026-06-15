#!/usr/bin/env python3
"""
Numerical proof-of-concept for Build A (the prepende bench cell):
a coupled phase-shift-oscillator Ising machine solving Max-Cut.

Model (standard Oscillator Ising Machine, Wang & Roychowdhury):
    dtheta_i/dt = -sum_j J_ij * sin(theta_i - theta_j) - Ks * sin(2*theta_i) + noise
The -Ks*sin(2 theta) term is the sub-harmonic injection lock (SHIL) at 2*f0;
it creates two stable phases {0, pi} = Ising spins {+1, -1}.
For Max-Cut we set J_ij = -1 on each edge (antiferromagnetic), so the energy
minimum puts edge endpoints in anti-phase = cut. We anneal Ks up and noise down.

Pure stdlib. Brute-force the optimum for small graphs and report the hit-rate.
"""
import math, random

def brute_force_maxcut(n, edges):
    best = -1
    # fix node 0 in partition 0 (cut is gauge-invariant)
    for mask in range(1 << (n - 1)):
        spin = [0] * n
        for k in range(n - 1):
            spin[k + 1] = (mask >> k) & 1
        cut = sum(1 for (a, b) in edges if spin[a] != spin[b])
        if cut > best:
            best = cut
    return best

def cut_value(spin, edges):
    return sum(1 for (a, b) in edges if spin[a] != spin[b])

def simulate(n, edges, seed, steps=3000, dt=0.04, Ks_max=1.6, noise0=0.6):
    rng = random.Random(seed)
    theta = [rng.uniform(0, 2 * math.pi) for _ in range(n)]
    nbr = [[] for _ in range(n)]
    for (a, b) in edges:
        nbr[a].append(b)
        nbr[b].append(a)
    for t in range(steps):
        frac = t / steps
        Ks = Ks_max * frac           # anneal SHIL up
        noise = noise0 * (1 - frac)  # anneal noise down
        sq = math.sqrt(dt)
        newtheta = theta[:]
        for i in range(n):
            ti = theta[i]
            # J_ij = -1 on edges -> drift = sum_j sin(theta_i - theta_j)
            coup = 0.0
            for j in nbr[i]:
                coup += math.sin(ti - theta[j])
            drift = coup - Ks * math.sin(2 * ti)
            newtheta[i] = ti + dt * drift + noise * sq * rng.gauss(0, 1)
        theta = newtheta
    spin = [0 if math.cos(t) > 0 else 1 for t in theta]  # nearest of {0, pi}
    return spin

def run_graph(name, n, edges, trials=200, seed0=0):
    opt = brute_force_maxcut(n, edges)
    hits = 0
    best = -1
    cuts = []
    for s in range(trials):
        spin = simulate(n, edges, seed=seed0 + s)
        c = cut_value(spin, edges)
        cuts.append(c)
        best = max(best, c)
        if c >= opt:
            hits += 1
    mean = sum(cuts) / len(cuts)
    print(f"{name:18s} n={n:2d} edges={len(edges):3d}  optimal_cut={opt:3d}  "
          f"best_found={best:3d}  mean={mean:5.2f} ({mean/opt*100:4.1f}% of opt)  "
          f"hit-rate={hits/trials*100:5.1f}%  ({hits}/{trials})")
    return opt, best, hits / trials

def cycle(n):
    return [(i, (i + 1) % n) for i in range(n)]

def complete(n):
    return [(i, j) for i in range(n) for j in range(i + 1, n)]

def petersen():
    outer = [(i, (i + 1) % 5) for i in range(5)]
    spokes = [(i, i + 5) for i in range(5)]
    inner = [(5 + i, 5 + (i + 2) % 5) for i in range(5)]
    return outer + spokes + inner

def random_graph(n, p, seed):
    rng = random.Random(seed)
    return [(i, j) for i in range(n) for j in range(i + 1, n) if rng.random() < p]

if __name__ == "__main__":
    print("Build A (coupled-oscillator Ising machine) vs Max-Cut optimum")
    print("=" * 92)
    run_graph("C5 cycle", 5, cycle(5))
    run_graph("K5 complete", 5, complete(5))
    run_graph("Petersen", 10, petersen())
    run_graph("random n=10 p=.5", 10, random_graph(10, 0.5, 42))
    run_graph("random n=12 p=.4", 12, random_graph(12, 0.4, 7))
    print("=" * 92)
    print("hit-rate = fraction of 200 random-start trials that reached the proven optimum")
