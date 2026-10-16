import AppKit
import LanternCore

/// Opens a file in the app macOS uses for its type, at a line when that app supports it (LineJump).
enum EditorOpener {
    static func open(_ url: URL, line: Int?) {
        guard let line, let app = NSWorkspace.shared.urlForApplication(toOpen: url),
              let bundle = Bundle(url: app), let id = bundle.bundleIdentifier
        else {
            NSWorkspace.shared.open(url)
            return
        }
        switch LineJump.make(bundleId: id, appURL: app, executable: bundle.executableURL, file: url.path, line: line) {
        case .url(let editorURL):
            if !NSWorkspace.shared.open(editorURL) { NSWorkspace.shared.open(url) }
        case .command(let executable, let arguments, let environment):
            let process = Process()
            process.executableURL = URL(fileURLWithPath: executable)
            process.arguments = arguments
            process.environment = ProcessInfo.processInfo.environment.merging(environment) { $1 }
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            process.terminationHandler = { p in  // the launcher failed: still open the file
                if p.terminationStatus != 0 { DispatchQueue.main.async { NSWorkspace.shared.open(url) } }
            }
            do { try process.run() } catch { NSWorkspace.shared.open(url) }
        case .plain:
            NSWorkspace.shared.open(url)
        }
    }
}
