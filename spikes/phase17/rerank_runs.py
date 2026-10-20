"""Reranks the top 20 of the fusion shipped today with a cross-encoder and saves the new order with its cost, for
fusion_analyze.py (DECISIONS.md, "Reranker measured"). Runs outside Lantern's environment, in a throwaway one:

    uv run --no-project --with mlx-lm==0.26.3 python -I rerank_runs.py qwen <runs dir> <out dir>
    uv run --no-project --with sentence-transformers==5.1.0 --with torch==2.8.0 python -I rerank_runs.py bge <runs dir> <out dir>

qwen: mlx-community/Qwen3-Reranker-0.6B-4bit (MLX, what Lantern could ship); bge: BAAI/bge-reranker-v2-m3 (PyTorch on
the GPU through MPS, fp16; no MLX port exists). Only test queries, original and one-word forms.
"""

import json
import re
import resource
import sys
import time
from pathlib import Path

TOP = 20
DOC_CHARS = 1500
STOP_PL_EN = None  # filled from the runs' own shipped rule below


def shipped_order(r, sentence):
    """The fusion shipped today (hybrid.fuse): RRF k=10 words x0.5 for a sentence, k=20 words x2 otherwise."""
    k, w = (10, 0.5) if sentence else (20, 2.0)
    s = {}
    for i, (d, *_) in enumerate(r["v"]):
        s[d] = s.get(d, 0.0) + 1.0 / (k + i + 1)
    for i, (d, _, cov, *_) in enumerate(r["k"]):
        if cov >= 0.5:
            s[d] = s.get(d, 0.0) + w * cov / (k + i + 1)
    return sorted(s, key=lambda d: -s[d])


def chunk_for(r, doc, texts):
    for x in (*r["v"], *r["k"]):
        if x[0] == doc:
            return texts.get(str(x[-1]), "")
    return ""


class Qwen:
    REPO, REV = "mlx-community/Qwen3-Reranker-0.6B-4bit", "5f324548f1d20c2b5a450f126fc6ef2fb1126524"
    PREFIX = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the "
              "Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n")
    SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    TASK = "Given a web search query, retrieve relevant passages that answer the query"  # the model card's default

    def __init__(self):
        import mlx.core as mx
        from huggingface_hub import snapshot_download
        from mlx_lm import load

        self.mx = mx
        mx.set_memory_limit(2 * 1024**3)  # Lantern's rule: always cap MLX (unbounded, a first try reached 12 GB)
        mx.set_cache_limit(256 * 1024**2)
        path = snapshot_download(self.REPO, revision=self.REV)
        self.model, self.tok = load(path)
        self.yes = self.tok.convert_tokens_to_ids("yes")
        self.no = self.tok.convert_tokens_to_ids("no")

    def scores(self, query, docs):
        mx = self.mx
        seqs = [self.tok.encode(self.PREFIX + f"<Instruct>: {self.TASK}\n<Query>: {query}\n<Document>: {d}" + self.SUFFIX)
                for d in docs]  # fmt: skip
        width = max(len(s) for s in seqs)
        pad = self.tok.pad_token_id or 0
        batch = mx.array([s + [pad] * (width - len(s)) for s in seqs])  # right padding: causal, last real token is read
        # only the last real token's hidden state goes through the output layer: full logits for every position of
        # 20 sequences (20 x ~600 x 151 936 values) were what took 12 GB in the first try
        hidden = self.model.model(batch)
        last = mx.array([len(s) - 1 for s in seqs])
        h = hidden[mx.arange(len(seqs)), last]
        if getattr(self.model.args, "tie_word_embeddings", False):
            picked = self.model.model.embed_tokens.as_linear(h)
        else:
            picked = self.model.lm_head(h)
        pair = mx.stack([picked[:, self.no], picked[:, self.yes]], axis=1)
        p = mx.softmax(pair.astype(mx.float32), axis=1)[:, 1]
        mx.eval(p)
        return p.tolist()

    def peak_mb(self):
        return self.mx.get_peak_memory() / 2**20


class Bge:
    REPO, REV = "BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"

    def __init__(self):
        import torch
        from sentence_transformers import CrossEncoder

        self.torch = torch
        self.model = CrossEncoder(self.REPO, revision=self.REV, device="mps", max_length=512,
                                  model_kwargs={"torch_dtype": torch.float16})  # fmt: skip

    def scores(self, query, docs):
        return [float(x) for x in self.model.predict([(query, d) for d in docs], batch_size=TOP)]

    def peak_mb(self):
        return self.torch.mps.driver_allocated_memory() / 2**20


def peak_footprint():
    """Lifetime peak physical footprint (Activity Monitor's Memory), read as Lantern's service/perf.py does."""
    import ctypes
    import os
    import struct

    buf = ctypes.create_string_buffer(512)
    if ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(os.getpid(), 4, buf) != 0:  # RUSAGE_INFO_V4
        return None
    return round(struct.unpack_from("<Q", buf, 240)[0] / 2**20)  # ri_lifetime_max_phys_footprint


def main():
    which, runs, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else None  # a short trial first: memory and speed
    out.mkdir(parents=True, exist_ok=True)
    stop = set(json.loads((runs.parent / "stopwords.json").read_text()))
    t_load = time.perf_counter()
    model = Qwen() if which == "qwen" else Bge()
    load_s = time.perf_counter() - t_load
    timings = []
    for p in sorted(runs.glob("*.json")):
        d = json.loads(p.read_text())
        texts = d["texts"]
        result = []
        for r in d["records"]:
            if r["split"] not in ("test",) or r["form"] == "every word":
                continue
            if limit is not None and len(result) >= limit:
                break
            sentence = any(w in stop for w in re.findall(r"[^\W\d_]+", r["q"].lower()))
            order = shipped_order(r, sentence)
            top = order[:TOP]
            t0 = time.perf_counter()
            sc = model.scores(r["q"], [chunk_for(r, doc, texts)[:DOC_CHARS] for doc in top]) if top else []
            timings.append(time.perf_counter() - t0)
            reranked = [doc for _, doc in sorted(zip(sc, top, strict=True), key=lambda x: -x[0])] + order[TOP:]
            result.append({"qid": r["qid"], "form": r["form"], "order": reranked[:50], "base": order[:50]})
        (out / p.name).write_text(json.dumps(result))
        print(which, p.stem, len(result), flush=True)
    timings.sort()
    summary = {"model": which, "load_s": round(load_s, 1), "queries": len(timings),
               "ms_per_query_median": round(timings[len(timings) // 2] * 1000, 1),
               "ms_per_query_p90": round(timings[int(len(timings) * 0.9)] * 1000, 1),
               "accelerator_peak_mb": round(model.peak_mb()),
               "process_peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20),
               "process_peak_footprint_mb": peak_footprint()}  # fmt: skip
    (out / "summary.txt").write_text(json.dumps(summary))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
