"""SigLIP 2 base (224 px), the second photo model (DECISIONS.md, Phase 7).

- SiglipImageEmbedder: image tower of the original google/siglip2-base-patch16-224 (fp16, torch on MPS). Used only in
  the index worker, which exits after indexing (torch never stays resident in the daemon).
- SiglipQueryEmbedder: text tower of mlx-community/siglip2-base-patch16-224-8bit on MLX (no torch), loaded by the
  daemon on demand (~0.33 s, ~+340 MB) and released after inactivity. Vectors match the original (cos >= 0.999).
Both report the same ModelInfo, because they produce vectors in the same space.
"""

import gc
import json
from pathlib import Path

import numpy as np

from lantern.config import enforce_offline
from lantern.store.db import ModelInfo

IMAGE_REPO, IMAGE_REVISION = "google/siglip2-base-patch16-224", "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
TEXT_REPO, TEXT_REVISION = "mlx-community/siglip2-base-patch16-224-8bit", "5249fc157310584fe99dae6964707278eb6df50f"
DIM = 768
MAX_TEXT_TOKENS = 64  # SigLIP is trained on fixed-length (64) padded, lowercased text and pools the last position
INFO = ModelInfo(name=IMAGE_REPO, revision=IMAGE_REVISION, backend="siglip", dtype="fp16+mlx8", dim=DIM)


def _snapshot(repo: str, revision: str) -> Path | None:
    enforce_offline()
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return Path(snapshot_download(repo, revision=revision, local_files_only=True))
    except LocalEntryNotFoundError:
        return None


def available() -> bool:
    """Both halves downloaded (photo search then uses the pair; otherwise EmbeddingGemma alone)."""
    return _snapshot(IMAGE_REPO, IMAGE_REVISION) is not None and _snapshot(TEXT_REPO, TEXT_REVISION) is not None


class SiglipImageEmbedder:
    def __init__(self):
        self._model = None
        self._processor = None

    def model_info(self) -> ModelInfo:
        return INFO

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoProcessor

        path = _snapshot(IMAGE_REPO, IMAGE_REVISION)
        if path is None:
            raise RuntimeError(f"{IMAGE_REPO} is not downloaded; see README")
        self._model = AutoModel.from_pretrained(path, dtype=torch.float16).to("mps").eval()
        self._processor = AutoProcessor.from_pretrained(path)

    def embed_images(self, images: list) -> np.ndarray:
        import torch

        self._load()
        out = []
        with torch.no_grad():
            for i in range(0, len(images), 16):
                inputs = self._processor(images=images[i : i + 16], return_tensors="pt").to("mps")
                inputs = {k: (v.half() if v.is_floating_point() else v) for k, v in inputs.items()}
                feats = self._model.get_image_features(**inputs)
                feats = feats if torch.is_tensor(feats) else feats.pooler_output
                out.append(torch.nn.functional.normalize(feats.float(), dim=-1).cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, DIM), dtype=np.float32)

    def unload(self) -> None:
        self._model = self._processor = None
        gc.collect()
        try:
            import torch

            torch.mps.empty_cache()
        except Exception:  # noqa: BLE001 - torch absent or no MPS: nothing to release
            pass


class SiglipQueryEmbedder:
    def __init__(self):
        self._model = None
        self._tok = None

    def model_info(self) -> ModelInfo:
        return INFO

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        import mlx.core as mx
        import mlx.nn as nn
        from tokenizers import Tokenizer

        from lantern.embedding.mlx_loader import _model_package

        path = _snapshot(TEXT_REPO, TEXT_REVISION)
        if path is None:
            raise RuntimeError(f"{TEXT_REPO} is not downloaded; see README")
        _model_package()  # package stubs: mlx-vlm model code without its torch-importing __init__ files
        import mlx_vlm.models.siglip as sp

        config = json.loads((path / "config.json").read_text())
        cfg = sp.ModelConfig.from_dict(config)
        cfg.text_config = sp.TextConfig.from_dict(config["text_config"])
        model = sp.Model(cfg)
        weights = {}
        for f in sorted(path.glob("*.safetensors")):
            weights.update(mx.load(str(f)))
        weights = model.sanitize(weights)  # keeps the text tower only
        q = config["quantization"]
        nn.quantize(model, group_size=q["group_size"], bits=q["bits"],
                    class_predicate=lambda p, m: hasattr(m, "to_quantized") and f"{p}.scales" in weights)  # fmt: skip
        model.load_weights(list(weights.items()), strict=True)
        mx.eval(model.parameters())
        tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        tok.enable_truncation(MAX_TEXT_TOKENS)
        tok.enable_padding(length=MAX_TEXT_TOKENS, pad_id=tok.token_to_id("<pad>"), pad_token="<pad>")
        self._model, self._tok = model, tok

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        import mlx.core as mx

        self.load()
        ids = mx.array([e.ids for e in self._tok.encode_batch([t.lower() for t in texts])])
        emb = self._model(ids).text_embeds
        mx.eval(emb)
        return np.array(emb.astype(mx.float32))

    def unload(self) -> None:
        if self._model is None:
            return
        import mlx.core as mx

        self._model = self._tok = None
        gc.collect()
        mx.clear_cache()
