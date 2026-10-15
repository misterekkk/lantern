# Plan Fazy 3: wyszukiwanie hybrydowe i higiena indeksu

**Status:** zrealizowany (2026-10-15), wyniki w DECISIONS.md, sekcja „Faza 3: wyniki”.

## Zakres

1. **Jeden ranking (RRF):** lista wektorowa i lista BM25 (FTS5) łączone przez Reciprocal Rank Fusion (k = 60)
   na poziomie plików. Każdy wynik pokazuje, skąd pochodzi dopasowanie („znaczenie #1 · słowa #3”).
2. **Lepsze dopasowanie słów:** pomijanie słów nieistotnych (PL/EN: „jak”, „się”, „the”…), dopasowanie polskiej odmiany
   przez prefiks rdzenia („decyzyjnego” → `decyzyjn*`), wagi kolumn BM25 (tekst 1, ścieżka 1,5, symbol 3).
3. **`search_events`:** każde wyszukiwanie zapisuje zapytanie i wyniki z cechami (pozycje i wyniki z obu list, RRF).
   `lantern open N` otwiera N-ty wynik ostatniego wyszukiwania i zapisuje, który wynik wybrano (dane pod przyszły reranker).
   `lantern clear-history` usuwa historię.
4. **Higiena indeksu:**
   - wersja potoku (`pipeline_version`) i model (`model_id`) przy każdym pliku: zmiana zasad dzielenia lub modelu
     → automatyczny reindeks przy następnym `index`; migracja schematu v1 → v2,
   - `lantern remove <folder>` usuwa folder z indeksu (także gdy już nie istnieje),
   - `lantern compact` oraz automatyczne zagęszczanie po dużych usunięciach: FTS `optimize` + `VACUUM` +
     checkpoint WAL, `secure_delete` i FTS `secure-delete` włączone, żeby usunięte dane faktycznie znikały z pliku.
5. Przeniesienia po hashu i usuwanie wpisów były już w Fazie 2.

## Testy

RRF (plik w obu listach wygrywa, dokładny identyfikator z samej listy słów jest w wynikach), słowa pomijalne i prefiksy,
zapis `search_events` i `open`, migracja v1 → v2, reindeks po zmianie wersji potoku i po zmianie modelu, `remove`
oraz **brak usuniętego tekstu w surowych bajtach pliku bazy po `compact`**.
