# Decyzje i otwarte pytania

Format: ADR (Architecture Decision Record). Każda decyzja ma status, kontekst, decyzję i konsekwencje.
Otwarte pytania są na końcu pliku.

---

## Faza 0: wyniki rozpoznania (2026-10-15)

Środowisko pomiarów: MacBook Pro, Apple M4, 16 GB RAM, macOS 26.6.2.
Python 3.12.15 (uv), torch 2.14.1, transformers 5.19.0, sentence-transformers 6.1.0,
Ollama 0.40.4 (Homebrew), llama.cpp 0.6.0 build 11429 (Homebrew), sqlite-vec 0.1.9, SQLite 3.53.1.
Model: `google/embeddinggemma-2`, rewizja `914f7f89142e33e77833254d9c9b90c3cef7303b` (Apache 2.0, nie jest gated).

Skrypty pomiarowe są w `spikes/phase0/`, a surowe dane i wyniki w `spikes/phase0/data/` (poza gitem).

### Tabela: backend × modalność

| Backend | Modalność | Działa? | Pamięć | Szybkość | Uwagi |
|---|---|---|---|---|---|
| sentence-transformers, MPS, bf16, 270M | tekst | tak | RSS ~1,2 GB, MPS do ~3,4 GB* | 7,8 fragm./s (~560 tok.) | 5/5 w teście poprawności, brak NaN |
| sentence-transformers, MPS, fp32, 270M | tekst | tak | RSS ~2,2 GB, MPS do ~4,5 GB* | 6,3 fragm./s | wolniej, więcej pamięci niż bf16 |
| sentence-transformers, CPU, fp32, 270M | tekst | tak | RSS ~3,1 GB | 5,3 fragm./s | możliwy fallback |
| sentence-transformers, CPU, bf16 | tekst | tak | RSS ~3,0 GB | 0,3 fragm./s | **nie używać** |
| sentence-transformers, MPS, bf16, 570M | tekst + audio | **tak** | RSS ~1,2 GB, MPS ~4,3 GB* (okna 30 s), ~8 GB* (okno 300 s) | audio 300 s w 8,2 s (~36× realtime) | **domyślnie ucina audio po 30 s**, patrz ADR-003 |
| Ollama, `270m-bf16-text` | tekst | tak | runner ~570–720 MB | 12,4 fragm./s | wektory identyczne z ST (cos = 1,000) |
| Ollama, `570m-nvfp4-text-audio` | tekst | tak | runner ~1,1 GB | 11,0 fragm./s | cos vs ST ≈ 0,985 (kwantyzacja) |
| Ollama, `570m-bf16-text-audio` | audio | **częściowo** | runner ~1,25 GB | 120 s → 15 s, 200 s → 33 s | `{"input":[{"audio":"<base64 wav>"}]}`, zgodne z ST (cos ≥ 0,994); **≥ ~230 s runner pada (EOF)** |
| llama.cpp, `ggml-org/embeddinggemma-2-GGUF` | tekst / audio | **nie (na tej wersji)** | – | – | GGUF i mmproj (z wieżą audio) istnieją, ale brew build 11429: `unknown model architecture: 'gemma-embedding2'` |
| **MLX, `mlx-community/embeddinggemma-2-8bit`** | tekst + audio | **tak** | Metal: wagi ~1,18 GB, szczyt ~3,3 GB | tekst 13,2 fragm./s; audio 30 s → 0,37 s, 300 s → 6,1 s | **cos vs ST fp32: tekst 0,9998, audio 0,9998** |
| MLX, `…-8bit`, tylko wieża tekstowa | tekst | tak | Metal: wagi **~275 MB**, szczyt ~1,07 GB (batch 16 × 560 tok.) | 13,2 fragm./s | patrz „RAM” niżej |
| MLX, `…-4bit` | tekst + audio | tak | Metal: wagi ~1,05 GB | jak 8-bit | **dryf**: tekst cos ~0,98, audio 0,957–0,970 |
| MLX, `…-bf16` | tekst + audio | tak | Metal: wagi ~1,42 GB | 13,9 fragm./s | cos ≈ 1,000 |

MLX uruchamiany przez `mlx-vlm` z **niewydanej gałęzi** (commit `3d87e88402f307efbf68e568971aa887ee7d9ed0`,
gałąź `pc/embeddinggemma-2`), MLX 0.32.4. Konwersja `mlx-community` jest społecznościowa (nieoficjalna, nie od Google),
ale wektory 8-bit są numerycznie zgodne z oryginałem. Przetwarzanie wstępne (tokenizer, cechy audio) robimy procesorem
z transformers z oficjalnego repo, czyli dokładnie tak samo jak w ST. Testowane w osobnym venv (`spikes/phase0/.venv-mlx`).

### RAM: ile naprawdę zajmuje model

Liczby z internetu (~191 MB tekst, ~567 MB pełny) dotyczą kwantyzacji na telefonie. Na tym Macu (MLX 8-bit):
- **sam tekst w spoczynku:** wagi ~275 MB + Python/MLX/tokenizer ~100–150 MB, czyli **~400–450 MB**, pod warunkiem że
  ładujemy tylko wagi tekstowe i nie importujemy torch (import transformers+torch to dodatkowe ~330 MB RSS);
- **szczyt przy indeksowaniu tekstu:** ~1,1 GB (zależy od wielkości batcha, można zmniejszyć);
- **z wieżą audio:** wagi ~1,2 GB (wieża audio 300M zostaje w bf16 także w wersjach kwantyzowanych), szczyt ~3,3 GB;
- ładowanie modelu trwa 0,1–0,5 s, więc wieżę audio można ładować tylko na czas zadań audio z kolejki.

### Test: pojedyncze słowo w nagraniu („krowa”)

3 dodatkowe nagrania PL po ~30 s, w których słowo pada raz, mimochodem, w zupełnie innym kontekście
(korek → „jakaś krowa stała na środku jezdni”, zebranie wspólnoty → „ukradł jej rower”, spotkanie z klientem →
„zostawiłem parasol”). Wyszukiwanie wśród 16 nagrań, MLX 8-bit. Pozycja właściwego nagrania:

| Zapytanie | audio, okna 30 s | transkrypcja → wektor | transkrypcja → BM25 dokładne | transkrypcja → BM25 prefiks |
|---|---|---|---|---|
| krowa | 1 | 1 | 1 | 1 |
| krowy na drodze | 1 | 1 | brak | 1 |
| kradzież roweru | 1 | 1 | brak | 1 |
| rower | 2 | 1 | 1 | 1 |
| parasol | 1 | 2 | 1 | 1 |
| zapomniany parasol i deszcz | 2 | 1 | 1 | 1 |

- **H4:** Do szukania pojedynczego wypowiedzianego słowa najpewniejsza jest transkrypcja + FTS z dopasowaniem prefiksu
  (polska odmiana: „krowy” vs „krowa”). Dokładne BM25 zawodzi na odmianie. Sam embedding audio też często trafia,
  ale na 16 nagraniach to mało. Przy tysiącach nagrań pojedyncze słowo w 30 s oknie raczej utonie.
- Wniosek dla architektury: **dla audio potrzebne są oba tory**, embedding audio (znaczenie, dźwięki niewerbalne)
  oraz transkrypcja (słowa, podgląd, BM25), połączone przez RRF.

\* `torch.mps.driver_allocated_memory()` obejmuje cache alokatora MPS, czyli realne szczytowe zajęcie pamięci zunifikowanej.
Da się je ograniczyć przez `torch.mps.empty_cache()` i `PYTORCH_MPS_HIGH_WATERMARK_RATIO`. Do sprawdzenia w Fazie 2.

Transkrypcja (dla porównania): mlx-whisper `large-v3-turbo`, ~14× realtime (718 s audio w 51 s z ładowaniem), szczyt RSS ~1,8 GB.

### Ustalenia, których nie było w dokumentacji

1. **Ucinanie audio po 30 s.** Ekstraktor cech (`Gemma4AudioFeatureExtractor`) ma domyślnie `max_length=480000` próbek
   (30 s przy 16 kHz) i `truncation=True`. Wszystko po 30 s jest po cichu ignorowane: osadzenia nagrań różniących się
   tylko końcówką po 30 s mają cos = 1,00. Karta modelu podaje ~327 s, co osiąga się dopiero przez
   `model[0].processing_kwargs = {"audio": {"max_length": 320 * 16000, "truncation": True}}`.
   Po tej zmianie końcówka faktycznie wpływa na wektor.
2. **Pillow i torchvision są wymagane** nawet przy wyłączonej wieży wizyjnej (`EmbeddingGemma2Processor` ich importuje).
3. **Nie używać float16** (zalecenie z karty modelu). bf16 na MPS działa bez NaN.
4. **FTS5 `remove_diacritics 2` nie zamienia „ł” na „l”.** „lodz” nie znajduje „łódź”, ale „gesla” znajduje „gęślą”.
   Polski tekst trzeba normalizować (ł→l) zarówno przy indeksowaniu, jak i w zapytaniu.
5. **Python 3.13 z python.org nie ma `enable_load_extension`**, więc sqlite-vec na nim nie ruszy. Python 3.12 instalowany przez uv działa.
6. **Ollama łączy się z ollama.com** w tle (log: `model recommendations cache`). Do opisania w PRIVACY.md i do zablokowania lub odrzucenia.
7. Model w HF ma gotowe prompty: `SearchQuery`, `Document` (`title: none | text: `), `CodeRetrieval` i inne.

### Test jakości: audio embedding vs transkrypcja (hipotezy, nie fakty)

Korpus: 13 nagrań syntetycznych (`say`: Zosia PL, Daniel EN). 8 PL i 4 EN po 36–56 s
oraz 1 nagranie PL 159 s z trzema tematami (od 0 s, 59 s i 108 s). 22 zapytania parafrazujące, w tym 2 z dokładnym terminem,
4 międzyjęzykowe i 3 celujące w segmenty długiego nagrania. Ranking na poziomie nagrania (najlepszy fragment).

| Metoda | hit@1 | MRR |
|---|---|---|
| audio, jeden embedding na nagranie (limit 320 s) | 0,91 | 0,955 |
| audio, domyślny limit 30 s | 0,91 | 0,939 |
| audio, okna 30 s / krok 25 s | 0,91 | 0,947 |
| transkrypcja Whisper → embedding tekstu | **1,00** | **1,000** |
| transkrypcja → BM25 (FTS5) | 0,73 | 0,773 |
| RRF(transkrypcja-wektor, BM25) | 0,95 | 0,977 |
| RRF(audio-okna, transkrypcja-wektor, BM25) | 0,95 | 0,977 |
| oryginalny tekst skryptu → embedding (górna granica) | 1,00 | 1,000 |

Lokalizacja segmentu w długim nagraniu: okna audio 3/3, fragmenty transkrypcji 3/3.

Wnioski (hipotezy):
- **H1:** Embedding audio działa zaskakująco dobrze także po polsku i międzyjęzykowo (MRR ~0,95), ale transkrypcja
  z embeddingiem tekstu jest lepsza (1,00) i daje tekst do BM25, podglądu wyników i cytowania.
- **H2:** Domyślny limit 30 s psuje wyszukiwanie późniejszych fragmentów: temat od 108 s spadł na 6. miejsce.
  Okna z nakładką (30 s) rozwiązują to i dają znaczniki czasu.
- **H3:** BM25 na transkrypcji jest słaby międzyjęzykowo (brak trafień) i przy „ORA-01555” (Whisper zapisał „o 01555”).
  RRF z BM25 lekko obniżył wynik względem samego wektora na tym korpusie, choć na kodzie będzie odwrotnie.
- **Zastrzeżenia:** mowa syntetyczna jest dużo łatwiejsza od prawdziwej (szum, akcenty, wielu mówców), próba jest mała
  i jest efekt sufitu. Do powtórzenia na prawdziwych nagraniach w Fazie 4.

### Prywatność bibliotek (pomiar)

- Ładowanie modelu i embedowanie tekstu i audio przez sentence-transformers z
  `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1`:
  **0 prób połączeń** (przechwycone `socket.connect` i `getaddrinfo`, `spikes/phase0/probe_network.py`).
- Bez trybu offline huggingface_hub sprawdza wersję modelu w sieci przy ładowaniu (tego nie mierzyłem, to znane zachowanie biblioteki).
- Ollama: okresowe pobieranie rekomendacji modeli z ollama.com (widoczne w logu serwera).
- mlx-whisper: model pobierany z HF Hub. Po pobraniu działa z `HF_HUB_OFFLINE=1` (do potwierdzenia w Fazie 4).

---

## Faza 2: wyniki (2026-10-15)

Test: `lantern index ~/Studia/Projekt` (22 projekty, 653 pliki tekstu i kodu), MLX 8-bit, tylko wieża tekstowa.

| Pomiar | Wynik |
|---|---|
| zgodność wektorów MLX z referencją ST fp32 | cos ≥ 0,9998 (test `-m model`) |
| pełne indeksowanie | 283 s, 645 plików, 4920 fragmentów, 8 pominiętych (> 2 MB lub binarne), 0 błędów |
| ponowne `index` bez zmian | 0,1 s |
| szczyt pamięci przy indeksowaniu | 2,04 GB (phys_footprint, `/usr/bin/time -l`) |
| demon w spoczynku (model tekstowy załadowany) | ~790 MB, z czego ~337 MB to import torch przez `mlx-vlm` |
| czas zapytania (embedding zapytania + KNN) | 25–29 ms; samo FTS ≤ 1 ms |
| rozmiar bazy | 28 MB |
| ruch sieciowy przy ładowaniu i embedowaniu | 0 prób połączeń (test `-m model`) |

Ustalenia:
1. **Bez limitu pamięci MLX indeksowanie urosło do 13,4 GB** (MLX trzymał pamięć roboczą GPU). `mx.set_memory_limit(3 GB)`
   i `mx.set_cache_limit(256 MB)` obniżyły szczyt do ~2 GB bez utraty szybkości. Limity są ustawiane przy ładowaniu modelu.
2. **Pliki z danymi dominowały koszt** (zrzut modelu ML, duże CSV, lockfile'y: ~1,45 znaku/token, 940 fragmentów
   ucinanych na limicie). Decyzja: lockfile'y wykluczone domyślnie; CSV/TSV i duże pliki z przewagą cyfr indeksowane
   tylko od początku (nagłówek i pierwsze wiersze). Czas indeksowania spadł z 498 s do 283 s.
3. **Łączenie partii z wielu plików nie przyspieszyło** (GPU było już nasycone), ale zostało, bo upraszcza pracę na małych plikach.
4. Linie dłuższe niż okno (zminifikowany kod, JSON notebooków) są cięte. Wcześniej jeden fragment miał 25 tys. znaków.
5. Lista po słowach (OR wszystkich tokenów) łapie słowa pomijalne („jak”, „się”). Do poprawy w Fazie 3.

---

## Faza 3: wyniki (2026-10-15)

| Pomiar | Wynik |
|---|---|
| czas zapytania hybrydowego (embedding + KNN + FTS + RRF + zapis historii) | 29–39 ms |
| migracja istniejącej bazy v1 → v2 | automatyczna; 631 plików przeindeksowanych po zmianie wersji potoku (287 s) |
| szczyt pamięci przy reindeksie | 2,04 GB |
| usunięcie folderu + `compact` | usunięty tekst nie występuje w surowych bajtach pliku bazy (test automatyczny) |

Decyzje:
1. **RRF na poziomie plików** (k = 60, po 50 kandydatów z każdej listy). Plik znaleziony przez obie listy wygrywa,
   a dokładny identyfikator z samej listy słów nadal trafia do wyników. Wynik pokazuje źródło („meaning #1 · keywords #3”).
2. **Słowa pomijalne PL/EN + prefiks rdzenia** zamiast stemmera: słowa ≥ 7 liter tracą 3 ostatnie znaki, 5–6 liter tracą 2,
   minimum 4 znaki („decyzyjnego” → `decyzyjn*`). Identyfikatory i liczby dopasowywane dokładnie.
   Wagi kolumn BM25: tekst 1, ścieżka 1,5, symbol 3.
3. **`search_events`** zapisuje zapytanie i dla każdego wyniku pozycje i wyniki z obu list oraz RRF, bez treści plików.
   `lantern open N` zapisuje wybrany wynik. To dane pod reranker (Treelite/LightGBM nadal poza zakresem).
4. **`pipeline_version` i `model_id` przy pliku** (schemat v2): zmiana zasad dzielenia lub modelu powoduje
   automatyczny reindeks. Zamyka otwarte pytanie 12.
5. **Wymazywanie danych:** `secure_delete`, FTS5 `secure-delete`, a przy `compact` FTS `optimize` + `VACUUM` +
   checkpoint WAL. Automatyczne zagęszczanie po `index`, gdy wolne strony przekraczają 25% pliku.

Obserwacje:
- Krótkie fragmenty (2–3 linie kodu z komentarzem) potrafią wygrać wektorowo z dłuższymi opisami. Kandydat do
  rerankera albo do łączenia bardzo krótkich fragmentów z sąsiadami (do obserwacji).

---

## Faza 4: wyniki (2026-10-15)

Test: 16 plików (WAV, MP3, M4A i film MP4 z polskim nagraniem 2:38 o trzech tematach), ~13 min mowy syntetycznej
(PL i EN), prawdziwe modele: EmbeddingGemma 2 audio (MLX 8-bit) i Whisper large-v3-turbo (MLX).

| Pomiar | Wynik |
|---|---|
| zgodność embeddingu audio MLX z referencją ST fp32 | cos ≥ 0,999 (test `-m model`) |
| Whisper po polsku, offline | poprawna transkrypcja, 0 prób połączeń (test `-m model`) |
| indeksowanie 16 plików (okna audio + transkrypcja) | 74–76 s, 0 błędów, plik wideo bez dźwięku pominięty |
| szczyt pamięci przy indeksowaniu mediów | 3,9 GB (modele audio ładowane po kolei, nie razem) |
| zapytania | „krowa”, „kradzież roweru”, „wymiana klocków hamulcowych”, „how do trees cool cities” → właściwy plik na 1. miejscu; „sadzenie pomidorów w szklarni” → film @ 1:48 (temat zaczyna się w 1:48) |

Decyzje:
1. **Dwa tory dla audio i wideo** (ADR-003): okna audio 30 s / krok 25 s z jawnym limitem procesora oraz transkrypcja
   Whisperem we fragmentach ~20 s. Oba trafiają do tej samej tabeli wektorów (ten sam model, ta sama przestrzeń),
   a transkrypcja także do FTS. Ranking łączy je na poziomie plików.
2. **Dwa etapy kolejki** (`index`, potem `transcribe`): wieża audio EmbeddingGemma (~0,9 GB) i Whisper (~1,8 GB)
   nigdy nie są załadowane jednocześnie. Każdy ładuje się raz na cały etap i jest zwalniany po nim.
3. **Wideo = ścieżka dźwiękowa** (ffmpeg `-map 0:a:0 -vn`). Klatki to Faza 7.
4. **Dekodowanie strumieniowe** okien (pamięć ~1 okno niezależnie od długości). Whisper dostaje całość do 4 h.
5. **Hash z próbek dla plików > 64 MB** (rozmiar + 3 × 4 MB): skan wielogigabajtowych filmów nie czyta całych plików.
6. **Procesor audio z repo `mlx-community`** daje embeddingi zgodne z referencją, więc nie trzeba pobierać oficjalnego
   repo Google (uproszczona instalacja).
7. **Podgląd trafienia przez okno audio** pokazuje transkrypcję z tego samego odcinka czasu.
8. **Wersje potoku per rodzaj pliku** (`PIPELINE_VERSIONS`): zmiana zasad audio nie wymusza reindeksu tekstu.

### Test na zaszumionych nagraniach (`spikes/phase4/noise_eval.py`)

Ten sam korpus (16 nagrań, 28 zapytań) zdegradowany `audiomentations` i ffmpeg, indeksowany prawdziwym potokiem.
hit@1 / MRR:

| Warunek | Ranking Lanterna | Tylko okna audio | Tylko transkrypcja | Tylko słowa | WER Whispera |
|---|---|---|---|---|---|
| czyste | 1,00 / 1,000 | 0,89 / 0,946 | 1,00 / 1,000 | 0,89 / 0,893 | 4% |
| szum różowy 10 dB | 0,96 / 0,982 | 0,75 / 0,849 | 0,96 / 0,982 | 0,89 / 0,893 | 5% |
| szum różowy 0 dB | 1,00 / 1,000 | 0,71 / 0,777 | 1,00 / 1,000 | 0,86 / 0,875 | 14% |
| rozmowy w tle 5 dB | 1,00 / 1,000 | 0,75 / 0,831 | 1,00 / 1,000 | 0,89 / 0,893 | 7% |
| telefon (300–3400 Hz, szum 15 dB, MP3 32 kb/s) | 1,00 / 1,000 | 0,79 / 0,826 | 1,00 / 1,000 | 0,89 / 0,893 | 8% |

Wnioski (hipotezy, mowa syntetyczna + sztuczny szum):
- **Whisper jest bardzo odporny** (WER 4% → 14% przy szumie równym mowie). Tor transkrypcji niesie wyszukiwanie audio.
- **Embedding samego dźwięku słabnie z szumem** (MRR 0,95 → 0,78), ale nadal jest potrzebny bez transkrypcji
  (`--no-transcribe`) i dla treści niewerbalnych.
- **Poprawka fuzji wynikła z tego testu.** Przed nią ranking Lanterna miał 0,93 / 0,958 we wszystkich warunkach,
  czyli gorzej niż sama transkrypcja. Przyczyna: przy zapytaniu w innym języku niż nagranie lista słów łapała inne
  nagranie po jednym przypadkowym wspólnym słowie („ogrodzie”, „rate”), a RRF nagradza obecność na obu listach.
  Teraz trafienie po słowach waży w RRF tyle, jaką część słów zapytania pokrywa, a poniżej 50% nie liczy się wcale
  (`MIN_KEYWORD_COVERAGE`). Zapytania jednowyrazowe i identyfikatory mają pełne pokrycie, więc dokładne trafienia nie tracą.
  Wyszukiwanie w kodzie i dokumentach na `~/Studia/Projekt` bez pogorszenia.
- Prawdziwe nagrania (pogłos, wielu mówców naraz, akcenty) nadal są otwartym pytaniem 14. Pogłosu nie testowałem
  (`RoomSimulator` wymaga `pyroomacoustics`).

---

## Faza 5: wyniki (2026-10-15)

| Pomiar | Wynik |
|---|---|
| demon w spoczynku (model tekstowy załadowany) | **~455–490 MB** (wcześniej ~790 MB) |
| wyszukiwanie przez demona | 19–89 ms na zapytanie; cała komenda `lantern search` z CLI 40–110 ms |
| indeksowanie 42 plików (kod, tekst, nagranie, film) po `approve` | 51 s, 0 błędów; demon przez cały czas 490 MB, proces indeksujący do 3,3 GB, potem kończy się |
| testy demona | prawdziwy socket, proces roboczy, FSEvents, tryb ask/auto, pauza, bateria, uprawnienia |

Decyzje:
1. **ADR-004: indeksowanie w osobnym procesie.** Demon trzyma tylko model tekstowy (wyszukiwanie). Proces roboczy
   (`python -m lantern.service.worker`, priorytet tła `PRIO_DARWIN_BG`) startuje, gdy jest zatwierdzona praca, i kończy
   się po opróżnieniu kolejki. Wieża audio, Whisper i procesor audio (który importuje torch) nigdy nie zostają w demonie.
   Wyszukiwanie nie czeka na indeksowanie. Pauza to SIGTERM: bieżący plik jest kończony, reszta wraca do kolejki.
2. **Ładowanie modelu bez torch** (`embedding/mlx_loader.py`): pakiety `mlx_vlm`, `models/gemma4`, `models/qwen3_5`
   zastąpione pustymi pakietami zastępczymi, żeby nie wykonywać ich `__init__` (to one ciągną transformers → torch).
   Zgodność wektorów z referencją bez zmian (testy `-m model`). Zamyka otwarte pytanie 13.
3. **MLX i wątki:** strumienie GPU są przypisane do wątku. Model jest ładowany i wywoływany w jednym dedykowanym wątku
   demona (zapytania z wielu wątków API są do niego kolejkowane). Błąd wyszedł dopiero na żywym demonie
   („There is no Stream(gpu, 0) in current thread”); jest test z prawdziwym modelem w układzie jak produkcja.
4. **Tryb `ask`:** po starcie (i po zdarzeniach obserwatora, gdy nic nie jest indeksowane) nowe i zmienione pliki czekają
   na zgodę. Zmiany wykryte w trakcie zatwierdzonej rundy są do niej dołączane. Usunięcia są stosowane od razu.
   Po restarcie demon pyta także o pliki pozostawione w kolejce przez poprzednie uruchomienie.
5. **API:** JSON w liniach przez socket Unix (`0600`, katalog danych `0700`), jedno zapytanie na połączenie: proste
   dla CLI i dla Swift (Network.framework). Gdy demon działa, CLI deleguje do niego zapisy, żeby nigdy dwa procesy
   nie indeksowały naraz z dwoma modelami w pamięci.
6. **Pauza na baterii / temperaturze** przez `pmset -g batt` i `pmset -g therm` (co 30 s, bez dodatkowych zależności).

Znalezione na żywo i poprawione: `chmod 0700` na katalogu nadrzędnym socketu (przy `LANTERN_SOCKET=/tmp/...` to byłby
systemowy `/tmp`); teraz uprawnienia dostaje tylko katalog danych Lanterna.

---

## Wstępny test wyszukiwania zdjęć (przed Fazą 7, 2026-10-15)

`spikes/phase7/image_eval.py`. Flickr30k, 1000 zdjęć z 5 opisami angielskimi każde. Zapytania: 12 pojedynczych pojęć
(EN i PL, np. „dog” / „pies”; zdjęcie trafne, gdy któryś opis wspomina pojęcie), wszystkie 5000 opisów EN oraz 25 opisów
przetłumaczonych na polski. EmbeddingGemma 2: wieża wizyjna MLX (zgodność z ST fp32: cos 0,9999). SigLIP 2:
`google/siglip2-base-patch16-256` (transformers, MPS, fp32).

| | EmbeddingGemma 2 | SigLIP 2 base |
|---|---|---|
| „dog”, psy w top 10 | 10/10 | 10/10 |
| „pies”, psy w top 10 | 4/10 (gołe słowo) → **10/10** z kontekstem („pies na zdjęciu”) | 10/10 |
| pojedyncze pojęcia EN, mAP | 0,862 | 0,856 |
| pojedyncze pojęcia PL, mAP | 0,649 (gołe słowo) → **0,819** („… na zdjęciu”) | 0,720 |
| pełne opisy EN, R@1 / R@5 | **0,862 / 0,974** | 0,794 / 0,946 |
| pełne opisy PL, R@1 / R@5 | **0,88 / 1,00** | 0,64 / 0,92 |
| szybkość embeddingu zdjęć (M4) | 2 zdj./s (280 tokenów obrazu) | **46 zdj./s** |
| szczyt pamięci | 1,3 GB | ~1,5 GB (torch) |

Wnioski (hipotezy):
- **Słabość „pies” to dwuznaczność, nie brak polskiego:** po angielsku „pies” to placki. Jednowyrazowe zapytanie bez
  kontekstu językowego jest czytane jako angielskie. Dla toru zdjęć Lantern powinien dodawać kontekst
  (np. „{zapytanie} na zdjęciu” / „photo of {query}”) albo osobny prompt. Do dobrania w Fazie 7.
- **EmbeddingGemma 2 lepiej rozumie opisy całymi zdaniami** (zwłaszcza po polsku), **SigLIP 2 jest 23× szybszy.**
  Przy dużych bibliotekach zdjęć szybkość decyduje: 20 tys. zdjęć to ~2,8 h (EG2) vs ~7 min (SigLIP 2).
- Do sprawdzenia w Fazie 7: EG2 z mniejszym budżetem tokenów obrazu (70–140 zamiast 280; szybciej, jakość?),
  SigLIP 2 so400m (lepszy, wolniejszy), wersje MLX SigLIP, oraz HEIC i zdjęcia z telefonu.

---

## Etap „cały komputer” (po Fazie 5, 2026-10-15)

Suchy przebieg na prawdziwym katalogu domowym (`lantern index ~ --dry-run`, tylko metadane, 5,5 s):
5789 plików tekstu i kodu (119 MB), 3 pliki audio/wideo, 405 zdjęć, 4 elementy tylko w iCloud. Po dwóch przykładowych
wykluczeniach w `~/Downloads` (rozpakowane źródła biblioteki, zbiór JSON): ~1900 plików, szacunek pierwszego
indeksowania ~1,2 h w tle.

Decyzje:
1. **Polityka katalogu domowego:** pomijany `~/Library` (z wyjątkiem iCloud Drive), `~/Applications`, biblioteka
   muzyki Apple, pakiety macOS (`*.app`, `*.photoslibrary`, iWork, obrazy VM, `.dmg`…) i ukryte katalogi/pliki
   (z wyjątkiem `.github`). Biurko i Dokumenty w iCloud Drive to dowiązania do `~/Desktop` i `~/Documents`, a skaner nie
   podąża za dowiązaniami, więc nie ma duplikatów.
2. **iCloud „tylko w chmurze”** (`SF_DATALESS`): pomijane, bo odczyt pobrałby plik. Zaindeksowany plik przeniesiony
   do chmury zostaje w indeksie (nadal istnieje; otwarcie go pobierze).
3. **Brak uprawnień** (prywatność macOS, chmod): folder pomijany, a jego wpisy **chronione przed usunięciem**.
   Wcześniej `os.walk` pomijał taki folder po cichu, co usunęłoby jego pliki z indeksu.
4. **Własne wykluczenia** (`lantern exclude add/remove/list`, lista `exclude` w `config.toml`): ścieżki bezwzględne
   (`~` dozwolone) albo wzorce jak w `.gitignore`. Zamyka otwarte pytanie 16.
5. **Skala (`spikes/scale/scale_eval.py`):** zostajemy przy pełnych wektorach `float[768]`.

| Fragmentów | float[768] | int8[768] | bit[768] + doprecyzowanie top 200 | float[256] |
|---|---|---|---|---|
| 100 tys. | 70 ms | 55 ms | 53 ms | 21 ms |
| 300 tys. | 220 ms | 168 ms | 124 ms | 67 ms |
| 1 mln | 743 ms | 558 ms | 337 ms | 229 ms |
| zgodność top 10 z dokładnym (projekty / zdjęcia) | 100% | 95% / 95% | 97% / 99,8% | 78% / 80% |

   Matryoshka 256 traci wyraźnie (wbrew „prawie bez strat” z karty modelu) i odpada. Bity z doprecyzowaniem to plan
   na indeksy > ~300 tys. fragmentów (dodatkowa tabela `bit[768]` obok `float[768]`, wypełniana jednym zapytaniem SQL
   przez `vec_quantize_binary`). Dziś niepotrzebne: katalog domowy to ~kilkadziesiąt tysięcy fragmentów (~20–50 ms).

---

## Faza 6: wyniki (2026-10-15)

Aplikacja Swift (`app/`, Swift Package): pasek menu (MenuBarExtra), panel wyszukiwania pod ⌥Space, okno ustawień,
powiadomienia z akcjami. Sprawdzona na prawdziwych zrzutach okien (`screencapture -l`) na izolowanym indeksie.

Decyzje (uzgodnione z użytkownikiem):
1. **Skrót ⌥Space** (Carbon `RegisterEventHotKey`: działa w tle, bez uprawnień Dostępności). Spotlight zostaje pod ⌘Space.
2. **Aplikacja uruchamia demona jako proces potomny**, więc zgody macOS (Biurko, Dokumenty, Pobrane, iCloud Drive)
   są przypisane do Lantern.app. Działający już demon (LaunchAgent) jest używany zamiast drugiego.
   `LANTERN_EXIT_WITH_PARENT=1`: demon kończy się, gdy aplikacja zniknie (wykryty na żywo osierocony demon po wymuszonym zamknięciu).
3. **Podpis „Apple Development”** (stały podpis = zgody przetrwają przebudowy). Na tym Macu brakowało certyfikatu
   pośredniego Apple WWDR G3 (był tylko wygasły G1), przez co certyfikat był „nieważny”; dodany do pęku kluczy.
4. **Panel bez ramki** (`.borderless`, przezroczyste tło, rozmiar z SwiftUI, górna krawędź przypięta). Pierwsza wersja
   (`.titled`) miała przezroczysty pas nad panelem i systemowy cień/ramkę wokół prostokąta okna („dziwna obwódka”
   zauważona przez użytkownika). Na macOS 26 systemowy cień okna wyłączony, bo Liquid Glass rysuje własny.
5. **Własne okno ustawień** zamiast sceny `Settings` SwiftUI, która w aplikacji bez Docka nie otwierała się niezawodnie.
6. Animacje minimalistyczne: płynne pojawianie się panelu (0,16 s), przesuwające się zaznaczenie wyników
   (`matchedGeometryEffect`), przejścia karty zgody, liczniki `numericText`, puls ikony podczas indeksowania.
7. Budowanie przez toolchain Xcode 26 (`DEVELOPER_DIR`), bo Command Line Tools mają SDK 15.4 bez Liquid Glass.

---

## Faza 7: wybór modelu do zdjęć (2026-10-16)

`spikes/phase7/image_bench.py` + `run.sh`: 10 konfiguracji, każda w osobnym procesie, ten sam zbiór (Flickr30k 1k,
12 pojęć EN/PL, 5000 opisów EN, 25 opisów PL). „PL+ctx” = zapytanie „{słowo} na zdjęciu”. Pamięć: „spoczynek” po
załadowaniu modelu (zawyżona o ~0,5 GB zdjęć testowych trzymanych w RAM i import torch w procesorze), „szczyt” =
peak memory footprint procesu.

| Konfiguracja | zdj./s | spoczynek | szczyt | mAP EN | mAP PL | mAP PL+ctx | R@1 EN | R@1 PL | „pies” w top 10 (goły / z kontekstem) |
|---|---|---|---|---|---|---|---|---|---|
| EG2 8-bit, 70 tok. | 9,5 | 2,1 GB | 5,9 GB | 0,811 | 0,611 | 0,765 | 0,812 | 0,80 | 6 / 10 |
| **EG2 8-bit, 140 tok.** | **4,4** | 2,4 GB | 5,8 GB | 0,835 | 0,637 | **0,788** | **0,847** | **0,84** | 6 / 10 |
| EG2 8-bit, 280 tok. | 2,0 | 2,8 GB | 5,8 GB | 0,862 | 0,648 | 0,819 | 0,862 | 0,88 | 4 / 10 |
| EG2 8-bit, 560 tok. | 0,8 | 2,6 GB | 5,8 GB | 0,866 | 0,661 | 0,824 | 0,866 | 0,88 | 3 / 10 |
| EG2 4-bit, 140 tok. | 4,6 | 2,3 GB | 5,9 GB | 0,818 | 0,616 | 0,741 | 0,842 | 0,76 | 4 / 10 |
| EG2 bf16, 140 tok. | 4,5 | 2,6 GB | 5,8 GB | 0,834 | 0,631 | 0,785 | 0,845 | 0,84 | 6 / 10 |
| SigLIP 2 base fp32 | 48,4 | 2,7 GB | 3,7 GB | 0,856 | 0,720 | 0,705 | 0,794 | 0,64 | 10 / 10 |
| SigLIP 2 base fp16 | **57,7** | 2,1 GB | **2,2 GB** | 0,856 | 0,720 | 0,704 | 0,794 | 0,64 | 10 / 10 |
| SigLIP 2 large fp16 | 18,0 | 2,9 GB | 4,1 GB | 0,847 | 0,675 | 0,677 | 0,818 | 0,76 | 9 / 10 |
| SigLIP 2 so400m fp16 | 5,7 | 3,6 GB | 4,7 GB | 0,858 | 0,739 | 0,682 | 0,833 | 0,88 | 9 / 10 |

Wnioski:
1. **Kwantyzacja EG2 nie wpływa na szybkość zdjęć** (wieża wizyjna zawsze bf16); 4-bit wyraźnie traci jakość,
   bf16 ≈ 8-bit. Zostaje 8-bit (ten sam model co tekst i audio).
2. **Budżet tokenów to główne pokrętło:** 140 tokenów = 2,2× szybciej niż domyślne 280 przy niewielkiej stracie
   (mAP PL+ctx 0,788 vs 0,819; R@1 PL 0,84 vs 0,88). 70 tokenów = 4,8× szybciej, nadal lepiej niż SigLIP po polsku.
3. **SigLIP 2 base fp16 jest 13× szybszy** i ma najmniejszy szczyt pamięci, lepiej łapie gołe jednowyrazowe zapytania
   (także „pies”), ale słabo rozumie polskie opisy zdaniem (R@1 PL 0,64 vs 0,84) i wymagałby drugiego modelu w demonie
   do zapytań (osobna przestrzeń wektorów, +RAM na stałe). so400m dorównuje EG2 po polsku, ale jest wolny i ciężki.
4. **Rekomendacja:** EG2 8-bit, **140 tokenów/zdjęcie**, zapytania do zdjęć z kontekstem językowym. Zero dodatkowej
   pamięci w spoczynku (zapytania liczy ten sam model tekstowy, który demon już trzyma), jedna przestrzeń wektorów,
   lepszy polski. 405 zdjęć użytkownika ≈ 1,5 min, 20 tys. ≈ 75 min w tle. SigLIP 2 base fp16 jako możliwy „tryb szybki”
   dla bardzo dużych bibliotek (otwarte pytanie).
5. Szczyt ~5,8 GB dla EG2 w tym teście wynika z limitu MLX ustawionego na 4 GB + zdjęć w RAM; w procesie roboczym
   Lanterna limit to 3 GB, a zdjęcia są przetwarzane po jednym. Do zmierzenia przy wdrożeniu.

### Para SigLIP 2 + EmbeddingGemma 2 (`fusion_eval.py`, `pair_cost.py`)

Jakość (A = SigLIP 2 base fp16, B = EG2 8-bit 140 tok.; PL pojedyncze słowa: B z kontekstem, A gołe słowo):

| Strategia | mAP EN | mAP PL | R@1 EN | R@5 EN | R@1 PL |
|---|---|---|---|---|---|
| tylko A | 0,856 | 0,720 | 0,794 | 0,946 | 0,64 |
| tylko B | 0,835 | 0,788 | 0,847 | 0,968 | 0,84 |
| **z-score (znormalizowane wyniki, suma)** | **0,872** | **0,814** | **0,859** | **0,973** | **0,88** |
| RRF po równo | 0,873 | 0,807 | 0,833 | 0,969 | 0,84 |
| RRF z wagą od długości zapytania | 0,873 | 0,779 | 0,847 | 0,972 | 0,80 |
| kaskada: A top 100 → rerank B | 0,812 | 0,746 | 0,847 | 0,969 | 0,84 |

Koszt w demonie (jeden proces, jak demon): EG2 tekst 387 MB → + SigLIP 2 base text encoder fp16 (torch) **2091 MB**
(+1,7 GB na stałe, w tym ~0,23 GB sam import torch). Zimne ładowanie SigLIP na żądanie: ~4,7 s (import torch 2,5 s +
model 2,2 s), więc nie nadaje się do ładowania przy otwarciu panelu. Zapytanie (ciepło): EG2 9,5 ms, SigLIP 8,3 ms,
oba 16,9 ms. Indeksowanie pary: ~4,1 zdj./s (SigLIP dokłada ~0,02 s/zdjęcie do 0,23 s EG2), dwa wektory na zdjęcie.

Wnioski (hipotezy; mały zbiór: 12 pojęć, 25 opisów PL; 5000 opisów EN najbardziej wiarygodne):
- Para z łączeniem z-score daje +1,2–4 pkt we wszystkich miarach. Kaskada i waga od długości zapytania nie pomagają.
- Oba modele odpowiadają w < 20 ms łącznie, więc wyniki można łączyć przed wyświetleniem: nie ma „spóźnionych” wyników.
- Koszt: +1,7 GB RAM w demonie (torch). Do sprawdzenia: SigLIP 2 w MLX 8-bit (`mlx-community/siglip2-base-patch16-224-8bit`),
  który mógłby obniżyć koszt pary do kilkuset MB bez torch.

### SigLIP 2 w MLX 8-bit do zapytań (`mlx_siglip.py`)

`mlx-community/siglip2-base-patch16-224-8bit` ładowany kodem `mlx_vlm.models.siglip` (model tylko tekstowy) przez te
same pakiety zastępcze co EG2: **bez torch**. Wektory zdjęć z oryginalnego `google/siglip2-base-patch16-224` (fp16, torch
w procesie roboczym, 75,9 zdj./s).

- Zgodność wektorów zapytań MLX 8-bit z torch fp32: cos min 0,9994, średnio 1,0000.
- Koszt w demonie (jak demon): EG2 tekst 387 MB → + SigLIP tekst MLX **726 MB (+339 MB)**, ładowanie **0,33 s**;
  zapytanie: EG2 8,4 ms + SigLIP 6,0 ms = 14,3 ms. Po serii zapytań 889 MB (cache MLX, ograniczany limitami).
- Jakość pary (z-score) SigLIP 224 MLX + EG2 140: mAP EN 0,866, mAP PL 0,814, R@1 EN 0,858, R@1 PL 0,84
  (samo EG2: 0,835 / 0,788 / 0,847 / 0,84) — praktycznie jak para z torch 256 px, przy ~5× mniejszym koszcie RAM.

Decyzja kierunkowa do Fazy 7: **para EG2 (140 tok.) + SigLIP 2 base 224**, łączenie z-score dla zdjęć; SigLIP tekst
w demonie ładowany na żądanie (przy otwarciu panelu / pierwszym zapytaniu, 0,33 s) i zwalniany po bezczynności, więc
w spoczynku demon zostaje przy ~390–450 MB. Wektory zdjęć obu modeli liczy proces roboczy.

---

## ADR-001: Backend embeddingów, MLX 8-bit (mlx-vlm), z fallbackiem sentence-transformers

**Status:** zaakceptowana kierunkowo (2026-10-15), szczegóły do potwierdzenia w Fazie 2

**Kontekst:** Potrzebne są tekst, kod i audio w jednej przestrzeni wektorowej, lokalnie i offline, przy niskim zużyciu RAM
na 16 GB. Użytkownik preferuje natywny MLX, jeśli jest port. Pomiar: MLX 8-bit jest ~1,7× szybszy od ST na tekście,
~1,3× na audio, a wektory są zgodne z oryginałem (cos 0,9998). Ollama ma audio, ale pada na długich oknach.
llama.cpp nie działa z obecną wersją.

**Decyzja:**
- Backend główny: **MLX** (`mlx-vlm`, przypięty commit), wagi `mlx-community/embeddinggemma-2-8bit` przypięte do rewizji HF.
- Wieża tekstowa ładowana stale (~0,4–0,5 GB w spoczynku), wieża audio **ładowana na żądanie** na czas zadań audio i zwalniana po nich.
- Docelowo przetwarzanie wstępne bez torch (tokenizer z `tokenizers`, cechy mel w numpy), żeby oszczędzić ~330 MB.
  Do tego czasu używamy procesora z transformers.
- Backend zapasowy za tym samym interfejsem: `sentence-transformers` (MPS, bf16), np. gdyby gałąź mlx-vlm się rozjechała.
- Embedder za interfejsem (`embed_text`, `embed_audio`, `model_info`).

**Konsekwencje:**
- Ryzyko: mlx-vlm z niewydanej gałęzi i społeczna konwersja wag. Łagodzimy to przypięciem commitu i rewizji oraz testem
  zgodności z ST (cos ≥ 0,999 na zestawie kontrolnym) w testach integracyjnych.
- Wektor zapisywany z `model_id` = nazwa wag, rewizja, backend, kwantyzacja i wymiar. MLX-8bit i ST-bf16 to **różne**
  `model_id`, mimo cos 0,9998. Zmiana oznacza reindeks.

## ADR-002: Magazyn, SQLite + FTS5 + sqlite-vec w jednym pliku

**Status:** proponowana

**Kontekst:** Potrzebne są metadane, pełnotekstowe wyszukiwanie słów, wektory, transakcyjne usuwanie i rzeczywiste
kasowanie danych z dysku.

**Decyzja:** Jeden plik SQLite z tabelami `vec0` (sqlite-vec, cosinus) i FTS5. LanceDB odrzucone dla v1.

**Konsekwencje:** Usunięcie pliku to jedna transakcja (fragmenty + FTS + wektory). VACUUM faktycznie zmniejsza plik
(pomiar: 305 MB → 156 MB po usunięciu połowy). KNN brute-force: 78 ms dla 100 tys. × 768d, więc wystarczy do kilkuset
tysięcy fragmentów. Powyżej tego: Matryoshka 256d albo kwantyzacja int8 (otwarte pytanie). Wymaga Pythona z
`enable_load_extension` (uv).

## ADR-003: Audio, okna z nakładką i jawny limit długości

**Status:** zaakceptowana i zrealizowana (Faza 4)

**Decyzja:** ffmpeg do 16 kHz mono, potem okna **30 s z krokiem 25 s** (wstępnie, do weryfikacji w Fazie 4). Zawsze ustawiamy
jawnie `processing_kwargs["audio"]["max_length"]` na długość okna, nigdy nie polegamy na domyślnym limicie.
Długie okna (300 s) są możliwe, ale zabierają ~8 GB pamięci MPS i dają gorszą lokalizację w czasie.
Transkrypcja (mlx-whisper large-v3-turbo) to **drugi tor** wyszukiwania audio w Fazie 4 (H1, H4): tekst do FTS z
dopasowaniem prefiksu, do podglądu i do embeddingu tekstowego. Whisper ładowany tylko na czas transkrypcji (~1,8 GB),
nigdy równocześnie z wieżą audio EmbeddingGemma.

---

## Otwarte pytania

1. Transkrypcja jako drugi tor audio jest przyjęta kierunkowo (ADR-003). Otwarte zostaje, czy przy bardzo dużej liczbie nagrań nie robić jej leniwie (najpierw embedding audio, potem transkrypcja w tle).
8. Polska odmiana w FTS: naiwny prefiks („krow*”) zadziałał w teście, ale daje fałszywe trafienia przy krótkich rdzeniach. Alternatywa: stemmer lub lematyzator dla PL (np. Morfologik/Stempel). Do decyzji w Fazie 3.
9. Stabilność gałęzi `mlx-vlm` z EmbeddingGemma 2: śledzić, kiedy trafi do wydania na PyPI.
2. Optymalna długość okna audio (30 / 60 / 120 s) na prawdziwych nagraniach.
3. Limit pamięci MPS w demonie (`PYTORCH_MPS_HIGH_WATERMARK_RATIO`, `empty_cache`), żeby indeksowanie nie zabierało RAM innym aplikacjom na 16 GB.
4. Wymiar wektorów: 768 vs Matryoshka 256 (karta modelu podaje „prawie bez strat”), co dałoby 3× mniejszą bazę.
5. llama.cpp: nowsza wersja niż build 11429 może obsługiwać `gemma-embedding2` (GGUF jest od ggml-org, nieoficjalny). Niesprawdzone.
6. Ollama audio: przyczyna awarii przy ≥ ~230 s (pamięć? limit runnera MLX?). Nieistotne przy oknach 30 s.
7. Nieoficjalny port Swift MLX (`Obscyra-app/EmbeddingGemma2Swift`) jako potencjalny backend dla aplikacji Swift w Fazie 6.
10. Integracja z wyszukiwaniem systemowym (Cmd+Space). Spotlight nie pozwala podpiąć własnego rankingu znaczeniowego
    (Core Spotlight dopasowuje po swoich słowach kluczowych). **Kierunek przyjęty przez użytkownika (2026-10-15):** własny
    panel (NSPanel + SwiftUI) wyglądający jak Spotlight, pod globalnym skrótem. Użytkownik może wyłączyć skrót Spotlight
    w Ustawieniach systemowych → Klawiatura → Skróty i przypisać ⌘Space Lanternowi (tak robią Raycast i Alfred).
    W panelu ewentualne prefiksy poleceń (np. „lantern search: kot w butach”, „audio:”, „kod:”). Akcja App Intents
    w Spotlight jako dodatek. Do weryfikacji w Fazie 6. Wymóg dla demona już teraz: zapytanie w ~100–200 ms (spełnione: 25–29 ms).
11. Wyszukiwanie po obrazie: zdjęcia oraz klatki filmów (ffmpeg → klatki → wieża wizyjna, +~0,35 GB na czas zadań).
    Model to obsługuje, ale jakość obrazów i wideo w benchmarkach jest wyraźnie niższa niż tekstu, a na tym Macu
    nic nie mierzyłem. **Użytkownik chce tej opcji (2026-10-15):** Faza 7, poprzedzona krótkim testem. Dźwięk z filmów
    (ffmpeg, znaczniki czasu wypowiedzi) wchodzi do Fazy 4.
    Szkic potoku klatek (propozycje użytkownika, do weryfikacji w teście Fazy 7):
    - wykrywanie zmian scen w ffmpeg (`select='gt(scene,T)'` + `showinfo` dla znaczników czasu), z dolnym limitem
      częstotliwości (np. min. 1 klatka na 30–60 s, bo wykrywanie scen gubi wolne zmiany, np. wykład ze slajdami),
    - deduplikacja podobnych klatek hashem percepcyjnym (dHash/pHash, odległość Hamminga poniżej progu) **przed**
      embeddingiem; zachowana klatka reprezentuje odcinek [start, koniec] aż do następnej zachowanej,
    - zysk: mniej obliczeń GPU i mniejsza baza. Szczyt RAM się nie zmienia, bo zależy od wielkości partii, a nie od liczby klatek,
    - progi (scena T, Hamming) dobrać w teście na kilku typach nagrań (film, wykład ze slajdami, nagranie z telefonu).
    Model dla obrazów: wstępny test zrobiony (sekcja „Wstępny test wyszukiwania zdjęć”); decyzja w Fazie 7 między
    wieżą wizyjną EmbeddingGemma 2 (lepsza na opisach, 2 zdj./s) a **SigLIP 2** (23× szybszy, osobna przestrzeń). SigLIP oznacza osobny `model_id`
    i osobną tabelę wektorów, łączoną z resztą przez RRF na poziomie plików. Wektorów nie mieszamy.
12. ~~Wersjonowanie chunkera~~ — rozwiązane w Fazie 3 (`pipeline_version`, `model_id`).
13. ~~Pamięć w spoczynku~~ — rozwiązane w Fazie 5 (~490 MB: loader bez torch + indeksowanie w osobnym procesie).
    Opcja na przyszłość: zwalnianie modelu po długiej bezczynności (~50–100 MB, pierwsze zapytanie +0,5–1 s).
14. Jakość wyszukiwania audio na prawdziwych nagraniach użytkownika (szum, wielu mówców, akcenty): potrzebny zestaw
    kilku nagrań z zapytaniami. Do zrobienia, gdy użytkownik je udostępni.
15. Fuzja ważona pokryciem słów działa na testach; dalsze strojenie (np. wagi list) albo reranker, gdy zbierze się historia `search_events`.
16. ~~Własne wykluczenia~~ — rozwiązane (`lantern exclude`).
17. Strumień zdarzeń z API zamiast odpytywania `status` co 2 s (aplikacja działa na odpytywaniu; koszt pomijalny, ale do rozważenia).
19. Odtwarzanie nagrania/filmu od znacznika czasu po otwarciu wyniku (dziś: otwarcie pliku + znacznik w wyniku).
20. Zmiana skrótu ⌥Space w ustawieniach (dziś stały).
18. Wyszukiwanie bitowe z doprecyzowaniem, gdy indeks przekroczy ~300 tys. fragmentów (zmierzone, gotowe do wdrożenia).
21. ~~Para modeli do zdjęć~~ — sprawdzone: SigLIP 2 MLX 8-bit kosztuje +339 MB tylko po załadowaniu (0,33 s, na żądanie).
22. Wyszukiwanie fraz w nagraniach: nakładanie się fragmentów transkrypcji, znaczniki czasu słów (Whisper word timestamps),
    tolerancja błędów ASR w FTS (indeks trigramowy FTS5 dla transkrypcji).
23. Zestaw testowy na danych użytkownika: 50–100 zapytań z ręcznie oznaczonymi odpowiedziami (krótkie, długie, polskie,
    slang), recall@k i MRR; test termiczny 20+ min; pełna konfiguracja każdego przebiegu zapisywana obok wyników.
