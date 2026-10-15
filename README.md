# Lantern

Lokalna aplikacja na macOS do przeszukiwania plików **po znaczeniu**, a nie tylko po słowach: tekst, kod, dokumenty
i nagrania audio. Silnikiem jest otwarty model embeddingowy **EmbeddingGemma 2** (Google DeepMind, Apache 2.0),
uruchamiany natywnie na Apple Silicon przez MLX.

> **Stan projektu: Faza 2 (rdzeń, CLI).** Działa indeksowanie folderów z tekstem i kodem oraz wyszukiwanie
> po znaczeniu i po słowach (dwie osobne listy) z linii poleceń. Jeszcze nie ma: połączonego rankingu (Faza 3),
> PDF i audio (Faza 4), demona działającego w tle (Faza 5) ani aplikacji (Faza 6).
> To README opisuje tylko to, co faktycznie działa, a dalsze funkcje oznacza fazą, w której się pojawią.

## Użycie

```bash
uv run lantern index ~/Projekty            # pierwszy raz: skan + indeksowanie; kolejne: tylko zmiany
uv run lantern search "optymalizacja drzewa decyzyjnego"
uv run lantern search "backoff_wrapper" -n 5
uv run lantern status
```

- `index` porównuje folder z indeksem: nowe i zmienione pliki indeksuje, przeniesione rozpoznaje po treści (bez
  ponownego liczenia), usunięte usuwa z indeksu razem z fragmentami i wektorami.
- Jeśli folder jest niedostępny (odłączony dysk, udział sieciowy), nic nie jest usuwane. Jeśli zniknęłaby ponad połowa
  plików folderu, `index` odmawia i wymaga `--force`.
- `search` pokazuje dwie listy: **po znaczeniu** (EmbeddingGemma 2) i **po słowach** (dokładne nazwy, komunikaty błędów),
  z czasem odpowiedzi. Od Fazy 3 będą połączone w jeden ranking.
- Indeksowane typy: kod (Python, JS/TS, Go, Rust, Java, Kotlin, Swift, C/C++, C#, Ruby, PHP i inne, dzielony na
  funkcje i klasy) oraz tekst (Markdown, txt, JSON, YAML, HTML, notebooki itd.). Pliki ponad 2 MB i binarne są pomijane.

## Prywatność w skrócie

- Wszystko działa **lokalnie i offline**, po jednorazowym pobraniu modelu. Brak telemetrii i zewnętrznych API.
- Lantern **tylko czyta** indeksowane pliki. Nigdy ich nie modyfikuje, nie przenosi ani nie usuwa.
- Indeks to jeden plik SQLite w `~/Library/Application Support/Lantern/` (poza iCloud).
- Szczegóły: [docs/PRIVACY.md](docs/PRIVACY.md).

## Wymagania

| Co | Po co | Instalacja |
|---|---|---|
| Mac z Apple Silicon (testowane: M4, 16 GB) | MLX działa tylko na Apple Silicon | – |
| Xcode Command Line Tools | kompilator i git; później aplikacja Swift (Faza 6, wtedy pełny Xcode) | `xcode-select --install` |
| Homebrew | menedżer pakietów | https://brew.sh |
| uv | środowisko Pythona i zależności | `brew install uv` |
| Python 3.12 | język demona; **instalowany przez uv**, nie systemowy | automatycznie przez `uv sync` |
| ffmpeg | dekodowanie audio do 16 kHz mono (Faza 4) | `brew install ffmpeg` |

Instalowane automatycznie przez `uv sync` do `.venv` projektu (nic globalnie):
MLX i `mlx-vlm` (backend modelu, przypięty commit gałęzi z EmbeddingGemma 2), `tokenizers`, `transformers`, `torch`,
`torchvision` i `pillow` (wymagane przez `mlx-vlm` i przyszłe przetwarzanie audio), `sqlite-vec` (wektory w SQLite),
`tree-sitter-language-pack` (parsery kodu), `pathspec` (`.gitignore`), `charset-normalizer` (kodowania plików),
`soundfile`.

**Nie są potrzebne:** Ollama, llama.cpp, LanceDB. Zostały sprawdzone i odrzucone w Fazie 0, uzasadnienie jest w
`docs/DECISIONS.md`.

> Python z python.org (np. 3.13) **nie nadaje się**: nie obsługuje ładowania rozszerzeń SQLite, czego wymaga
> sqlite-vec. Python instalowany przez uv to obsługuje.

## Instalacja krok po kroku

```bash
# 1. Narzędzia systemowe
xcode-select --install                       # jeśli jeszcze nie ma
# Homebrew: https://brew.sh. Jeśli `brew` nie jest w PATH (Apple Silicon):
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
brew install uv ffmpeg

# 2. Projekt i zależności (uv sam pobierze Pythona 3.12 do ~/.local/share/uv)
cd lantern
uv sync

# 3. Jednorazowe pobranie modelu (z internetem)
#    a) wagi MLX 8-bit (~1,2 GB)
uv run hf download mlx-community/embeddinggemma-2-8bit --revision 7505ef2f8ddef45efef6d060865f27989b3c9cec
#    b) konfiguracja procesora i tokenizer z oficjalnego repo Google (bez wag 1,5 GB)
uv run hf download google/embeddinggemma-2 --revision 914f7f89142e33e77833254d9c9b90c3cef7303b \
    --exclude "model.safetensors"

# 4. Sprawdzenie
uv run lantern --help
uv run pytest
```

Model trafia do `~/.cache/huggingface/hub/`.

### Tryb offline (wymuszony)

Po pobraniu modelu ustaw (np. w `~/.zprofile`, a w Fazie 5 trafią do plisty LaunchAgenta):

```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
```

W Fazie 0 sprawdzono, że z tymi zmiennymi ładowanie modelu i embedowanie nie otwiera żadnych połączeń sieciowych.

## Testy i lint

```bash
uv run pytest            # szybkie testy (bez prawdziwego modelu)
uv run pytest -m model   # testy z prawdziwym modelem (wymagają pobranego modelu): zgodność z referencją,
                         # brak ruchu sieciowego, wyszukiwanie parafrazy
uv run ruff check
```

## Foldery i wykluczenia

- Folder dodajesz przez `lantern index <folder>`. Każde kolejne wywołanie dla tego folderu aktualizuje indeks.
- Domyślne wykluczenia (zawsze aktywne), lista w `daemon/lantern/config.py`:
  - katalogi: `.git`, `node_modules`, `.venv`/`venv`, `__pycache__`, `build`, `dist`, `target`, `out`, `.cache`,
    `DerivedData`, `Pods` i inne katalogi buildów oraz cache,
  - pliki z sekretami: `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, certyfikaty, klucze SSH `id_*`, `.netrc`, `.npmrc`,
    `credentials*`, `secrets.*`, `*secret*.json|yaml`, `*token*.json|txt`, `*.tfstate`, `*.tfvars` i inne,
  - wszystko, co wykluczają pliki `.gitignore` (także zagnieżdżone),
  - linki symboliczne (nigdy nie wychodzimy poza wskazany folder).
- Własna konfiguracja wykluczeń (`~/Library/Application Support/Lantern/config.toml`) pojawi się w Fazie 5.

## Gdzie są dane i jak wykluczyć je z Time Machine

- Indeks: `~/Library/Application Support/Lantern/index.db` (od Fazy 2). Ten katalog nie jest synchronizowany z iCloud.
- Model: `~/.cache/huggingface/hub/`.
- Wykluczenie indeksu z kopii Time Machine (zalecane, bo indeks zawiera pochodne Twoich plików):

```bash
tmutil addexclusion "$HOME/Library/Application Support/Lantern"
tmutil isexcluded "$HOME/Library/Application Support/Lantern"   # sprawdzenie
```

## Użycie na kodzie firmowym

- Indeks zawiera **dane pochodne z kodu**: fragmenty tekstu (do wyszukiwania słów) oraz wektory, które pozwalają częściowo
  odtworzyć treść. Traktuj plik indeksu jak kopię kodu.
- Nic nie opuszcza komputera, ale **uzgodnij użycie z działem IT/security** przed indeksowaniem repozytoriów firmowych
  (polityki DLP, szyfrowanie dysku, kopie zapasowe).
- Zalecenia: FileVault włączony, indeks wykluczony z Time Machine, domyślne wykluczenia sekretów pozostawione.
- Usunięcie folderu z indeksu usuwa jego fragmenty i wektory, a okresowy `VACUUM` faktycznie czyści je z pliku bazy (od Fazy 3).

## Znane ograniczenia i otwarte pytania

- Backend MLX korzysta z **niewydanej gałęzi `mlx-vlm`** (przypięty commit) i społecznościowej konwersji wag
  `mlx-community`. Wektory są zgodne z oryginałem (cos 0,9998), ale to zależność do obserwowania.
- Pamięć: `lantern search` z załadowanym modelem zajmuje ~790 MB, indeksowanie do ~2 GB (zmierzone na M4).
  W Fazie 5 cel to ~450 MB w spoczynku. Część audio (~0,9 GB) będzie ładowana tylko na czas indeksowania nagrań.
- CSV/TSV i duże pliki z przewagą liczb są indeksowane tylko od początku (nagłówek i pierwsze wiersze). Lockfile'y
  (`package-lock.json`, `uv.lock` itd.) są pomijane.
- Lista wyników „po słowach” jest na razie prosta (dowolne ze słów zapytania). Połączony ranking w Fazie 3.
- Domyślny procesor ucina audio po 30 s bez ostrzeżenia. Lantern zawsze ustawia limit jawnie (okna z nakładką).
- Polska odmiana i litera „ł” wymagają normalizacji w wyszukiwaniu słów (FTS5). Rozwiązanie w Fazie 3.
- Pełna lista otwartych pytań: [docs/DECISIONS.md](docs/DECISIONS.md#otwarte-pytania).

## Struktura

```
README.md, CLAUDE.md, pyproject.toml
docs/        ARCHITECTURE.md, DECISIONS.md (ADR + otwarte pytania), PRIVACY.md
daemon/      kod demona (pakiet `lantern`): api, scanner, queue, extractors, chunking, embedding, store, search, cli.py
app/         aplikacja Swift w pasku menu (Faza 6, na razie placeholder)
launchd/     szablon LaunchAgenta (Faza 5)
spikes/      skrypty pomiarowe Fazy 0 (nie są częścią aplikacji)
tests/       testy
```
