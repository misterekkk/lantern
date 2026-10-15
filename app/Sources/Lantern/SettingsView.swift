import AppKit
import LanternCore
import SwiftUI

/// Own settings window: SwiftUI's Settings scene does not reliably come to the front in a menu-bar-only app.
@MainActor
final class SettingsWindowController {
    static let shared = SettingsWindowController()
    private var window: NSWindow?

    func show() {
        if window == nil {
            let host = NSHostingController(rootView: SettingsView().environmentObject(AppState.shared))
            let w = NSWindow(contentViewController: host)
            w.title = String(localized: "Lantern Settings")
            w.styleMask = [.titled, .closable, .miniaturizable]
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

/// Settings window (⌘,): general, folders and exclusions, privacy, about.
struct SettingsView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        TabView {
            GeneralTab().tabItem { Label("General", systemImage: "gearshape") }
            FoldersTab().tabItem { Label("Folders", systemImage: "folder") }
            PrivacyTab().tabItem { Label("Privacy", systemImage: "hand.raised") }
            AboutTab().tabItem { Label("About", systemImage: "info.circle") }
        }
        .frame(width: 560, height: 420)
        .task { await state.loadSettings() }
    }
}

private struct GeneralTab: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Form {
            Section {
                Toggle("Open Lantern at login", isOn: Binding(
                    get: { state.launchAtLogin }, set: { state.launchAtLogin = $0 }))
                LabeledContent("Search shortcut") { Text("⌥ Space").font(.system(.body, design: .rounded)) }
            }
            if let s = state.settings {
                Section("Indexing options") {
                    Picker("New and changed files", selection: Binding(
                        get: { s.indexMode }, set: { state.setConfig("index_mode", $0) })) {
                        Text("Ask before indexing").tag("ask")
                        Text("Index automatically").tag("auto")
                    }
                    Toggle("Transcribe recordings and videos (Whisper)", isOn: Binding(
                        get: { s.transcribe }, set: { state.setConfig("transcribe", $0 ? "true" : "false") }))
                    Toggle("Pause on battery power", isOn: Binding(
                        get: { s.pauseOnBattery }, set: { state.setConfig("pause_on_battery", $0 ? "true" : "false") }))
                    Toggle("Pause when the Mac is hot", isOn: Binding(
                        get: { s.pauseOnThermal }, set: { state.setConfig("pause_on_thermal", $0 ? "true" : "false") }))
                }
            } else {
                Text(state.connectionError ?? String(localized: "Connecting to the daemon…")).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

private struct FoldersTab: View {
    @EnvironmentObject var state: AppState
    @State private var newPattern = ""

    var body: some View {
        Form {
            Section("Searched folders") {
                ForEach(state.status?.roots ?? []) { root in
                    HStack {
                        Image(systemName: root.available ? "folder.fill" : "externaldrive.badge.xmark")
                            .foregroundStyle(root.available ? Color.accentColor : .secondary)
                        Text(Format.shortPath(root.path)).lineLimit(1).truncationMode(.middle)
                        if !root.available { Text("unavailable").font(.caption).foregroundStyle(.secondary) }
                        Spacer()
                        Button(role: .destructive) { state.removeFolder(root.path) } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless)
                            .help("Remove the folder and its data from the index")
                    }
                }
                Button("Add Folder…") { state.chooseFolder() }
            }
            Section {
                ForEach(state.settings?.exclude ?? [], id: \.self) { pattern in
                    HStack {
                        Text(pattern).font(.system(.body, design: .monospaced))
                        Spacer()
                        Button { state.removeExclude(pattern) } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless)
                    }
                }
                HStack {
                    TextField("e.g. ~/Downloads/archive or *.csv", text: $newPattern)
                    Button("Add") {
                        state.addExclude(newPattern)
                        newPattern = ""
                    }
                    .disabled(newPattern.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            } header: {
                Text("Exclusions")
            } footer: {
                Text("Always skipped: ~/Library (except iCloud Drive), files with secrets, .git, node_modules, iCloud files not on this Mac.")
                    .font(.caption)
            }
        }
        .formStyle(.grouped)
    }
}

private struct PrivacyTab: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Form {
            Section {
                Label("Everything runs locally and offline. Lantern sends no data.", systemImage: "lock.shield")
                Label("Indexed files are only read, never changed.", systemImage: "doc.text.magnifyingglass")
            }
            Section("Data") {
                Button("Clear Search History") { state.clearHistory() }
                Button("Compact Index (erase deleted data)") { state.compact() }
                Button("Show Data Folder in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: DaemonClient.dataDirectory)])
                }
            }
            Section("File access") {
                Text("Desktop, Documents, Downloads and iCloud Drive need macOS permission. Folders without access are skipped, and their previously indexed files are kept.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Button("Open Privacy Settings") {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!)
                }
            }
        }
        .formStyle(.grouped)
    }
}

private struct AboutTab: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "sparkle.magnifyingglass")
                .font(.system(size: 48, weight: .semibold))
                .foregroundStyle(.tint)
            Text("Lantern").font(.title2.weight(.semibold))
            Text("Local search by meaning in files, code and recordings.")
                .foregroundStyle(.secondary)
            Text("Model: EmbeddingGemma 2 (MLX, 8-bit) · Whisper large-v3-turbo")
                .font(.caption)
                .foregroundStyle(.secondary)
            if let s = state.status {
                Text("Daemon: pid \(s.pid), \(s.memoryMb) MB · \(s.indexedFiles) files, \(s.chunks) chunks")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text("Version \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev")")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
