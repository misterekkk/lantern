# Phase 3 plan: hybrid search and index hygiene

**Status:** done (2026-10-15); results in DECISIONS.md, section "Phase 3: results".

## Scope

1. **One ranking (RRF):** the vector list and the BM25 (FTS5) list fused with Reciprocal Rank Fusion (k = 60) at file
   level. Each result shows where the match comes from ("meaning #1 · keywords #3").
2. **Better word matching:** stop words skipped (Polish/English: "jak", "się", "the"…), Polish inflection matched by
   stem prefix ("decyzyjnego" → `decyzyjn*`), BM25 column weights (text 1, path 1.5, symbol 3).
3. **`search_events`:** every search stores the query and results with features (positions and scores from both lists,
   RRF). `lantern open N` opens the N-th result of the last search and records which one was chosen (data for a future
   reranker). `lantern clear-history` deletes the history.
4. **Index hygiene:**
   - pipeline version (`pipeline_version`) and model (`model_id`) on every file: a change of chunking rules or model
     → automatic re-index on the next `index`; schema migration v1 → v2,
   - `lantern remove <folder>` removes a folder from the index (also when it no longer exists),
   - `lantern compact` and automatic compaction after large deletions: FTS `optimize` + `VACUUM` + a WAL checkpoint,
     with `secure_delete` and FTS `secure-delete` on, so deleted data really leaves the file.
5. Moves by hash and deleting entries were already done in Phase 2.

## Tests

RRF (a file in both lists wins, an exact identifier found only by the word list is in the results), stop words and
prefixes, storing `search_events` and `open`, migration v1 → v2, re-index after a pipeline version change and after a
model change, `remove`, and **no deleted text in the raw bytes of the database file after `compact`**.
