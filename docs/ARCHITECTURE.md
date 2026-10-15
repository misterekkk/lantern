# Architektura

## Przegląd

```
Aplikacja Swift (pasek menu)    CLI / skrypty
          └──── socket Unix (0600, tylko użytkownik) ───┐
                                                        ▼
Demon (LaunchAgent; model tekstowy w pamięci do wyszukiwania, ~490 MB)
  API · pętla sterująca · Skaner + Watcher (FSEvents) · tryb ask/auto · pauza (bateria, temperatura)
        │ uruchamia, gdy jest zatwierdzona praca, i zatrzymuje (pauza)
  Proces indeksujący (priorytet tła; kończy się po opróżnieniu kolejki) · Kolejka zadań (SQLite)
        │
  Ekstraktory (tekst, kod, PDF, audio) → Chunker → Embedder (MLX, EmbeddingGemma 2 8-bit)
        │                                             └─ audio/wideo: okna audio + transkrypcja (mlx-whisper)
        ▼
  SQLite (jeden plik): metadane + FTS5 (słowa) + sqlite-vec (wektory)
        ▲
  Wyszukiwanie hybrydowe: wektory + BM25 → RRF → search_events
```

Cała logika jest w demonie. Klienci tylko wywołują API.

## Zasady, których się trzymamy

- **Skan jest źródłem prawdy.** FSEvents to tylko optymalizacja, bo zdarzenia giną, gdy demon nie działa. Przy starcie
  porównujemy (ścieżka, rozmiar, mtime) z bazą, hash treści liczymy tylko w razie potrzeby.
- **Ochrona przed masowym usunięciem:** niedostępny folder główny (odłączony dysk, udział sieciowy) nie powoduje usuwania wpisów.
- **Przeniesienia** rozpoznajemy po hashu treści, bez ponownego liczenia embeddingów.
- **Kolejka** w SQLite: idempotentna, wznawialna, błąd jednego pliku nie blokuje reszty.
- **Embedder za interfejsem**, `model_id` przy każdym wektorze, zakaz mieszania modeli.
- **Wyszukiwanie hybrydowe:** same wektory słabo trafiają w nazwy funkcji i komunikaty błędów, więc łączymy je z BM25 przez RRF.
- **Audio:** ffmpeg do 16 kHz mono, okna 30 s z krokiem 25 s, znaczniki czasu. Dwa tory: embedding audio oraz transkrypcja
  (słowa, podgląd). Uzasadnienie: DECISIONS.md, H1–H4.
- **Budżet RAM (16 GB):** wieża tekstowa ładowana stale (~0,4–0,5 GB), wieża audio i Whisper tylko na czas zadań,
  nigdy równocześnie.

## Fazy

| Faza | Zakres | Stan |
|---|---|---|
| 0 | rozpoznanie backendów, pomiary, test jakości | zrobione (DECISIONS.md) |
| 1 | szkielet repo i dokumentacja | zrobione |
| 2 | rdzeń: schemat, skaner, ekstraktor tekstu/kodu, chunker, embedder, CLI `index/search/status` | zrobione |
| 3 | wyszukiwanie hybrydowe (RRF), higiena indeksu, VACUUM, `search_events`, polska normalizacja FTS | zrobione |
| 4 | audio (także ścieżka dźwiękowa filmów przez ffmpeg): okna, embedding audio, transkrypcja, znaczniki czasu, ocena na prawdziwych nagraniach | zrobione (ocena na mowie syntetycznej; prawdziwe nagrania: otwarte pytanie 14) |
| 5 | demon + API (socket Unix), FSEvents, LaunchAgent, pauza na baterii i przy wysokiej temperaturze; tryb „pytaj”: po starcie (także po dłuższym wyłączeniu) skan wykrywa nowe i zmienione pliki i pyta, czy je zaindeksować | zrobione |
| 5b | cały komputer: polityka katalogu domowego, iCloud tylko lokalnie, ochrona folderów bez uprawnień, własne wykluczenia, `--dry-run`, test skali | zrobione |
| 6 | aplikacja Swift, tylko za zgodą: pasek menu (włącz/wyłącz, status, kolejka, „znaleziono N nowych plików, zaindeksować?”), panel wyszukiwania w stylu Spotlight pod globalnym skrótem, design zgodny z Apple HIG (SwiftUI, materiały Liquid Glass, SF Symbols) | zrobione |
| 7 | opcjonalnie: wyszukiwanie w zdjęciach i klatkach filmów (ffmpeg → klatki → wieża wizyjna), poprzedzone testem jakości i szybkości | – |

Poza zakresem na razie: reranker LightGBM (najpierw zbieramy `search_events`), podsumowania folderów przez LLM,
synchronizacja, chmura, wielu użytkowników.
