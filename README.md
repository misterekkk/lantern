# Lantern

Lokalna aplikacja na macOS do przeszukiwania plików **po znaczeniu**, a nie tylko po słowach: tekst, kod, dokumenty
i nagrania audio. Silnikiem jest otwarty model embeddingowy **EmbeddingGemma 2** (Google DeepMind, Apache 2.0),
uruchamiany natywnie na Apple Silicon przez MLX.

> **Stan projektu: Faza 6 (aplikacja w pasku menu).** Działa indeksowanie tekstu, kodu, nagrań audio i ścieżki
> dźwiękowej filmów, wyszukiwanie łączące znaczenie i słowa w jeden ranking, demon w tle oraz aplikacja macOS:
> ikona w pasku menu (stan, zgoda na indeksowanie, pauza, foldery) i panel wyszukiwania w stylu Spotlight pod
> **⌥Space**. Jeszcze nie ma wyszukiwania po obrazie (Faza 7).

## Aplikacja (pasek menu i panel ⌥Space)

```bash
app/build.sh                 # buduje i podpisuje app/build/Lantern.app (wymaga Xcode 26, patrz niżej)
open app/build/Lantern.app   # albo przenieś Lantern.app do /Applications
```

- **Ikona w pasku menu** pokazuje stan (gotowy, indeksuje, czeka na zgodę, pauza), postęp, liczbę plików i pamięć
  demona; przyciski: Zaindeksuj / Później, Wstrzymaj / Wznów, Dodaj folder…, Ustawienia…, Zakończ.
- **⌥Space** otwiera panel wyszukiwania (jak Spotlight): ↑↓ wybór, ↩ otwórz, ⌘↩ pokaż w Finderze, Esc zamknij.
  Nagrania i filmy pokazują moment wypowiedzi (np. `vlog.mp4 · 1:48`).
- **Powiadomienie** z przyciskami „Zaindeksuj” / „Później”, gdy demon znajdzie nowe pliki (tryb „pytaj”).
- **Ustawienia:** uruchamianie przy logowaniu, tryb indeksowania, transkrypcja, pauza na baterii/temperaturze,
  foldery, wykluczenia, wyczyszczenie historii, zagęszczenie indeksu.
- **Aplikacja sama uruchamia demona** (proces potomny), więc zgody macOS na Biurko, Dokumenty i Pobrane dotyczą
  aplikacji Lantern i pojawiają się jako zwykłe okna „Lantern chce uzyskać dostęp do…”. Gdy aplikacja się zamknie
  (także po awarii), demon kończy się razem z nią. Jeśli działa już demon z LaunchAgenta, aplikacja z niego korzysta.
- Wymagania do budowania: Xcode 26 (SDK macOS 26 dla efektu Liquid Glass; skrypt używa go przez `DEVELOPER_DIR`,
  bez zmiany `xcode-select`). Podpis: certyfikat „Apple Development” (Xcode → Settings → Accounts → Manage
  Certificates); bez niego podpis tymczasowy (ad-hoc) i macOS może ponownie pytać o zgody po każdej przebudowie.
> To README opisuje tylko to, co faktycznie działa, a dalsze funkcje oznacza fazą, w której się pojawią.

## Użycie

```bash
uv run lantern index ~/Projekty            # pierwszy raz: skan + indeksowanie; kolejne: tylko zmiany
uv run lantern search "optymalizacja drzewa decyzyjnego"
uv run lantern search "backoff_wrapper" -n 5
uv run lantern open 1                      # otwórz 1. wynik ostatniego wyszukiwania (--reveal: pokaż w Finderze)
uv run lantern status
uv run lantern remove ~/Projekty           # usuń folder i wszystkie jego dane z indeksu
uv run lantern compact                     # fizycznie wymaż usunięte dane z pliku indeksu
uv run lantern clear-history               # usuń lokalną historię wyszukiwań
```

- `index` porównuje folder z indeksem: nowe i zmienione pliki indeksuje, przeniesione rozpoznaje po treści (bez
  ponownego liczenia), usunięte usuwa z indeksu razem z fragmentami i wektorami.
- Jeśli folder jest niedostępny (odłączony dysk, udział sieciowy), nic nie jest usuwane. Jeśli zniknęłaby ponad połowa
  plików folderu, `index` odmawia i wymaga `--force`.
- `search` łączy dwie listy w jeden ranking (Reciprocal Rank Fusion): **po znaczeniu** (EmbeddingGemma 2, parafrazy,
  inne języki) i **po słowach** (dokładne nazwy funkcji, komunikaty błędów). Przy każdym wyniku widać, skąd pochodzi
  dopasowanie, np. `(meaning #1 · keywords #3)`. Wyszukiwanie po słowach pomija słowa nieistotne („jak”, „się”, „the”)
  i dopasowuje polską odmianę po rdzeniu („decyzyjnego” znajdzie „decyzyjny”).
- Każde wyszukiwanie i otwarty wynik (`lantern open`) trafia do **lokalnej** historii w bazie (zapytanie, pozycje
  wyników, bez treści plików). To dane pod przyszłe uczenie rankingu. `--no-log` pomija zapis, `clear-history` czyści.
- Zmiana zasad dzielenia plików albo modelu powoduje automatyczny ponowny indeks przy następnym `index`.
- Indeksowane typy: kod (Python, JS/TS, Go, Rust, Java, Kotlin, Swift, C/C++, C#, Ruby, PHP i inne, dzielony na
  funkcje i klasy), tekst (Markdown, txt, JSON, YAML, HTML, notebooki itd.; pliki tekstowe ponad 2 MB i binarne są
  pomijane), audio (mp3, m4a, wav, flac, ogg, opus, aac, aiff…) i wideo (mp4, mov, mkv, webm, avi…, tylko dźwięk).

### Nagrania i filmy

- ffmpeg wyciąga dźwięk (16 kHz mono) strumieniowo, okno po oknie, więc długość nagrania nie wpływa na zużycie RAM.
- Dwa tory wyszukiwania, łączone w jednym rankingu:
  1. **embedding audio** (EmbeddingGemma 2) okien 30 s z nakładką 5 s: znaczenie wypowiedzi, także międzyjęzykowo,
  2. **transkrypcja** (Whisper large-v3-turbo, język wykrywany automatycznie) dzielona na fragmenty ~20 s:
     pojedyncze słowa („krowa”), podgląd tekstu w wynikach, wyszukiwanie po słowach.
- Wynik pokazuje plik i moment, np. `filmy/vlog.mp4 @ 1:48–2:13`, oraz tekst wypowiedzi z tego odcinka.
- Modele audio ładują się tylko na czas indeksowania nagrań, po kolei (najpierw embedding audio, potem Whisper),
  i są zwalniane po zakończeniu.
- `lantern index <folder> --no-transcribe` pomija transkrypcję (szybciej, bez Whispera, ale bez wyszukiwania
  pojedynczych słów).

## Prywatność w skrócie

- Wszystko działa **lokalnie i offline**, po jednorazowym pobraniu modelu. Brak telemetrii i zewnętrznych API.
- Lantern **tylko czyta** indeksowane pliki. Nigdy ich nie modyfikuje, nie przenosi ani nie usuwa.
- Indeks to jeden plik SQLite w `~/Library/Application Support/Lantern/` (poza iCloud).
- Szczegóły: [docs/PRIVACY.md](docs/PRIVACY.md).

## Wymagania

| Co | Po co | Instalacja |
|---|---|---|
| Mac z Apple Silicon (testowane: M4, 16 GB) | MLX działa tylko na Apple Silicon | – |
| Xcode Command Line Tools | kompilator i git | `xcode-select --install` |
| Xcode 26 | budowanie aplikacji w pasku menu (SDK macOS 26) | App Store |
| Homebrew | menedżer pakietów | https://brew.sh |
| uv | środowisko Pythona i zależności | `brew install uv` |
| Python 3.12 | język demona; **instalowany przez uv**, nie systemowy | automatycznie przez `uv sync` |
| ffmpeg | dekodowanie audio i ścieżki dźwiękowej filmów do 16 kHz mono | `brew install ffmpeg` |

Instalowane automatycznie przez `uv sync` do `.venv` projektu (nic globalnie):
MLX i `mlx-vlm` (backend modelu, przypięty commit gałęzi z EmbeddingGemma 2), `tokenizers`, `transformers`, `torch`,
`torchvision` i `pillow` (wymagane przez `mlx-vlm` i przyszłe przetwarzanie audio), `sqlite-vec` (wektory w SQLite),
`tree-sitter-language-pack` (parsery kodu), `pathspec` (`.gitignore`), `charset-normalizer` (kodowania plików),
`mlx-whisper` (transkrypcja), `soundfile`.

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
#    b) Whisper do transkrypcji nagrań i filmów (~1,6 GB; pomiń, jeśli nie indeksujesz audio/wideo
#       albo używasz `lantern index --no-transcribe`)
uv run hf download mlx-community/whisper-large-v3-turbo --revision a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb

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

## Demon w tle

```bash
uv run lantern daemon install     # instaluje i uruchamia LaunchAgent (start przy logowaniu, niski priorytet)
uv run lantern daemon run         # albo: demon na pierwszym planie (Ctrl+C kończy)
uv run lantern status             # stan demona, kolejka, pamięć
uv run lantern pending            # pliki wykryte przez demona, czekające na zgodę
uv run lantern approve            # zaindeksuj je (albo: lantern decline — zapytaj ponownie przy kolejnych zmianach)
uv run lantern pause | resume     # wstrzymaj / wznów indeksowanie w tle
uv run lantern config index_mode auto   # indeksuj od razu, bez pytania (domyślnie: ask)
uv run lantern daemon stop | uninstall
```

- **Po starcie** (także po dłuższym wyłączeniu komputera lub demona) demon skanuje wszystkie foldery. Usunięte pliki
  znikają z indeksu od razu, a nowe i zmienione w trybie `ask` czekają na `lantern approve`. Jeśli `notify = true`,
  demon wysyła powiadomienie macOS. W trakcie działania zmiany wykrywa obserwator FSEvents (po kilku sekundach ciszy).
- **Gdy demon działa**, `lantern index/search/status/remove/compact/clear-history` łączą się z nim (wyszukiwanie
  używa już załadowanego modelu, ~20–90 ms). `lantern index <folder>` z demonem dodaje folder i od razu zatwierdza
  jego indeksowanie, które idzie w tle.
- **Indeksowanie w osobnym procesie** z priorytetem tła: modele audio i Whisper żyją tylko w nim i znikają z pamięci
  po zakończeniu. Demon w spoczynku zajmuje ~490 MB.
- **Pauza automatyczna** na zasilaniu z baterii i przy ostrzeżeniu termicznym (`pause_on_battery`, `pause_on_thermal`).
  Bieżący plik jest kończony, reszta wraca do kolejki.
- **Ustawienia** (`lantern config`): `index_mode` (`ask`/`auto`), `pause_on_battery`, `pause_on_thermal`,
  `transcribe`, `notify`, `debounce_s`. Zapisane w `~/Library/Application Support/Lantern/config.toml`.
- **API:** socket Unix `~/Library/Application Support/Lantern/lantern.sock` (`0600`, katalog `0700`), JSON w liniach.
  Tego samego API użyje aplikacja w pasku menu (Faza 6).
- **Logi:** `~/Library/Application Support/Lantern/logs/` (ścieżki i błędy, bez treści plików i zapytań).

## Foldery i wykluczenia

- Folder dodajesz przez `lantern index <folder>`. Możesz też dodać cały katalog domowy: `lantern index ~`.
  Najpierw warto zrobić suchy przebieg `lantern index ~ --dry-run`, który tylko liczy pliki i szacuje czas, niczego
  nie czytając z treści i niczego nie zapisując.
- **Zasady dla katalogu domowego** (zawsze aktywne):
  - pomijany jest `~/Library` (poczta, cache, dane aplikacji), z wyjątkiem **iCloud Drive**,
  - pomijane są `~/Applications`, biblioteka muzyki Apple i wszystkie pakiety macOS (`*.app`, `*.photoslibrary`,
    pakiety iWork `*.pages/*.numbers/*.key`, obrazy maszyn wirtualnych, `.dmg` itd.),
  - pomijane są ukryte katalogi i pliki (`~/.ssh`, `~/.aws`, `~/.config`, dotfiles), z wyjątkiem `.github`.
- **iCloud:** pliki, które iCloud trzyma tylko w chmurze („optymalizuj miejsce”), są pomijane, bo ich odczyt
  pobrałby je z sieci. Trafią do indeksu, gdy będą na dysku. Plik już zaindeksowany, który iCloud przeniósł do chmury,
  zostaje w indeksie: wyszukiwanie go znajdzie, a otwarcie pobierze.
- **Uprawnienia macOS:** do Biurka, Dokumentów, Pobranych i iCloud Drive proces potrzebuje zgody (Ustawienia
  systemowe → Prywatność i ochrona → Pliki i foldery albo Pełny dostęp do dysku). Folder bez dostępu jest pomijany,
  a jego wcześniej zaindeksowane pliki **nie są usuwane**; `lantern status` pokazuje ostrzeżenie. Wygodne nadawanie
  uprawnień przyjdzie z aplikacją (Faza 6).
- Domyślne wykluczenia (lista w `daemon/lantern/config.py` i `scanner/walk.py`):
  - katalogi: `.git`, `node_modules`, `.venv`/`venv`, `__pycache__`, `build`, `dist`, `target`, `out`, `.cache`,
    `DerivedData`, `Pods` i inne katalogi buildów oraz cache,
  - pliki z sekretami: `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, certyfikaty, klucze SSH `id_*`, `.netrc`, `.npmrc`,
    `credentials*`, `secrets.*`, `*secret*.json|yaml`, `*token*.json|txt`, `*.tfstate`, `*.tfvars` i inne,
  - lockfile'y (`package-lock.json`, `uv.lock`, `Cargo.lock` …), wszystko z plików `.gitignore` (także zagnieżdżonych),
  - linki symboliczne (nigdy nie wychodzimy poza wskazany folder).
- **Własne wykluczenia:**

  ```bash
  uv run lantern exclude add ~/Downloads/commons-math3-3.6.1   # konkretny folder lub plik
  uv run lantern exclude add "*.csv"                           # wzorzec jak w .gitignore (w każdym folderze)
  uv run lantern exclude list
  uv run lantern exclude remove "*.csv"
  ```

  Zmiana działa przy następnym skanie (demon skanuje od razu); pasujące, już zaindeksowane pliki są wtedy usuwane
  z indeksu.

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
- `lantern remove <folder>` usuwa fragmenty i wektory folderu i od razu zagęszcza bazę (VACUUM). Test automatyczny
  sprawdza, że usunięty tekst nie występuje w surowych bajtach pliku bazy. Po usunięciu wielu plików `index` sam zagęszcza bazę.

## Znane ograniczenia i otwarte pytania

- Backend MLX korzysta z **niewydanej gałęzi `mlx-vlm`** (przypięty commit) i społecznościowej konwersji wag
  `mlx-community`. Wektory są zgodne z oryginałem (cos 0,9998), ale to zależność do obserwowania.
- Pamięć (zmierzone na M4): demon w spoczynku ~490 MB; proces indeksujący do ~2 GB dla tekstu i do ~3,3 GB dla
  nagrań i filmów (embedding audio, potem Whisper), po czym kończy się i zwalnia pamięć.
- Transkrypcja obejmuje maksymalnie pierwsze 4 godziny nagrania (Whisper dekoduje całość do pamięci, ~230 MB na godzinę).
  Embedding audio obejmuje całe nagranie.
- Jakość wyszukiwania audio sprawdzona dotąd na mowie syntetycznej (`say`); prawdziwe nagrania (szum, wielu mówców)
  mogą wypaść gorzej.
- Duże pliki (> 64 MB, głównie filmy) są rozpoznawane po rozmiarze i próbkach treści (początek, środek, koniec), a nie
  po pełnym hashu. Zmiana w innym miejscu pliku bez zmiany rozmiaru i daty modyfikacji nie zostanie wykryta.
- CSV/TSV i duże pliki z przewagą liczb są indeksowane tylko od początku (nagłówek i pierwsze wiersze). Lockfile'y
  (`package-lock.json`, `uv.lock` itd.) są pomijane.
- Polska odmiana w wyszukiwaniu słów to przybliżenie (prefiks rdzenia), a nie prawdziwy stemmer. Krótkie słowa
  (do 4 liter, np. „bazą”) muszą pasować dokładnie.
- Domyślny procesor ucina audio po 30 s bez ostrzeżenia. Lantern zawsze ustawia limit jawnie (okna z nakładką).
- Polska odmiana i litera „ł” wymagają normalizacji w wyszukiwaniu słów (FTS5). Rozwiązanie w Fazie 3.
- Pełna lista otwartych pytań: [docs/DECISIONS.md](docs/DECISIONS.md#otwarte-pytania).

## Struktura

```
README.md, CLAUDE.md, pyproject.toml
docs/        ARCHITECTURE.md, DECISIONS.md (ADR + otwarte pytania), PRIVACY.md
daemon/      kod demona (pakiet `lantern`): api, scanner, queue, extractors, chunking, embedding, store, search, cli.py
app/         aplikacja Swift w pasku menu (Swift Package: LanternCore + Lantern, build.sh)
launchd/     szablon LaunchAgenta (Faza 5)
spikes/      skrypty pomiarowe Fazy 0 (nie są częścią aplikacji)
tests/       testy
```
