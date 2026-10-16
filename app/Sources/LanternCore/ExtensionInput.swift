import Foundation

/// The ignored-extensions field: typed text becomes pills. `csv`, `.csv` and `*.csv` all mean ".csv" (same rule as
/// the daemon's normalize_extension); a comma, space, semicolon or newline completes a pill, the rest stays typed.
public enum ExtensionInput {
    public static func normalize(_ raw: String) -> String? {
        var ext = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        while ext.hasPrefix("*") { ext.removeFirst() }
        if !ext.hasPrefix(".") { ext = "." + ext }
        let body = ext.dropFirst()
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "._+-"))
        guard !body.isEmpty, ext.count <= 24, !body.hasPrefix("."),
              body.unicodeScalars.allSatisfy({ allowed.contains($0) })
        else { return nil }
        return ext
    }

    /// Splits typed text at separators: completed (normalised, valid) extensions and the unfinished remainder.
    public static func split(_ text: String) -> (completed: [String], invalid: [String], remainder: String) {
        let separators = CharacterSet(charactersIn: ", ;\n\t")
        var parts = text.components(separatedBy: separators)
        let remainder = parts.removeLast()  // text after the last separator is still being typed
        var completed: [String] = [], invalid: [String] = []
        for part in parts where !part.trimmingCharacters(in: .whitespaces).isEmpty {
            if let ext = normalize(part) { if !completed.contains(ext) { completed.append(ext) } } else { invalid.append(part) }
        }
        return (completed, invalid, remainder)
    }

    /// Suggestions for what is being typed: known/indexed extensions not ignored yet, matching the typed prefix;
    /// the most common in the index first.
    public static func suggestions(
        for typed: String, from items: [ExtensionSuggestions.Item], ignored: [String], limit: Int = 8
    ) -> [ExtensionSuggestions.Item] {
        var prefix = typed.trimmingCharacters(in: .whitespaces).lowercased()
        while prefix.hasPrefix("*") { prefix.removeFirst() }
        if !prefix.isEmpty && !prefix.hasPrefix(".") { prefix = "." + prefix }
        return items
            .filter { !ignored.contains($0.ext) && (prefix.isEmpty || $0.ext.hasPrefix(prefix)) }
            .sorted { ($0.indexed, $1.ext) > ($1.indexed, $0.ext) }
            .prefix(limit)
            .map { $0 }
    }
}

/// What a key or click does with a result (the panel's shortcuts; same idea for code, text and media).
public enum OpenAction: Equatable {
    case atLocation  // ↩ / double-click: code and text at the line, media at the moment
    case fromStart  // ⌥↩ / ⌥ double-click: top of the file, beginning of the recording
    case reveal  // ⌘↩
    case preview  // ⌘Y: quick look inside Lantern
}

public enum MediaOpen {
    /// Files QuickTime Player plays (plan B); others (mkv, webm, …) fall back to Lantern's preview (plan A).
    public static let quickTimeExtensions: Set<String> = [
        "mov", "mp4", "m4v", "m4a", "mp3", "wav", "aiff", "aif", "aac", "caf", "3gp",
    ]
    public static func quickTimePlays(_ path: String) -> Bool {
        quickTimeExtensions.contains((path as NSString).pathExtension.lowercased())
    }
    /// Start a few seconds before the match so the sentence is heard from its beginning.
    public static func startSeconds(_ startMs: Int?) -> Double {
        max(0, Double(startMs ?? 0) / 1000 - 3)
    }
}
