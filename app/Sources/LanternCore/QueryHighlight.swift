import Foundation

/// The parts of a result's text that match the query's words, so the panel can show why it matched.
/// Words are compared folded (`TextFold`: "lodz" marks "Łódź") and by their start, so Polish endings may differ
/// ("faktura" marks "fakturę"); the whole word is marked. Short and common words are skipped.
public enum QueryHighlight {
    static let skipped: Set<String> = [
        "the", "and", "for", "with", "about", "when", "that", "this", "from", "into", "what", "where", "was", "were",
        "jak", "dla", "czy", "nie", "sie", "oraz", "przez", "przy", "jest", "gdzie", "kiedy", "albo", "lub",
    ]

    /// Stems to look for: query words of 3+ characters (2+ for numbers), long words without their ending.
    public static func stems(_ query: String) -> [String] {
        var seen = Set<String>()
        return query.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).compactMap { part in
            let word = TextFold.fold(String(part))
            let isNumber = word.allSatisfy(\.isNumber)
            guard word.count >= (isNumber ? 2 : 3), !skipped.contains(word) else { return nil }
            let stem = word.count >= 7 ? String(word.prefix(word.count - 2)) : word
            return seen.insert(stem).inserted ? stem : nil
        }
    }

    /// UTF-16 ranges of whole words in `text` that start with one of the query's stems, sorted, not overlapping.
    public static func ranges(query: String, in text: String) -> [NSRange] {
        let ns = text as NSString
        let wordChars = CharacterSet.alphanumerics
        func isWordChar(_ i: Int) -> Bool {
            guard i >= 0, i < ns.length, let scalar = Unicode.Scalar(ns.character(at: i)) else { return false }
            return wordChars.contains(scalar)
        }
        var found: [NSRange] = []
        for stem in stems(query) {
            for hit in TextFold.ranges(of: stem, in: text) where !isWordChar(hit.location - 1) {
                var end = NSMaxRange(hit)
                while isWordChar(end) { end += 1 }
                found.append(NSRange(location: hit.location, length: end - hit.location))
            }
        }
        found.sort { $0.location < $1.location }
        var merged: [NSRange] = []
        for range in found {
            if let last = merged.last, range.location <= NSMaxRange(last) {
                merged[merged.count - 1] = NSUnionRange(last, range)
            } else {
                merged.append(range)
            }
        }
        return merged
    }
}
