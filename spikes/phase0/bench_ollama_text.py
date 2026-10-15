"""Phase 0 spike: Ollama text throughput on the same batch as bench_st.py."""

import time

import requests

URL = "http://127.0.0.1:11434/api/embed"
chunk = ("Lantern indeksuje pliki lokalnie i pozwala je przeszukiwać po znaczeniu. " * 40)[:2400]
batch = ["title: none | text: " + chunk + str(i) for i in range(64)]
for tag in ["270m-bf16-text", "570m-nvfp4-text-audio"]:
    m = f"embeddinggemma-2:{tag}"
    requests.post(URL, json={"model": m, "input": batch[:4]}, timeout=600).raise_for_status()
    t0 = time.perf_counter()
    for i in range(0, 64, 16):
        requests.post(URL, json={"model": m, "input": batch[i:i + 16]}, timeout=600).raise_for_status()
    print(f"{tag}: {64 / (time.perf_counter() - t0):.1f} chunks/s")
