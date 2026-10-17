import AppKit
import SwiftUI

// Building blocks shared by the settings panes, modelled on System Settings: a header card (large icon, title, one
// line of description), rows with small coloured icons, grouped forms and a soft fade where content scrolls under
// the toolbar.

/// A rounded, gradient-filled square with a white SF Symbol (sidebar and row icons).
struct SettingsIcon: View {
    let symbol: String
    let color: Color
    var size: CGFloat = 22

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: size * 0.5, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(RoundedRectangle(cornerRadius: size * 0.27, style: .continuous).fill(color.gradient))
    }
}

/// The card at the top of a pane: icon, title and what the pane is for.
struct PaneHeader: View {
    let symbol: String
    let color: Color
    let title: LocalizedStringKey
    let text: LocalizedStringKey

    var body: some View {
        Section {
            VStack(spacing: 8) {
                SettingsIcon(symbol: symbol, color: color, size: 54)
                    .shadow(color: .black.opacity(0.15), radius: 3, y: 1)
                Text(title).font(.title2.weight(.semibold))
                Text(text)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .padding(.horizontal, 24)
        }
    }
}

/// A grouped form with the system's soft edge where rows slide under the toolbar.
struct SettingsPage<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        Form { content }
            .formStyle(.grouped)
            .softScrollEdge()
    }
}

extension View {
    /// macOS 26: the system's soft scroll edge (content fades under the toolbar instead of being cut off).
    /// Earlier systems: the same look with a gradient mask at the top.
    @ViewBuilder func softScrollEdge() -> some View {
        if #available(macOS 26, *) {
            scrollEdgeEffectStyle(.soft, for: .top)
        } else {
            mask(
                VStack(spacing: 0) {
                    LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom).frame(height: 18)
                    Rectangle()
                }
            )
        }
    }
}

/// App language: follows macOS unless the user picks one (stored per app, like System Settings → Language & Region
/// → Applications). Takes effect after a restart.
enum AppLanguage: String, CaseIterable, Identifiable {
    case system, pl, en
    var id: String { rawValue }

    var title: String {
        switch self {
        case .system: String(localized: "Same as macOS")
        case .pl: "Polski"
        case .en: "English"
        }
    }

    static var current: AppLanguage {
        let domain = UserDefaults.standard.persistentDomain(forName: Bundle.main.bundleIdentifier ?? "") ?? [:]
        guard let langs = domain["AppleLanguages"] as? [String], let first = langs.first else { return .system }
        return AppLanguage(rawValue: String(first.prefix(2))) ?? .system
    }

    func apply() {
        if self == .system {
            UserDefaults.standard.removeObject(forKey: "AppleLanguages")
        } else {
            UserDefaults.standard.set([rawValue], forKey: "AppleLanguages")
        }
    }

    /// Quit (the daemon stops with the app) and open Lantern again once this process is gone.
    @MainActor static func relaunch() {
        let pid = ProcessInfo.processInfo.processIdentifier
        let path = Bundle.main.bundlePath.replacingOccurrences(of: "'", with: "'\\''")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "while kill -0 \(pid) 2>/dev/null; do sleep 0.2; done; open '\(path)'"]
        try? p.run()
        NSApp.terminate(nil)
    }
}
