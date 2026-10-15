"""Phase 7: what would the daemon hold to answer photo queries with both models? Measures, in one process like the
daemon: EmbeddingGemma 2 text tower (MLX) + SigLIP 2 text encoder (torch MPS, fp16): footprint after each load,
cold load time, warm query latency of each and of both."""

import ctypes
import os
import struct
import time

os.environ["HF_HUB_OFFLINE"] = "1"


def fp() -> float:
    buf = ctypes.create_string_buffer(4096)
    ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(os.getpid(), 4, buf)
    return struct.unpack_from("<Q", buf, 72)[0] / 2**20


def timed(fn, n=20):
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1000


print(f"start: {fp():.0f} MB")
t0 = time.perf_counter()
from lantern.embedding.mlx_backend import MLXEmbedder  # noqa: E402

eg = MLXEmbedder()
eg.load()
print(f"+ EmbeddingGemma text tower: {fp():.0f} MB (load {time.perf_counter() - t0:.2f}s)")
eg_ms = timed(lambda: eg.embed_text(["task: search result | query: pies na zdjęciu"]))

t0 = time.perf_counter()
import torch  # noqa: E402
from transformers import AutoModel, AutoTokenizer  # noqa: E402

print(f"+ import torch/transformers: {fp():.0f} MB ({time.perf_counter() - t0:.2f}s)")
t0 = time.perf_counter()
repo, rev = "google/siglip2-base-patch16-256", "3f9f96cb90da5dbc758b01813f2f6f1aee24c1ab"
full = AutoModel.from_pretrained(repo, revision=rev, torch_dtype=torch.float16)
text = full.text_model.to("mps").eval()  # the daemon only needs the text encoder for queries
del full
tok = AutoTokenizer.from_pretrained(repo, revision=rev)
import gc  # noqa: E402

gc.collect()
print(f"+ SigLIP 2 base text encoder fp16: {fp():.0f} MB (load {time.perf_counter() - t0:.2f}s)")


def siglip_query():
    with torch.no_grad():
        inp = tok(["pies"], padding="max_length", max_length=64, return_tensors="pt").to("mps")
        out = text(**inp).pooler_output
        torch.mps.synchronize()
        return out


sig_ms = timed(siglip_query)
both_ms = timed(lambda: (siglip_query(), eg.embed_text(["task: search result | query: pies na zdjęciu"])))
print(f"query latency (warm): EmbeddingGemma {eg_ms:.1f} ms, SigLIP {sig_ms:.1f} ms, both sequentially {both_ms:.1f} ms")
print(f"steady footprint with both: {fp():.0f} MB")
