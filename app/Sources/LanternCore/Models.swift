import Foundation

/// Mirrors `Daemon.api_status` (daemon/lantern/service/core.py). Keys are snake_case on the wire.
public struct DaemonStatus: Decodable, Equatable, Sendable {
    public struct Worker: Decodable, Equatable, Sendable {
        public var running: Bool
        public var stage: String?
        public var done: Int?
        public var total: Int?
        public var path: String?
    }

    public struct Root: Decodable, Equatable, Sendable, Identifiable {
        public var path: String
        public var lastScan: Double?
        public var available: Bool
        public var id: String { path }
    }

    public struct LastRun: Decodable, Equatable, Sendable {
        public var indexed: Int?
        public var transcribed: Int?
        public var failed: Int?
        public var finished: String?
    }

    public struct ModelDownload: Decodable, Equatable, Sendable {
        public var running: Bool
        public var key: String?
        public var step: Int?
        public var total: Int?
        public var downloadedMb: Int?
        public var approxMb: Int?
        public var error: String?
    }

    public var pid: Int
    public var memoryMb: Int
    public var mode: String
    public var approved: Bool
    public var awaitingApproval: Bool
    public var waitingForSettings: Bool?  // new work waits until the Settings window closes
    public var paused: String?
    public var worker: Worker
    public var queued: [String: Int]
    public var failedJobs: Int
    public var files: [String: Int]
    public var kinds: [String: Int]?
    public var photoModelLoaded: Bool?
    public var whisperDownloaded: Bool?
    public var modelsMissing: Bool?
    public var modelDownload: ModelDownload?
    public var chunks: Int
    public var roots: [Root]
    public var lastRun: LastRun?
    public var warnings: [String]

    public var queuedTotal: Int { queued.values.reduce(0, +) }
    public var filesTotal: Int { files.values.reduce(0, +) }
    public var indexedFiles: Int { files["indexed"] ?? 0 }
}

/// Mirrors `lantern.search.hybrid.Result`.
public struct SearchResult: Decodable, Equatable, Sendable, Identifiable {
    public var path: String
    public var chunkId: Int
    public var startLine: Int?
    public var endLine: Int?
    public var symbol: String?
    public var preview: String
    public var score: Double
    public var vectorRank: Int?
    public var keywordRank: Int?
    public var imageRank: Int?
    public var kind: String
    public var startMs: Int?
    public var endMs: Int?
    public var page: Int?  // PDF page
    public var textRank: Int?  // photos found by the text read from them (OCR)
    public var date: String?  // when the file was made, "2023-05-14" (EXIF, QuickTime, its name or the file system)
    public var dateSource: String?  // exif | quicktime | name | fs
    public var place: String?  // the city it was made in, already in the app's language
    public var country: String?  // ISO code: Format.whenWhere names it in the macOS language
    public var appCategory: String?  // applications: App Store category ("social-networking")
    public var settingPane: String?  // System Settings: the pane of a setting (nil for a pane itself)
    public var settingSymbol: String?  // its icon: SF Symbol and enclosure colour, as System Settings draws it
    public var settingColor: String?
    public var settingsAppName: String?  // macOS's own name for System Settings ("Ustawienia systemowe")
    public var iconPath: String?  // answers, dictionary, shortcuts, web: the icon of the app that handles them
    public var musicArtist: String?  // songs: artist and album; a song or a playlist: the playlist it plays from
    public var musicAlbum: String?
    public var musicPlaylist: String?
    public var musicCount: Int?  // playlists: number of songs

    public init(
        path: String, chunkId: Int, startLine: Int? = nil, endLine: Int? = nil, symbol: String? = nil,
        preview: String = "", score: Double = 0, vectorRank: Int? = nil, keywordRank: Int? = nil,
        imageRank: Int? = nil, kind: String, startMs: Int? = nil, endMs: Int? = nil
    ) {
        self.path = path; self.chunkId = chunkId; self.startLine = startLine; self.endLine = endLine
        self.symbol = symbol; self.preview = preview; self.score = score; self.vectorRank = vectorRank
        self.keywordRank = keywordRank; self.imageRank = imageRank; self.kind = kind
        self.startMs = startMs; self.endMs = endMs
    }

    /// Unique across sections (a definition and a file result can share a chunk).
    // Settings share their pane's URL as the path: the title tells them apart.
    public var id: String { "\(kind)|\(chunkId)|\(startLine ?? -1)|\(path)|\(isSetting ? symbol ?? "" : "")" }
    public var isMedia: Bool { startMs != nil }
    public var isVisual: Bool { kind == "image" || kind == "frame" }
    public var isDefinition: Bool { kind == "definition" }
    public var isPDF: Bool { kind == "pdf" }
    public var isApp: Bool { kind == "app" }
    public var isSetting: Bool { kind == "setting" }
    public var isAnswer: Bool { kind == "answer" }
    /// Rows that are not files: nothing to preview, ↩ opens or copies them.
    public var isAction: Bool {
        ["app", "setting", "answer", "dictionary", "shortcut", "web", "clipboard", "menu", "window", "song", "playlist"]
            .contains(kind)
    }
    /// Line to open the file at in a code editor (definitions and code/text chunks).
    public var openLine: Int? { isMedia || isVisual || isPDF ? nil : startLine }
}

/// An application (`search/apps.py`): matched by name, or by what it does ("meaning").
public struct AppResult: Decodable, Equatable, Sendable {
    public var path: String
    public var name: String
    public var category: String?  // App Store category, e.g. "social-networking"
    public var bundleId: String?
    public var match: String  // exact | prefix | word | initials | typo | keyword | meaning

    /// As a row: chunk 0 means "an application" in the search history.
    public var asResult: SearchResult {
        var row = SearchResult(path: path, chunkId: 0, symbol: name, kind: "app")
        row.appCategory = category
        return row
    }
}

/// A System Settings pane or a setting inside one (`search/settings.py`); opens through its URL.
public struct SettingResult: Decodable, Equatable, Sendable {
    public var key: String
    public var pane: String
    public var title: String
    public var url: String
    public var symbol: String?
    public var color: String?
    public var isPane: Bool
    public var match: String

    public func asResult(appName: String?) -> SearchResult {
        var row = SearchResult(path: url, chunkId: 0, symbol: title, kind: "setting")
        row.settingsAppName = appName
        row.settingPane = isPane ? nil : pane
        row.settingSymbol = symbol
        row.settingColor = color
        return row
    }
}

/// An instant answer computed by the daemon: arithmetic, a unit conversion, the time in another city.
public struct AnswerResult: Decodable, Equatable, Sendable {
    public var kind: String  // math | unit | time
    public var title: String
    public var subtitle: String
    public var copy: String

    public var asResult: SearchResult {
        var row = SearchResult(path: "answer:" + copy, chunkId: 0, symbol: title, preview: subtitle, kind: "answer")
        row.iconPath = kind == "time" ? "/System/Applications/Clock.app" : "/System/Applications/Calculator.app"
        return row
    }
}

/// A dictionary entry for a single word (Dictionary.app's dictionaries).
public struct WordDefinitionResult: Decodable, Equatable, Sendable {
    public var word: String
    public var text: String

    public var asResult: SearchResult {
        let link = "dict://" + (word.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? word)
        var row = SearchResult(path: link, chunkId: 0, symbol: word, preview: text, kind: "dictionary")
        row.iconPath = "/System/Applications/Dictionary.app"
        return row
    }
}

/// One of the user's shortcuts (Shortcuts app); ↩ runs it.
public struct ShortcutResult: Decodable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var match: String

    public var asResult: SearchResult {
        var parts = URLComponents(string: "shortcuts://run-shortcut")!
        parts.queryItems = [URLQueryItem(name: "name", value: name)]
        var row = SearchResult(path: parts.url?.absoluteString ?? "shortcuts://", chunkId: 0, symbol: name,
                               kind: "shortcut")
        row.iconPath = "/System/Applications/Shortcuts.app"
        return row
    }
}

/// A song or playlist of the user's Apple Music library (`search/music.py`). Path: `music:song:<id>[:<playlist id>]`
/// or `music:playlist:<id>`, the persistent IDs Music's AppleScript takes.
public struct MusicResult: Decodable, Equatable, Sendable {
    public var id: String
    public var kind: String  // song | playlist
    public var source: String?  // apple | spotify (older daemons: apple)
    public var title: String
    public var artist: String
    public var album: String
    public var track: Int
    public var downloaded: Bool
    public var playlistId: String?
    public var playlistName: String?
    public var count: Int
    public var match: String

    public var asResult: SearchResult {
        let from = source ?? "apple"
        let path = kind == "playlist" ? "music:\(from):playlist:\(id)"
            : "music:\(from):song:\(id)" + (playlistId.map { ":\($0)" } ?? "")
        var row = SearchResult(path: path, chunkId: 0, symbol: title, kind: kind == "playlist" ? "playlist" : "song")
        row.iconPath = from == "spotify" ? "/Applications/Spotify.app" : "/System/Applications/Music.app"
        row.musicArtist = artist
        row.musicAlbum = album
        row.musicPlaylist = playlistName
        row.musicCount = count
        return row
    }

    /// The parts of a music row's path: (source, kind, item id, playlist id).
    public static func parse(_ path: String) -> (source: String, kind: String, id: String, playlist: String?)? {
        let parts = path.split(separator: ":").map(String.init)
        guard parts.count >= 4, parts[0] == "music" else { return nil }
        return (parts[1], parts[2], parts[3], parts.count > 4 ? parts[4] : (parts[2] == "playlist" ? parts[3] : nil))
    }
}

/// A code definition from the symbols table: `def add_two(x, y)` in skrypt.py, line 200.
public struct Definition: Decodable, Equatable, Sendable {
    public var path: String
    public var line: Int
    public var endLine: Int
    public var name: String
    public var kind: String
    public var signature: String
    public var parent: String?
    public var match: String
    public var chunkId: Int?

    /// As a result row: title = name, preview = signature, opened at `line`.
    public var asResult: SearchResult {
        SearchResult(
            path: path, chunkId: chunkId ?? -1, startLine: line, endLine: endLine, symbol: name,
            preview: signature, kind: "definition")
    }
}

public struct SearchResponse: Decodable, Equatable, Sendable {
    public var results: [SearchResult]
    public var photos: [SearchResult]
    public var photosFirst: Bool
    public var definitions: [Definition]
    public var categories: [ResultCategory]
    public var category: ResultCategory?
    public var completion: String?
    public var apps: [AppResult]
    public var appsFirst: Bool
    public var settings: [SettingResult]
    public var settingsFirst: Bool
    /// The System Settings app's name in the language of macOS: things from the system keep the system's language,
    /// Lantern's own words follow Lantern's.
    public var settingsAppName: String?
    public var shortcuts: [ShortcutResult]
    public var answer: AnswerResult?
    public var definition: WordDefinitionResult?
    public var definitionFirst: Bool
    /// Each section's confidence on one 0..1 scale (`hybrid._section_scores`); sections are ordered by it, and the
    /// app adds its own (clipboard, web) on the same scale.
    public var sectionScores: [String: Double]
    public var music: [MusicResult]
    /// The row usually opened for exactly this query (`hybrid.learned_choice`): shown first.
    public var learned: String?
    /// The query the daemon searched instead, when a spelling guess replaced an unknown word (`search/spelling.py`).
    public var correctedQuery: String?
    public var eventId: Int?
    public var vectorMs: Double
    public var keywordMs: Double

    enum CodingKeys: String, CodingKey {
        case results, photos, photosFirst, definitions, categories, category, completion, apps, appsFirst, eventId
        case settings, settingsFirst, settingsAppName, shortcuts, answer, definition, definitionFirst, sectionScores
        case vectorMs, keywordMs, correctedQuery, music, learned
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        results = try c.decode([SearchResult].self, forKey: .results)
        photos = try c.decodeIfPresent([SearchResult].self, forKey: .photos) ?? []  // daemons before Phase 7
        photosFirst = try c.decodeIfPresent(Bool.self, forKey: .photosFirst) ?? false
        definitions = try c.decodeIfPresent([Definition].self, forKey: .definitions) ?? []  // before Phase 8
        let names = try c.decodeIfPresent([String].self, forKey: .categories) ?? []
        categories = names.compactMap(ResultCategory.init(rawValue:))  // unknown future categories are ignored
        category = try c.decodeIfPresent(String.self, forKey: .category).flatMap(ResultCategory.init(rawValue:))
        completion = try c.decodeIfPresent(String.self, forKey: .completion)
        apps = try c.decodeIfPresent([AppResult].self, forKey: .apps) ?? []
        appsFirst = try c.decodeIfPresent(Bool.self, forKey: .appsFirst) ?? false
        settings = try c.decodeIfPresent([SettingResult].self, forKey: .settings) ?? []
        settingsFirst = try c.decodeIfPresent(Bool.self, forKey: .settingsFirst) ?? false
        settingsAppName = try c.decodeIfPresent(String.self, forKey: .settingsAppName)
        shortcuts = try c.decodeIfPresent([ShortcutResult].self, forKey: .shortcuts) ?? []
        answer = try c.decodeIfPresent(AnswerResult.self, forKey: .answer)
        definition = try c.decodeIfPresent(WordDefinitionResult.self, forKey: .definition)
        definitionFirst = try c.decodeIfPresent(Bool.self, forKey: .definitionFirst) ?? false
        sectionScores = try c.decodeIfPresent([String: Double].self, forKey: .sectionScores) ?? [:]
        correctedQuery = try c.decodeIfPresent(String.self, forKey: .correctedQuery)
        music = try c.decodeIfPresent([MusicResult].self, forKey: .music) ?? []
        learned = try c.decodeIfPresent(String.self, forKey: .learned)
        eventId = try c.decodeIfPresent(Int.self, forKey: .eventId)
        vectorMs = try c.decode(Double.self, forKey: .vectorMs)
        keywordMs = try c.decode(Double.self, forKey: .keywordMs)
    }

    /// Results grouped like Spotlight: code definitions (exact answers) first, then photos and files with the more
    /// confident one first (decided by the daemon). Same order as the daemon logs, so history ranks match.
    public var sections: [ResultSection] {
        let definitions = ResultSection(kind: .definitions, results: definitions.map(\.asResult))
        let files = ResultSection(kind: .files, results: results)
        let photos = ResultSection(kind: .photos, results: photos)
        let apps = ResultSection(kind: .apps, results: apps.map(\.asResult))
        let settings = ResultSection(kind: .settings, results: settings.map { $0.asResult(appName: settingsAppName) })
        let rest = [definitions] + (photosFirst ? [photos, files] : [files, photos])
        // Apps and settings matched by name are the top hits (like Spotlight); found only by meaning, they come last.
        let answerSection = ResultSection(kind: .answer, results: answer.map { [$0.asResult] } ?? [])
        let shortcutSection = ResultSection(kind: .shortcuts, results: shortcuts.map(\.asResult))
        let dictionary = ResultSection(kind: .dictionary, results: definition.map { [$0.asResult] } ?? [])
        let musicSection = ResultSection(kind: .music, results: music.map(\.asResult))
        // Same order as the daemon logs: answer, then name matches, then the rest, then hints.
        if !sectionScores.isEmpty {  // one scale for every kind: highest first, ties keep this order
            let all = [answerSection, apps, shortcutSection, settings, definitions, photos, files, dictionary,
                       musicSection]
            return all.filter { !$0.results.isEmpty && sectionScores[$0.kind.daemonName] != nil }
                .enumerated().sorted { a, b in
                    let (sa, sb) = (sectionScores[a.element.kind.daemonName]!, sectionScores[b.element.kind.daemonName]!)
                    return sa != sb ? sa > sb : a.offset < b.offset
                }.map(\.element)
        }
        let first = [answerSection] + (appsFirst ? [apps] : []) + [shortcutSection]
            + (definitionFirst ? [dictionary] : []) + (settingsFirst ? [settings] : [])
        let last = (settingsFirst ? [] : [settings]) + (appsFirst ? [] : [apps]) + (definitionFirst ? [] : [dictionary])
        return (first + rest + last + [musicSection]).filter { !$0.results.isEmpty }
    }
}

/// Filter pills in the panel (`hybrid.CATEGORIES` in the daemon), in a fixed order so ⌘1…⌘9 and ⌘0 stay the same.
public enum ResultCategory: String, CaseIterable, Sendable, Identifiable {
    case apps, settings, shortcuts, clipboard, code, docs, pdf, photos, media, music
    public var id: String { rawValue }
}

/// The inline completion shown after the typed text (accepted with →).
public enum QueryCompletion {
    /// The part to show after `query`, if `completion` still extends what is typed now (responses can be stale).
    public static func remainder(query: String, completion: String?) -> String? {
        guard let completion, completion.count > query.count,
              completion.lowercased().hasPrefix(query.lowercased()), !query.isEmpty
        else { return nil }
        return String(completion.dropFirst(query.count))
    }
}

public struct ResultSection: Equatable, Sendable, Identifiable {
    public enum Kind: String, Sendable {
        case answer, apps, shortcuts, settings, definitions, files, photos, dictionary, clipboard, web, menu, windows
        case music

        /// The section's name in `section_scores`.
        public var daemonName: String { self == .dictionary ? "definition" : rawValue }
    }
    public var kind: Kind
    public var results: [SearchResult]
    public var id: Kind { kind }

    public init(kind: Kind, results: [SearchResult]) {
        self.kind = kind
        self.results = results
    }
}

/// Mirrors `lantern.service.settings.Settings`.
public struct DaemonSettings: Decodable, Equatable, Sendable {
    public var indexMode: String
    public var pauseOnBattery: Bool
    public var pauseOnLowPower: Bool
    public var pauseOnThermal: Bool
    public var transcribe: Bool
    public var indexPhotos: Bool
    public var indexVideoFrames: Bool
    public var notify: Bool
    public var debounceS: Double
    public var exclude: [String]
    public var excludeExtensions: [String]
    public var indexAudioVideo: Bool
    public var indexImageText: Bool
    public var extraLanguages: [String]?
    public var places: Bool?  // GPS -> city (nil: a daemon from before phase 16)
    public var people: Bool?  // people in photos: counts, women/men by appearance (nil: before phase 16)
}

/// Preferred languages (`api_languages`): the macOS ones, the ones added in Settings, and the list in use.
public struct LanguageList: Decodable, Equatable, Sendable {
    public var system: [String]
    public var extra: [String]
    public var effective: [String]
}

/// One folder level of the settings tree (`api_tree`).
public struct TreeListing: Decodable, Equatable, Sendable {
    public var path: String
    public var entries: [TreeEntry]
    public var truncated: Bool?
    public var note: String?  // cloud | no_access
}

public struct TreeEntry: Decodable, Equatable, Sendable, Identifiable {
    public enum State: String, Decodable, Sendable { case included, partial, excluded, skipped }
    public var name: String
    public var path: String
    public var isDir: Bool
    public var state: State
    public var reason: String?  // why a skipped entry is skipped (hidden, secret, build, gitignore, extension…)
    public var indexed: Int  // files in the index (folders) or 0/1 (files)
    public var id: String { path }
    public var toggleable: Bool { state != .skipped }
}

/// `api_perf`: memory history and current work.
public struct PerfSnapshot: Decodable, Equatable, Sendable {
    public struct Process: Decodable, Equatable, Sendable {
        public var mb: Int?
        public var peakMb: Int?
    }
    public struct Worker: Decodable, Equatable, Sendable {
        public var running: Bool
        public var mb: Int?
        public var peakMb: Int?
        public var lastPeakMb: Int?
        public var stage: String?
        public var done: Int?
        public var total: Int?
        public var path: String?
        public var rate: Double?
    }
    public struct Model: Decodable, Equatable, Sendable, Identifiable {
        public var key: String
        public var repo: String
        public var revision: String
        public var sizeMb: Int?
        public var downloaded: Bool
        public var loadedDaemon: Bool
        public var loadedWorker: Bool
        public var workerParts: [String]
        public var id: String { key }
    }
    public var intervalS: Double
    public var samples: [[Double?]]  // [seconds ago (negative), daemon MB, worker MB or null]
    public var daemon: Process
    public var worker: Worker
    public var models: [Model]

    public struct Point: Equatable, Sendable { public var t: Double; public var mb: Double; public var series: String }
    /// Chart points per series ("daemon", "worker"); a gap where the worker was not running.
    public var points: [Point] {
        samples.flatMap { s -> [Point] in
            guard s.count == 3, let t = s[0] else { return [] }
            var out: [Point] = []
            if let d = s[1] { out.append(Point(t: t, mb: d, series: "daemon")) }
            if let w = s[2] { out.append(Point(t: t, mb: w, series: "worker")) }
            return out
        }
    }
}

/// `api_preview`: lines around a text/code result.
public struct TextPreview: Decodable, Equatable, Sendable {
    public var path: String
    public var startLine: Int
    public var endLine: Int
    public var totalLines: Int
    public var lines: [[Line]]

    public enum Line: Decodable, Equatable, Sendable {
        case number(Int), text(String)
        public init(from decoder: Decoder) throws {
            let c = try decoder.singleValueContainer()
            if let n = try? c.decode(Int.self) { self = .number(n) } else { self = .text(try c.decode(String.self)) }
        }
    }

    public var numbered: [(number: Int, text: String)] {
        lines.compactMap { pair in
            guard pair.count == 2, case .number(let n) = pair[0], case .text(let s) = pair[1] else { return nil }
            return (n, s)
        }
    }
}

public struct ExtensionSuggestions: Decodable, Equatable, Sendable {
    public struct Item: Decodable, Equatable, Sendable {
        public var ext: String
        public var kind: String?
        public var indexed: Int
    }
    public var suggestions: [Item]
    public var ignored: [String]
}

public struct ScanSummary: Decodable, Equatable, Sendable {
    public var root: String
    public var new: Int?
    public var changed: Int?
    public var removed: Int?
    public var error: String?
}

public enum ResultLayout {
    /// The web row's place on the daemon's scale: below answers, name matches and files that contain every word of
    /// the query; above guesses by meaning.
    public static let webScore = 0.5

    /// The learned row first: its section moves to the top and the row to the top of its section. A learned app
    /// that the name match did not find ("d" -> Discord, a typo, a private abbreviation) is added: measured on
    /// simulated launches, that lifted ↩ hitting the intended app from 74% to 90% (docs/DECISIONS.md).
    public static func learnedFirst(_ sections: [ResultSection], key: String?) -> [ResultSection] {
        guard let key else { return sections }
        guard let s = sections.firstIndex(where: { $0.results.contains { $0.id == key } }) else {
            guard let app = appRow(key) else { return sections }
            var out = sections
            if let a = out.firstIndex(where: { $0.kind == .apps }) {
                var apps = out.remove(at: a)
                apps.results.insert(app, at: 0)
                return [apps] + out
            }
            return [ResultSection(kind: .apps, results: [app])] + out
        }
        var section = sections[s]
        if let r = section.results.firstIndex(where: { $0.id == key }) {
            section.results.insert(section.results.remove(at: r), at: 0)
        }
        var out = sections
        out.remove(at: s)
        return [section] + out
    }

    /// An application row from its row id ("app|0|-1|/Applications/Slack.app|"), if the app is still installed.
    static func appRow(_ key: String) -> SearchResult? {
        let parts = key.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
        guard parts.count >= 4, parts[0] == "app", FileManager.default.fileExists(atPath: parts[3]) else { return nil }
        let name = (FileManager.default.displayName(atPath: parts[3]) as NSString).deletingPathExtension
        return SearchResult(path: parts[3], chunkId: 0, symbol: name, kind: "app")
    }

    /// Adds the app's own sections (clipboard, web) to the daemon's, ordered on the same 0..1 scale.
    public static func merge(_ sections: [ResultSection], scores: [String: Double],
                             extra: [(section: ResultSection, score: Double)]) -> [ResultSection] {
        guard !scores.isEmpty else { return withWeb(sections + extra.filter { $0.section.kind != .web }.map(\.section),
                                                    web: extra.filter { $0.section.kind == .web }.map(\.section)) }
        var rows = sections.enumerated().map { (section: $0.element, score: scores[$0.element.kind.daemonName] ?? 0,
                                                order: $0.offset) }
        rows += extra.enumerated().map { (section: $0.element.section, score: $0.element.score,
                                          order: sections.count + $0.offset) }
        return rows.sorted { $0.score != $1.score ? $0.score > $1.score : $0.order < $1.order }.map(\.section)
    }

    /// Older daemons without scores: the web row near the top when nothing local contains the query's words.
    public static func withWeb(_ sections: [ResultSection], web: [ResultSection]) -> [ResultSection] {
        guard !web.isEmpty else { return sections }
        let local: Set<ResultSection.Kind> = [.files, .photos, .definitions]
        let wordMatch = sections.contains { section in
            local.contains(section.kind)
                && section.results.contains { $0.keywordRank != nil || $0.textRank != nil || $0.isDefinition }
        }
        let answered = sections.contains { $0.kind == .answer || $0.kind == .dictionary }
        if wordMatch || answered { return sections + web }
        let lead = sections.prefix { !local.contains($0.kind) }
        return Array(lead) + web + Array(sections.dropFirst(lead.count))
    }
}
