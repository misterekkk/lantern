# Prywatność i bezpieczeństwo

Lantern ma działać także na kodzie firmowym, dlatego prywatność jest wymaganiem nadrzędnym.

## Zasady

1. **Lokalnie i offline.** Jedyny ruch sieciowy to jednorazowe, ręczne pobranie modelu (`hf download`, patrz README).
   Potem biblioteki działają z `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `DO_NOT_TRACK=1`.
2. **Tylko odczyt plików użytkownika.** Żadnych zapisów, przeniesień ani usunięć w indeksowanych folderach.
3. **Brak telemetrii** w kodzie Lanterna: żadnych analityk, raportów awarii ani połączeń wychodzących.
4. **API** tylko przez socket Unix z uprawnieniami `0600` (Faza 5).
5. **Domyślne wykluczenia sekretów:** `.env*`, `*.pem`, `*.p12`, `*.pfx`, `*.key`, klucze SSH `id_*`, pliki tokenów
   i poświadczeń, katalogi `.git`, `node_modules`, buildy, cache. Respektowany jest `.gitignore` (Faza 2).
6. **Usunięte znaczy usunięte:** fragmenty, FTS i wektory kasowane w jednej transakcji, okresowy `VACUUM` (Faza 3).

## Gdzie są dane

| Dane | Lokalizacja | Uwagi |
|---|---|---|
| Indeks (SQLite) | `~/Library/Application Support/Lantern/index.db` | poza iCloud; wyklucz z Time Machine (README) |
| Konfiguracja | `~/Library/Application Support/Lantern/config.toml` | od Fazy 2 |
| Logi demona | `~/Library/Logs/Lantern/` | od Fazy 5; bez treści plików i zapytań |
| Modele | `~/.cache/huggingface/hub/` | publiczne wagi, bez danych użytkownika |

Indeks zawiera **dane pochodne**: fragmenty tekstu oraz wektory, z których da się częściowo odtworzyć treść.
`search_events` zawiera historię zapytań. Traktuj plik indeksu jak kopię indeksowanych danych.

## Co robią biblioteki (zmierzone w Fazie 0)

| Biblioteka | Zachowanie sieciowe | Jak ograniczamy |
|---|---|---|
| huggingface_hub / transformers | bez trybu offline: zapytania do huggingface.co przy ładowaniu modelu (sprawdzanie wersji) | zmienne offline; **zmierzone: 0 prób połączeń** przy ładowaniu i embedowaniu tekstu i audio |
| sentence-transformers (backend zapasowy) | jak wyżej | jak wyżej |
| MLX / mlx-vlm | brak własnego ruchu; wagi przez huggingface_hub | zmienne offline (do potwierdzenia pomiarem w Fazie 2) |
| mlx-whisper (Faza 4) | wagi przez huggingface_hub | zmienne offline (do potwierdzenia w Fazie 4) |
| Ollama (**nieużywana**) | serwer okresowo pobiera „model recommendations” z ollama.com (widoczne w logu) | nie jest częścią Lanterna |
| Homebrew (instalacja) | analityka Homebrew przy `brew install` | opcjonalnie `brew analytics off` (nie dotyczy działania Lanterna) |

Pomiar: `spikes/phase0/probe_network.py` przechwytuje `socket.connect` i `getaddrinfo` w procesie Pythona.
W Fazie 2 dodamy analogiczny test dla backendu MLX jako test automatyczny.

## Użycie na kodzie firmowym

- Uzgodnij użycie z IT/security przed indeksowaniem repozytoriów firmowych.
- Włącz FileVault, wyklucz indeks z Time Machine, nie wyłączaj domyślnych wykluczeń sekretów.
- Usunięcie folderu z konfiguracji usuwa jego dane z indeksu, a `VACUUM` czyści je z pliku.
