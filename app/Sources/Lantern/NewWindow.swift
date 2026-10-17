import AppKit

/// ⌥↩ on an application: a new window instead of the existing one (↩ brings the app forward with its windows, as in
/// Spotlight). Browsers take `--new-window`, Safari and Finder their own scripting; other apps get their menu's
/// "New Window" pressed when menu items are on (Settings → Privacy, Accessibility), else they just come forward.
/// Single-window apps (Discord) simply come forward.
@MainActor
enum NewWindow {
    private static let browsers: Set<String> = [
        "com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.canary", "com.microsoft.edgemac",
        "com.brave.Browser", "com.vivaldi.Vivaldi", "com.operasoftware.Opera", "org.chromium.Chromium",
        "org.mozilla.firefox",
    ]
    private static let titles = ["New Window", "Nowe okno", "Neues Fenster", "Nouvelle fenêtre", "Nueva ventana"]

    static func open(_ app: URL) {
        let bundle = Bundle(url: app)?.bundleIdentifier ?? ""
        if browsers.contains(bundle) {
            // A second launch with the flag: the running browser opens the window (its single-instance handoff).
            let config = NSWorkspace.OpenConfiguration()
            config.arguments = ["--new-window"]
            config.createsNewApplicationInstance = NSRunningApplication.runningApplications(withBundleIdentifier: bundle)
                .isEmpty == false
            NSWorkspace.shared.openApplication(at: app, configuration: config)
            return
        }
        let script: String? = switch bundle {
        case "com.apple.Safari": "tell application \"Safari\"\nmake new document\nactivate\nend tell"
        case "com.apple.finder": "tell application \"Finder\"\nmake new Finder window\nactivate\nend tell"
        default: nil
        }
        if let script {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            process.arguments = ["-e", script]
            try? process.run()
            return
        }
        NSWorkspace.shared.openApplication(at: app, configuration: NSWorkspace.OpenConfiguration()) { running, _ in
            guard let running, UserDefaults.standard.bool(forKey: MenuCommands.key) else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { MenuCommands.press(titles, in: running) }
        }
    }
}
