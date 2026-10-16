# Decisions and open questions

Format: ADR (Architecture Decision Record). Each decision has a status, context, decision and consequences.
Phase results with measurements come first, the ADRs follow, and the open questions are at the end of the file.

---

## Phase 0: survey results (2026-10-15)

Measurement setup: MacBook Pro, Apple M4, 16 GB RAM, macOS 26.6.2.
Python 3.12.15 (uv), torch 2.14.1, transformers 5.19.0, sentence-transformers 6.1.0,
Ollama 0.40.4 (Homebrew), llama.cpp 0.6.0 build 11429 (Homebrew), sqlite-vec 0.1.9, SQLite 3.53.1.
Model: `google/embeddinggemma-2`, revision `914f7f89142e33e77833254d9c9b90c3cef7303b` (Apache 2.0, not gated).

The measurement scripts are in `spikes/phase0/`; raw data and results in `spikes/phase0/data/` (not in git).

### Table: backend × modality

| Backend | Modality | Works? | Memory | Speed | Notes |
|---|---|---|---|---|---|
| sentence-transformers, MPS, bf16, 270M | text | yes | RSS ~1.2 GB, MPS up to ~3.4 GB* | 7.8 chunks/s (~560 tok.) | 5/5 in the sanity test, no NaN |
| sentence-transformers, MPS, fp32, 270M | text | yes | RSS ~2.2 GB, MPS up to ~4.5 GB* | 6.3 chunks/s | slower and more memory than bf16 |
| sentence-transformers, CPU, fp32, 270M | text | yes | RSS ~3.1 GB | 5.3 chunks/s | possible fallback |
| sentence-transformers, CPU, bf16 | text | yes | RSS ~3.0 GB | 0.3 chunks/s | **do not use** |
| sentence-transformers, MPS, bf16, 570M | text + audio | **yes** | RSS ~1.2 GB, MPS ~4.3 GB* (30 s windows), ~8 GB* (300 s window) | 300 s of audio in 8.2 s (~36× realtime) | **truncates audio at 30 s by default**, see ADR-003 |
| Ollama, `270m-bf16-text` | text | yes | runner ~570–720 MB | 12.4 chunks/s | vectors identical to ST (cos = 1.000) |
| Ollama, `570m-nvfp4-text-audio` | text | yes | runner ~1.1 GB | 11.0 chunks/s | cos vs ST ≈ 0.985 (quantisation) |
| Ollama, `570m-bf16-text-audio` | audio | **partly** | runner ~1.25 GB | 120 s → 15 s, 200 s → 33 s | `{"input":[{"audio":"<base64 wav>"}]}`, matches ST (cos ≥ 0.994); **from ~230 s the runner dies (EOF)** |
| llama.cpp, `ggml-org/embeddinggemma-2-GGUF` | text / audio | **no (this version)** | – | – | the GGUF and mmproj (with the audio tower) exist, but brew build 11429: `unknown model architecture: 'gemma-embedding2'` |
| **MLX, `mlx-community/embeddinggemma-2-8bit`** | text + audio | **yes** | Metal: weights ~1.18 GB, peak ~3.3 GB | text 13.2 chunks/s; audio 30 s → 0.37 s, 300 s → 6.1 s | **cos vs ST fp32: text 0.9998, audio 0.9998** |
| MLX, `…-8bit`, text tower only | text | yes | Metal: weights **~275 MB**, peak ~1.07 GB (batch 16 × 560 tok.) | 13.2 chunks/s | see "RAM" below |
| MLX, `…-4bit` | text + audio | yes | Metal: weights ~1.05 GB | as 8-bit | **drift**: text cos ~0.98, audio 0.957–0.970 |
| MLX, `…-bf16` | text + audio | yes | Metal: weights ~1.42 GB | 13.9 chunks/s | cos ≈ 1.000 |

MLX runs through `mlx-vlm` from an **unreleased branch** (commit `3d87e88402f307efbf68e568971aa887ee7d9ed0`, branch
`pc/embeddinggemma-2`), MLX 0.32.4. The `mlx-community` conversion is a community one (unofficial, not from Google),
but the 8-bit vectors are numerically consistent with the original. Preprocessing (tokenizer, audio features) uses the
transformers processor from the official repo, exactly as in ST. Tested in a separate venv (`spikes/phase0/.venv-mlx`).

### RAM: how much the model really takes

The figures on the internet (~191 MB text, ~567 MB full) are for phone quantisations. On this Mac (MLX 8-bit):
- **text only, idle:** weights ~275 MB + Python/MLX/tokenizer ~100–150 MB, i.e. **~400–450 MB**, provided only the text
  weights are loaded and torch is not imported (importing transformers+torch adds ~330 MB RSS);
- **peak while indexing text:** ~1.1 GB (depends on the batch size, can be reduced);
- **with the audio tower:** weights ~1.2 GB (the 300M audio tower stays bf16 even in quantised versions), peak ~3.3 GB;
- loading takes 0.1–0.5 s, so the audio tower can be loaded only for the duration of audio jobs from the queue.

### Test: a single word in a recording ("krowa", cow)

3 extra Polish recordings of ~30 s in which the word appears once, in passing, in an unrelated context
(traffic jam → "some cow was standing in the middle of the road", residents' meeting → "he stole her bike", client
meeting → "I left my umbrella"). Search among 16 recordings, MLX 8-bit. Rank of the right recording:

| Query | audio, 30 s windows | transcript → vector | transcript → exact BM25 | transcript → prefix BM25 |
|---|---|---|---|---|
| krowa (cow) | 1 | 1 | 1 | 1 |
| krowy na drodze (cows on the road) | 1 | 1 | none | 1 |
| kradzież roweru (bike theft) | 1 | 1 | none | 1 |
| rower (bike) | 2 | 1 | 1 | 1 |
| parasol (umbrella) | 1 | 2 | 1 | 1 |
| zapomniany parasol i deszcz (forgotten umbrella and rain) | 2 | 1 | 1 | 1 |

- **H4:** To find a single spoken word, transcript + FTS with prefix matching is the most reliable (Polish inflection:
  "krowy" vs "krowa"). Exact BM25 fails on inflection. Audio embeddings alone often hit too, but 16 recordings is
  little; among thousands, a single word in a 30 s window will probably drown.
- For the architecture: **audio needs both tracks**, audio embeddings (meaning, non-verbal sounds) and transcripts
  (words, previews, BM25), fused with RRF.

\* `torch.mps.driver_allocated_memory()` includes the MPS allocator cache, i.e. the real peak use of unified memory.
It can be limited with `torch.mps.empty_cache()` and `PYTORCH_MPS_HIGH_WATERMARK_RATIO`.

Transcription (for comparison): mlx-whisper `large-v3-turbo`, ~14× realtime (718 s of audio in 51 s including
loading), peak RSS ~1.8 GB.

### Findings that were not in the documentation

1. **Audio is truncated at 30 s.** The feature extractor (`Gemma4AudioFeatureExtractor`) defaults to
   `max_length=480000` samples (30 s at 16 kHz) and `truncation=True`. Everything after 30 s is silently ignored:
   embeddings of recordings that differ only after 30 s have cos = 1.00. The model card's ~327 s needs
   `model[0].processing_kwargs = {"audio": {"max_length": 320 * 16000, "truncation": True}}`; after that the ending
   really affects the vector.
2. **Pillow and torchvision are required** even with the vision tower off (`EmbeddingGemma2Processor` imports them).
3. **Never use float16** (model card recommendation). bf16 on MPS works without NaN.
4. **FTS5 `remove_diacritics 2` does not turn "ł" into "l".** "lodz" does not find "łódź", but "gesla" finds "gęślą".
   Polish text must be normalised (ł→l) both when indexing and in queries.
5. **The python.org Python 3.13 has no `enable_load_extension`**, so sqlite-vec cannot run on it. The Python 3.12
   installed by uv works.
6. **Ollama connects to ollama.com** in the background (log: `model recommendations cache`). Documented in PRIVACY.md;
   Ollama was rejected.
7. The HF model ships prompts: `SearchQuery`, `Document` (`title: none | text: `), `CodeRetrieval` and others.

### Quality test: audio embeddings vs transcripts (hypotheses, not facts)

Corpus: 13 synthetic recordings (`say`: Zosia PL, Daniel EN). 8 Polish and 4 English of 36–56 s, plus one Polish
recording of 159 s with three topics (from 0 s, 59 s and 108 s). 22 paraphrasing queries, including 2 with an exact
term, 4 cross-language and 3 aimed at segments of the long recording. Ranking per recording (best chunk).

| Method | hit@1 | MRR |
|---|---|---|
| audio, one embedding per recording (320 s limit) | 0.91 | 0.955 |
| audio, default 30 s limit | 0.91 | 0.939 |
| audio, 30 s windows / 25 s hop | 0.91 | 0.947 |
| Whisper transcript → text embedding | **1.00** | **1.000** |
| transcript → BM25 (FTS5) | 0.73 | 0.773 |
| RRF(transcript vector, BM25) | 0.95 | 0.977 |
| RRF(audio windows, transcript vector, BM25) | 0.95 | 0.977 |
| original script text → embedding (upper bound) | 1.00 | 1.000 |

Locating a segment in the long recording: audio windows 3/3, transcript chunks 3/3.

Conclusions (hypotheses):
- **H1:** Audio embeddings work surprisingly well in Polish and across languages (MRR ~0.95), but transcripts with text
  embeddings are better (1.00) and give text for BM25, result previews and quoting.
- **H2:** The default 30 s limit breaks search for later passages: the topic starting at 108 s fell to 6th place.
  Overlapping windows (30 s) fix it and give timestamps.
- **H3:** BM25 on transcripts is weak across languages (no hits) and for "ORA-01555" (Whisper wrote "o 01555"). RRF
  with BM25 slightly lowered the score versus the vector alone on this corpus, although on code it will be the opposite.
- **Caveats:** synthetic speech is much easier than real speech (noise, accents, several speakers), the sample is
  small and there is a ceiling effect. To be repeated on real recordings.

### Library privacy (measured)

- Loading the model and embedding text and audio through sentence-transformers with
  `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1`:
  **0 connection attempts** (intercepted `socket.connect` and `getaddrinfo`, `spikes/phase0/probe_network.py`).
- Without offline mode huggingface_hub checks the model version online when loading (not measured; known behaviour).
- Ollama: periodically fetches model recommendations from ollama.com (visible in the server log).
- mlx-whisper: the model comes from the HF Hub; after the download it works with `HF_HUB_OFFLINE=1` (confirmed in
  Phase 4 by a test).

---

## Phase 2: results (2026-10-15)

Test: `lantern index ~/Studia/Projekt` (22 projects, 653 text and code files), MLX 8-bit, text tower only.

| Measurement | Result |
|---|---|
| MLX vectors vs the ST fp32 reference | cos ≥ 0.9998 (`-m model` test) |
| full indexing | 283 s, 645 files, 4920 chunks, 8 skipped (> 2 MB or binary), 0 errors |
| repeated `index` without changes | 0.1 s |
| peak memory while indexing | 2.04 GB (phys_footprint, `/usr/bin/time -l`) |
| idle daemon (text model loaded) | ~790 MB, of which ~337 MB is the torch import by `mlx-vlm` |
| query time (query embedding + KNN) | 25–29 ms; FTS alone ≤ 1 ms |
| database size | 28 MB |
| network traffic while loading and embedding | 0 connection attempts (`-m model` test) |

Findings:
1. **Without an MLX memory limit indexing grew to 13.4 GB** (MLX kept GPU working memory). `mx.set_memory_limit(3 GB)`
   and `mx.set_cache_limit(256 MB)` brought the peak down to ~2 GB without losing speed. The limits are set when the
   model loads.
2. **Data files dominated the cost** (an ML model dump, large CSVs, lockfiles: ~1.45 characters/token, 940 chunks cut
   at the limit). Decision: lockfiles excluded by default; CSV/TSV and large number-heavy files indexed by their head
   only (header and first rows). Indexing time fell from 498 s to 283 s.
3. **Batching chunks across files did not speed things up** (the GPU was already saturated), but it stayed because it
   simplifies small files.
4. Lines longer than a window (minified code, notebook JSON) are cut. Before, one chunk had 25,000 characters.
5. The word list (OR of all tokens) catches stop words ("jak", "się"). Fixed in Phase 3.

---

## Phase 3: results (2026-10-15)

| Measurement | Result |
|---|---|
| hybrid query time (embedding + KNN + FTS + RRF + history write) | 29–39 ms |
| migration of an existing database v1 → v2 | automatic; 631 files re-indexed after the pipeline version change (287 s) |
| peak memory during re-indexing | 2.04 GB |
| folder removal + `compact` | the removed text is absent from the raw bytes of the database file (automated test) |

Decisions:
1. **RRF at file level** (k = 60, 50 candidates from each list). A file found by both lists wins, and an exact
   identifier found only by the word list still makes the results. Each result shows its source
   ("meaning #1 · keywords #3").
2. **Polish/English stop words + stem prefix** instead of a stemmer: words of ≥ 7 letters lose the last 3 characters,
   5–6 letters lose 2, minimum 4 characters ("decyzyjnego" → `decyzyjn*`). Identifiers and numbers match exactly.
   BM25 column weights: text 1, path 1.5, symbol 3.
3. **`search_events`** stores the query and, for every result, the positions and scores from both lists and RRF,
   without file contents. `lantern open N` records the chosen result. Data for a reranker (Treelite/LightGBM still out
   of scope).
4. **`pipeline_version` and `model_id` per file** (schema v2): a change of chunking rules or model triggers an
   automatic re-index. Closes open question 12.
5. **Erasing data:** `secure_delete`, FTS5 `secure-delete`, and on `compact` FTS `optimize` + `VACUUM` + a WAL
   checkpoint. Automatic compaction after `index` when free pages exceed 25% of the file.

Observations:
- Short chunks (2–3 lines of code with a comment) can beat longer descriptions on vectors. A candidate for the reranker
  or for merging very short chunks with their neighbours (to watch).

---

## Phase 4: results (2026-10-15)

Test: 16 files (WAV, MP3, M4A and an MP4 video with a 2:38 Polish recording on three topics), ~13 minutes of synthetic
speech (Polish and English), real models: EmbeddingGemma 2 audio (MLX 8-bit) and Whisper large-v3-turbo (MLX).

| Measurement | Result |
|---|---|
| MLX audio embeddings vs the ST fp32 reference | cos ≥ 0.999 (`-m model` test) |
| Whisper in Polish, offline | correct transcript, 0 connection attempts (`-m model` test) |
| indexing 16 files (audio windows + transcripts) | 74–76 s, 0 errors, a video without sound skipped |
| peak memory while indexing media | 3.9 GB (audio models loaded one after the other, never together) |
| queries | "krowa", "kradzież roweru", "wymiana klocków hamulcowych", "how do trees cool cities" → the right file first; "sadzenie pomidorów w szklarni" → the video @ 1:48 (the topic starts at 1:48) |

Decisions:
1. **Two tracks for audio and video** (ADR-003): 30 s audio windows with a 25 s hop and an explicit processor limit,
   and Whisper transcripts in ~20 s chunks. Both go to the same vector table (same model, same space), and transcripts
   also to FTS. The ranking fuses them at file level.
2. **Two queue stages** (`index`, then `transcribe`): the EmbeddingGemma audio tower (~0.9 GB) and Whisper (~1.8 GB)
   are never loaded at the same time. Each loads once for its whole stage and is released afterwards.
3. **Video = its sound track** (ffmpeg `-map 0:a:0 -vn`). Frames came in Phase 7.
4. **Streaming decoding** of windows (memory ~1 window regardless of length). Whisper gets the whole file up to 4 h.
5. **Sampled hash for files > 64 MB** (size + 3 × 4 MB): scanning multi-gigabyte videos does not read whole files.
6. **The audio processor from the `mlx-community` repo** gives embeddings matching the reference, so the official
   Google repo is not needed (simpler installation).
7. **Previews of audio-window hits** show the transcript of the same time span.
8. **Pipeline versions per file kind** (`PIPELINE_VERSIONS`): changing the audio rules does not force a text re-index.

### Test on noisy recordings (`spikes/phase4/noise_eval.py`)

The same corpus (16 recordings, 28 queries) degraded with `audiomentations` and ffmpeg, indexed by the real pipeline.
hit@1 / MRR:

| Condition | Lantern ranking | Audio windows only | Transcript only | Words only | Whisper WER |
|---|---|---|---|---|---|
| clean | 1.00 / 1.000 | 0.89 / 0.946 | 1.00 / 1.000 | 0.89 / 0.893 | 4% |
| pink noise 10 dB | 0.96 / 0.982 | 0.75 / 0.849 | 0.96 / 0.982 | 0.89 / 0.893 | 5% |
| pink noise 0 dB | 1.00 / 1.000 | 0.71 / 0.777 | 1.00 / 1.000 | 0.86 / 0.875 | 14% |
| background talk 5 dB | 1.00 / 1.000 | 0.75 / 0.831 | 1.00 / 1.000 | 0.89 / 0.893 | 7% |
| phone (300–3400 Hz, 15 dB noise, MP3 32 kb/s) | 1.00 / 1.000 | 0.79 / 0.826 | 1.00 / 1.000 | 0.89 / 0.893 | 8% |

Conclusions (hypotheses; synthetic speech + artificial noise):
- **Whisper is very robust** (WER 4% → 14% with noise as loud as the speech). The transcript track carries audio search.
- **Sound-only embeddings weaken with noise** (MRR 0.95 → 0.78), but they are still needed without transcripts
  (`--no-transcribe`) and for non-verbal content.
- **A fusion fix came out of this test.** Before it, the Lantern ranking scored 0.93 / 0.958 in every condition, i.e.
  worse than transcripts alone. Cause: with a query in another language than the recording, the word list caught a
  different recording through one incidental shared word ("ogrodzie", "rate"), and RRF rewards presence in both lists.
  Now a keyword hit counts in RRF in proportion to the share of query words it covers, and not at all below 50%
  (`MIN_KEYWORD_COVERAGE`). Single-word queries and identifiers have full coverage, so exact hits lose nothing. Code
  and document search on `~/Studia/Projekt` did not get worse.
- Real recordings (reverb, several speakers at once, accents) remain open question 14. Reverb was not tested
  (`RoomSimulator` needs `pyroomacoustics`).

---

## Phase 5: results (2026-10-15)

| Measurement | Result |
|---|---|
| idle daemon (text model loaded) | **~455–490 MB** (before: ~790 MB) |
| search through the daemon | 19–89 ms per query; the whole `lantern search` CLI command 40–110 ms |
| indexing 42 files (code, text, a recording, a video) after `approve` | 51 s, 0 errors; the daemon stayed at 490 MB, the worker up to 3.3 GB, then it exited |
| daemon tests | real socket, worker process, FSEvents, ask/auto mode, pause, battery, permissions |

Decisions:
1. **ADR-004: indexing in a separate process.** The daemon holds only the text model (search). The worker
   (`python -m lantern.service.worker`, background priority `PRIO_DARWIN_BG`) starts when work is approved and exits
   when the queue is empty. The audio tower, Whisper and the audio processor (which imports torch) never stay in the
   daemon. Search never waits for indexing. Pause is SIGTERM: the current file is finished, the rest goes back to the
   queue.
2. **Loading the model without torch** (`embedding/mlx_loader.py`): the `mlx_vlm`, `models/gemma4` and
   `models/qwen3_5` packages are replaced by empty stub packages so their `__init__` does not run (that is what pulls
   transformers → torch). Vectors still match the reference (`-m model` tests). Closes open question 13.
3. **MLX and threads:** GPU streams belong to a thread. The model is loaded and called on one dedicated daemon thread
   (queries from several API threads are queued to it). The bug only showed up in the live daemon ("There is no
   Stream(gpu, 0) in current thread"); there is now a test with the real model in the production layout.
4. **`ask` mode:** after start (and after watcher events, when nothing is being indexed) new and changed files wait for
   approval. Changes found during an approved round join it. Deletions apply immediately. After a restart the daemon
   also asks about files the previous run left in the queue.
5. **API:** JSON lines over a Unix socket (`0600`, data folder `0700`), one request per connection: simple for the CLI
   and for Swift. When the daemon runs, the CLI delegates writes to it, so two processes never index at once with two
   models in memory.
6. **Pause on battery / heat** through `pmset -g batt` and `pmset -g therm` (every 30 s, no extra dependencies).

Found live and fixed: `chmod 0700` on the socket's parent folder (with `LANTERN_SOCKET=/tmp/...` that would have been
the system `/tmp`); now only Lantern's data folder gets the permissions.

---

## Early photo search test (before Phase 7, 2026-10-15)

`spikes/phase7/image_eval.py`. Flickr30k, 1000 photos with 5 English captions each. Queries: 12 single concepts
(English and Polish, e.g. "dog" / "pies"; a photo is relevant when one of its captions mentions the concept), all 5000
English captions and 25 captions translated into Polish. EmbeddingGemma 2: MLX vision tower (vs ST fp32: cos 0.9999).
SigLIP 2: `google/siglip2-base-patch16-256` (transformers, MPS, fp32).

| | EmbeddingGemma 2 | SigLIP 2 base |
|---|---|---|
| "dog", dogs in the top 10 | 10/10 | 10/10 |
| "pies", dogs in the top 10 | 4/10 (bare word) → **10/10** with context ("pies na zdjęciu") | 10/10 |
| single concepts EN, mAP | 0.862 | 0.856 |
| single concepts PL, mAP | 0.649 (bare word) → **0.819** ("… na zdjęciu") | 0.720 |
| full captions EN, R@1 / R@5 | **0.862 / 0.974** | 0.794 / 0.946 |
| full captions PL, R@1 / R@5 | **0.88 / 1.00** | 0.64 / 0.92 |
| photo embedding speed (M4) | 2 photos/s (280 image tokens) | **46 photos/s** |
| peak memory | 1.3 GB | ~1.5 GB (torch) |

Conclusions (hypotheses):
- **The "pies" weakness is ambiguity, not missing Polish:** in English "pies" are pastries. A one-word query without
  language context is read as English. For the photo track Lantern should add context
  (e.g. "{query} na zdjęciu" / "photo of {query}") or a separate prompt. Tuned in Phase 7.
- **EmbeddingGemma 2 understands whole-sentence captions better** (especially in Polish), **SigLIP 2 is 23× faster.**
  For large photo libraries speed matters: 20,000 photos take ~2.8 h (EG2) vs ~7 min (SigLIP 2).
- To check in Phase 7: EG2 with a smaller image token budget (70–140 instead of 280; faster, quality?), SigLIP 2
  so400m (better, slower), MLX versions of SigLIP, HEIC and phone photos.

---

## "Whole Mac" stage (after Phase 5, 2026-10-15)

Dry run on the real home folder (`lantern index ~ --dry-run`, metadata only, 5.5 s): 5789 text and code files
(119 MB), 3 audio/video files, 405 photos, 4 iCloud-only items. After two sample exclusions in `~/Downloads` (unpacked
library sources, a JSON dataset): ~1900 files, first indexing estimated at ~1.2 h in the background.

Decisions:
1. **Home-folder policy:** skip `~/Library` (except iCloud Drive), `~/Applications`, the Apple Music library, macOS
   packages (`*.app`, `*.photoslibrary`, iWork, VM images, `.dmg`…) and hidden folders/files (except `.github`).
   Desktop and Documents in iCloud Drive are links to `~/Desktop` and `~/Documents`, and the scanner does not follow
   links, so there are no duplicates.
2. **iCloud "cloud only"** (`SF_DATALESS`): skipped, because reading would download the file. An indexed file that was
   evicted to the cloud stays in the index (it still exists; opening it downloads it).
3. **No permission** (macOS privacy, chmod): the folder is skipped and its entries are **protected from deletion**.
   Before, `os.walk` silently skipped such a folder, which would have removed its files from the index.
4. **User exclusions** (`lantern exclude add/remove/list`, the `exclude` list in `config.toml`): absolute paths
   (`~` allowed) or `.gitignore`-style patterns. Closes open question 16.
5. **Scale (`spikes/scale/scale_eval.py`):** we stay with full `float[768]` vectors.

| Chunks | float[768] | int8[768] | bit[768] + rerank top 200 | float[256] |
|---|---|---|---|---|
| 100k | 70 ms | 55 ms | 53 ms | 21 ms |
| 300k | 220 ms | 168 ms | 124 ms | 67 ms |
| 1M | 743 ms | 558 ms | 337 ms | 229 ms |
| top-10 agreement with exact (projects / photos) | 100% | 95% / 95% | 97% / 99.8% | 78% / 80% |

   Matryoshka 256 loses clearly (despite "almost lossless" on the model card) and is rejected. Bits with reranking are
   the plan for indexes above ~300k chunks (an extra `bit[768]` table next to `float[768]`, filled with one SQL query
   through `vec_quantize_binary`). Not needed today: a home folder is a few tens of thousands of chunks (~20–50 ms).

---

## Phase 6: results (2026-10-15)

The Swift app (`app/`, Swift package): menu bar (MenuBarExtra), search panel on ⌥Space, settings window,
notifications with actions. Checked on real window captures (`screencapture -l`) with an isolated index.

Decisions (agreed with the user):
1. **⌥Space shortcut** (Carbon `RegisterEventHotKey`: works in the background, no Accessibility permission). Spotlight
   stays on ⌘Space.
2. **The app starts the daemon as a child process**, so macOS permissions (Desktop, Documents, Downloads, iCloud Drive)
   belong to Lantern.app. A daemon that is already running (LaunchAgent) is used instead of starting a second one.
   `LANTERN_EXIT_WITH_PARENT=1`: the daemon exits when the app goes away (an orphaned daemon was found live after a
   force quit).
3. **"Apple Development" signing** (a stable signature keeps permissions across rebuilds). This Mac was missing the
   Apple WWDR G3 intermediate certificate (only the expired G1 was there), which made the certificate "invalid"; it was
   added to the keychain.
4. **Borderless panel** (`.borderless`, transparent background, size from SwiftUI, top edge pinned). The first version
   (`.titled`) had a transparent strip above the panel and a system shadow/frame around the window rectangle ("a strange
   outline" noticed by the user). On macOS 26 the system window shadow is off, because Liquid Glass draws its own.
5. **Own settings window** instead of SwiftUI's `Settings` scene, which did not open reliably in an app without a Dock
   icon.
6. Minimal animations: smooth panel appearance (0.16 s), a sliding result highlight (`matchedGeometryEffect`),
   approval card transitions, `numericText` counters, a pulsing icon while indexing.
7. Built with the Xcode 26 toolchain (`DEVELOPER_DIR`), because the Command Line Tools ship SDK 15.4 without Liquid Glass.

---

## Phase 7: choosing the photo model (2026-10-16)

`spikes/phase7/image_bench.py` + `run.sh`: 10 configurations, each in its own process, on the same set (Flickr30k 1k,
12 concepts EN/PL, 5000 English captions, 25 Polish captions). "PL+ctx" = the query "{word} na zdjęciu" ("{word} in
the photo"). Memory: "idle" after loading the model (inflated by ~0.5 GB of test photos held in RAM and the torch
import in the processor), "peak" = the process's peak memory footprint.

| Configuration | photos/s | idle | peak | mAP EN | mAP PL | mAP PL+ctx | R@1 EN | R@1 PL | "pies" in top 10 (bare / with context) |
|---|---|---|---|---|---|---|---|---|---|
| EG2 8-bit, 70 tok. | 9.5 | 2.1 GB | 5.9 GB | 0.811 | 0.611 | 0.765 | 0.812 | 0.80 | 6 / 10 |
| **EG2 8-bit, 140 tok.** | **4.4** | 2.4 GB | 5.8 GB | 0.835 | 0.637 | **0.788** | **0.847** | **0.84** | 6 / 10 |
| EG2 8-bit, 280 tok. | 2.0 | 2.8 GB | 5.8 GB | 0.862 | 0.648 | 0.819 | 0.862 | 0.88 | 4 / 10 |
| EG2 8-bit, 560 tok. | 0.8 | 2.6 GB | 5.8 GB | 0.866 | 0.661 | 0.824 | 0.866 | 0.88 | 3 / 10 |
| EG2 4-bit, 140 tok. | 4.6 | 2.3 GB | 5.9 GB | 0.818 | 0.616 | 0.741 | 0.842 | 0.76 | 4 / 10 |
| EG2 bf16, 140 tok. | 4.5 | 2.6 GB | 5.8 GB | 0.834 | 0.631 | 0.785 | 0.845 | 0.84 | 6 / 10 |
| SigLIP 2 base fp32 | 48.4 | 2.7 GB | 3.7 GB | 0.856 | 0.720 | 0.705 | 0.794 | 0.64 | 10 / 10 |
| SigLIP 2 base fp16 | **57.7** | 2.1 GB | **2.2 GB** | 0.856 | 0.720 | 0.704 | 0.794 | 0.64 | 10 / 10 |
| SigLIP 2 large fp16 | 18.0 | 2.9 GB | 4.1 GB | 0.847 | 0.675 | 0.677 | 0.818 | 0.76 | 9 / 10 |
| SigLIP 2 so400m fp16 | 5.7 | 3.6 GB | 4.7 GB | 0.858 | 0.739 | 0.682 | 0.833 | 0.88 | 9 / 10 |

Conclusions:
1. **EG2 quantisation does not affect photo speed** (the vision tower is always bf16); 4-bit clearly loses quality,
   bf16 ≈ 8-bit. We keep 8-bit (the same model as text and audio).
2. **The token budget is the main dial:** 140 tokens = 2.2× faster than the default 280 for a small loss
   (mAP PL+ctx 0.788 vs 0.819; R@1 PL 0.84 vs 0.88). 70 tokens = 4.8× faster, still better than SigLIP in Polish.
3. **SigLIP 2 base fp16 is 13× faster** with the smallest memory peak and catches bare one-word queries better (also
   "pies"), but understands Polish sentence captions poorly (R@1 PL 0.64 vs 0.84) and would need a second model in the
   daemon for queries (a separate vector space, permanent extra RAM). so400m matches EG2 in Polish but is slow and heavy.
4. **Recommendation:** EG2 8-bit, **140 tokens per photo**, photo queries with a language cue. No extra idle memory
   (queries use the same text model the daemon already holds), one vector space, better Polish. The user's 405 photos
   ≈ 1.5 min, 20,000 ≈ 75 min in the background. SigLIP 2 base fp16 as a possible "fast mode" for very large libraries.
5. The ~5.8 GB peak for EG2 in this test comes from an MLX limit set to 4 GB + photos in RAM; Lantern's worker uses a
   3 GB limit and processes photos one at a time. Measured during implementation (below).

### The SigLIP 2 + EmbeddingGemma 2 pair (`fusion_eval.py`, `pair_cost.py`)

Quality (A = SigLIP 2 base fp16, B = EG2 8-bit 140 tok.; Polish single words: B with context, A the bare word):

| Strategy | mAP EN | mAP PL | R@1 EN | R@5 EN | R@1 PL |
|---|---|---|---|---|---|
| A only | 0.856 | 0.720 | 0.794 | 0.946 | 0.64 |
| B only | 0.835 | 0.788 | 0.847 | 0.968 | 0.84 |
| **z-score (normalised scores, summed)** | **0.872** | **0.814** | **0.859** | **0.973** | **0.88** |
| RRF, equal weights | 0.873 | 0.807 | 0.833 | 0.969 | 0.84 |
| RRF weighted by query length | 0.873 | 0.779 | 0.847 | 0.972 | 0.80 |
| cascade: A top 100 → rerank with B | 0.812 | 0.746 | 0.847 | 0.969 | 0.84 |

Cost in the daemon (one process, like the daemon): EG2 text 387 MB → + SigLIP 2 base text encoder fp16 (torch)
**2091 MB** (+1.7 GB permanently, ~0.23 GB of it the torch import alone). Cold loading SigLIP on demand: ~4.7 s (torch
import 2.5 s + model 2.2 s), too slow to load when the panel opens. Query (warm): EG2 9.5 ms, SigLIP 8.3 ms, both
16.9 ms. Indexing the pair: ~4.1 photos/s (SigLIP adds ~0.02 s per photo to EG2's 0.23 s), two vectors per photo.

Conclusions (hypotheses; small set: 12 concepts, 25 Polish captions; the 5000 English captions are the most reliable):
- The pair with z-score fusion gains 1.2–4 points on every measure. The cascade and query-length weighting do not help.
- Both models answer in < 20 ms together, so results can be fused before display: no "late" results.
- Cost: +1.7 GB RAM in the daemon (torch). To check: SigLIP 2 on MLX 8-bit
  (`mlx-community/siglip2-base-patch16-224-8bit`), which could bring the pair's cost down to a few hundred MB without
  torch.

### SigLIP 2 on MLX 8-bit for queries (`mlx_siglip.py`)

`mlx-community/siglip2-base-patch16-224-8bit` loaded with the `mlx_vlm.models.siglip` code (text model only) through
the same stub packages as EG2: **no torch**. Photo vectors come from the original `google/siglip2-base-patch16-224`
(fp16, torch in the worker, 75.9 photos/s).

- MLX 8-bit query vectors vs torch fp32: cos min 0.9994, mean 1.0000.
- Cost in the daemon (like the daemon): EG2 text 387 MB → + SigLIP text MLX **726 MB (+339 MB)**, loading **0.33 s**;
  query: EG2 8.4 ms + SigLIP 6.0 ms = 14.3 ms. After a series of queries 889 MB (MLX cache, bounded by the limits).
- Quality of the pair (z-score) SigLIP 224 MLX + EG2 140: mAP EN 0.866, mAP PL 0.814, R@1 EN 0.858, R@1 PL 0.84
  (EG2 alone: 0.835 / 0.788 / 0.847 / 0.84) — practically the same as the torch 256 px pair at ~5× less RAM.

Direction for Phase 7: **the EG2 (140 tok.) + SigLIP 2 base 224 pair**, z-score fusion for photos; the SigLIP text
encoder in the daemon loaded on demand (when the panel opens / on the first query, 0.33 s) and released after idling,
so the idle daemon stays at ~390–450 MB. The worker computes the photo vectors of both models.

---

## Phase 7: results (2026-10-16)

Implemented: photos (jpg, png, heic, gif, webp, tiff, bmp) and video frames, the EG2 (140 tok.) + SigLIP 2 base 224
model pair, separate photo vector tables per model (`vec_images_<id>`), a photos section in the API, CLI and panel.

Decisions:
- **Frames:** ffmpeg `select='isnan(prev_selected_t)+gt(scene,0.3)+gte(t-prev_selected_t,30)'` (a scene change or at
  least every 30 s), 448 px wide, at most 400 frames per video. Duplicates are dropped before embedding: dHash
  (Hamming ≤ 6) **and** the mean colour of a 4×4 grid (≤ 24). dHash alone treated plain slides of different colours as
  identical (scenes were lost in the test). A frame stands for the span until the next kept frame.
- **Photos:** Pillow (`exif_transpose`, downscaled to 1024 px), HEIC and Pillow failures through `sips` (without `-Z`,
  which upscaled small photos). Skipped: shorter side < 96 px, > 100 MB, decoding errors. A video without sound is
  indexed through its frames alone.
- **Photo list:** a candidate pool from both models' KNN, exact similarities for the whole pool, a z-score per model,
  summed. Only photos with z ≥ 1 per model are listed (`PHOTO_MATCH_Z`). Without this threshold every query got the
  "closest of the rest": a plain green video frame won almost every query.
- **Two sections instead of one ranking.** First I tried fusing photos with files through RRF weighted by each list's
  confidence. It worked in a photo folder, but on a mixed index (the user's projects + 80 photos) "pies" and "rower"
  returned Lantern source files containing those words, because agreement between the vector and word lists beat a
  single photo hit. Photos and documents are different kinds of results, so they are now separate sections (like
  Spotlight), and the section with the more confident list goes first.
  - file list confidence: top similarity − median of the candidates, ramp 0.02–0.08 → 0.25–1,
  - photo list confidence: z of the best photo per model, ramp 1–4 → 0.25–1,
  - a relative comparison, because there is no absolute threshold for photos: the confidence ranges of text and visual
    queries overlap (e.g. "gitara": photos 0.66, "postgres": 0.75),
  - a query that looks like code (`_`, brackets, `camelCase`, `a.b`, paths), e.g. "def backoff_wrapper(", always puts
    files first: code vectors have flat scores, so the confidence comparison alone put photos first here,
  - photos matched by file name also go to the photos section (after content matches). Video frames stay in the files
    section with the video's sound.

Results (mixed index: ~700 code and text files from the user's projects, a video, a recording and 106 images, including
80 Flickr30k photos; 12 queries):

| Query | First section | 1st file / 1st photo |
|---|---|---|
| postgres | files | README.md (PostgreSQL) |
| backoff_wrapper | files | test_model.py |
| ConnectionRefusedError | files | protocol.py |
| optymalizacja drzewa (tree optimisation) | files | fast_tree4.cpp |
| README instalacja (README installation) | files | README.md |
| pies / dog | photos | a dog (both) |
| rower (bike) | photos | a bike |
| dziecko w wodzie (child in the water) | photos | a child in the water |
| gitara (guitar) | photos | a guitar |
| zachód słońca (sunset) | photos | a sunset |
| samochód na parkingu (car in a car park) | photos | a car |

12 of 12 queries with the right section first; in the photo folder alone 9 of 9 queries with the right photo first.

Costs (M4): indexing 80 photos in 25 s including loading both models (~3.2 photos/s), peak process RSS ~3.9 GB.
Daemon: +339 MB only while the SigLIP query model is loaded (panel open, `warmup`), released after 5 minutes.
Tests: EG2 photo vectors vs sentence-transformers (cos ≥ 0.999), SigLIP MLX vs torch (cos ≥ 0.999, no torch import),
the right photo first, 0 network connections while loading and running both models.

Limitations (hypotheses to check on a real library): a small test set; no OCR (text in screenshots is not searchable
by words); the z ≥ 1 threshold assumes a pool of a few dozen candidates — with only a few photos in the index it cuts
almost everything (name matches remain). Turning frames off does not remove frames of videos already indexed.

## Phase 8: results (2026-10-16)

Implemented: a `symbols` table (schema v3) from tree-sitter, corrected code chunk names, a "Definitions" section in the
API, CLI and panel, opening a result at its line in the default editor.

Decisions:
- **A separate symbol table instead of only the `symbol` column in FTS.** A chunk had one name, so methods of small
  classes and functions merged into one chunk were lost. FTS5 also split `add_two` into "add" + "two". Now every
  definition has a row: name, style-insensitive key (`add_two` = `addTwo` → `addtwo`), kind, signature, parent class
  and exact line.
- **No FTS5 trigram index:** a plain table with an index on the key is enough. Measured at 300k symbols: exact
  < 0.01 ms, prefix (index range) < 1 ms, fragment (scan) ~14 ms. Deletion is simpler too: rows cascade with the file
  (secure_delete), no separate virtual table.
- **Query parsing:** the identifier before "(", otherwise the last one outside brackets; keywords (`def`, `class`, `fn`,
  `public`, `void`…) give a kind hint or are skipped; `A::b` / `A.b` → parent class; the number of arguments from the
  brackets. Natural-language sentences do not search definitions; a single word only exact names.
- **Order:** definitions always first (they are exact answers), then photos/files by the Phase 7 rule. The search
  history stores results in display order (fixed a Phase 7 bug: with photos first, `lantern open 1` opened the first
  file instead of the first photo). Signatures (code content) are not stored in the history.
- **Name bugs found by the language test and fixed:** C/C++ gave the return type as the name ("int"), Go did not name
  `type P struct`, C++ macros (`NAMESPACE_BEGIN namespace x {`) parsed as functions. A C/C++ function counts only with a
  real function declarator. A signature ends at the first `{` outside brackets.
- **Opening at the line** in the default app for the file type (like a double-click in Finder):
  - VS Code `vscode://file/…:line` — verified live (cursor on line 5),
  - Cursor `cursor://file/…:line` — scheme registered by the app, same format as VS Code; not launched,
  - Xcode `xed -l` — needs `DEVELOPER_DIR` pointing at Xcode (on this Mac `xcode-select` points at the Command Line
    Tools and `xed` fails on its own); the `-l` flag checked in `xed --help`,
  - JetBrains: the app's launcher with `--line` (documented by JetBrains); not launched (RAM),
  - others (e.g. IDLE, set on this Mac for `.py`): a plain open, never a different app.

Results:
- All 22 languages in the test (`tests/test_symbols.py`) give the expected definitions and lines.
- The user's real projects: 4394 symbols from 438 code files (functions 2162, methods 1525, classes 312…). 12 queries
  in different styles (a pasted `def …:` line, the bare name, another naming style, a fragment, `Class.method`,
  `Class::method`, Java, C++): **12/12 with the right definition**. In 8/12 exactly the expected file is first; the
  other 4 are ties between identical copies (e.g. `HistogramBasedDecisionTree` in 9 copies across 3 projects).
  < 1 ms per query.
- Re-indexing code after the pipeline change: 570 files in ~4 minutes (vectors recomputed, because the names in the
  chunk titles changed).

Limitations: definitions only, no usages (calls); declarations without a body (prototypes in C headers) are skipped;
with many copies of the same definition the section (5 entries) may not fit them all.

## Phase 9: results (2026-10-16)

Implemented: a settings window laid out like System Settings, a folder tree with checkboxes and search, ignored
extensions (pills), an audio and video switch, a Performance pane, the ⌘Y preview in the search panel, recordings
from the timestamp (plans A and B), new shortcuts (↩ / ⌥↩ / ⌘Y / ⌘↩).

Decisions:
- **The tree = plain path exclusions.** Unchecking adds the path (nested entries are dropped as redundant). Checking
  an item inside an excluded folder replaces the folder's exclusion with exclusions of the siblings along the way, so
  only that item comes back. The scanner needs nothing new. States: checked, partial, unchecked, skipped (with the
  reason, from the same code as the scanner: `dir_skip_reason` / `file_skip_reason`).
- **Tree search** walks names in the indexed folders (names only, no contents), ignores case and Polish characters
  (also "ł"), never enters always-skipped folders but does enter excluded ones (so they can be brought back). Limit:
  100 results or 3 s.
- **The mass-deletion guard counts only files gone from the disk.** Excluding a large folder (files still exist) does
  not need `--force`; an unplugged disk or a wrong folder is still blocked.
- **Extensions:** `csv`, `.csv`, `*.csv` and `CSV` mean the same; the pill shows `.csv` (the way the extension
  appears in a file name in Finder). A comma, space, semicolon or Return completes a pill; ＋ adds what is typed or
  opens the suggestions (types Lantern indexes, with the number of indexed files). Defaults `.ipynb`, `.csv`, `.tsv`.
- **Memory:** the footprint Activity Monitor shows (`proc_pid_rusage`, `ri_phys_footprint`). The daemon samples every
  second on its own (10-minute history), so the chart has data as soon as the window opens. Peaks come from
  `ri_lifetime_max_phys_footprint`: verified that a 300 MB spike between samples stays in the peak. Speed over the last
  20 s (over 60 s, model loading halved it). Time axis: clock time in the system format (24 h in Polish), last 60 s
  by default.
- **⌘Y preview instead of Space:** focus is in the query field, so Space must type a space; ⌘Y is the system Quick
  Look shortcut (Finder, Raycast). The preview replaces the list inside the panel, so the keyboard keeps working
  without switching windows.
- **Plan B (QuickTime from the moment) verified live:** a script with `current time` opened the video and jumped to
  10 s (after 1 s of playback it returned `10,665`, with a comma: that is why whole seconds are passed, never a
  fraction). The macOS permission to control QuickTime is asked the first time; without it ↩ shows plan A. mkv/webm:
  neither QuickTime nor the preview (same AVFoundation engine) can play them, so they open from the start in their
  default app.
- **Live measurement (80 photos, isolated daemon):** worker 0.7 → 3.2 GB (kernel peak 3220 MB, highest 1 s sample
  3198 MB), daemon 391 MB afterwards; the pane showed the loaded parts (EmbeddingGemma vision, SigLIP).
- **Compact counts:** exact up to 9,999, then compact like macOS ("12.3K", "100M"; "12,3 tys." in Polish, with a
  no-break space), so counts never overflow the menu, the sidebar or the tree.

After the user's feedback the settings window was rebuilt from screenshots of System Settings (macOS 26): a sidebar
with search and a Lantern status row, panes in groups, ← → arrows and the title in the toolbar, header cards (icon,
title, description), rows with coloured icons, subpages (tree, patterns), a soft scroll edge
(`scrollEdgeEffectStyle(.soft)`), dependent options dimmed instead of indented. An app language picker (same as macOS /
Polish / English) in General, with a relaunch; README and docs in English (public repository).

Limitations: the QuickTime permission was verified from the VS Code process (the Lantern app asks for its own on the
first ↩ on a recording); on very large folders (the whole home folder) tree search may not finish in 3 s and then shows
the first results.

## Phase 10: demo site (2026-10-16)

`site/` holds a static, dependency-free tour: a Mac-like desktop (own wallpaper, own SVG icons, the visitor's system
font) with Liquid Glass-style hint bubbles. Steps: open Lantern in the menu bar → start it → index 1,248 new files
(animated progress, notification) → close the menu → ⌥Space (or click the key tile: Alt+Space is taken by Windows,
and a Mac with Lantern installed would catch ⌥Space itself) → type the suggested phrase (other letters are rejected;
"Type it for me" for touch screens) → results in Lantern's sections → ⌘Y preview → three more searches → end card.
Scenarios: a photo ("dog on the beach"), a moment in a video ("when we talked about the match", plays from 1:45),
code at its line ("def add_two(x, y):", line 200), a song by its lyrics. Result images are own SVG drawings, not stock
photos; the song is found through its transcribed lyrics, which Lantern really does (Whisper), rather than claiming
that audio embeddings recognise music.

Checks: headless Chrome screenshots of every step at 1440×900 and 1280×720, phone emulation (390×844 shows a short
intro with "Show it anyway"), and a DevTools-protocol test that clicks and types through the whole tour (20 checks,
including the rejected wrong letter and playback from the timestamp). No request leaves the page except the GitHub
link. Deployment: `.github/workflows/pages.yml` (GitHub Actions → Pages) once the repository is public.

## OCR research: text in images (2026-10-16, before a possible Phase 11)

Test set (`scratchpad`, not in git): 40 Flickr photos + 35 images with text (a Polish invoice rendered from a PDF and 3
near-identical invoices, a Windows error dialog and 3 copies with other codes, a Python traceback screenshot, a
whiteboard, a meme, a parking ticket and 2 copies with other VINs/plates, ~20 Lantern screenshots). File names were
neutral (`IMG_9xxx`) so name matches could not help. Indexed by the real photo pipeline (EG2 vision 140 tokens +
SigLIP 2), searched with `image_search`.

- **The photo models already find text images by meaning:** 9 of 10 descriptive queries ("faktura za prąd", "błąd
  aktualizacji windows", "mandat za parkowanie", "wykres zużycia pamięci"…) put an image of the right kind first
  ("zrzut ekranu z kodem" did not: rank 11).
- **They even read large, clean text:** asked for a document's own number, they picked it from its near-identical
  siblings 15 times out of 18 (chance ~5): invoice numbers 4/4, error codes 4/4, plates 3/3, VINs 2/3, amounts 2/4.
  **Small text fails:** "Plików w indeksie" in a UI screenshot ranked 8th, "Postgres 16" on the whiteboard 4th.
- **Apple Vision OCR** (`VNRecognizeTextRequest`, on device, part of macOS, 30 languages including Polish) read all
  18 exact strings; Polish diacritics come out right ("Prywatność", "Plików w indeksie: 749"); small dense text gets
  occasional letter errors ("Sprzedawea", "Pónoc"), digits and codes stay exact. Cost: text detection 6 ms/image,
  fast recognition 10–15 ms, accurate 25 ms for a photo and ~80–240 ms for a text-heavy screenshot; peak footprint
  ~110 MB, no model to download.
- **A cheap gate works:** fast recognition with "at least 2 words of 3+ letters" flagged 31/35 text images and 2/40
  photos; the 4 misses were transparent PDF exports (below).
- **EXIF is a weak signal:** 0/40 of the Flickr photos had any EXIF (downloads and messengers strip it), so "no EXIF"
  cannot mean "probably a screenshot".
- **Bug found and fixed:** transparent PNGs (PDF exports, graphics) reached both the models and Vision as black text on
  black (pixel values 0–17). The photo loader now composites transparency onto white (image pipeline 2).

Conclusion: OCR is worth adding for exact strings and small text, with Vision's own detector as the gate (no extra
model, OpenCV or ONNX runtime). Plan and open decisions: open question 24.

## Phase 11: results — PDFs and text in images (2026-10-16)

Implemented: PDFs through PDFKit (page by page, `page` on chunks, schema v4; scanned pages via OCR), text in images
through Apple Vision with the cascade from "OCR research" (priority by name → fast gate → accurate reading →
clean-up), `ocr` chunks in the photos section, an OCR switch (Settings → File Types, `index_image_text`).

Decisions:
- **PyObjC instead of a Swift helper.** The Python worker calls Vision and PDFKit directly (`pyobjc-framework-Vision`,
  `pyobjc-framework-Quartz`): no second binary, no IPC, works for the CLI and in tests. Only the worker imports them
  (first import ~5 s, then cached), so the daemon's memory is unchanged.
- **OCR reads the original file** (≤ 4096 px), not the 1024 px copy made for the embeddings: small screen text would
  otherwise be unreadable.
- **No separate detector model, OpenCV or ONNX:** Vision's fast pass is the gate (10–15 ms).
- **Camera EXIF (make, model, GPS) is not used to skip images:** photos of receipts, whiteboards and documents come
  from cameras too, and the gate is cheap; EXIF is also missing on most downloaded or messenger photos.
- **OCR text counts by meaning only for descriptive queries.** For "FV/2025/09/0932" the meaning list put a parking
  ticket above the right invoice; numbers and codes now match the image text by words only.
- **Compounds as phrases:** "187,15 zł" matched another invoice through "187,40 zł" and a date with "15". Tokens
  joined by `.,/:-` are phrase terms: FTS retrieves the phrase and its parts (recall, e.g. Whisper writing
  "o 01555"), but keyword coverage needs the parts adjacent (precision).
- **PDF viewers:** Preview has no URL scheme or scripting for a page (checked: standard suite only), so ↩ opens the
  PDF and ⌘Y shows the page in Lantern (PDFKit) with the passage highlighted.

Results:
- OCR test set (75 images: 40 photos + 35 with text, near-identical documents): OCR found text in all 35 text images
  and in none of the 40 photos. Asked for an exact string, the right image is first in the photos section in 20 of 21
  cases (before OCR: 15 of 18 picks among siblings by the photo models); the remaining one is a phrase that appears on
  several screenshots.
- PDFs: "faktura za prąd", "FV/2025/10/1187" and "energia elektryczna taryfa G11" find the invoice PDF (page 1) first.
  The user's projects folder contained one PDF, skipped by Lantern until now and indexed after this phase.
- Cost: indexing the 75 images took 32 s instead of 25 s; a text PDF page ~14 ms, a scanned page ~130 ms.
- No regressions: the 12 mixed queries keep the right section first; code definitions unchanged (12/12).

## Panel: filter pills, completion and shortcut hints (2026-10-16)

Reference: the real Spotlight on macOS 26 (opened on this Mac): a pill bar under the field (sources of the results:
apps such as IDLE or Numbers, or categories such as Applications, Menu Items), inline completion of the top hit's name
("term" → "terminal — Open"), Tab on the top hit scopes the search to that app, and a contextual chip on the selected
row ("Search (Terminal) · tab"); no shortcut bar at the bottom.

Decisions:
- **Filter pills by kind of result** instead of apps: All, Code, Documents, PDFs, Photos, Recordings and videos, in a
  fixed order (stable ⌘1…⌘6), only for kinds with matches; the daemon filters (`category`) so a pill shows more of
  that kind, not a subset of 8. Tab / ⇧Tab cycle the pills (Spotlight's Tab also narrows the scope).
- **Inline completion only from what exists or what the user said:** earlier queries that led to an opened result,
  then definitions and file names; never invented sentences (semantic search does not need them). → accepts it.
- **History of search-as-you-type:** a query that extends the previous one within 5 s, with nothing opened, replaces
  it ("postgres", not "po", "pos", "post"…); cleaner suggestions and cleaner reranker data.
- **Shortcut hints on the selected row by default, the bottom bar optional (off by default).** Spotlight, Alfred and
  Apple's own panels show no shortcut bar; Spotlight shows the relevant key on the selected row. A permanent bar costs
  a row of space for something learnt in a day, while row hints teach the two keys that matter (↩, ⌘Y) exactly where
  they apply. Settings → General → Shortcut hints: on the selected result / bar at the bottom / hidden.
- **Edges:** pills and the bar start at the same 20 pt inset as the result text; the selection is concentric with the
  panel (radius 26 − inset 8 = 18), as macOS 26 draws nested shapes; keycaps are small capsules like Spotlight's.
- Verified with key events sent only to Lantern (`CGEvent.postToPid`): → accepts and moves the caret to the end,
  Tab / ⇧Tab move between pills (⇧Tab arrives as U+0019 and first selected the field's text — fixed).

## Power and QoS (2026-10-16)

Questions: is `pmset` parsing the right source, does the background priority really apply, which QoS should each part
run at, and should Low Power Mode pause indexing. Measured on the M4 (16 GB, macOS 26.6):

| Reading | Cost per call | Notes |
|---|---|---|
| `pmset -g batt` (subprocess) | 5.7 ms | text parsing; the old source |
| IOKit `IOPSGetProvidingPowerSourceType` (ctypes) | 0.07 ms | "AC Power" / "Battery Power"; public IOPowerSources API |
| `NSProcessInfo.isLowPowerModeEnabled`, `thermalState` via the Objective-C runtime (ctypes) | < 0.01 ms, +0 MB | Foundation is already loaded in every macOS process |
| the same through PyObjC | +36 ms import, +22 MB | rejected for the daemon |

| Priority | Query embedding (median) | 16 passages (batch) | CPU loop |
|---|---|---|---|
| normal process / default thread QoS | 9.2 ms | 963 ms | 71 ms |
| user-initiated / utility thread QoS | 9.1 / 9.3 ms | – | 75 / 76 ms |
| background (`PRIO_DARWIN_BG` process, or background thread QoS) | 21–28 ms | 980 ms | 493 ms |

- `setpriority(PRIO_DARWIN_PROCESS, 0, PRIO_DARWIN_BG)` does apply: `ps` shows priority 4 instead of 31. The GPU-bound
  batches of indexing are as fast as at normal priority, while CPU-bound work yields to the user. **The worker stays
  background.** (The old comment claimed a GPU priority change; that is not what was measured and was removed.)
- **The daemon must not be background**: it embeds queries. The LaunchAgent had `ProcessType = Background`, `Nice 5`
  and `LowPriorityIO`, which would make every search ~2.5× slower in the embedding step and slow FTS reads under disk
  load. Now `ProcessType = Interactive` (launchd's documented choice when responsiveness depends on a job that cannot
  be `Adaptive`, and `Adaptive` only follows XPC, not our Unix socket), and the background work is demoted inside:
  the model thread runs at user-initiated QoS, scans and the control loop at utility, indexing in the background
  worker. New threads do not inherit the creator's QoS (measured: a thread started from a user-interactive main
  thread runs at default), so each thread sets its own (`service/qos.py`).
- **Low Power Mode pauses indexing** (`pause_on_low_power`, default on, Settings → Battery and Heat): the user asked the
  Mac to save energy, and indexing is deferrable. It is read from NSProcessInfo and, as a cross-check, `pmset -g`
  (`lowpowermode 1`).
- **Heat:** pause from NSProcessInfo thermal state "serious" (macOS is already throttling) or when `pmset -g therm`
  reports a warning level or a CPU speed limit. `lantern status` and `api_status.power` show the source, Low Power Mode
  and the thermal state.
- Not verified: toggling Low Power Mode needs `sudo pmset` or System Settings, and heat cannot be produced on demand;
  whether NSProcessInfo refreshes in a long-running process without a run loop is therefore unverified, hence the
  pmset cross-check (open question 33). The LaunchAgent's effect on QoS can only be checked after installing it
  (open question 34).

## Letters without a decomposition (2026-10-16)

FTS5's `remove_diacritics 2` and Foundation's `.diacriticInsensitive` only remove combining marks. Letters that are
their own characters in Unicode were checked one by one in both: ł, ø, đ, ð, ħ, ŧ, ŋ, ı, ß, æ, œ, þ (and ligatures
such as ﬁ, ĳ) are **not** folded, so "lodz" did not find "Łódź" and "soren" did not find "Søren"; ą, ü, ř, ș, ő, ç,
ñ, å, ơ are. Decision: one fold table (`textnorm._FOLD`, after NFKC for ligatures) applied at index and query time,
mirrored in the app (`LanternCore/TextFold.swift`) for the PDF highlight. A transliteration library (Unidecode,
AnyAscii) was not added: it would also rewrite scripts the models handle natively (Cyrillic, Greek, CJK) and the
table covers the Latin letters that matter. Existing indexes are re-folded by schema migration 4 → 5 (FTS rows only,
no re-embedding). A test fails if a future SQLite starts folding these letters itself.

## ADR-001: Embedding backend, MLX 8-bit (mlx-vlm), with a sentence-transformers fallback

**Status:** accepted (2026-10-15), confirmed in Phases 2–5

**Context:** text, code and audio are needed in one vector space, locally and offline, with low RAM use on 16 GB. The
user prefers native MLX when a port exists. Measured: MLX 8-bit is ~1.7× faster than ST on text and ~1.3× on audio,
and the vectors match the original (cos 0.9998). Ollama has audio but fails on long windows. llama.cpp does not work
with the current version.

**Decision:**
- Main backend: **MLX** (`mlx-vlm`, pinned commit), weights `mlx-community/embeddinggemma-2-8bit` pinned to an HF
  revision.
- The text tower stays loaded (~0.4–0.5 GB idle); the audio and vision towers are **loaded on demand** in the worker
  for audio/photo jobs and released afterwards.
- Preprocessing without torch in the daemon (tokenizer from `tokenizers`, stub packages in the loader); the worker
  uses the transformers processor for audio and images.
- Fallback backend behind the same interface: `sentence-transformers` (MPS, bf16), e.g. if the mlx-vlm branch drifts.
- Embedders behind an interface (`embed_text`, `embed_audio`, `embed_images`, `model_info`).

**Consequences:**
- Risk: mlx-vlm from an unreleased branch and community-converted weights. Mitigated by pinning the commit and the
  revision and by a reference test against ST (cos ≥ 0.999 on a control set) in the integration tests.
- Every vector is stored with a `model_id` = weights name, revision, backend, quantisation and dimension. MLX-8bit and
  ST-bf16 are **different** `model_id`s despite cos 0.9998. Switching means re-indexing.

## ADR-002: Storage, SQLite + FTS5 + sqlite-vec in one file

**Status:** accepted

**Context:** metadata, full-text word search, vectors, transactional deletion and real erasure from disk are needed.

**Decision:** one SQLite file with `vec0` tables (sqlite-vec, cosine) and FTS5. LanceDB rejected for v1.

**Consequences:** deleting a file is one transaction (chunks + FTS + vectors). VACUUM really shrinks the file
(measured: 305 MB → 156 MB after deleting half). Brute-force KNN: 78 ms for 100k × 768d, enough for a few hundred
thousand chunks; above that, binary vectors with reranking (measured in the "whole Mac" stage). Needs a Python with
`enable_load_extension` (uv).

## ADR-003: Audio, overlapping windows and an explicit length limit

**Status:** accepted and implemented (Phase 4)

**Decision:** ffmpeg to 16 kHz mono, then **30 s windows with a 25 s hop**. We always set
`processing_kwargs["audio"]["max_length"]` explicitly to the window length and never rely on the default limit. Long
windows (300 s) are possible but take ~8 GB of MPS memory and localise worse in time. Transcription (mlx-whisper
large-v3-turbo) is the **second track** of audio search (H1, H4): text for FTS with prefix matching, previews and text
embeddings. Whisper is loaded only for transcription (~1.8 GB), never together with the EmbeddingGemma audio tower.

## ADR-004: Indexing in a separate worker process

**Status:** accepted and implemented (Phase 5); see "Phase 5: results", decision 1.

---

## Open questions

1. Transcription as the second audio track is accepted (ADR-003). Still open: with very many recordings, whether to
   transcribe lazily (audio embeddings first, transcripts later in the background).
2. The best audio window length (30 / 60 / 120 s) on real recordings.
3. ~~MPS memory limit in the daemon~~ — superseded: MLX memory and cache limits are set at load (Phase 2), torch/MPS
   runs only in the worker.
4. ~~Vector size 768 vs Matryoshka 256~~ — measured in the "whole Mac" stage: 256 loses clearly (top-10 agreement
   78–80%); we keep 768.
5. llama.cpp: a newer build than 11429 may support `gemma-embedding2` (the GGUF is from ggml-org, unofficial). Not
   checked.
6. Ollama audio: why it fails at ≥ ~230 s (memory? an MLX runner limit?). Irrelevant with 30 s windows.
7. The unofficial Swift MLX port (`Obscyra-app/EmbeddingGemma2Swift`) as a backend for the Swift app. Not needed: the
   app is a client of the daemon.
8. Polish inflection in FTS: the stem prefix (Phase 3) works in tests but gives false hits for short stems. Alternative:
   a Polish stemmer or lemmatiser (e.g. Morfologik/Stempel).
9. Stability of the `mlx-vlm` branch with EmbeddingGemma 2: watch for it to land in a PyPI release.
10. Integration with system search (⌘Space). Spotlight cannot plug in a custom semantic ranking (Core Spotlight matches
    its own keywords). **Direction chosen by the user (2026-10-15):** an own Spotlight-like panel (NSPanel + SwiftUI)
    on a global shortcut, done in Phase 6. The user can turn off the Spotlight shortcut in System Settings → Keyboard →
    Shortcuts and give ⌘Space to Lantern (as Raycast and Alfred do). Possible extras: command prefixes in the panel
    ("audio:", "code:"), an App Intents action in Spotlight.
11. ~~Image search~~ — implemented in Phase 7 (see "Phase 7: results"). Photos and video frames, with frame selection
    by scene changes and a 30 s floor, perceptual-hash deduplication before embedding, and the EG2 + SigLIP 2 pair.
12. ~~Chunker versioning~~ — solved in Phase 3 (`pipeline_version`, `model_id`).
13. ~~Idle memory~~ — solved in Phase 5 (~490 MB: loader without torch + indexing in a separate process). Future
    option: release the model after a long idle period (~50–100 MB, first query +0.5–1 s).
14. Audio search quality on the user's real recordings (noise, several speakers, accents): needs a set of recordings
    with queries. To do when the user provides them.
15. Keyword fusion weighted by term coverage works in tests; further tuning (e.g. list weights) or a reranker once the
    `search_events` history has grown.
16. ~~User exclusions~~ — solved (`lantern exclude`, the folder tree, ignored extensions).
17. An event stream from the API instead of polling `status` every 2 s and `perf` every second while the Performance
    pane is open (negligible cost, but worth considering).
18. Binary search with reranking once the index exceeds ~300k chunks (measured, ready to implement).
19. ~~Playing from the timestamp~~ — implemented in Phase 9 (QuickTime from the moment, ⌘Y preview).
20. Changing the ⌥Space shortcut in the settings (fixed today).
21. ~~Model pair for photos~~ — checked: SigLIP 2 MLX 8-bit costs +339 MB only while loaded (0.33 s, on demand).
22. Phrase search in recordings: overlapping transcript chunks, word timestamps (Whisper word timestamps), tolerance
    for ASR errors in FTS (an FTS5 trigram index for transcripts).
23. A test set on the user's data: 50–100 queries with hand-labelled answers (short, long, Polish, slang), recall@k and
    MRR; a 20+ minute thermal test; the full configuration of every run stored next to the results.
24. ~~OCR~~ — implemented in Phase 11. Original plan: OCR of photos and screenshots with Apple Vision (measured, see "OCR research"): a cascade of path/name and screenshot
    metadata (priority only) → Vision text detection on every image (6 ms) → accurate recognition only for images with
    text → line confidence and garbage filters, watermark lines dropped → an `ocr` chunk per image (FTS for exact
    strings, text embedding for meaning). To decide: a Swift helper vs pyobjc, OCR on by default or opt-in (screenshots
    can hold sensitive text), PDFs (text layer through PDFKit, OCR only for scanned pages).
25. The photo threshold (z ≥ 1) for very small libraries (a few photos) and tuning the section order rule on real user
    data; the photos section as a thumbnail grid instead of rows.
26. Searching symbol usages (calls, imports) and declarations without a body (prototypes in C/C++ headers).
27. Line jumps in Cursor and JetBrains IDEs to verify live (VS Code verified); other editors (Zed, Sublime Text,
    BBEdit, Nova) to add once their mechanism is checked.
28. The definitions section with many identical copies (e.g. "+4 copies") instead of filling every slot.
29. GitHub Pages: enable Pages (source: GitHub Actions) once the repository is public; the workflow and the site are
    ready in `.github/workflows/pages.yml` and `site/`.
30. PDF page jumps in viewers other than Preview (Skim, Acrobat) to verify; Preview cannot open a page.
31. OCR on real libraries (phone photos of documents: perspective, blur) and on large screenshot folders; tuning the
    gate (two words of 3+ letters) if it lets through noise such as shirt numbers or shop signs.
32. Scoping with Tab to a folder (Spotlight's "search in app" equivalent), e.g. Tab on a result from ~/Studia/Projekt
    searches only there.
33. Low Power Mode and thermal state through NSProcessInfo in a long-running daemon: confirm they refresh without a run
    loop (toggle Low Power Mode, watch `lantern status`); until then `pmset` is checked too.
34. After `lantern daemon install`: check the daemon's priority (`ps -o pri`) and search latency under the
    `Interactive` LaunchAgent.
