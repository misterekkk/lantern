# Prywatność i bezpieczeństwo

Lantern ma działać także na kodzie firmowym, dlatego prywatność jest wymaganiem nadrzędnym.

## Zasady

1. **Lokalnie i offline.** Jedyny ruch sieciowy to jednorazowe, ręczne pobranie modelu (`hf download`, patrz README).
   Potem biblioteki działają z `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `DO_NOT_TRACK=1`.
2. **Tylko odczyt plików użytkownika.** Żadnych zapisów, przeniesień ani usunięć w indeksowanych folderach.
3. **Brak telemetrii** w kodzie Lanterna: żadnych analityk, raportów awarii ani połączeń wychodzących.
4. **API** tylko przez socket Unix z uprawnieniami `0600`, w katalogu danych z uprawnieniami `0700` (zrobione w Fazie 5,
   sprawdzane testem). Brak portu sieciowego.
5. **Domyślne wykluczenia sekretów:** `.env*`, `*.pem`, `*.p12`, `*.pfx`, `*.key`, klucze SSH `id_*`, pliki tokenów
   i poświadczeń, katalogi `.git`, `node_modules`, buildy, cache. Respektowany jest `.gitignore` (Faza 2).
6. **Usunięte znaczy usunięte:** fragmenty, FTS i wektory kasowane w jednej transakcji; `PRAGMA secure_delete`,
   FTS5 `secure-delete`, a po usunięciach `optimize` + `VACUUM` + checkpoint WAL (`lantern compact`, automatycznie
   po dużych usunięciach i po `lantern remove`). Test sprawdza brak usuniętego tekstu w surowych bajtach pliku bazy.
7. **iCloud i uprawnienia:** pliki iCloud nieobecne na dysku nie są czytane (odczyt pobrałby je z chmury);
   `~/Library` (poczta, dane aplikacji) jest pomijany; folder bez uprawnień macOS jest pomijany bez usuwania jego wpisów.
8. **Zdjęcia:** w indeksie są tylko wektory zdjęć i klatek (bez miniatur ani kopii obrazów). Miniatury w panelu
   generuje na bieżąco macOS (Quick Look) z oryginalnych plików. Klatki filmów trafiają na czas indeksowania do
   katalogu tymczasowego i są usuwane zaraz po wczytaniu do pamięci (przed liczeniem wektorów).
9. **Historia wyszukiwań** (`search_events`) jest tylko lokalna i zawiera zapytania oraz pozycje wyników, bez treści
   plików. `lantern search --no-log` pomija zapis, `lantern clear-history` usuwa całą historię i zagęszcza bazę.

## Gdzie są dane

| Dane | Lokalizacja | Uwagi |
|---|---|---|
| Indeks (SQLite) | `~/Library/Application Support/Lantern/index.db` | poza iCloud; wyklucz z Time Machine (README) |
| Konfiguracja | `~/Library/Application Support/Lantern/config.toml` | `lantern config` |
| Socket API | `~/Library/Application Support/Lantern/lantern.sock` | `0600`, tylko gdy demon działa |
| Logi demona | `~/Library/Application Support/Lantern/logs/` | ścieżki plików i komunikaty błędów; bez treści plików i zapytań |
| Modele | `~/.cache/huggingface/hub/` | publiczne wagi, bez danych użytkownika |

Indeks zawiera **dane pochodne**: fragmenty tekstu oraz wektory, z których da się częściowo odtworzyć treść.
`search_events` zawiera historię zapytań. Traktuj plik indeksu jak kopię indeksowanych danych.

## Co robią biblioteki (zmierzone w Fazie 0)

| Biblioteka | Zachowanie sieciowe | Jak ograniczamy |
|---|---|---|
| huggingface_hub / transformers | bez trybu offline: zapytania do huggingface.co przy ładowaniu modelu (sprawdzanie wersji) | zmienne offline; **zmierzone: 0 prób połączeń** przy ładowaniu i embedowaniu tekstu i audio |
| sentence-transformers (backend zapasowy) | jak wyżej | jak wyżej |
| MLX / mlx-vlm | brak własnego ruchu; wagi przez huggingface_hub | `local_files_only`; **zmierzone: 0 prób połączeń** (test `-m model`) |
| mlx-whisper | wagi przez huggingface_hub | ładowany z lokalnej kopii (`local_files_only`); **zmierzone: 0 prób połączeń** przy transkrypcji (test `-m model`) |
| SigLIP 2 (transformers + torch w procesie roboczym, MLX w demonie) | wagi przez huggingface_hub | `local_files_only` + zmienne offline; **zmierzone: 0 prób połączeń** przy ładowaniu i liczeniu wektorów zdjęć i zapytań (test `-m model`) |
| Pillow, `sips` (zdjęcia) | brak ruchu sieciowego; czytają plik zdjęcia | HEIC przez `sips` do pliku tymczasowego w katalogu systemowym, usuwanego od razu |
| ffmpeg | brak ruchu sieciowego; tylko czyta plik wejściowy, wynik idzie do potoku | wywoływany z `-nostdin`, bez plików wyjściowych |
| Ollama (**nieużywana**) | serwer okresowo pobiera „model recommendations” z ollama.com (widoczne w logu) | nie jest częścią Lanterna |
| Homebrew (instalacja) | analityka Homebrew przy `brew install` | opcjonalnie `brew analytics off` (nie dotyczy działania Lanterna) |

Pomiar: `spikes/phase0/probe_network.py` przechwytuje `socket.connect` i `getaddrinfo` w procesie Pythona.
W Fazie 2 dodamy analogiczny test dla backendu MLX jako test automatyczny.

## Użycie na kodzie firmowym

- Uzgodnij użycie z IT/security przed indeksowaniem repozytoriów firmowych.
- Włącz FileVault, wyklucz indeks z Time Machine, nie wyłączaj domyślnych wykluczeń sekretów.
- Usunięcie folderu z konfiguracji usuwa jego dane z indeksu, a `VACUUM` czyści je z pliku.
