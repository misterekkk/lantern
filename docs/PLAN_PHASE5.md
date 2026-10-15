# Plan Fazy 5: demon w tle i API

**Status:** zrealizowany (2026-10-15), wyniki w DECISIONS.md, sekcja „Faza 5: wyniki”.

## Cel

Lantern działa w tle jako LaunchAgent. Model jest stale gotowy (szybkie wyszukiwanie dla przyszłego panelu
w stylu Spotlight), zmiany w folderach są wykrywane same, a w trybie „pytaj” demon niczego nie indeksuje bez zgody.
CLI i przyszła aplikacja Swift są tylko klientami API.

## Elementy

| Element | Rozwiązanie |
|---|---|
| Proces | `lantern daemon run` (pierwszy plan; tak uruchamia go launchd) |
| API | socket Unix `~/Library/Application Support/Lantern/lantern.sock`, uprawnienia `0600`, katalog `0700`; protokół: JSON w liniach (`{"id", "method", "params"}` → `{"id", "result"}` / `{"id", "error"}`), prosty do obsługi z Pythona i ze Swift (Network.framework) |
| Metody | `status`, `search`, `opened`, `roots.list/add/remove`, `scan`, `pending`, `approve`, `decline`, `pause`, `resume`, `config.get/set`, `shutdown` |
| Start | skan wszystkich folderów (źródło prawdy, także po dłuższym wyłączeniu); niedostępny folder pomijany bez usuwania |
| Tryb indeksowania | `ask` (domyślny): nowe i zmienione pliki czekają na `approve` (CLI `lantern approve`, później przycisk w pasku menu i powiadomienie); `auto`: indeksuje od razu. Usunięcia są stosowane od razu (prywatność) |
| Obserwator | FSEvents (`watchdog`), zdarzenia grupowane (debounce kilka sekund), potem skan zmienionego folderu; skan przy starcie pozostaje źródłem prawdy |
| Praca w tle | jeden wątek indeksujący z QoS background (`pthread_set_qos_class_self_np`), wyszukiwanie w osobnym wątku z normalnym priorytetem; model chroniony blokadą |
| Pauza | ręczna (`pause`/`resume`), automatyczna na zasilaniu z baterii i przy ostrzeżeniu termicznym (`pmset -g batt`, `pmset -g therm`); przerwanie między plikami, niedokończone zadania wracają do kolejki |
| Konfiguracja | `config.toml` w katalogu danych: `index_mode`, `pause_on_battery`, `transcribe`, `notify`, dodatkowe wykluczenia |
| LaunchAgent | `lantern daemon install` / `uninstall` (plist z szablonu, `launchctl bootstrap gui/<uid>`); instaluje użytkownik |
| CLI | `search`, `status`, `pending`, `approve`, `pause`, `resume` łączą się z demonem, jeśli działa; bez demona działają bezpośrednio na bazie jak dotąd |
| Pamięć w spoczynku | próba zejścia z ~790 MB do ~450 MB przez ładowanie modelu bez importu torch (otwarte pytanie 13) |

## Testy

API przez socket (fake embedder, krótka ścieżka socketu w `/tmp`), uprawnienia `0600`, tryb `ask` (pending → approve
→ indexed), tryb `auto`, wykrycie nowego pliku przez obserwator, pauza i wznowienie w trakcie kolejki (zadania wracają
do kolejki), pauza na baterii (parser `pmset`), `shutdown`.
