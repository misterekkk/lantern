import Foundation
import LanternCore

/// Runs the Python daemon as a child process of the app, so that macOS attributes file access (Desktop, Documents,
/// Downloads, iCloud Drive) to Lantern.app and asks the user once. If a daemon is already running (LaunchAgent,
/// `lantern daemon run`), the app uses it instead of starting a second one.
@MainActor
final class DaemonSupervisor {
    enum Mode: Equatable { case idle, external, child, failed(String) }

    private(set) var mode: Mode = .idle
    private var process: Process?
    private var quitting = false
    private var restarts = 0
    private let client = DaemonClient()

    /// Python of the project's virtual environment, baked into Info.plist by app/build.sh (LanternPython).
    private var python: String? {
        if let env = ProcessInfo.processInfo.environment["LANTERN_PYTHON"], !env.isEmpty { return env }
        return Bundle.main.object(forInfoDictionaryKey: "LanternPython") as? String
    }

    func start() async {
        if await client.ping() {
            mode = .external
            return
        }
        launch()
    }

    private func launch() {
        guard let python, FileManager.default.isExecutableFile(atPath: python) else {
            mode = .failed(String(localized: "Lantern's Python was not found (LanternPython in Info.plist). Build the app with app/build.sh."))
            return
        }
        let logs = URL(fileURLWithPath: DaemonClient.dataDirectory).appendingPathComponent("logs")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let log = logs.appendingPathComponent("daemon.log")
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: nil)
        }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "lantern.cli", "daemon", "run"]
        var env = ProcessInfo.processInfo.environment
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        env["DO_NOT_TRACK"] = "1"
        env["LANTERN_NOTIFY"] = "0"  // the app posts its own notifications (with actions)
        env["LANTERN_EXIT_WITH_PARENT"] = "1"  // never leave an orphaned daemon if the app crashes
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        p.environment = env
        if let handle = try? FileHandle(forWritingTo: log) {
            handle.seekToEndOfFile()
            p.standardOutput = handle
            p.standardError = handle
        }
        p.terminationHandler = { [weak self] proc in
            Task { @MainActor in self?.terminated(status: proc.terminationStatus) }
        }
        do {
            try p.run()
            process = p
            mode = .child
        } catch {
            mode = .failed(String(localized: "Could not start the daemon: \(error.localizedDescription)"))
        }
    }

    private func terminated(status: Int32) {
        process = nil
        guard !quitting else { return }
        restarts += 1
        if restarts > 5 {
            mode = .failed(String(localized: "The daemon keeps exiting with an error (code \(status)). Details: logs/daemon.log"))
            return
        }
        let delay = UInt64(min(30, 1 << restarts)) * 1_000_000_000
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: delay)
            if !self.quitting { self.launch() }
        }
    }

    /// Ask the daemon to finish the current file and exit (called when the app quits).
    func stop() {
        quitting = true
        guard let process, process.isRunning else { return }
        process.terminate()  // SIGTERM: the daemon stops its worker gracefully
        let deadline = Date().addingTimeInterval(10)
        while process.isRunning && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    }
}
