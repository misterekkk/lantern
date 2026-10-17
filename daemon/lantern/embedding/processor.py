"""EmbeddingGemma 2's audio and image preprocessing without torch (docs/DECISIONS.md, "Phase 14").

`AutoProcessor` picks transformers' torch image processor and refuses to build the processor without a video
processor, so the downloadable app would need torch (~550 MB) only to prepare inputs. The same processor class is
built here from its numpy/PIL parts: `Gemma4AudioFeatureExtractor` (numpy) and `Gemma4ImageProcessorPil` (PIL), no
video processor (Lantern never sends video to the model). Measured identical to AutoProcessor's output: audio
features and token ids equal, pixel values within 6e-8.
"""

import inspect
import json
import sys
from pathlib import Path


def load_processor(snapshot: Path):
    """The EmbeddingGemma 2 processor of a downloaded snapshot, never importing torch. Parts and settings come from
    the snapshot's processor_config.json, as with AutoProcessor."""
    import transformers.models.gemma4.image_processing_pil_gemma4 as pil

    # The processor module imports helpers from the torch image processor module; the PIL module defines the same
    # names. Only used when that module has not been imported already (it then works with torch present anyway).
    sys.modules.setdefault("transformers.models.gemma4.image_processing_gemma4", pil)
    from transformers import AutoTokenizer
    from transformers.models.embedding_gemma2.processing_embedding_gemma2 import EmbeddingGemma2Processor
    from transformers.models.gemma4.feature_extraction_gemma4 import Gemma4AudioFeatureExtractor

    class _Processor(EmbeddingGemma2Processor):
        def check_argument_for_proper_class(self, argument_name, argument):  # no video processor is fine
            if argument is None:
                return None
            return super().check_argument_for_proper_class(argument_name, argument)

    config = json.loads((snapshot / "processor_config.json").read_text())
    params = inspect.signature(EmbeddingGemma2Processor.__init__).parameters
    settings = {k: v for k, v in config.items() if k in params and not isinstance(v, dict)}  # e.g. audio_seq_length

    return _Processor(
        feature_extractor=Gemma4AudioFeatureExtractor.from_dict(config["feature_extractor"]),
        image_processor=pil.Gemma4ImageProcessorPil.from_dict(config["image_processor"]),
        tokenizer=AutoTokenizer.from_pretrained(snapshot),
        video_processor=None,
        **settings,
    )
