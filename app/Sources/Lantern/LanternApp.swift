import AppKit
import LanternCore
import SwiftUI
import UserNotifications

@main
struct LanternApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @ObservedObject private var state = AppState.shared

    var body: some Scene {
        MenuBarExtra {
            MenuView().environmentObject(state)
        } label: {
            Image(systemName: state.menuIcon)
                .contentTransition(.symbolEffect(.replace))
        }
        .menuBarExtraStyle(.window)
    }
}

/// Starts the daemon, registers ⌥Space, handles notification actions and stops the daemon on quit.
final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        MainActor.assumeIsolated {
            AppState.shared.start()
            AppState.shared.registerShortcut()
            ClipboardHistory.shared.start()
            MusicSync.shared.start()
            SpotifySync.shared.start()
            NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                                   object: nil, queue: .main) { _ in
                Task { @MainActor in SearchPanelController.shared.screensChanged() }
            }
            handleLaunchArguments()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { AppState.shared.supervisor.stop() }
    }

    /// Development flags: `--show-search "<query>"` opens the panel on launch (`--preview`: then ⌘Y on the first result); `--snapshot <dir>` then writes
    /// search.png and menu.png into <dir> (layout checks without screen-recording permission).
    @MainActor private func handleLaunchArguments() {
        let args = CommandLine.arguments
        func value(after flag: String) -> String? {
            guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        if args.contains("--show-settings") {
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                SettingsWindowController.shared.show()
            }
        }
        if args.contains("--music-dump") {  // development: what iTunesLibrary returns (asks for Media & Apple Music)
            if let (songs, playlists) = MusicLibrary.read() {
                let downloaded = songs.filter(\.downloaded).count
                print("music: \(songs.count) songs (\(downloaded) downloaded), \(playlists.count) playlists")
                for p in playlists.prefix(10) { print("  playlist \(p.name): \(p.songs.count) songs") }
                for s in songs.prefix(5) { print("  song \(s.title) — \(s.artist) · \(s.album) #\(s.track) \(s.id)") }
            } else {
                print("music: no access")
            }
            fflush(stdout)
        }
        if args.contains("--show-menu") {
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                MenuBarMenu.button()?.performClick(nil)
            }
        }
        guard let query = value(after: "--show-search") else { return }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            SearchPanelController.shared.show(query: query)
            if let name = value(after: "--category") {  // a filter pill chosen (development screenshots)
                SearchPanelController.shared.model.category = ResultCategory(rawValue: name)
            }
            if args.contains("--preview") {  // quick look of the first result (development screenshots)
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                SearchPanelController.shared.model.act(.preview)
            }
            guard let dir = value(after: "--snapshot") else { return }
            try? await Task.sleep(nanoseconds: 4_000_000_000)  // results arrive
            let folder = URL(fileURLWithPath: dir)
            SearchPanelController.shared.snapshot(to: folder.appendingPathComponent("search.png"))
            let renderer = ImageRenderer(content: MenuView().environmentObject(AppState.shared)
                .background(Color(nsColor: .windowBackgroundColor)))
            renderer.scale = 2
            if let image = renderer.nsImage, let tiff = image.tiffRepresentation,
               let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                try? png.write(to: folder.appendingPathComponent("menu.png"))
            }
            print("snapshots written to \(dir)")
            fflush(stdout)
        }
    }

    // Notification buttons: "Index Now" / "Later".
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                            withCompletionHandler completion: @escaping () -> Void) {
        let action = response.actionIdentifier
        Task { @MainActor in
            if action == "approve" { AppState.shared.approve() }
            if action == "decline" { AppState.shared.decline() }
            completion()
        }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler completion: @escaping (UNNotificationPresentationOptions) -> Void) {
        completion([.banner, .sound])
    }
}

/// The menu bar window of MenuBarExtra. SwiftUI has no API to dismiss it, so an action that opens a window closes it
/// the way a click on the icon does: the system's own quick fade, and the next click opens it again normally.
@MainActor enum MenuBarMenu {
    static func button() -> NSStatusBarButton? {
        func find(_ view: NSView?) -> NSStatusBarButton? {
            guard let view else { return nil }
            if let button = view as? NSStatusBarButton { return button }
            return view.subviews.lazy.compactMap { find($0) }.first
        }
        return NSApp.windows.lazy.compactMap { find($0.contentView) }.first
    }

    private static var openWindow: NSWindow? {
        NSApp.windows.first { $0.isVisible && String(describing: type(of: $0)).contains("MenuBarExtraWindow") }
    }

    static var isOpen: Bool { openWindow != nil }

    /// Fades out like a system menu after choosing an item (clicking elsewhere closes it at once, as MenuBarExtra
    /// already does). Measured on macOS 26 with CGWindowList: alpha falls exponentially, about half after 40 ms,
    /// 0.15 after 100 ms, gone after ~200 ms; this replays that curve, then closes through the icon so MenuBarExtra's
    /// own state stays right.
    static func close() {
        guard let window = openWindow else { return }
        let start = CACurrentMediaTime()
        let timer = Timer(timeInterval: 1.0 / 120, repeats: true) { timer in
            let t = CACurrentMediaTime() - start
            MainActor.assumeIsolated {
                guard t < 0.2 else {
                    timer.invalidate()
                    window.alphaValue = 0
                    button()?.performClick(nil)
                    Task { @MainActor in
                        try? await Task.sleep(nanoseconds: 250_000_000)  // hidden by now: ready for the next opening
                        window.alphaValue = 1
                    }
                    return
                }
                window.alphaValue = exp(-t / 0.055)
            }
        }
        RunLoop.main.add(timer, forMode: .common)
    }
}
