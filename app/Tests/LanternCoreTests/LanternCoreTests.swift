import Foundation
import Testing
@testable import LanternCore

@Suite struct ModelsTests {
    @Test func decodesDaemonStatus() throws {
        // Shape of Daemon.api_status (daemon/lantern/service/core.py).
        let json = """
        {"pid": 42, "memory_mb": 490, "mode": "ask", "approved": false, "awaiting_approval": true, "paused": null,
         "worker": {"running": true, "event": "progress", "stage": "index", "done": 3, "total": 10, "path": "/a/b.py"},
         "queued": {"code": 7, "audio": 1}, "failed_jobs": 0, "files": {"indexed": 120, "pending": 8},
         "chunks": 900, "roots": [{"path": "/Users/x/Projekty", "last_scan": 1760000000, "available": true}],
         "models_missing": true, "model_download": {"running": true, "key": "embeddinggemma", "step": 1, "total": 2,
          "downloaded_mb": 340, "approx_mb": 1200},
         "last_run": {}, "warnings": ["12:00:00 something"]}
        """
        let s = try DaemonClient.decoder.decode(DaemonStatus.self, from: Data(json.utf8))
        #expect(s.awaitingApproval && s.queuedTotal == 8 && s.indexedFiles == 120)
        #expect(s.worker.done == 3 && s.roots.first?.available == true && s.lastRun?.indexed == nil)
        #expect(s.modelsMissing == true && s.modelDownload?.downloadedMb == 340 && s.modelDownload?.approxMb == 1200)
    }

    @Test func decodesSearchResponseWithMediaResult() throws {
        let json = """
        {"results": [{"path": "/Users/x/Filmy/vlog.mp4", "chunk_id": 7, "start_line": null, "end_line": null,
          "symbol": null, "preview": "sadzonki pomidorów", "score": 0.03, "vector_rank": 1, "vector_score": 0.7,
          "keyword_rank": 1, "keyword_score": 5.0, "keyword_coverage": 1.0, "kind": "transcript",
          "start_ms": 108000, "end_ms": 133000}], "event_id": 5, "vector_ms": 20.5, "keyword_ms": 0.4}
        """
        let r = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(r.results.first?.isMedia == true && r.eventId == 5)
        #expect(Format.title(r.results[0]) == "vlog.mp4  ·  1:48")
        #expect(Format.sources(r.results[0]) == "meaning · words")  // English source; Polish comes from pl.lproj
    }

    @Test func photosSayWhenAndWhere() throws {
        // Phase 16: hybrid.Result date, date_source, place (in the app's language), country code.
        let json = """
        {"results": [], "event_id": null, "vector_ms": 0, "keyword_ms": 0,
         "photos": [{"path": "/Users/x/Zdjęcia/IMG_1.HEIC", "chunk_id": 3, "start_line": null, "end_line": null,
          "symbol": null, "preview": "", "score": 0, "vector_rank": null, "keyword_rank": null, "kind": "image",
          "date": "2023-05-14", "date_source": "exif", "place": "Kraków", "country": "PL"},
          {"path": "/Users/x/a.txt", "chunk_id": 4, "start_line": null, "end_line": null, "symbol": null,
          "preview": "", "score": 0, "vector_rank": null, "keyword_rank": null, "kind": "text",
          "date": "2021-04-01", "date_source": "fs"}]}
        """
        let r = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        let photo = try #require(r.photos.first)
        #expect(Format.whenWhere(photo, locale: Locale(identifier: "pl_PL")) == "14 maja 2023 · Kraków, Polska")
        #expect(Format.whenWhere(photo, locale: Locale(identifier: "en_US")) == "May 14, 2023 · Kraków, Poland")
        #expect(Format.whenWhere(r.photos[1]) == nil)  // a document's file-system date is not shown
        let older = try DaemonClient.decoder.decode(DaemonSettings.self, from: Data("""
        {"index_mode": "ask", "pause_on_battery": true, "pause_on_low_power": true, "pause_on_thermal": true,
         "transcribe": true, "index_photos": true, "index_video_frames": true, "notify": true, "debounce_s": 3,
         "exclude": [], "exclude_extensions": [], "index_audio_video": true, "index_image_text": true}
        """.utf8))
        #expect(older.places == nil)  // a daemon from before phase 16: no switch shown
    }
}

@Suite struct ProtocolTests {
    @Test func remoteErrorsAreSurfaced() {
        let reply = Data(#"{"id": 1, "error": {"type": "ValueError", "message": "unknown method 'x'"}}"#.utf8)
        #expect(throws: DaemonError.remote(type: "ValueError", message: "unknown method 'x'")) {
            try DaemonClient.decodeReply(reply)
        }
    }

    @Test func missingSocketMeansNotRunning() async {
        let client = DaemonClient(socketPath: "/tmp/lantern-test-\(UUID().uuidString.prefix(8)).sock")
        await #expect(throws: DaemonError.notRunning) { try await client.callRaw("ping") }
    }
}

@Suite struct FormatTests {
    @Test func clock() {
        #expect(Format.clock(105_000) == "1:45")
        #expect(Format.clock(3_725_000) == "1:02:05")
    }

    @Test func photoResultsShowTheImageSource() throws {
        let json = """
        {"results": [{"path": "/Users/x/Zdjecia/pies.heic", "chunk_id": 9, "preview": "", "score": 0.03,
          "image_rank": 1, "kind": "image"}], "event_id": 1, "vector_ms": 9, "keyword_ms": 0.3}
        """
        let r = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8)).results[0]
        #expect(r.isVisual && Format.sources(r) == "image" && Format.title(r) == "pies.heic")
    }

    @Test func photosComeAsASectionOrderedByTheDaemon() throws {
        let json = """
        {"results": [{"path": "/a/notes.md", "chunk_id": 1, "preview": "", "score": 0.02, "kind": "text"}],
         "photos": [{"path": "/a/pies.jpg", "chunk_id": 2, "preview": "", "score": 3.1, "image_rank": 1,
           "kind": "image"}], "photos_first": true, "event_id": 1, "vector_ms": 9, "keyword_ms": 0.3}
        """
        var resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(resp.sections.map(\.kind) == [.photos, .files])
        resp.photosFirst = false
        #expect(resp.sections.map(\.kind) == [.files, .photos])
        resp.photos = []
        #expect(resp.sections.map(\.kind) == [.files])
    }

    @Test func responsesWithoutPhotosStillDecode() throws {
        let json = #"{"results": [], "event_id": null, "vector_ms": 1, "keyword_ms": 1}"#
        let resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(resp.photos.isEmpty && !resp.photosFirst && resp.sections.isEmpty)
        #expect(resp.correctedQuery == nil)
    }

    @Test func aCorrectedQueryDecodes() throws {
        let json = #"{"results": [], "vector_ms": 1, "keyword_ms": 1, "corrected_query": "faktura za prąd"}"#
        let resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(resp.correctedQuery == "faktura za prąd")
    }

    @Test func musicRowsPlayFromTheirPlaylist() throws {
        let json = #"""
        {"results": [], "vector_ms": 1, "keyword_ms": 1, "section_scores": {"music": 0.95},
         "music": [{"id": "17AFDBA96AC7754E", "kind": "song", "title": "SKELETONS", "artist": "Travis Scott",
                    "album": "ASTROWORLD", "track": 5, "downloaded": true, "playlist_id": "105C4C31744622C1",
                    "playlist_name": "Gym", "count": 0, "match": "title_exact", "score": 0.95},
                   {"id": "105C4C31744622C1", "kind": "playlist", "title": "Gym", "artist": "", "album": "",
                    "track": 0, "downloaded": false, "playlist_id": "105C4C31744622C1", "playlist_name": "Gym",
                    "count": 34, "match": "playlist", "score": 0.8}]}
        """#
        let resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        let rows = resp.sections.first { $0.kind == .music }?.results ?? []
        #expect(rows.count == 2 && rows[0].isAction && rows[0].kind == "song")
        let song = try #require(MusicResult.parse(rows[0].path))
        #expect(song.source == "apple" && song.kind == "song" && song.id == "17AFDBA96AC7754E"
                && song.playlist == "105C4C31744622C1")
        #expect(MusicResult.parse(rows[1].path)?.playlist == "105C4C31744622C1")
        #expect(Format.subtitle(rows[0]).hasPrefix("Travis Scott · ASTROWORLD"))
        #expect(ResultCategory.music.rawValue == "music" && ResultCategory.allCases.count == 10)
        let spotify = MusicResult(id: "sp-3eekarcy7kvgVJJE", kind: "song", source: "spotify", title: "Skeletons",
                                  artist: "Travis Scott", album: "ASTROWORLD", track: 5, downloaded: false,
                                  playlistId: "sp-liked", playlistName: "Liked Songs", count: 0, match: "title_exact")
        let parsed = try #require(MusicResult.parse(spotify.asResult.path))
        #expect(parsed.source == "spotify" && parsed.id == "sp-3eekarcy7kvgVJJE" && parsed.playlist == "sp-liked")
        #expect(spotify.asResult.iconPath == "/Applications/Spotify.app")
    }

    @Test func theLearnedRowComesFirst() {
        let slack = SearchResult(path: "/Applications/Slack.app", chunkId: 0, symbol: "Slack", kind: "app")
        let sleep = SearchResult(path: "/Applications/Sleep.app", chunkId: 0, symbol: "Sleep", kind: "app")
        let file = SearchResult(path: "/a/slajdy.pdf", chunkId: 4, kind: "pdf")
        let sections = [ResultSection(kind: .files, results: [file]), ResultSection(kind: .apps, results: [sleep, slack])]
        let out = ResultLayout.learnedFirst(sections, key: slack.id)
        #expect(out.map(\.kind) == [.apps, .files] && out[0].results.first == slack)
        #expect(ResultLayout.learnedFirst(sections, key: nil) == sections)
        #expect(ResultLayout.learnedFirst(sections, key: "gone") == sections)
        // learned but not matched by name ("d" -> Finder): added at the top; an uninstalled app is not
        let finder = "app|0|-1|/System/Library/CoreServices/Finder.app|"
        let added = ResultLayout.learnedFirst(sections, key: finder)
        #expect(added[0].kind == .apps && added[0].results.first?.path == "/System/Library/CoreServices/Finder.app"
                && added[0].results.count == 3 && added[0].results.first?.id == finder)
        #expect(ResultLayout.learnedFirst(sections, key: "app|0|-1|/Applications/Gone.app|") == sections)
    }

    @Test func onlyPlainWordsAreSpellChecked() {
        #expect(SpellingWords.candidates(in: "faktrua za prad") == ["faktrua", "prad"])
        #expect(SpellingWords.candidates(in: "IrisDatabase backoff_wrapper v2 config.toml ORA-01555 USB") == [])
        #expect(SpellingWords.candidates(in: "def add_two(x, y)").isEmpty)
        #expect(SpellingWords.candidates(in: "Warszwie").first == "Warszwie")  // a capital first letter is fine
    }

    @Test func definitionsComeFirstAndOpenAtTheirLine() throws {
        let json = """
        {"results": [{"path": "/a/uzycie.py", "chunk_id": 1, "preview": "", "score": 0.02, "kind": "code",
           "start_line": 3, "end_line": 3}],
         "definitions": [{"path": "/a/skrypt.py", "line": 200, "end_line": 201, "name": "add_two",
           "kind": "function", "signature": "def add_two(x, y)", "parent": null, "match": "exact", "score": 4,
           "chunk_id": 7}],
         "photos": [], "photos_first": false, "event_id": 1, "vector_ms": 9, "keyword_ms": 0.3}
        """
        let resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(resp.sections.map(\.kind) == [.definitions, .files])
        let d = resp.sections[0].results[0]
        #expect(d.isDefinition && d.openLine == 200 && Format.title(d) == "add_two  ·  skrypt.py")
        #expect(Format.sources(d) == "definition" && d.id != resp.results[0].id)
    }

    @Test func lineJumpUsesTheDefaultEditorsOwnMechanism() {
        let app = URL(fileURLWithPath: "/Applications/X.app")
        let vscode = LineJump.make(bundleId: "com.microsoft.VSCode", appURL: app, executable: nil,
                                   file: "/Users/x/My Code/a.py", line: 200)
        #expect(vscode == .url(URL(string: "vscode://file/Users/x/My%20Code/a.py:200")!))
        let xcode = LineJump.make(bundleId: "com.apple.dt.Xcode", appURL: URL(fileURLWithPath: "/Applications/Xcode.app"),
                                  executable: nil, file: "/a.swift", line: 5)
        #expect(xcode == .command(executable: "/usr/bin/xed", arguments: ["-l", "5", "/a.swift"],
                                  environment: ["DEVELOPER_DIR": "/Applications/Xcode.app/Contents/Developer"]))
        let idea = LineJump.make(bundleId: "com.jetbrains.pycharm", appURL: app,
                                 executable: URL(fileURLWithPath: "/Applications/PyCharm.app/Contents/MacOS/pycharm"),
                                 file: "/a.py", line: 9)
        #expect(idea == .command(executable: "/Applications/PyCharm.app/Contents/MacOS/pycharm",
                                 arguments: ["--line", "9", "/a.py"], environment: [:]))
        #expect(LineJump.make(bundleId: "org.python.IDLE", appURL: app, executable: nil, file: "/a.py", line: 1) == .plain)
    }

    @Test func extensionPillsFromTypedText() {
        #expect(ExtensionInput.normalize("csv") == ".csv" && ExtensionInput.normalize("*.CSV") == ".csv")
        #expect(ExtensionInput.normalize("a b") == nil && ExtensionInput.normalize("*") == nil)
        let r = ExtensionInput.split("*.csv, .json,tsv,, bad/x, .lo")
        #expect(r.completed == [".csv", ".json", ".tsv"] && r.invalid == ["bad/x"] && r.remainder == ".lo")
        #expect(ExtensionInput.split("csv").completed.isEmpty)  // no separator yet: still typing
    }

    @Test func extensionSuggestionsFilterAsYouType() {
        let items = [
            ExtensionSuggestions.Item(ext: ".json", kind: "text", indexed: 40),
            ExtensionSuggestions.Item(ext: ".js", kind: "code", indexed: 90),
            ExtensionSuggestions.Item(ext: ".csv", kind: "text", indexed: 3),
        ]
        #expect(ExtensionInput.suggestions(for: "j", from: items, ignored: []).map(\.ext) == [".js", ".json"])
        #expect(ExtensionInput.suggestions(for: "*.js", from: items, ignored: [".js"]).map(\.ext) == [".json"])
    }

    @Test func treeAndPerfDecode() throws {
        let tree = """
        {"path": "/p", "entries": [{"name": "app", "path": "/p/app", "is_dir": true, "state": "partial",
          "reason": null, "indexed": 12}, {"name": ".env", "path": "/p/.env", "is_dir": false, "state": "skipped",
          "reason": "hidden", "indexed": 0}], "truncated": false}
        """
        let listing = try DaemonClient.decoder.decode(TreeListing.self, from: Data(tree.utf8))
        #expect(listing.entries[0].state == .partial && !listing.entries[1].toggleable)
        let perf = """
        {"interval_s": 1, "samples": [[-2, 480.5, null], [-1, 481, 1200.2]],
         "daemon": {"mb": 481, "peak_mb": 900}, "worker": {"running": true, "mb": 1200, "peak_mb": 3900,
         "last_peak_mb": null, "stage": "index", "done": 3, "total": 10, "path": "/a.jpg", "rate": 3.1},
         "models": [{"key": "whisper", "repo": "r", "revision": "v", "where": "worker", "size_mb": 1550,
           "downloaded": true, "loaded_daemon": false, "loaded_worker": false, "worker_parts": []}]}
        """
        let snap = try DaemonClient.decoder.decode(PerfSnapshot.self, from: Data(perf.utf8))
        #expect(snap.points.count == 3 && snap.points.last?.series == "worker" && snap.worker.rate == 3.1)
        let preview = #"{"path": "/a.py", "start_line": 2, "end_line": 2, "total_lines": 3, "lines": [[1, "def main():"], [2, "    pass"]]}"#
        let p = try DaemonClient.decoder.decode(TextPreview.self, from: Data(preview.utf8))
        #expect(p.numbered.map(\.number) == [1, 2] && p.numbered[1].text == "    pass")
    }

    @Test func mediaOpensInQuickTimeOnlyForItsFormats() {
        #expect(MediaOpen.quickTimePlays("/a/vlog.MOV") && !MediaOpen.quickTimePlays("/a/film.mkv"))
        #expect(MediaOpen.startSeconds(108_000) == 105 && MediaOpen.startSeconds(1_000) == 0)
    }

    @Test func countsStayShort() {
        let en = Locale(identifier: "en_US"), pl = Locale(identifier: "pl_PL")
        #expect(Format.count(749, locale: en) == "749" && Format.count(9_999, locale: en) == "9,999")
        #expect(Format.count(12_345, locale: en) == "12.3K" && Format.count(100_000_000, locale: en) == "100M")
        // Polish uses a no-break space, so "12,3 tys." never wraps.
        #expect(Format.count(12_345, locale: pl) == "12,3\u{00A0}tys." && Format.count(100_000_000, locale: pl) == "100\u{00A0}mln")
    }

    @Test func filterCategoriesAndCompletionDecode() throws {
        let json = """
        {"results": [], "photos": [], "event_id": null, "vector_ms": 1, "keyword_ms": 1,
         "categories": ["code", "pdf", "future"], "category": "pdf", "completion": "backoff_wrapper"}
        """
        let resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(resp.categories == [.code, .pdf] && resp.category == .pdf && resp.completion == "backoff_wrapper")
    }

    @Test func completionRemainderFollowsWhatIsTyped() {
        #expect(QueryCompletion.remainder(query: "backoff_w", completion: "backoff_wrapper") == "rapper")
        #expect(QueryCompletion.remainder(query: "BACKOFF_W", completion: "BACKOFF_Wrapper") == "rapper")
        #expect(QueryCompletion.remainder(query: "backoff_x", completion: "backoff_wrapper") == nil)  // stale
        #expect(QueryCompletion.remainder(query: "backoff_wrapper", completion: "backoff_wrapper") == nil)
        #expect(QueryCompletion.remainder(query: "", completion: "x") == nil)
    }

    @Test func shortPath() {
        #expect(Format.shortPath("/Users/x/Dokumenty/a.md", home: "/Users/x") == "~/Dokumenty/a.md")
        #expect(Format.shortPath("/Volumes/Dysk/a.md", home: "/Users/x") == "/Volumes/Dysk/a.md")
    }
}

@Suite struct TextFoldTests {
    @Test func foldsLettersFoundationLeavesAlone() {
        #expect(TextFold.fold("Łódź Søren Straße Ærø ﬁle") == "lodz soren strasse aero file")
    }

    @Test func rangesPointIntoTheOriginalText() {
        let text = "Spotkanie w Łodzi, potem Straße 5"
        let ns = text as NSString
        #expect(TextFold.ranges(of: "lodzi", in: text).map { ns.substring(with: $0) } == ["Łodzi"])
        #expect(TextFold.ranges(of: "strasse", in: text).map { ns.substring(with: $0) } == ["Straße"])
        #expect(TextFold.ranges(of: "nic", in: text).isEmpty)
    }
}

@Suite struct QueryHighlightTests {
    private func marked(_ query: String, _ text: String) -> [String] {
        QueryHighlight.ranges(query: query, in: text).map { (text as NSString).substring(with: $0) }
    }

    @Test func marksWholeWordsByTheirStartFolded() {
        #expect(marked("dog on the beach", "Took Rex to the beach; the dog loved beaches") == ["beach", "dog", "beaches"])
        #expect(marked("faktura za prąd lodz", "Fakturę za prąd z Łodzi opłacono") == ["Fakturę", "prąd", "Łodzi"])
        #expect(marked("error 0x80070002", "Error code: 0x80070002") == ["Error", "0x80070002"])
        #expect(marked("cat", "concatenate") == [])  // only word starts
    }
}

@Suite struct AppResultTests {
    private func response(appsFirst: Bool, match: String) throws -> SearchResponse {
        let json = """
        {"results": [{"path": "/x/notes.md", "chunk_id": 3, "start_line": 1, "end_line": 4, "symbol": null,
          "preview": "", "score": 0.1, "vector_rank": 1, "keyword_rank": null, "kind": "text",
          "start_ms": null, "end_ms": null}],
         "apps": [{"path": "/Applications/Discord.app", "name": "Discord", "category": "social-networking",
          "bundle_id": "com.hnc.Discord", "match": "\(match)", "score": 1.0}],
         "apps_first": \(appsFirst), "categories": ["apps", "docs"], "event_id": 1, "vector_ms": 1, "keyword_ms": 1}
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(SearchResponse.self, from: Data(json.utf8))
    }

    @Test func appMatchedByNameIsTheTopHit() throws {
        let resp = try response(appsFirst: true, match: "typo")
        #expect(resp.sections.map(\.kind) == [.apps, .files])
        let app = resp.sections[0].results[0]
        #expect(app.isApp && Format.title(app) == "Discord" && app.chunkId == 0)
        #expect(resp.categories == [.apps, .docs])
    }

    @Test func appsFoundByMeaningComeLast() throws {
        #expect(try response(appsFirst: false, match: "meaning").sections.map(\.kind) == [.files, .apps])
    }
}

@Suite struct SettingResultTests {
    @Test func settingsMatchedByWordsComeFirstAndOpenTheirPane() throws {
        let json = """
        {"results": [], "settings": [{"key": "com.apple.Date-Time-Settings.extension/24HourTime", "pane": "Data i czas",
          "title": "Używaj formatu 24‑godzinnego", "url": "x-apple.systempreferences:com.apple.Date-Time-Settings.extension",
          "symbol": "calendar.badge.clock", "color": "Blue", "is_pane": false, "match": "words", "score": 0.6}],
         "settings_first": true, "categories": ["settings"], "vector_ms": 1, "keyword_ms": 1}
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let resp = try decoder.decode(SearchResponse.self, from: Data(json.utf8))
        let row = resp.sections[0].results[0]
        #expect(resp.sections.map(\.kind) == [.settings] && row.isSetting && row.path.hasPrefix("x-apple.systempreferences:"))
        #expect(Format.title(row) == "Używaj formatu 24‑godzinnego" && Format.subtitle(row).hasSuffix("Data i czas"))
    }
}

@Suite struct WebRowTests {
    private let web = [ResultSection(kind: .web, results: [SearchResult(path: "https://x", chunkId: 0, kind: "web")])]

    private func file(keyword: Int?) -> ResultSection {
        ResultSection(kind: .files, results: [SearchResult(path: "/a.md", chunkId: 1, keywordRank: keyword, kind: "text")])
    }

    @Test func comesFirstWhenNothingLocalContainsTheWords() {
        #expect(ResultLayout.withWeb([file(keyword: nil)], web: web).map(\.kind) == [.web, .files])
    }

    @Test func staysLastWhenAFileMatchesByWords() {
        #expect(ResultLayout.withWeb([file(keyword: 1)], web: web).map(\.kind) == [.files, .web])
    }
}

@Suite struct SectionScoreTests {
    @Test func appSectionsJoinTheDaemonsScale() {
        let files = ResultSection(kind: .files, results: [SearchResult(path: "/a.md", chunkId: 1, kind: "text")])
        let web = ResultSection(kind: .web, results: [SearchResult(path: "https://x", chunkId: 0, kind: "web")])
        let clip = ResultSection(kind: .clipboard, results: [SearchResult(path: "clipboard:1", chunkId: 0, kind: "clipboard")])
        let weak = ResultLayout.merge([files], scores: ["files": 0.35], extra: [(clip, 0.9), (web, ResultLayout.webScore)])
        #expect(weak.map(\.kind) == [.clipboard, .web, .files])
        let strong = ResultLayout.merge([files], scores: ["files": 0.72], extra: [(web, ResultLayout.webScore)])
        #expect(strong.map(\.kind) == [.files, .web])
    }
}
