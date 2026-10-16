import Foundation

/// Letter folding for matching typed text against document text, mirroring `lantern.textnorm.fold`.
/// Foundation's `.diacriticInsensitive` handles letters with combining marks (ą, ü) but, like SQLite FTS5, leaves
/// letters that are separate characters in Unicode: "lodz" does not find "Łódź", "soren" does not find "Søren".
public enum TextFold {
    /// Lowercase keys only: text is lowercased first. Keep in sync with `_FOLD` in textnorm.py.
    static let table: [Character: String] = [
        "ł": "l", "ø": "o", "đ": "d", "ð": "d", "ħ": "h", "ŧ": "t", "ŋ": "n", "ı": "i", "ĸ": "k", "ƀ": "b",
        "ǥ": "g", "ƚ": "l", "ɨ": "i", "ʉ": "u", "ß": "ss", "æ": "ae", "œ": "oe", "þ": "th",
    ]

    /// Lowercase, ligatures expanded, diacritics removed, the letters above spelled plainly.
    public static func fold(_ text: String) -> String {
        text.map(fold(character:)).joined()
    }

    static func fold(character: Character) -> String {
        let lower = String(character).precomposedStringWithCompatibilityMapping.lowercased()
        return lower.map { table[$0] ?? String($0) }.joined().folding(options: .diacriticInsensitive, locale: nil)
    }

    /// Where `needle` occurs in `text`, compared folded; UTF-16 ranges of the original text (NSString, PDFKit).
    public static func ranges(of needle: String, in text: String) -> [NSRange] {
        let target = Array(fold(needle).unicodeScalars)
        guard !target.isEmpty else { return [] }
        var scalars: [Unicode.Scalar] = []
        var source: [NSRange] = []  // the original character each folded scalar came from
        var offset = 0
        for character in text {
            let length = character.utf16.count
            for scalar in fold(character: character).unicodeScalars {
                scalars.append(scalar)
                source.append(NSRange(location: offset, length: length))
            }
            offset += length
        }
        guard scalars.count >= target.count else { return [] }
        var found: [NSRange] = []
        var i = 0
        while i <= scalars.count - target.count {
            if scalars[i] == target[0], Array(scalars[i..<(i + target.count)]) == target {
                let start = source[i].location
                found.append(NSRange(location: start, length: NSMaxRange(source[i + target.count - 1]) - start))
                i += target.count
            } else {
                i += 1
            }
        }
        return found
    }
}
