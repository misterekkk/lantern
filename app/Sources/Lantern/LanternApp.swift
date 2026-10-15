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
    private var hotKey: HotKey?

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        MainActor.assumeIsolated {
            AppState.shared.start()
            hotKey = HotKey { Task { @MainActor in SearchPanelController.shared.toggle() } }
            if hotKey == nil { AppState.shared.lastMessage = String(localized: "The ⌥Space shortcut is used by another app.") }
            handleLaunchArguments()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { AppState.shared.supervisor.stop() }
    }

    /// Development flags: `--show-search "<query>"` opens the panel on launch; `--snapshot <dir>` then writes
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
        if args.contains("--show-menu") {
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                Self.statusItemButton()?.performClick(nil)
            }
        }
        guard let query = value(after: "--show-search") else { return }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            SearchPanelController.shared.show(query: query)
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

    /// Development: the menu bar button created by MenuBarExtra (to open the menu window for screenshots).
    @MainActor private static func statusItemButton() -> NSStatusBarButton? {
        func find(_ view: NSView?) -> NSStatusBarButton? {
            guard let view else { return nil }
            if let button = view as? NSStatusBarButton { return button }
            return view.subviews.lazy.compactMap { find($0) }.first
        }
        return NSApp.windows.lazy.compactMap { find($0.contentView) }.first
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
