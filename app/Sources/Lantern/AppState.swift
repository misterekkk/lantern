import AppKit
import Foundation
import LanternCore
import ServiceManagement
import UserNotifications

/// App-wide state: daemon status (polled), settings, and the actions behind the menu and settings windows.
@MainActor
final class AppState: ObservableObject {
    static let shared = AppState()

    @Published private(set) var status: DaemonStatus?
    @Published private(set) var settings: DaemonSettings?
    @Published private(set) var connectionError: String?
    @Published var lastMessage: String?

    let client = DaemonClient()
    let supervisor = DaemonSupervisor()
    private var timer: Timer?
    private var wasAwaiting = false

    enum Phase { case starting, offline, problem, setup, paused, awaitingApproval, indexing, idle }

    var phase: Phase {
        guard let status else { return connectionError == nil ? .starting : .offline }
        if status.indexProblem != nil { return .problem }
        if status.modelDownload?.running == true { return .indexing }
        if status.modelsMissing == true { return .setup }
        if status.paused != nil { return .paused }
        if status.awaitingApproval || status.waitingForSettings == true { return .awaitingApproval }
        if status.worker.running { return .indexing }
        return .idle
    }

    var menuIcon: String {
        switch phase {
        case .starting, .offline: "magnifyingglass"
        case .paused: "pause.circle"
        case .problem: "exclamationmark.triangle"
        case .setup: "arrow.down.circle"  // setup not finished: download the search model (not "approve files")
        case .awaitingApproval: "exclamationmark.magnifyingglass"
        case .indexing: "arrow.triangle.2.circlepath"
        case .idle: "sparkle.magnifyingglass"
        }
    }

    // MARK: - Lifecycle

    private var hotKey: HotKey?
    @Published private(set) var shortcut = SearchShortcut.current

    /// (Re)registers the search shortcut chosen in Settings → General.
    func registerShortcut() {
        hotKey?.invalidate()  // releases the old one first
        hotKey = nil
        shortcut = SearchShortcut.current
        guard SearchLauncher.current == .lantern else {  // Raycast or Alfred opens instead and shows Lantern's results
            shortcutNeedsRetry = false
            return
        }
        hotKey = HotKey(modifiers: shortcut.carbonModifiers) { Task { @MainActor in SearchPanelController.shared.toggle() } }
        if hotKey == nil {
            lastMessage = String(localized: "The \(shortcut.label) shortcut is used by another app.")
        }
        shortcutNeedsRetry = shortcut == .commandSpace && SpotlightShortcut.isOn
    }

    /// ⌘Space chosen while Spotlight still had it: register again once Spotlight lets go.
    private(set) var shortcutNeedsRetry = false

    func start() {
        Task {
            await supervisor.start()
            if case .failed(let reason) = supervisor.mode { connectionError = reason }
            await refresh()
            await loadSettings()
        }
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refresh() }
        }
        setUpNotifications()
    }

    /// Development (`--ui-snapshot`): a status set by hand stays, the 2-second poll leaves it alone.
    private(set) var previewing = false

    func preview(_ status: DaemonStatus, setup: SetupInfo? = nil) {
        previewing = true
        self.status = status
        self.connectionError = nil
        if let setup { self.setup = setup }
    }

    func refresh() async {
        guard !previewing else { return }
        do {
            // The open Settings window holds back new indexing (the daemon releases it when we stop saying so).
            let s = try await client.call("status", ["settings_open": SettingsWindowController.shared.isOpen],
                                          as: DaemonStatus.self, timeout: 5)
            let justGotModel = status?.modelsMissing == true && s.modelsMissing != true
            if status == nil || justGotModel {
                SearchPanelController.shared.daemonCameUp()  // a search made too early: again
                MusicSync.shared.syncIfChanged(force: true)  // a new daemon may have an older copy of the library
            }
            status = s
            connectionError = nil
            if settings == nil { await loadSettings() }  // the daemon was still starting when the app asked
            if s.awaitingApproval && !wasAwaiting && s.waitingForSettings != true { notifyApprovalNeeded(s) }
            showWelcomeIfNeeded()
            wasAwaiting = s.awaitingApproval
        } catch {
            status = nil
            if case .failed(let reason) = supervisor.mode {
                connectionError = reason
            } else {
                connectionError = (error as? LocalizedError)?.errorDescription ?? "\(error)"
            }
        }
    }

    @Published var languages: LanguageList?

    func loadLanguages() async {
        languages = try? await client.call("languages", as: LanguageList.self)
    }

    func changeLanguage(_ action: String, _ code: String) {
        Task {
            languages = try? await client.call("languages", ["action": action, "language": code], as: LanguageList.self)
        }
    }

    func loadSettings() async {
        settings = try? await client.call("config_get", as: DaemonSettings.self)
    }

    // MARK: - Actions

    private func run(_ method: String, _ params: [String: Any] = [:], message: String? = nil) {
        Task {
            do {
                try await client.callRaw(method, params, timeout: 600)
                if let message { lastMessage = message }
            } catch {
                lastMessage = (error as? LocalizedError)?.errorDescription ?? "\(error)"
            }
            await refresh()
            await loadSettings()
        }
    }

    /// This Mac's memory, profile and models (welcome window, the menu's download card).
    @Published var setup: SetupInfo?

    func loadSetup() async {
        setup = try? await client.call("setup", as: SetupInfo.self)
    }

    func rebuildIndex() { run("rebuild_index", message: String(localized: "Rebuilding the index…")) }

    /// Settings → About: the daemon's report (Markdown, no file names or contents) on the clipboard, for a bug report.
    func copyDiagnostics() {
        Task {
            struct Report: Decodable { let text: String }
            do {
                let r = try await client.call("diagnostics", as: Report.self, timeout: 20)
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(r.text, forType: .string)
                lastMessage = String(localized: "Diagnostics copied. Paste them into your report.")
            } catch {
                lastMessage = (error as? LocalizedError)?.errorDescription ?? "\(error)"
            }
        }
    }

    /// One folder from the welcome window (indexing starts at once: choosing it is the approval).
    func addFolder(_ path: String) { run("index", ["path": path]) }

    // MARK: - Welcome

    static let welcomeDoneKey = "welcomeDone"
    private var welcomeChecked = false

    /// First launch: the welcome window. Someone updating from an older Lantern (folders indexed, model present) is
    /// not asked again. `--welcome` shows it anyway and never records it as done (development screenshots).
    func showWelcomeIfNeeded() {
        guard !welcomeChecked, let s = status else { return }
        welcomeChecked = true
        let forced = CommandLine.arguments.contains("--welcome")
        if !forced {
            if UserDefaults.standard.bool(forKey: Self.welcomeDoneKey) { return }
            if !s.roots.isEmpty && s.modelsMissing != true {
                UserDefaults.standard.set(true, forKey: Self.welcomeDoneKey)
                return
            }
        }
        WelcomeWindowController.shared.show()
    }

    /// The welcome window closed before the search model was downloaded: one reminder, with this Mac's sizes.
    func notifySetupUnfinished() {
        guard status?.modelsMissing == true else { return }
        Task {
            if setup == nil { await loadSetup() }
            let gb = Double(setup?.recommendedMb ?? 1200) / 1000
            let content = UNMutableNotificationContent()
            content.title = String(localized: "Finish setting up Lantern")
            content.body = String(localized: "Download the search model (about \(gb.formatted(.number.precision(.fractionLength(1)))) GB). It takes one click; after that Lantern works offline.")
            content.categoryIdentifier = "setup"
            try? await UNUserNotificationCenter.current().add(
                UNNotificationRequest(identifier: "setup", content: content, trigger: nil))
        }
    }

    func approve() { run("approve") }
    func decline() { run("decline") }
    func pause() { run("pause") }
    func resume() { run("resume") }
    func downloadModels(_ models: [String]) { run("download_models", ["models": models]) }
    func setConfig(_ key: String, _ value: String) { run("config_set", ["key": key, "value": value]) }
    func addExclude(_ pattern: String) { run("exclude", ["action": "add", "pattern": pattern]) }
    func removeExclude(_ pattern: String) { run("exclude", ["action": "remove", "pattern": pattern]) }
    func addExtension(_ ext: String) { run("extensions", ["action": "add", "extension": ext]) }
    func removeExtension(_ ext: String) { run("extensions", ["action": "remove", "extension": ext]) }
    /// Check/uncheck in the folder tree; the daemon rewrites path exclusions and rescans.
    func setIncluded(_ path: String, _ included: Bool) async {
        do {
            try await client.callRaw("tree_set", ["path": path, "included": included], timeout: 60)
        } catch {
            lastMessage = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        }
        await loadSettings()
    }
    func removeFolder(_ path: String) { run("remove", ["path": path], message: String(localized: "Removed \(Format.shortPath(path)) from the index.")) }
    func clearHistory() { run("clear_history", message: String(localized: "Search history deleted.")) }
    /// Compacts the index and returns a line for the window that asked (sizes before and after).
    func compact() async -> String {
        struct Sizes: Decodable { let before: Int; let after: Int }
        do {
            let r = try await client.call("compact", as: Sizes.self, timeout: 600)
            await refresh()
            let mb = { (bytes: Int) in String(format: "%.1f MB", Double(bytes) / 1_048_576) }
            return String(localized: "Done: \(mb(r.before)) → \(mb(r.after)). Deleted data is erased from the file.")
        } catch {
            return (error as? LocalizedError)?.errorDescription ?? "\(error)"
        }
    }

    /// Folders most people want searched: the standard ones and common code folders that exist on this Mac.
    static var commonFolders: [String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let names = ["Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music",
                     "Developer", "Projects", "Projekty", "Code", "src", "dev", "repos", "GitHub"]
        return names.map { "\(home)/\($0)" }.filter { path in
            var isDir: ObjCBool = false
            return FileManager.default.fileExists(atPath: path, isDirectory: &isDir) && isDir.boolValue
        }
    }

    /// Folders from `commonFolders` that are not indexed yet (nor inside an indexed folder).
    var missingCommonFolders: [String] {
        let roots = status?.roots.map(\.path) ?? []
        return Self.commonFolders.filter { folder in !roots.contains { folder == $0 || folder.hasPrefix($0 + "/") } }
    }

    /// One click instead of picking them one by one; removing the unwanted ones afterwards is easier than adding.
    /// With Settings open, indexing waits until they close (the folder tree can still exclude parts).
    func addCommonFolders() {
        let folders = missingCommonFolders
        guard !folders.isEmpty else { return }
        Task {
            for folder in folders { _ = try? await client.callRaw("index", ["path": folder], timeout: 600) }
            lastMessage = String(localized: "Added \(folders.count) folders.")
            await refresh()
        }
    }

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = String(localized: "Index")
        panel.message = String(localized: "Choose a folder for Lantern to search.")
        NSApp.activate(ignoringOtherApps: true)
        if panel.runModal() == .OK, let url = panel.url {
            run("index", ["path": url.path], message: String(localized: "Added \(Format.shortPath(url.path)). Indexing runs in the background."))
        }
    }

    // MARK: - Login item

    var launchAtLogin: Bool {
        get { SMAppService.mainApp.status == .enabled }
        set {
            do {
                if newValue { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                lastMessage = String(localized: "Could not change launch at login: \(error.localizedDescription)")
            }
            objectWillChange.send()
        }
    }

    // MARK: - Notifications

    private func setUpNotifications() {
        let center = UNUserNotificationCenter.current()
        let index = UNNotificationAction(identifier: "approve", title: String(localized: "Index Now"), options: [])
        let later = UNNotificationAction(identifier: "decline", title: String(localized: "Later"), options: [])
        let finish = UNNotificationAction(identifier: "setup", title: String(localized: "Finish Setup"), options: [.foreground])
        center.setNotificationCategories([
            UNNotificationCategory(identifier: "approval", actions: [index, later], intentIdentifiers: []),
            UNNotificationCategory(identifier: "setup", actions: [finish], intentIdentifiers: []),
        ])
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private func notifyApprovalNeeded(_ s: DaemonStatus) {
        let content = UNMutableNotificationContent()
        content.title = "Lantern"
        content.body = String(localized: "Found \(s.queuedTotal) new or changed files. Index them now?")
        content.categoryIdentifier = "approval"
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "approval", content: content, trigger: nil))
    }
}
