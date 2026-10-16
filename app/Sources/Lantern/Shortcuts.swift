import Carbon.HIToolbox
import Foundation

/// The shortcut that opens the search panel. ⌘Space replaces Spotlight: macOS keeps that shortcut for Spotlight
/// until it is turned off in System Settings, so Lantern explains how (it never changes system settings itself).
enum SearchShortcut: String, CaseIterable, Identifiable {
    case optionSpace, commandSpace, controlOptionSpace

    static let key = "searchShortcut"
    static var current: SearchShortcut {
        UserDefaults.standard.string(forKey: key).flatMap(SearchShortcut.init(rawValue:)) ?? .optionSpace
    }

    var id: String { rawValue }

    var label: String {
        switch self {
        case .optionSpace: "⌥ Space"
        case .commandSpace: "⌘ Space"
        case .controlOptionSpace: "⌃⌥ Space"
        }
    }

    var carbonModifiers: UInt32 {
        switch self {
        case .optionSpace: UInt32(optionKey)
        case .commandSpace: UInt32(cmdKey)
        case .controlOptionSpace: UInt32(controlKey | optionKey)
        }
    }
}

/// Spotlight's own ⌘Space (symbolic hotkey 64 in com.apple.symbolichotkeys), read only.
enum SpotlightShortcut {
    /// True while ⌘Space still opens Spotlight (also when the user never changed it: on by default).
    static var isOn: Bool {
        let hotkeys = UserDefaults(suiteName: "com.apple.symbolichotkeys")?.dictionary(forKey: "AppleSymbolicHotKeys")
        guard let entry = hotkeys?["64"] as? [String: Any] else { return true }
        return (entry["enabled"] as? Bool) ?? ((entry["enabled"] as? Int).map { $0 != 0 } ?? true)
    }

    /// System Settings → Keyboard (Keyboard Shortcuts… → Spotlight is where it is turned off).
    static let keyboardSettings = URL(string: "x-apple.systempreferences:com.apple.Keyboard-Settings.extension")!
}
