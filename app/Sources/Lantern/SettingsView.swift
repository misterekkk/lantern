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
            // Like System Settings: the sidebar runs up into the title bar.
            w.styleMask = [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView]
            w.toolbarStyle = .unified
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

/// Settings window (gear in the menu bar), laid out like System Settings: panes in a sidebar on the left.
enum SettingsPane: String, CaseIterable, Identifiable {
    case general, folders, performance, privacy, about
    var id: String { rawValue }

    var title: LocalizedStringKey {
        switch self {
        case .general: "General"
        case .folders: "Folders"
        case .performance: "Performance"
        case .privacy: "Privacy"
        case .about: "About"
        }
    }

    var symbol: String {
        switch self {
        case .general: "gearshape.fill"
        case .folders: "folder.fill"
        case .performance: "gauge.with.dots.needle.67percent"
        case .privacy: "hand.raised.fill"
        case .about: "info"
        }
    }

    var color: Color {
        switch self {
        case .general: .gray
        case .folders: .blue
        case .performance: .green
        case .privacy: .indigo
        case .about: .secondary
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject var state: AppState
    /// Development: `--settings-tab folders|performance|…` opens that pane (screenshots).
    @State private var pane: SettingsPane? = {
        let args = CommandLine.arguments
        let name = args.firstIndex(of: "--settings-tab").flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil }
        return name.flatMap(SettingsPane.init(rawValue:)) ?? .general
    }()

    var body: some View {
        NavigationSplitView {
            List(SettingsPane.allCases, selection: $pane) { p in
                Label {
                    Text(p.title)
                } icon: {
                    Image(systemName: p.symbol)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 22, height: 22)
                        .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(p.color.gradient))
                }
                .tag(p)
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 240)
            .toolbar(removing: .sidebarToggle)
        } detail: {
            Group {
                switch pane ?? .general {
                case .general: GeneralTab()
                case .folders: FoldersTab()
                case .performance: PerformanceTab()
                case .privacy: PrivacyTab()
                case .about: AboutTab()
                }
            }
            .navigationTitle(pane?.title ?? "General")
        }
        .frame(minWidth: 760, idealWidth: 820, minHeight: 560, idealHeight: 660)
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
                    Toggle("Search photos by content", isOn: Binding(
                        get: { s.indexPhotos }, set: { state.setConfig("index_photos", $0 ? "true" : "false") }))
                    Toggle("Index audio files and videos", isOn: Binding(
                        get: { s.indexAudioVideo },
                        set: { state.setConfig("index_audio_video", $0 ? "true" : "false") }))
                    Group {
                        Toggle("Transcribe recordings and videos (Whisper)", isOn: Binding(
                            get: { s.transcribe }, set: { state.setConfig("transcribe", $0 ? "true" : "false") }))
                        Toggle("Search video frames", isOn: Binding(
                            get: { s.indexVideoFrames },
                            set: { state.setConfig("index_video_frames", $0 ? "true" : "false") }))
                    }
                    .padding(.leading, 18)
                    .disabled(!s.indexAudioVideo)
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

    private var patterns: [String] { state.settings?.exclude.filter { !$0.hasPrefix("/") && !$0.hasPrefix("~") } ?? [] }

    var body: some View {
        ScrollViewReader { proxy in
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
                FolderTree()
            } header: {
                Text("What Lantern indexes")
            } footer: {
                Text("Expand a folder down to single files and uncheck what Lantern must not read. Greyed items are always skipped; hover for the reason.")
                    .font(.caption)
            }
            Section {
                ExtensionField()
                    .id("extensions")  // `--scroll-to extensions`: development screenshots
            } header: {
                Text("Ignored file extensions")
            } footer: {
                Text("Type an extension and a comma, or use +. Already indexed files with these extensions are removed from the index.")
                    .font(.caption)
            }
            Section {
                ForEach(patterns, id: \.self) { pattern in
                    HStack {
                        Text(pattern).font(.system(.body, design: .monospaced))
                        Spacer()
                        Button { state.removeExclude(pattern) } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless)
                    }
                }
                HStack {
                    // String, not a string literal: a literal Text would read ** as Markdown bold.
                    TextField("", text: $newPattern, prompt: Text(String(localized: "e.g. **/fixtures/** or draft-*")))
                        .labelsHidden()
                    Button("Add") {
                        state.addExclude(newPattern)
                        newPattern = ""
                    }
                    .disabled(newPattern.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            } header: {
                Text("Exclusion patterns")
            } footer: {
                Text("Advanced: .gitignore-style patterns matched in every indexed folder. Always skipped: ~/Library (except iCloud Drive), files with secrets, .git, node_modules, iCloud files not on this Mac.")
                    .font(.caption)
            }
        }
        .formStyle(.grouped)
        .onAppear {
            if CommandLine.arguments.contains("--scroll-to") {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { proxy.scrollTo("extensions", anchor: .top) }
            }
        }
        }
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
            Text("Models: EmbeddingGemma 2 · SigLIP 2 · Whisper large-v3-turbo (details in Performance)")
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
