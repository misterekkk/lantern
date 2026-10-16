import Foundation

/// Which words of a query may go to the spell checker: plain words of 4+ letters. Identifiers (snake_case,
/// camelCase), acronyms, words with digits or dots and code-looking queries are left alone: the spell checker does not
/// know them, and the daemon would keep them anyway when the index knows them.
public enum SpellingWords {
    public static func candidates(in query: String) -> [String] {
        if query.contains("(") || query.contains("{") || query.contains("=") { return [] }
        var words: [String] = []
        for token in query.split(whereSeparator: { $0.isWhitespace }) {
            let word = String(token)
            guard word.count >= 4, word.allSatisfy(\.isLetter) else { continue }
            let rest = word.dropFirst()
            guard !rest.contains(where: \.isUppercase) else { continue }  // camelCase, USB
            if !words.contains(word) { words.append(word) }
        }
        return words
    }
}
