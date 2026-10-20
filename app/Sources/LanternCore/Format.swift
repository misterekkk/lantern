import Foundation

public enum Format {
    /// 105_000 ms -> "1:45"; 3_725_000 ms -> "1:02:05".
    public static func clock(_ ms: Int) -> String {
        let s = ms / 1000
        return s >= 3600
            ? String(format: "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60)
            : String(format: "%d:%02d", s / 60, s % 60)
    }

    /// File counts that always fit: exact up to 9,999, then compact like macOS ("12K", "1.2M"; in Polish
    /// "12 tys.", "1,2 mln"), so even 100,000,000 files stay a few characters wide.
    public static func count(_ n: Int, locale: Locale = .current) -> String {
        if n < 10_000 { return n.formatted(.number.locale(locale)) }
        return n.formatted(.number.notation(.compactName).precision(.fractionLength(0...1)).locale(locale))
    }

    /// Absolute path -> "~/…" when inside the home folder.
    public static func shortPath(_ path: String, home: String = NSHomeDirectory()) -> String {
        path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path
    }

    /// Title shown for a result: file name, plus the moment for audio/video.
    public static func title(_ r: SearchResult) -> String {
        if r.isApp { return r.symbol ?? ((r.path as NSString).lastPathComponent as NSString).deletingPathExtension }
        if r.isSetting || r.isAction { return r.symbol ?? "" }
        let name = (r.path as NSString).lastPathComponent
        if r.isDefinition, let symbol = r.symbol { return "\(symbol)  ·  \(name)" }
        if r.isPDF, let page = r.page { return String(localized: "\(name)  ·  page \(page)") }
        if let start = r.startMs { return "\(name)  ·  \(clock(start))" }
        if let symbol = r.symbol, !symbol.isEmpty { return "\(name)  ·  \(symbol)" }
        return name
    }

    /// "14 May 2023 · Kraków, Poland" for photos and recordings (any file whose date comes from its contents or name);
    /// nil when there is nothing to say. The country is named by macOS, in its language.
    public static func whenWhere(_ r: SearchResult, locale: Locale = .current) -> String? {
        guard r.isVisual || r.isMedia || ["exif", "quicktime", "name"].contains(r.dateSource ?? "") else { return nil }
        var parts: [String] = []
        if let text = r.date, let day = isoDay.date(from: text) {
            // parsed and shown in UTC: the date is a calendar day, never shifted by this Mac's time zone
            parts.append(day.formatted(Date.FormatStyle(date: .long, time: .omitted, locale: locale,
                                                        timeZone: TimeZone(identifier: "UTC")!)))
        }
        let country = r.country.flatMap { locale.localizedString(forRegionCode: $0) }
        if let place = r.place {
            parts.append(country.map { "\(place), \($0)" } ?? place)
        } else if let country {  // a camera without GPS, in a folder named after the country
            parts.append(country)
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// The question before adding a large folder: how many files, how big, roughly how long (in the app's language).
    public static func estimate(_ e: FolderEstimate) -> String {
        let files = (e.files ?? 0).formatted()
        let size = ByteCountFormatter.string(fromByteCount: e.bytes ?? 0, countStyle: .file)
        var lines = [e.complete == false
            ? String(localized: "At least \(files) files (\(size)); counting stopped early.")
            : String(localized: "About \(files) files (\(size)).")]
        if let m = e.minutes, m > 0 {
            let time = m >= 90 ? String(localized: "\(Int((Double(m) / 60).rounded())) h") : String(localized: "\(m) min")
            lines.append((e.recordings ?? 0) > 0
                ? String(localized: "The first indexing takes roughly \(time), plus the recordings.")
                : String(localized: "The first indexing takes roughly \(time)."))
        }
        if e.external == true {
            lines.append(String(localized: "It is on another disk: while it is disconnected, its files stay in the index and indexing waits."))
        }
        lines.append(String(localized: "A smaller folder (a project, Documents) is usually enough. You can leave parts out later in Settings → Folders."))
        return lines.joined(separator: " ")
    }

    /// Events that only report something done (grey, briefly); every other code is a problem worth a look (orange).
    public static let infoCodes: Set<String> = ["compacted", "schema_upgraded"]
    public static let infoShownFor: TimeInterval = 5 * 60
    public static let problemShownFor: TimeInterval = 30 * 60

    /// The newest event still worth showing in the menu, worded in the app's language, and whether it is only
    /// information. Old ones disappear by themselves: a note about compacting stayed until the daemon restarted.
    public static func currentWarning(_ events: [WarningEvent], now: Date = Date()) -> (text: String, info: Bool)? {
        for e in events.reversed() {
            guard let text = warning(e) else { continue }
            let info = infoCodes.contains(e.code)
            if let age = age(of: e, now: now), age > (info ? infoShownFor : problemShownFor) { continue }
            return (text, info)
        }
        return nil
    }

    /// Seconds since the event: from its timestamp, or from its time of day (earlier today, else yesterday).
    static func age(of e: WarningEvent, now: Date) -> TimeInterval? {
        if let ts = e.ts { return now.timeIntervalSince1970 - ts }
        let parts = e.time.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        let clock = Calendar.current.dateComponents([.hour, .minute, .second], from: now)
        let nowS = (clock.hour ?? 0) * 3600 + (clock.minute ?? 0) * 60 + (clock.second ?? 0)
        let thenS = parts[0] * 3600 + parts[1] * 60 + parts[2]
        return TimeInterval(nowS >= thenS ? nowS - thenS : nowS + 86_400 - thenS)
    }

    /// A daemon warning in the app's language (Lantern's own, not only the system's); nil for one that is not shown.
    public static func warning(_ e: WarningEvent) -> String? {
        let a = e.args
        let root = shortPath(a["root"] ?? "")
        switch e.code {
        case "memory_pause": return String(localized: "Indexing paused: the Mac is short of memory. It resumes by itself.")
        case "low_disk": return String(localized: "Indexing paused: less than 2 GB free on the disk of the index.")
        case "stuck_file":
            let name = ((a["path"] ?? "") as NSString).lastPathComponent
            return String(localized: "Skipped a file that took longer than \(a["minutes"] ?? "30") min: \(name)")
        case "worker_exited":
            return String(localized: "Indexing stopped unexpectedly (code \(a["code"] ?? "?")). Details are in logs/worker.log.")
        case "compacted": return String(localized: "Compacted the index after removing \(a["count"] ?? "?") files.")
        case "no_access":
            return String(localized: "No access to \(a["count"] ?? "?") folders in \(root); their indexed files are kept. Grant access in System Settings → Privacy & Security.")
        case "root_unavailable": return String(localized: "\(root) is not available (disconnected?); nothing was removed.")
        case "mass_deletion":
            return String(localized: "\(a["missing"] ?? "?") of \(a["total"] ?? "?") files are missing in \(root); nothing was removed.")
        case "dates_failed": return String(localized: "Reading file dates failed: \(a["detail"] ?? "")")
        case "apps_failed": return String(localized: "Could not read the applications: \(a["detail"] ?? "")")
        case "download_failed":
            return String(localized: "The model download failed (code \(a["code"] ?? "?")). Details are in logs/models.log.")
        case "model_load_failed": return String(localized: "Could not load the downloaded model: \(a["detail"] ?? "")")
        case "index_newer": return String(localized: "The index is from a newer Lantern.")
        case "index_damaged": return String(localized: "The index is damaged; Lantern can rebuild it.")
        case "schema_upgraded": return String(localized: "A newer Lantern upgraded the index; restarting.")
        case "settings_fixed":
            return String(localized: "A value in config.toml was not valid and was corrected: \(a["detail"] ?? "")")
        case "parent_exited": return nil
        default: return e.message.isEmpty ? nil : e.message
        }
    }

    /// Folder (and line range) shown under the title; photos and recordings start with when and where.
    public static func subtitle(_ r: SearchResult) -> String {
        if let when = whenWhere(r) {
            return "\(when)  —  \(shortPath((r.path as NSString).deletingLastPathComponent))"
        }
        if r.isApp { return appCategory(r.appCategory) }
        if r.isSetting {
            let settings = r.settingsAppName ?? String(localized: "System Settings")
            return r.settingPane.map { "\(settings) › \($0)" } ?? settings
        }
        switch r.kind {
        case "answer", "web", "clipboard", "menu", "window": return r.preview
        case "dictionary": return String(localized: "Dictionary")
        case "shortcut": return String(localized: "Shortcut")
        case "song":
            let about = [r.musicArtist, r.musicAlbum].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
            guard let list = r.musicPlaylist else { return about }
            return about + "  ·  " + String(localized: "from playlist “\(list)”")
        case "playlist":  // Spotify gives no songs for others' playlists: then just "Playlist"
            guard let count = r.musicCount, count > 0 else { return String(localized: "Playlist") }
            return String(localized: "Playlist · \(count) songs")
        default: break
        }
        let folder = shortPath((r.path as NSString).deletingLastPathComponent)
        if r.isDefinition, let line = r.startLine { return String(localized: "\(folder)  —  line \(line)") }
        if r.isPDF, let page = r.page { return String(localized: "\(folder)  —  page \(page)") }
        if let start = r.startLine, let end = r.endLine { return String(localized: "\(folder)  —  lines \(start)–\(end)") }
        return folder
    }

    /// Where the match comes from: meaning (text embeddings), image (photo models), words (BM25).
    public static func sources(_ r: SearchResult) -> String {
        if r.isDefinition { return String(localized: "definition") }
        if r.isApp || r.isSetting || r.isAction { return "" }
        var parts: [String] = []
        if r.vectorRank != nil { parts.append(String(localized: "meaning")) }
        if r.imageRank != nil { parts.append(String(localized: "image")) }
        if r.textRank != nil { parts.append(String(localized: "text in image")) }
        if r.keywordRank != nil && r.textRank == nil { parts.append(String(localized: "words")) }
        return parts.joined(separator: " · ")
    }

    /// "Application" or the App Store category in the app's language ("Developer Tools", "Narzędzia dla programistów").
    public static func appCategory(_ id: String?) -> String {
        switch id {
        case "business": String(localized: "Business")
        case "developer-tools": String(localized: "Developer Tools")
        case "education": String(localized: "Education")
        case "entertainment": String(localized: "Entertainment")
        case "finance": String(localized: "Finance")
        case "games": String(localized: "Games")
        case "graphics-design": String(localized: "Graphics & Design")
        case "healthcare-fitness": String(localized: "Health & Fitness")
        case "lifestyle": String(localized: "Lifestyle")
        case "medical": String(localized: "Medical")
        case "music": String(localized: "Music")
        case "news": String(localized: "News")
        case "photography": String(localized: "Photography")
        case "productivity": String(localized: "Productivity")
        case "reference": String(localized: "Reference")
        case "social-networking": String(localized: "Social Networking")
        case "sports": String(localized: "Sports")
        case "travel": String(localized: "Travel")
        case "utilities": String(localized: "Utilities")
        case "video": String(localized: "Video")
        case "weather": String(localized: "Weather")
        default: String(localized: "Application")
        }
    }
}
