import Foundation

public enum Format {
    /// 105_000 ms -> "1:45"; 3_725_000 ms -> "1:02:05".
    public static func clock(_ ms: Int) -> String {
        let s = ms / 1000
        return s >= 3600
            ? String(format: "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60)
            : String(format: "%d:%02d", s / 60, s % 60)
    }

    /// Absolute path -> "~/…" when inside the home folder.
    public static func shortPath(_ path: String, home: String = NSHomeDirectory()) -> String {
        path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path
    }

    /// Title shown for a result: file name, plus the moment for audio/video.
    public static func title(_ r: SearchResult) -> String {
        let name = (r.path as NSString).lastPathComponent
        if let start = r.startMs { return "\(name)  ·  \(clock(start))" }
        if let symbol = r.symbol, !symbol.isEmpty { return "\(name)  ·  \(symbol)" }
        return name
    }

    /// Folder (and line range) shown under the title.
    public static func subtitle(_ r: SearchResult) -> String {
        let folder = shortPath((r.path as NSString).deletingLastPathComponent)
        if let start = r.startLine, let end = r.endLine { return String(localized: "\(folder)  —  lines \(start)–\(end)") }
        return folder
    }

    /// Where the match comes from: meaning (text embeddings), image (photo models), words (BM25).
    public static func sources(_ r: SearchResult) -> String {
        var parts: [String] = []
        if r.vectorRank != nil { parts.append(String(localized: "meaning")) }
        if r.imageRank != nil { parts.append(String(localized: "image")) }
        if r.keywordRank != nil { parts.append(String(localized: "words")) }
        return parts.joined(separator: " · ")
    }
}
