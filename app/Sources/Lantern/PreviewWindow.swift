import AppKit
import LanternCore
import SwiftUI

/// `lantern://preview?path=<file>&ms=<moment>[&text=<passage>]`: the panel's own preview of a recording (the whole
/// file, started at the moment, scrubbable back to 0:00) in a small floating window, for Raycast's ⌘Y: Quick Look
/// cannot start at a moment. ⌘Y or Esc closes it, as in the panel. Only files in the index are opened: any web page
/// can ask for a `lantern://` URL.
@MainActor final class PreviewWindowController {
    static let shared = PreviewWindowController()
    private var window: NSPanel?

    func open(_ url: URL) {
        guard url.host == "preview", let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let path = items.first(where: { $0.name == "path" })?.value, path.hasPrefix("/") else { return }
        let ms = items.first(where: { $0.name == "ms" })?.value.flatMap(Int.init)
        let text = String((items.first(where: { $0.name == "text" })?.value ?? "").prefix(400))
        Task {
            struct File: Decodable { let indexed: Bool; let kind: String? }
            guard let file = try? await AppState.shared.client.call("file", ["path": path], as: File.self, timeout: 5),
                  file.indexed else {
                NSSound.beep()  // not a file Lantern indexed (or the daemon is not answering): nothing is opened
                return
            }
            let chunkKind = file.kind == "audio" ? "audio" : "frame"
            show(SearchResult(path: path, chunkId: -1, preview: text, kind: chunkKind, startMs: ms ?? 0))
        }
    }

    private func show(_ result: SearchResult) {
        window?.close()
        let audio = result.kind == "audio"
        let view = PreviewWindowView(result: result) { [weak self] in self?.window?.close() }
        let panel = NSPanel(contentViewController: NSHostingController(rootView: view))
        panel.styleMask = [.titled, .closable, .fullSizeContentView, .utilityWindow]
        panel.titlebarAppearsTransparent = true
        panel.title = Format.title(result)
        panel.level = .floating
        panel.isReleasedWhenClosed = false
        panel.setContentSize(NSSize(width: 600, height: audio ? 190 : 430))
        panel.center()
        window = panel
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
    }
}

private struct PreviewWindowView: View {
    let result: SearchResult
    let close: () -> Void

    var body: some View {
        PreviewView(content: .media(result))
            .padding(.top, 18)
            .background {  // ⌘Y closes the preview, as in the panel; Esc too
                Button("", action: close).keyboardShortcut("y", modifiers: .command).hidden()
                Button("", action: close).keyboardShortcut(.cancelAction).hidden()
            }
    }
}
