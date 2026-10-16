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
  Ekstraktory (tekst, kod, audio, zdjęcia, klatki) → Chunker → Embedder (MLX, EmbeddingGemma 2 8-bit)
        │                                   ├─ audio/wideo: okna audio + transkrypcja (mlx-whisper)
        │                                   └─ zdjęcia/klatki: wieża wizyjna EG2 + SigLIP 2 (drugi model)
        ▼
  SQLite (jeden plik): metadane + FTS5 (słowa) + sqlite-vec (wektory: osobne tabele na model i na tekst/zdjęcia)
        ▲
  Wyszukiwanie: definicje (tabela symboli) | pliki (wektory + BM25 → RRF) | zdjęcia (EG2 + SigLIP, z-score)
                → sekcje → search_events
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
- **Zdjęcia:** dwie listy (pliki i zdjęcia) to dwa różne rodzaje wyników, więc nie łączymy ich w jeden ranking;
  wyniki mają dwie sekcje, a wyżej jest ta, której lista jest pewniejsza (DECISIONS.md, Faza 7).
- **Budżet RAM (16 GB):** wieża tekstowa ładowana stale (~0,4–0,5 GB), model zapytań do zdjęć (SigLIP, ~0,34 GB) na
  żądanie; wieża audio, Whisper, wieża wizyjna i SigLIP do zdjęć tylko na czas zadań, audio i Whisper nigdy równocześnie.

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
| 7 | wyszukiwanie w zdjęciach i klatkach filmów (ffmpeg → klatki → wieża wizyjna EG2 + SigLIP 2), poprzedzone testem jakości i szybkości; sekcja zdjęć w panelu | zrobione |
| 8 | precyzyjne wyszukiwanie kodu: tabela symboli z tree-sittera (nazwa, rodzaj, sygnatura, linia), poprawne nazwy w C/C++/Go i metodach małych klas, sekcja „Definicje”, otwieranie na linii w domyślnym edytorze | zrobione |
| 9 | ustawienia i media (plan niżej) | – |

### Plan Fazy 9 (uzgodniony z użytkownikiem 2026-10-16)

- **Pasek menu zostaje minimalny.** Szczegóły są w oknie ustawień (koło zębate).
- **Drzewko folderów z polami wyboru:** rozwijanie aż do pojedynczych plików, trzy stany (zaznaczony, odznaczony,
  częściowo), wczytywanie po rozwinięciu, liczba plików w indeksie, podgląd skutków przed zapisem („z indeksu
  zniknie N plików”). Elementy pomijane zawsze są wyszarzone, z wyjaśnieniem. Pod spodem: istniejące wykluczenia ścieżek.
- **Ignorowane rozszerzenia:** pigułki z ✕, dodawanie przyciskiem ＋ z podpowiedziami filtrowanymi w trakcie pisania
  (znane typy + rozszerzenia obecne w folderach), dowolne nietypowe rozszerzenie. Wpisanie `csv`, `.csv` albo
  `*.csv` i przecinka (także spacji lub Entera) od razu tworzy pigułkę. Domyślnie `.ipynb`, `.csv`, `.tsv`;
  `.json` jako podpowiedź.
- **Zakładka Wydajność:** wykres RAM na żywo (co 1 s przy otwartym oknie): demon i proces indeksujący osobno,
  footprint jak w Monitorze aktywności. Do tego załadowane modele, bieżące zadanie i tempo, szczyt sesji oraz lista
  modeli (nazwa, rewizja, rozmiar, stan).
- **Przełącznik audio i filmów** (obok zdjęć, klatek i transkrypcji).
- **Filmy i nagrania od znacznika czasu:**
  - Spacja: plan A, szybki podgląd w oknie z systemowym `AVPlayerView`, start ~3 s przed wypowiedzią, tekst wypowiedzi
    i przycisk „Otwórz w QuickTime Player”. Systemowy Quick Look nie ma API do ustawienia momentu startu (sprawdzone w SDK).
  - ↩: plan B, QuickTime Player od momentu. Słownik skryptowy QuickTime ma zapisywalną właściwość `current time`;
    wymaga jednorazowej zgody na sterowanie (Automation). Do sprawdzenia na żywo, także dla mkv/webm. Gdy plan B
    zawiedzie, otwiera się plan A.
  - ⌥↩: cały film od początku w domyślnej aplikacji; ⌘↩: Finder. To samo dla nagrań audio i klatek filmów.

Poza zakresem na razie: reranker LightGBM (najpierw zbieramy `search_events`), podsumowania folderów przez LLM,
synchronizacja, chmura, wielu użytkowników.
