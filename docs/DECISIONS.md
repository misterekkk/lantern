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
    Model dla obrazów do porównania w teście: wieża wizyjna EmbeddingGemma 2 (jedna przestrzeń z tekstem i audio)
    vs **SigLIP 2** (osobny model obraz–tekst, wielojęzyczny, wersje MLX dostępne). SigLIP oznacza osobny `model_id`
    i osobną tabelę wektorów, łączoną z resztą przez RRF na poziomie plików. Wektorów nie mieszamy.
12. ~~Wersjonowanie chunkera~~ — rozwiązane w Fazie 3 (`pipeline_version`, `model_id`).
13. Pamięć w spoczynku ~790 MB: usunięcie importu torch (własny loader zamiast `mlx_vlm.__init__`) da ~450 MB.
    Alternatywa: zwalnianie modelu po bezczynności (~50–100 MB, pierwsze zapytanie +0,5–2,5 s). Decyzja w Fazie 5.
14. Jakość wyszukiwania audio na prawdziwych nagraniach użytkownika (szum, wielu mówców, akcenty): potrzebny zestaw
    kilku nagrań z zapytaniami. Do zrobienia, gdy użytkownik je udostępni.
15. Fuzja ważona pokryciem słów działa na testach; dalsze strojenie (np. wagi list) albo reranker, gdy zbierze się historia `search_events`.
