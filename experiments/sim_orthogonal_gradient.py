#!/usr/bin/env python3
"""
H1 (orthogonal-gradient), TOY proof-of-measurement.

The full hypothesis (NEEDS-INFRA, pre-registered separately): in a trained network, the
direction that most improves cross-task generalization is near-orthogonal to the direction
that most reduces in-distribution loss. We cannot run the frontier version here. This is the
TOY version: a tiny shared-feature MLP, two related regression tasks A (in-distribution) and
B (held-out transfer), measuring cosine(grad_A, grad_B) over training.

Interpretation: cosine ~0 => orthogonal (supports the perpendicular reading); cosine clearly
positive => aligned (transfer comes free with training, REFUTES strict orthogonality);
negative => anti-aligned (training hurts transfer). This is a toy proof of the MEASUREMENT,
not the frontier claim, and resolves nothing about the full hypothesis.

Pure numpy. Cosmetic Accelerate warnings are false positives.
"""
import os, warnings, json
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
warnings.filterwarnings("ignore", category=RuntimeWarning)
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
d, h = 8, 16          # input dim, hidden width


def teachers(seed):
    r = np.random.default_rng(seed)
    Wt1 = r.standard_normal((d, h))          # SHARED feature map (so transfer is real)
    wA = r.standard_normal(h)
    wB = r.standard_normal(h)                 # different readout => related but distinct task
    fA = lambda X: np.tanh(X @ Wt1) @ wA
    fB = lambda X: np.tanh(X @ Wt1) @ wB
    return fA, fB


def grad(X, y, W1, w2):
    H = np.tanh(X @ W1); yp = H @ w2; e = yp - y; N = len(y)
    gw2 = H.T @ e / N
    dH = np.outer(e, w2) * (1 - H ** 2) / N
    gW1 = X.T @ dH
    return np.concatenate([gW1.ravel(), gw2])


def run(seed):
    rng = np.random.default_rng(seed)
    fA, fB = teachers(seed + 100)
    X = rng.standard_normal((512, d))
    yA, yB = fA(X), fB(X)
    W1 = rng.standard_normal((d, h)) * 0.3
    w2 = rng.standard_normal(h) * 0.3
    lr, coss = 0.1, []
    for step in range(400):
        gA = grad(X, yA, W1, w2)
        gB = grad(X, yB, W1, w2)
        coss.append(float(gA @ gB / (np.linalg.norm(gA) * np.linalg.norm(gB) + 1e-12)))
        # SGD step on task A (the in-distribution task)
        gW1 = gA[:d * h].reshape(W1.shape); gw2 = gA[d * h:]
        W1 -= lr * gW1; w2 -= lr * gw2
    return np.array(coss)


if __name__ == "__main__":
    print("H1 TOY: cosine(grad_in-distribution-A, grad_held-out-related-B) over training")
    print("shared feature map (transfer is real), different readouts; 5 seeds")
    allc = np.stack([run(s) for s in range(5)])      # 5 x 400
    early = allc[:, :50].mean(); late = allc[:, -50:].mean()
    overall = allc.mean()
    print("mean cosine  overall=%.3f  early(0-50)=%.3f  late(350-400)=%.3f"
          % (overall, early, late))
    print("range across seeds (overall mean per seed): [%.3f, %.3f]"
          % (allc.mean(1).min(), allc.mean(1).max()))
    verdict = ("ORTHOGONAL (|cos|<0.15)" if abs(overall) < 0.15 else
               "ALIGNED (cos>0.15)" if overall > 0 else "ANTI-ALIGNED (cos<-0.15)")
    print("toy read:", verdict, "-> supports orthogonality?" , abs(overall) < 0.15)
    json.dump({"study": "H1-orthogonal-gradient-toy", "seeds": 5,
               "mean_cos_overall": round(overall, 4), "mean_cos_early": round(early, 4),
               "mean_cos_late": round(late, 4), "toy_verdict": verdict},
              open(os.path.join(HERE, "data", "h1-orthogonal-gradient-toy.json"), "w"), indent=2)
    print("wrote data/h1-orthogonal-gradient-toy.json")
