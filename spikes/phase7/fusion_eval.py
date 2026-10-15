"""Phase 7: does a SigLIP 2 + EmbeddingGemma 2 pair beat either model alone? Offline, from the vectors saved by
image_bench.py (no re-embedding).

Strategies (A = SigLIP 2, B = EmbeddingGemma 2):
  A alone, B alone
  rrf          Reciprocal Rank Fusion (k = 60), equal weights
  rrf_len      RRF with B's weight growing with query length (1 word -> 0.3, >= 6 words -> 0.7): a weight, not a switch
  zscore       per-query z-normalised similarities, summed (never raw similarities from two models)
  cascade      A retrieves the top 100, B re-ranks them ("fast first stage + contextual second stage")
For Polish single words B gets the language context ("pies na zdjęciu"), A the raw word (what the user typed).

Usage: python fusion_eval.py [siglip_tag] [eg2_tag]
"""

import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import image_eval as ie  # noqa: E402

BENCH = HERE / "data" / "bench"
K_RRF, CASCADE_N = 60, 100


def ranks(scores: np.ndarray) -> np.ndarray:
    """Rank (0 = best) of every image for one query."""
    order = np.argsort(-scores)
    r = np.empty_like(order)
    r[order] = np.arange(len(order))
    return r


def fused(a: np.ndarray, b: np.ndarray, words: int, method: str) -> np.ndarray:
    if method == "A":
        return a
    if method == "B":
        return b
    if method in ("rrf", "rrf_len"):
        wb = 0.5 if method == "rrf" else float(np.clip(0.3 + (words - 1) * 0.08, 0.3, 0.7))
        return (1 - wb) / (K_RRF + ranks(a)) + wb / (K_RRF + ranks(b))
    if method == "zscore":
        return (a - a.mean()) / a.std() + (b - b.mean()) / b.std()
    if method == "cascade":
        out = np.full_like(b, -np.inf)
        top = np.argsort(-a)[:CASCADE_N]
        out[top] = b[top]
        return out
    raise ValueError(method)


def main(sig_tag: str, eg_tag: str) -> None:
    data = ie.rows()
    A, B = np.load(BENCH / f"{sig_tag}.npz"), np.load(BENCH / f"{eg_tag}.npz")
    rel = {c[0]: np.array([bool(re.search(c[2], " ".join(r["captions"]).lower())) for r in data]) for c in ie.CONCEPTS}
    cap_target = np.array([i for i, r in enumerate(data) for _ in r["captions"]])
    cap_words = [len(c.split()) for r in data for c in r["captions"]]
    pl_target = np.array([next(i for i, r in enumerate(data) if r["file"] == f) for f in ie.PL_CAPTIONS])
    pl_words = [len(c.split()) for c in ie.PL_CAPTIONS.values()]

    def concept_map(method, qa_key, qb_key):
        aps = []
        for i, (en, *_rest) in enumerate(ie.CONCEPTS):
            s = fused(A["img"] @ A[f"q_{qa_key}"][i], B["img"] @ B[f"q_{qb_key}"][i], 1, method)
            hits = rel[en][np.argsort(-s)]
            prec = np.cumsum(hits) / np.arange(1, len(hits) + 1)
            aps.append((prec * hits).sum() / hits.sum())
        return float(np.mean(aps))

    def recall(method, key, target, words, k):
        sa, sb = A[f"q_{key}"] @ A["img"].T, B[f"q_{key}"] @ B["img"].T
        hits = [target[i] in np.argsort(-fused(sa[i], sb[i], words[i], method))[:k] for i in range(len(target))]
        return float(np.mean(hits))

    print(f"A = {sig_tag}, B = {eg_tag}")
    print(f"{'strategia':10s} mAP EN  mAP PL  R@1 EN  R@5 EN  R@1 PL  R@5 PL")
    for m in ("A", "B", "rrf", "rrf_len", "zscore", "cascade"):
        print(f"{m:10s} {concept_map(m, 'en', 'en'):.3f}   {concept_map(m, 'pl', 'pl_ctx'):.3f}   "
              f"{recall(m, 'cap_en', cap_target, cap_words, 1):.3f}   {recall(m, 'cap_en', cap_target, cap_words, 5):.3f}   "
              f"{recall(m, 'cap_pl', pl_target, pl_words, 1):.2f}    {recall(m, 'cap_pl', pl_target, pl_words, 5):.2f}")  # fmt: skip


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "siglip2-base-patch16-256-fp16",
         sys.argv[2] if len(sys.argv) > 2 else "eg2-8bit-140")  # fmt: skip
