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

    enum Phase { case starting, offline, paused, awaitingApproval, indexing, idle }

    var phase: Phase {
        guard let status else { return connectionError == nil ? .starting : .offline }
        if status.paused != nil { return .paused }
        if status.awaitingApproval { return .awaitingApproval }
        if status.worker.running { return .indexing }
        return .idle
    }

    var menuIcon: String {
        switch phase {
        case .starting, .offline: "magnifyingglass"
        case .paused: "pause.circle"
        case .awaitingApproval: "exclamationmark.magnifyingglass"
        case .indexing: "arrow.triangle.2.circlepath"
        case .idle: "sparkle.magnifyingglass"
        }
    }

    // MARK: - Lifecycle

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

    func refresh() async {
        do {
            let s = try await client.call("status", as: DaemonStatus.self, timeout: 5)
            status = s
            connectionError = nil
            if s.awaitingApproval && !wasAwaiting { notifyApprovalNeeded(s) }
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

    func approve() { run("approve") }
    func decline() { run("decline") }
    func pause() { run("pause") }
    func resume() { run("resume") }
    func setConfig(_ key: String, _ value: String) { run("config_set", ["key": key, "value": value]) }
    func addExclude(_ pattern: String) { run("exclude", ["action": "add", "pattern": pattern]) }
    func removeExclude(_ pattern: String) { run("exclude", ["action": "remove", "pattern": pattern]) }
    func removeFolder(_ path: String) { run("remove", ["path": path], message: String(localized: "Removed \(Format.shortPath(path)) from the index.")) }
    func clearHistory() { run("clear_history", message: String(localized: "Search history deleted.")) }
    func compact() { run("compact", message: String(localized: "Index compacted.")) }

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
        center.setNotificationCategories([
            UNNotificationCategory(identifier: "approval", actions: [index, later], intentIdentifiers: [])
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
