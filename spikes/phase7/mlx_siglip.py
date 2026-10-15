"""Phase 7: SigLIP 2 text encoder on MLX 8-bit (mlx-community/siglip2-base-patch16-224-8bit) for query embeddings
in the daemon, without torch. Image embeddings come from the original google/siglip2-base-patch16-224 (worker).

  parity   MLX 8-bit text vectors vs torch fp32 text vectors of the original model
  vectors  writes data/bench/siglip2-base-224-mlx8.npz: torch image vectors + MLX query vectors (for fusion_eval)
  cost     daemon-like process: EmbeddingGemma 2 text tower + MLX SigLIP text: footprint, load time, latency
"""

import ctypes
import json
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np

os.environ["HF_HUB_OFFLINE"] = "1"
HERE = Path(__file__).parent
BENCH = HERE / "data" / "bench"
MLX_REPO, MLX_REV = "mlx-community/siglip2-base-patch16-224-8bit", "5249fc157310584fe99dae6964707278eb6df50f"
PT_REPO, PT_REV = "google/siglip2-base-patch16-224", "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
MAX_LEN = 64  # SigLIP is trained on fixed-length (64) padded text and pools the last position


def fp() -> float:
    buf = ctypes.create_string_buffer(4096)
    ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(os.getpid(), 4, buf)
    return struct.unpack_from("<Q", buf, 72)[0] / 2**20


class MLXSiglipText:
    def __init__(self):
        import mlx.core as mx
        import mlx.nn as nn
        from huggingface_hub import snapshot_download
        from tokenizers import Tokenizer

        from lantern.embedding.mlx_loader import _model_package

        _model_package()  # package stubs: import mlx-vlm model code without its torch-importing __init__ files
        import mlx_vlm.models.siglip as sp

        path = Path(snapshot_download(MLX_REPO, revision=MLX_REV, local_files_only=True))
        config = json.loads((path / "config.json").read_text())
        cfg = sp.ModelConfig.from_dict(config)
        cfg.text_config = sp.TextConfig.from_dict(config["text_config"])
        model = sp.Model(cfg)
        weights = {}
        for f in sorted(path.glob("*.safetensors")):
            weights.update(mx.load(str(f)))
        weights = model.sanitize(weights)
        q = config["quantization"]
        nn.quantize(model, group_size=q["group_size"], bits=q["bits"],
                    class_predicate=lambda p, m: hasattr(m, "to_quantized") and f"{p}.scales" in weights)  # fmt: skip
        model.load_weights(list(weights.items()), strict=True)
        mx.eval(model.parameters())
        self.model, self.mx = model, mx
        tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        tok.enable_truncation(MAX_LEN)
        tok.enable_padding(length=MAX_LEN, pad_id=tok.token_to_id("<pad>"), pad_token="<pad>")
        self.tok = tok

    def embed(self, texts: list[str]) -> np.ndarray:
        out = []
        for i in range(0, len(texts), 64):
            ids = self.mx.array([e.ids for e in self.tok.encode_batch([t.lower() for t in texts[i : i + 64]])])
            emb = self.model(ids).text_embeds
            self.mx.eval(emb)
            out.append(np.array(emb.astype(self.mx.float32)))
        return np.concatenate(out)


def torch_text(texts: list[str]) -> np.ndarray:
    import torch
    from transformers import AutoModel, AutoProcessor

    model = AutoModel.from_pretrained(PT_REPO, revision=PT_REV, dtype=torch.float32).eval()
    proc = AutoProcessor.from_pretrained(PT_REPO, revision=PT_REV)
    with torch.no_grad():
        inp = proc(text=[t.lower() for t in texts], padding="max_length", max_length=MAX_LEN, truncation=True, return_tensors="pt")
        out = model.get_text_features(**inp)
        out = out if torch.is_tensor(out) else out.pooler_output
        return torch.nn.functional.normalize(out, dim=-1).numpy()


def parity() -> None:
    sys.path.insert(0, str(HERE))
    import image_eval as ie

    texts = [c[0] for c in ie.CONCEPTS] + [c[1] for c in ie.CONCEPTS] + list(ie.PL_CAPTIONS.values())[:10]
    a, b = MLXSiglipText().embed(texts), torch_text(texts)
    cos = (a * b).sum(1)
    print(f"MLX 8-bit vs torch fp32 text cos: min {cos.min():.4f}, mean {cos.mean():.4f}")


def vectors() -> None:
    base = np.load(BENCH / "siglip2-base-patch16-224-fp16.npz")
    enc = MLXSiglipText()
    sys.path.insert(0, str(HERE))
    import image_bench as ib
    import image_eval as ie

    q = {f"q_{k}": enc.embed(v) for k, v in ib.queries(ie.rows()).items()}
    np.savez(BENCH / "siglip2-base-224-mlx8.npz", img=base["img"], **q)
    print("written siglip2-base-224-mlx8.npz")


def cost() -> None:
    print(f"start: {fp():.0f} MB")
    from lantern.embedding.mlx_backend import MLXEmbedder

    t0 = time.perf_counter()
    eg = MLXEmbedder()
    eg.load()
    print(f"+ EmbeddingGemma 2 text tower: {fp():.0f} MB (load {time.perf_counter() - t0:.2f}s)")
    t0 = time.perf_counter()
    sig = MLXSiglipText()
    print(f"+ SigLIP 2 text encoder (MLX 8-bit): {fp():.0f} MB (load {time.perf_counter() - t0:.2f}s)")
    print("torch imported:", "torch" in sys.modules)

    def timed(fn, n=30):
        fn()
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t0) / n * 1000

    a = timed(lambda: eg.embed_text(["task: search result | query: pies na zdjęciu"]))
    b = timed(lambda: sig.embed(["pies"]))
    print(f"query latency: EmbeddingGemma {a:.1f} ms, SigLIP MLX {b:.1f} ms, both {a + b:.1f} ms; steady {fp():.0f} MB")


if __name__ == "__main__":
    {"parity": parity, "vectors": vectors, "cost": cost}[sys.argv[1]]()
