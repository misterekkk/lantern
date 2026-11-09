"""Load EmbeddingGemma 2 (mlx-vlm model code) without importing torch or transformers.

`mlx_vlm/__init__.py`, `mlx_vlm/models/gemma4/__init__.py`, `mlx_vlm/models/embedding_gemma2/__init__.py` (its
processor, since mlx-vlm 0.7.8) and `mlx_vlm.utils` import the generation stack (transformers, which pulls in torch:
~330 MB of resident memory for a daemon that only embeds text). The model modules themselves only need MLX. We
register empty package stubs pointing at the real directories, so importing the model files skips those `__init__`
files, and replicate the few steps of `mlx_vlm.encoder_loader.load_encoder_model` needed for this checkpoint
(config, sanitize, quantize, load).
Equivalence with the original loader is covered by the reference-vector tests (`pytest -m model`).
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

# Packages whose __init__ pulls in transformers; Gemma code only needs modules inside them.
_STUBS = (
    "mlx_vlm",
    "mlx_vlm.models",
    "mlx_vlm.models.gemma4",
    "mlx_vlm.models.qwen3_5",
    "mlx_vlm.models.embedding_gemma2",
    "mlx_vlm.speculative",  # gemma4/language.py imports a verifier whose package __init__ reaches mlx_vlm.utils
)


def _model_package():
    """The model's classes, as the package's `__init__` exports them, without running that `__init__`."""
    if "mlx_vlm" not in sys.modules:
        spec = importlib.util.find_spec("mlx_vlm")  # locates the package without executing it
        if spec is None or not spec.submodule_search_locations:
            raise ImportError("mlx-vlm is not installed")
        root = Path(next(iter(spec.submodule_search_locations)))
        for name in _STUBS:
            module = types.ModuleType(name)
            module.__path__ = [str(root.joinpath(*name.split(".")[1:]))]
            module.__package__ = name
            sys.modules[name] = module
    from mlx_vlm.models.embedding_gemma2 import config, embedding_gemma2

    return types.SimpleNamespace(
        Model=embedding_gemma2.Model,
        ModelConfig=config.ModelConfig,
        TextConfig=config.TextConfig,
        VisionConfig=config.VisionConfig,
        AudioConfig=config.AudioConfig,
    )


def load_embedding_gemma2(model_path: Path, lazy: bool = True):
    import mlx.core as mx
    import mlx.nn as nn

    package = _model_package()
    config = json.loads((model_path / "config.json").read_text())
    model_config = package.ModelConfig.from_dict(config)
    for name in ("text", "vision", "audio"):
        if config.get(f"{name}_config") is not None:
            setattr(
                model_config,
                f"{name}_config",
                getattr(package, f"{name.title()}Config").from_dict(config[f"{name}_config"]),
            )
    model = package.Model(model_config)

    index = model_path / "model.safetensors.index.json"
    files = (
        sorted({model_path / f for f in json.loads(index.read_text())["weight_map"].values()})
        if index.exists()
        else sorted(model_path.glob("*.safetensors"))
    )
    weights = {}
    for file in files:
        weights.update(mx.load(str(file)))
    if hasattr(model, "sanitize"):
        weights = model.sanitize(weights)

    quantization = config.get("quantization")
    if quantization:

        def quantizable(path: str, module) -> bool:
            if not hasattr(module, "to_quantized"):
                return False
            if hasattr(module, "weight") and module.weight.size % 64 != 0:
                return False
            return f"{path}.scales" in weights

        nn.quantize(
            model,
            group_size=quantization["group_size"],
            bits=quantization["bits"],
            mode=quantization.get("mode", "affine"),
            class_predicate=quantizable,
        )
    model.load_weights(list(weights.items()), strict=True)
    if not lazy:
        mx.eval(model.parameters())
    model.eval()
    return model
