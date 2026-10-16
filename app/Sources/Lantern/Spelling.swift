import AppKit
import LanternCore

/// Spell-checker guesses for the words of a query, from macOS (NSSpellChecker, on the Mac, nothing learned or sent).
/// A word counts as misspelled only when none of the user's languages knows it ("dolar" is Polish, not a misspelled
/// "dollar"). The checker's own first choice is often wrong, so every guess goes to the daemon, which keeps only a
/// guess its index knows (`search/spelling.py`).
@MainActor
enum Spelling {
    static func guesses(for query: String) -> [String: [String]] {
        let checker = NSSpellChecker.shared
        let available = Set(checker.availableLanguages)
        let languages = userLanguages().compactMap { code -> String? in
            let base = String(code.prefix { $0 != "-" && $0 != "_" })
            return available.contains(base) ? base : nil
        }
        guard !languages.isEmpty else { return [:] }
        var found: [String: [String]] = [:]
        for word in SpellingWords.candidates(in: query) {
            let range = NSRange(location: 0, length: (word as NSString).length)
            var options: [String] = []
            var known = false
            for language in Array(NSOrderedSet(array: languages)) as? [String] ?? languages {
                let miss = checker.checkSpelling(of: word, startingAt: 0, language: language, wrap: false,
                                                 inSpellDocumentWithTag: 0, wordCount: nil)
                if miss.location == NSNotFound {
                    known = true
                    break
                }
                options += checker.guesses(forWordRange: range, in: word, language: language,
                                           inSpellDocumentWithTag: 0) ?? []
            }
            let unique = Array(NSOrderedSet(array: options)) as? [String] ?? options
            if !known && !unique.isEmpty { found[word] = Array(unique.prefix(8)) }
        }
        return found
    }

    /// The languages set in macOS (plus the ones added in Lantern's settings), not the app's own: Lantern may run in
    /// English while the user writes Polish.
    private static func userLanguages() -> [String] {
        if let effective = AppState.shared.languages?.effective, !effective.isEmpty { return effective }
        let global = CFPreferencesCopyValue("AppleLanguages" as CFString, kCFPreferencesAnyApplication,
                                            kCFPreferencesCurrentUser, kCFPreferencesAnyHost) as? [String]
        return (global ?? Locale.preferredLanguages) + ["en"]
    }
}
