"""SigLIP 2 image tower on MLX, from the original google/siglip2-base-patch16-224 weights in float16 (as the torch
version it replaces), so photo search needs no torch (docs/DECISIONS.md, "Phase 14"). mlx-vlm has only SigLIP's text
tower; this is the vision transformer of transformers' SiglipVisionModel: 16 px patches + position embeddings,
12 pre-norm encoder layers, a final layer norm and the multihead attention pooling head, whose output is the image
embedding. Preprocessing as SiglipImageProcessor: RGB, 224×224 bilinear, (x / 255 - 0.5) / 0.5.
"""

import json
from pathlib import Path

import numpy as np

SIZE = 224


def _modules():
    import mlx.core as mx
    import mlx.nn as nn

    class Attention(nn.Module):
        def __init__(self, dim: int, heads: int):
            super().__init__()
            self.heads = heads
            self.q_proj = nn.Linear(dim, dim)
            self.k_proj = nn.Linear(dim, dim)
            self.v_proj = nn.Linear(dim, dim)
            self.out_proj = nn.Linear(dim, dim)

        def __call__(self, x, kv=None):
            kv = x if kv is None else kv
            b, n, d = x.shape
            m = kv.shape[1]
            h = self.heads

            def split(t, length):
                return t.reshape(b, length, h, d // h).transpose(0, 2, 1, 3)

            q, k, v = split(self.q_proj(x), n), split(self.k_proj(kv), m), split(self.v_proj(kv), m)
            out = mx.fast.scaled_dot_product_attention(q, k, v, scale=(d // h) ** -0.5)
            return self.out_proj(out.transpose(0, 2, 1, 3).reshape(b, n, d))

    class MLP(nn.Module):
        def __init__(self, dim: int, hidden: int):
            super().__init__()
            self.fc1 = nn.Linear(dim, hidden)
            self.fc2 = nn.Linear(hidden, dim)

        def __call__(self, x):
            return self.fc2(nn.gelu_approx(self.fc1(x)))  # gelu_pytorch_tanh

    class Layer(nn.Module):
        def __init__(self, dim: int, heads: int, hidden: int, eps: float):
            super().__init__()
            self.layer_norm1 = nn.LayerNorm(dim, eps=eps)
            self.self_attn = Attention(dim, heads)
            self.layer_norm2 = nn.LayerNorm(dim, eps=eps)
            self.mlp = MLP(dim, hidden)

        def __call__(self, x):
            x = x + self.self_attn(self.layer_norm1(x))
            return x + self.mlp(self.layer_norm2(x))

    class Head(nn.Module):
        def __init__(self, dim: int, heads: int, hidden: int, eps: float):
            super().__init__()
            self.probe = mx.zeros((1, 1, dim))
            self.attention = Attention(dim, heads)
            self.layernorm = nn.LayerNorm(dim, eps=eps)
            self.mlp = MLP(dim, hidden)

        def __call__(self, x):
            h = self.attention(mx.broadcast_to(self.probe, (x.shape[0], 1, x.shape[2])), x)
            h = h + self.mlp(self.layernorm(h))
            return h[:, 0]

    class Vision(nn.Module):
        def __init__(self, c: dict):
            super().__init__()
            dim, heads, hidden = c["hidden_size"], c["num_attention_heads"], c["intermediate_size"]
            eps, patch = c.get("layer_norm_eps", 1e-6), c["patch_size"]
            self.patch_embedding = nn.Conv2d(3, dim, kernel_size=patch, stride=patch)
            self.position_embedding = nn.Embedding((c["image_size"] // patch) ** 2, dim)
            self.layers = [Layer(dim, heads, hidden, eps) for _ in range(c["num_hidden_layers"])]
            self.post_layernorm = nn.LayerNorm(dim, eps=eps)
            self.head = Head(dim, heads, hidden, eps)

        def __call__(self, pixels):  # (batch, 224, 224, 3)
            x = self.patch_embedding(pixels)
            x = x.reshape(x.shape[0], -1, x.shape[-1])
            x = x + self.position_embedding.weight[None]
            for layer in self.layers:
                x = layer(x)
            return self.head(self.post_layernorm(x))

    return Vision


VISION_DEFAULTS = {"hidden_size": 768, "num_attention_heads": 12, "intermediate_size": 3072, "num_hidden_layers": 12,
                   "patch_size": 16, "image_size": 224, "layer_norm_eps": 1e-6}  # fmt: skip


def _convert(weights: dict) -> dict:
    """transformers' names and layouts → this module's: the vision tower only, torch's fused attention-pooling
    projection split into q/k/v, convolution kernels channels-last."""
    out = {}
    for name, w in weights.items():
        if not name.startswith("vision_model."):
            continue
        name = name.removeprefix("vision_model.")
        name = name.removeprefix("embeddings.").replace("encoder.layers.", "layers.")
        if name == "patch_embedding.weight":
            w = w.transpose(0, 2, 3, 1)
        if name == "head.attention.in_proj_weight":
            q, k, v = np.split(w, 3, axis=0)
            out.update({"head.attention.q_proj.weight": q, "head.attention.k_proj.weight": k,
                        "head.attention.v_proj.weight": v})  # fmt: skip
            continue
        if name == "head.attention.in_proj_bias":
            q, k, v = np.split(w, 3, axis=0)
            out.update({"head.attention.q_proj.bias": q, "head.attention.k_proj.bias": k,
                        "head.attention.v_proj.bias": v})  # fmt: skip
            continue
        out[name] = w
    return out


def load(snapshot: Path, dtype: str = "float16"):
    """The image tower from a downloaded google/siglip2-base-patch16-224 snapshot."""
    import mlx.core as mx
    from safetensors.numpy import load_file

    config = json.loads((snapshot / "config.json").read_text())
    model = _modules()({**VISION_DEFAULTS, **(config.get("vision_config") or {})})
    weights = {}
    for f in sorted(snapshot.glob("*.safetensors")):
        weights.update(load_file(str(f)))
    target = getattr(mx, dtype)
    model.load_weights([(k, mx.array(v).astype(target)) for k, v in _convert(weights).items()], strict=True)
    mx.eval(model.parameters())
    return model


def preprocess(images: list) -> np.ndarray:
    """PIL images → (batch, 224, 224, 3) float32 in [-1, 1], as SiglipImageProcessor (bilinear resize, mean/std 0.5)."""
    from PIL import Image

    out = np.empty((len(images), SIZE, SIZE, 3), dtype=np.float32)
    for i, image in enumerate(images):
        resized = image.convert("RGB").resize((SIZE, SIZE), Image.BILINEAR)
        out[i] = (np.asarray(resized, dtype=np.float32) / 255.0 - 0.5) / 0.5
    return out
