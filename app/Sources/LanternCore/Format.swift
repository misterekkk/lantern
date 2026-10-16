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
        let name = (r.path as NSString).lastPathComponent
        if r.isDefinition, let symbol = r.symbol { return "\(symbol)  ·  \(name)" }
        if r.isPDF, let page = r.page { return String(localized: "\(name)  ·  page \(page)") }
        if let start = r.startMs { return "\(name)  ·  \(clock(start))" }
        if let symbol = r.symbol, !symbol.isEmpty { return "\(name)  ·  \(symbol)" }
        return name
    }

    /// Folder (and line range) shown under the title.
    public static func subtitle(_ r: SearchResult) -> String {
        if r.isApp { return appCategory(r.appCategory) }
        let folder = shortPath((r.path as NSString).deletingLastPathComponent)
        if r.isDefinition, let line = r.startLine { return String(localized: "\(folder)  —  line \(line)") }
        if r.isPDF, let page = r.page { return String(localized: "\(folder)  —  page \(page)") }
        if let start = r.startLine, let end = r.endLine { return String(localized: "\(folder)  —  lines \(start)–\(end)") }
        return folder
    }

    /// Where the match comes from: meaning (text embeddings), image (photo models), words (BM25).
    public static func sources(_ r: SearchResult) -> String {
        if r.isDefinition { return String(localized: "definition") }
        if r.isApp { return "" }
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
