"""Phase 0 spike: sqlite-vec + FTS5 in one SQLite file (uv-managed Python)."""

import sqlite3
import tempfile
import time
from pathlib import Path

import numpy as np
import sqlite_vec

N, DIM = 100_000, 768
db_path = Path(tempfile.mkdtemp()) / "probe.db"
db = sqlite3.connect(db_path)
db.enable_load_extension(True)
sqlite_vec.load(db)
db.enable_load_extension(False)
print("sqlite", sqlite3.sqlite_version, "vec", db.execute("select vec_version()").fetchone()[0])

db.executescript(f"""
create table chunks(id integer primary key, text text);
create virtual table chunks_fts using fts5(text, content='chunks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2');
create virtual table vec_chunks using vec0(chunk_id integer primary key, embedding float[{DIM}] distance_metric=cosine);
""")
rng = np.random.default_rng(0)
vecs = rng.standard_normal((N, DIM)).astype(np.float32)
vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
t0 = time.perf_counter()
with db:
    db.executemany("insert into chunks(id, text) values (?, ?)", ((i, f"dokument numer {i} zażółć gęślą jaźń") for i in range(N)))
    db.execute("insert into chunks_fts(chunks_fts) values ('rebuild')")
    db.executemany("insert into vec_chunks(chunk_id, embedding) values (?, ?)", ((i, vecs[i].tobytes()) for i in range(N)))
print(f"insert {N}: {time.perf_counter() - t0:.1f}s, file {db_path.stat().st_size / 2**20:.0f} MB")

q = vecs[123] + 0.01 * rng.standard_normal(DIM).astype(np.float32)
t0 = time.perf_counter()
rows = db.execute("select chunk_id, distance from vec_chunks where embedding match ? and k = 10", (q.tobytes(),)).fetchall()
print(f"knn k=10 over {N}: {(time.perf_counter() - t0) * 1000:.0f} ms, top1={rows[0][0]}")
rows = db.execute("select rowid from chunks_fts where chunks_fts match ? limit 3", ("zazolc",)).fetchall()
print("fts diacritics-insensitive match:", len(rows) > 0)

with db:
    db.execute("delete from vec_chunks where chunk_id < ?", (N // 2,))
    db.execute("delete from chunks where id < ?", (N // 2,))
    db.execute("insert into chunks_fts(chunks_fts) values ('rebuild')")
before = db_path.stat().st_size
db.execute("vacuum")
print(f"vacuum after deleting half: {before / 2**20:.0f} MB -> {db_path.stat().st_size / 2**20:.0f} MB")
