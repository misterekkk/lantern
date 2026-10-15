# Plan Fazy 2: rdzeń (CLI, jeden folder)

**Status:** zrealizowany (2026-10-15), wyniki w DECISIONS.md, sekcja „Faza 2: wyniki”. Zakres: tekst i kod. PDF i audio to Faza 4, RRF i higiena indeksu to Faza 3.

## Cel

Działające od początku do końca: `lantern index ~/projekt` → `lantern search "optymalizacja drzewa decyzyjnego"` zwraca
np. README, w którym jest o tym mowa (wektory), oraz pliki z dokładnym słowem (FTS jako osobna lista).
`lantern status` pokazuje liczby plików, fragmentów, zadań i błędów.

## Moduły i pliki

| Moduł | Plik(i) | Zakres |
|---|---|---|
| store | `store/db.py`, `store/schema.sql`, `store/migrations.py` | połączenie (WAL, sqlite-vec, `foreign_keys`), `PRAGMA user_version` jako wersja schematu, lokalizacja `~/Library/Application Support/Lantern/index.db` (nadpisywalna przez `LANTERN_HOME` w testach) |
| scanner | `scanner/walk.py`, `scanner/ignore.py`, `scanner/diff.py` | rekurencyjny walk bez podążania za symlinkami poza root, wykluczenia domyślne + `.gitignore` (`pathspec`), diff z bazą: nowe / zmienione / usunięte / przeniesione |
| queue | `queue/jobs.py` | upsert `(file_id, kind)`, pobieranie partii, `running`→`queued` po crashu, limit prób = 3 |
| extractors | `extractors/text.py` | wykrywanie binarek (NUL w pierwszych 8 KB), limit rozmiaru (domyślnie 2 MB), UTF-8 z fallbackiem `charset-normalizer`, mapowanie rozszerzeń na `text` lub `code` |
| chunking | `chunking/code.py`, `chunking/text.py` | kod: tree-sitter (`tree-sitter-language-pack`) po funkcjach i klasach dla Python/JS/TS/Go/Rust/Java/Swift/C/C++; fallback i tekst: okna ~400 tokenów z nakładką 15% (Markdown po nagłówkach) |
| embedding | `embedding/base.py`, `embedding/mlx_backend.py`, `embedding/fake.py` | Protocol `Embedder`; MLX 8-bit, ładowanie leniwe, w Fazie 2 tylko wieża tekstowa; fake deterministyczny do testów |
| search | `search/basic.py` | wektor KNN (sqlite-vec) + FTS5 jako osobne listy; w Fazie 3 zastąpione przez RRF |
| cli | `cli.py` | `index <path>` (skan, kolejka, przetwarzanie z paskiem postępu), `search "<q>" [-n]`, `status` |

## Kluczowe zachowania

- **Diff:** (path, size, mtime_ns). Jeśli się zmieniły, liczymy hash (blake2b z hashlib). Ten sam hash = tylko aktualizacja metadanych.
- **Przeniesienie:** plik zniknął spod ścieżki A, a pod B pojawił się plik z tym samym hashem. Przepinamy `files.path`
  i aktualizujemy FTS (ścieżka), bez ponownego embeddingu.
- **Guard masowego usunięcia:** jeśli root nie istnieje lub nie jest czytelny, skan kończy się ostrzeżeniem i nic nie usuwa.
  Dodatkowo: jeśli w roocie zniknęłoby > 50% plików, a root jest na wolumenie zewnętrznym, wymagamy `--force`.
- **Indeksowanie pliku = jedna transakcja:** usuń stare fragmenty, FTS i wektory, potem wstaw nowe. Powtórka daje ten sam wynik.
- **Prompty:** dokumenty `title: <ścieżka względna>[::symbol] | text: …`, zapytania `task: search result | query: …`.
- **Polska normalizacja FTS** (ł→l) już w Fazie 2, bo to jedna funkcja i dotyczy schematu.
- **RAM:** batch embeddingu 8 fragmentów (szczyt ~0,6 GB zamiast ~1,1 GB), `mx.clear_cache()` po każdej partii.

## Testy

- `test_scanner.py`: nowe, zmienione (mtime i treść), zmienione tylko mtime (bez reindeksu), usunięte, przeniesione
  (bez wywołania embeddera, sprawdzane fake'iem z licznikiem), plik z sekretem pominięty, `.gitignore` respektowany.
- `test_guard.py`: niedostępny root → zero usunięć; po ponownym podłączeniu → normalny diff.
- `test_queue.py`: idempotentny upsert, wznowienie po „crashu”, błąd jednego pliku nie zatrzymuje reszty, limit prób.
- `test_store.py`: usunięcie pliku czyści chunks, FTS i vec; `model_id` rozdziela tabele wektorów.
- `test_chunking.py`: funkcje i klasy z próbki Pythona/TS, fallback na oknach.
- `@pytest.mark.model`: MLX vs referencja ST (cos ≥ 0,999), sanity check wyszukiwania na małym repo testowym,
  0 połączeń sieciowych przy ładowaniu i embedowaniu (przechwycenie socketów).

## Nowe zależności

`pathspec`, `tree-sitter-language-pack`, `charset-normalizer` (wszystkie przez uv; dopiszę do README).

## Kryteria ukończenia

1. `uv run pytest` i `uv run pytest -m model` przechodzą, `ruff check` czysty.
2. Ręczny test na tym repo i jednym prawdziwym projekcie: czas indeksowania, rozmiar bazy, szczyt RAM, 5 zapytań PL/EN z wynikami,
   **czas odpowiedzi na zapytanie** (cel: ~100–200 ms przy załadowanym modelu, pod przyszły panel typu Spotlight).
3. README zaktualizowane (komendy, konfiguracja wykluczeń), DECISIONS.md uzupełnione o nowe ustalenia.
