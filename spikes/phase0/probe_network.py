"""Phase 0 spike: does the embedding stack open network sockets in offline mode?

Wraps socket.connect/getaddrinfo to log every attempt while loading the model
and embedding text + audio with HF_HUB_OFFLINE=1.
"""

import os
import socket

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
attempts = []
_connect, _gai = socket.socket.connect, socket.getaddrinfo


def connect(self, addr):
    attempts.append(("connect", addr))
    return _connect(self, addr)


def getaddrinfo(host, *a, **kw):
    attempts.append(("dns", host))
    return _gai(host, *a, **kw)


socket.socket.connect, socket.getaddrinfo = connect, getaddrinfo

import numpy as np  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

m = SentenceTransformer("google/embeddinggemma-2", revision="914f7f89142e33e77833254d9c9b90c3cef7303b",
                        device="mps", config_kwargs={"vision_config": None}, model_kwargs={"torch_dtype": torch.bfloat16})
m.encode(["test zapytania"], prompt_name="SearchQuery")
m.encode({"audio": {"array": np.zeros(16000 * 5, dtype=np.float32), "sampling_rate": 16000}})
print("network attempts:", attempts or "none")
