"""Asks DigUp's search service (encoder warm, as in its app) the same test queries Lantern got (runs/<set>.json, from
collect_runs.py), and saves each query's ranked files with the service's timings and its memory (DECISIONS.md,
"Lantern and DigUp compared"). DigUp is built from source in a scratch folder (scripts/build-llama.sh, then
`swift build -c release --product digup`), its model fetched with `digup download-model --to DIR`, and each set
indexed into its own folder: `digup index <corpus> --index <dir> --include-datasets`.

    python3 digup_query.py <digup binary> <scratch folder> <set> <corpus folder>
"""

import json
import os
import struct
import ctypes
import subprocess
import sys
import time
from pathlib import Path


def footprint(pid):
    buf = ctypes.create_string_buffer(512)
    if ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(pid, 4, buf) != 0:
        return None
    return struct.unpack_from("<Q", buf, 72)[0] / 2**20, struct.unpack_from("<Q", buf, 240)[0] / 2**20


def main():
    binary, sp, name, corpus = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    runs = json.loads((sp / "runs" / f"{name}.json").read_text())
    proc = subprocess.Popen([binary, "search-service", "--index", str(sp / f"dg-{name}")],  # no folder filter: the index holds only this set
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(sp / f"dg-{name}.service.log", "w"),
                            text=True, bufsize=1, env={**os.environ, "DIGUP_MODELS": str(sp / "digup-models")})

    def ask(req):
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("search-service ended: see the service log")
            reply = json.loads(line)
            if reply.get("id") == req["id"]:
                return reply

    ask({"op": "prewarm", "id": 0})
    for _ in range(600):  # the encoder starts in the background
        if ask({"op": "status", "id": 1}).get("encoder") == "ready":
            break
        time.sleep(0.5)
    out, i = [], 2
    for r in runs["records"]:
        if r["split"] != "test":
            continue
        t0 = time.perf_counter()
        reply = ask({"op": "search", "q": r["q"], "limit": 50, "meaning": True, "id": i})
        wall = (time.perf_counter() - t0) * 1000
        i += 1
        files = []
        for h in reply.get("hits", []):
            stem = Path(h["path"]).stem if runs["mode"] == "beir" else h["path"]
            if stem not in files:
                files.append(stem)
        out.append({"qid": r["qid"], "form": r["form"], "order": files, "wall_ms": round(wall, 1),
                    "ms": reply.get("ms"), "meaning": reply.get("meaning")})
    kids = subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True, text=True).stdout.split()
    parts = [footprint(int(p)) for p in [proc.pid, *kids]]
    mem = [round(sum(x[0] for x in parts if x)), round(sum(x[1] for x in parts if x)), len(parts)]  # now, peak, processes
    rss = subprocess.run(["ps", "-o", "rss=", "-p", ",".join([str(proc.pid), *kids])], capture_output=True, text=True).stdout
    mem.append(round(sum(int(x) for x in rss.split()) / 1024))  # resident MB, with the model's mapped weights
    ask({"op": "quit", "id": i})
    proc.wait(timeout=30)
    (sp / f"dg-{name}.results.json").write_text(json.dumps({"results": out, "footprint_mb": mem}))
    walls = sorted(x["wall_ms"] for x in out)
    print(name, len(out), "median ms", walls[len(walls) // 2], "footprint MB (now, peak)", mem)


if __name__ == "__main__":
    main()
