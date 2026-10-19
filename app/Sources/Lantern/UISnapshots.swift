import AppKit
import LanternCore
import SwiftUI

/// Development: `--ui-snapshot <dir>` renders the menu in each state (statuses written as the daemon sends them) and
/// the welcome window's steps, as PNGs, without a daemon. Run with `-AppleLanguages "(pl)"` or `"(en)"` to check the
/// wording in each language. ImageRenderer draws SwiftUI only: system controls may come out as placeholders.
@MainActor enum UISnapshots {
    private static let base: [String: Any] = [
        "pid": 4595, "memory_mb": 430, "mode": "auto", "approved": true, "awaiting_approval": false, "paused": NSNull(),
        "worker": ["running": false], "queued": [String: Int](), "failed_jobs": 0,
        "files": ["indexed": 5663, "skipped": 33], "chunks": 61773, "roots": [[String: Any]](), "warnings": [String](),
        "warning_events": [[String: Any]](), "search_mode": "ready",
    ]

    private static let states: [(String, [String: Any])] = [
        ("menu-1-ready", [:]),
        ("menu-2-setup", ["models_missing": true]),
        ("menu-3-downloading", ["models_missing": true,
                                "model_download": ["running": true, "key": "embeddinggemma", "step": 1, "total": 1,
                                                   "downloaded_mb": 540, "approx_mb": 1200]]),
        ("menu-4-download-failed", ["models_missing": true,
                                    "model_download": ["running": false, "error": "download failed (code 1)"]]),
        ("menu-5-index-newer", ["index_problem": "newer", "files": [String: Int](), "chunks": 0]),
        ("menu-6-index-damaged", ["index_problem": "damaged", "files": [String: Int](), "chunks": 0]),
        ("menu-7-memory-pause", ["paused": "memory",
                                 "warnings": ["00:28:29 indexing paused: the Mac is short of memory; it resumes by itself"],
                                 "warning_events": [["time": "00:28:29", "code": "memory_pause", "args": [String: String](),
                                                     "message": "indexing paused: the Mac is short of memory"]]]),
        ("menu-8-indexing", ["worker": ["running": true, "stage": "people", "done": 120, "total": 250,
                                        "path": "/Users/x/Desktop/Portugalia/IMG_6794.JPG"]]),
    ]

    /// Development: `--preview-state <name>` (e.g. `menu-3-downloading`) shows that state in the real menu, for a real
    /// screenshot of the window (system controls included).
    static func preview(_ name: String) async {
        guard let change = states.first(where: { $0.0 == name })?.1 else { return }
        let json = base.merging(change) { _, new in new }
        guard let data = try? JSONSerialization.data(withJSONObject: json),
              let status = try? DaemonClient.decoder.decode(DaemonStatus.self, from: data) else { return }
        AppState.shared.preview(status, setup: sampleSetup)
        try? await Task.sleep(nanoseconds: 500_000_000)
        MenuBarMenu.button()?.performClick(nil)
    }

    private static var sampleSetup: SetupInfo? {
        try? DaemonClient.decoder.decode(SetupInfo.self, from: Data("""
            {"ram_gb": 8, "profile": "light", "suggested_mode": "on_demand", "recommended_mb": 1200, "models": [
             {"key": "embeddinggemma", "group": "required", "label": "", "approx_mb": 1200, "downloaded": false,
              "recommended": true, "heavy": false, "needs_gb": 0},
             {"key": "whisper", "group": "whisper", "label": "", "approx_mb": 1600, "downloaded": false,
              "recommended": false, "heavy": true, "needs_gb": 1.8}]}
            """.utf8))
    }

    static func run(to dir: URL) async {
        let setup = try? DaemonClient.decoder.decode(SetupInfo.self, from: Data("""
            {"ram_gb": 8, "profile": "light", "suggested_mode": "on_demand", "recommended_mb": 1200, "models": [
             {"key": "embeddinggemma", "group": "required", "label": "", "approx_mb": 1200, "downloaded": false,
              "recommended": true, "heavy": false, "needs_gb": 0},
             {"key": "whisper", "group": "whisper", "label": "", "approx_mb": 1600, "downloaded": false,
              "recommended": false, "heavy": true, "needs_gb": 1.8}]}
            """.utf8))
        for (name, change) in states {
            let json = base.merging(change) { _, new in new }
            guard let data = try? JSONSerialization.data(withJSONObject: json),
                  let status = try? DaemonClient.decoder.decode(DaemonStatus.self, from: data) else {
                print("ui snapshot: could not build \(name)")
                continue
            }
            AppState.shared.preview(status, setup: setup)
            try? await Task.sleep(nanoseconds: 300_000_000)
            render(MenuView().environmentObject(AppState.shared).frame(width: 300), to: dir.appendingPathComponent("\(name).png"))
        }
        print("ui snapshots written to \(dir.path)")
        fflush(stdout)
        await WelcomeWindowController.shared.snapshots(to: dir)
        NSApp.terminate(nil)
    }

    private static func render(_ view: some View, to url: URL) {
        let renderer = ImageRenderer(content: view.background(Color(nsColor: .windowBackgroundColor)))
        renderer.scale = 2
        guard let image = renderer.cgImage else { return }
        try? NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: url)
    }
}
