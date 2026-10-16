import AppKit
import LanternCore
import SwiftUI

/// Own settings window: SwiftUI's Settings scene does not reliably come to the front in a menu-bar-only app.
@MainActor
final class SettingsWindowController {
    static let shared = SettingsWindowController()
    private(set) var window: NSWindow?
    var isOpen: Bool { window?.isVisible == true }

    func close() { window?.close() }

    func show() {
        if window == nil {
            let host = NSHostingController(rootView: SettingsView().environmentObject(AppState.shared))
            host.sceneBridgingOptions = [.title, .toolbars]  // pane title, back button and search in the title bar
            let w = NSWindow(contentViewController: host)
            w.title = String(localized: "Lantern Settings")
            // Like System Settings: the sidebar runs up into the title bar.
            w.styleMask = [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView]
            w.toolbarStyle = .unified
            w.isReleasedWhenClosed = false
            w.setContentSize(NSSize(width: 880, height: 680))
            w.center()
            window = w
            // Closing the window releases indexing held while it was open: tell the daemon right away.
            NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: w, queue: .main) { _ in
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: 50_000_000)  // after isVisible turns false
                    await AppState.shared.refresh()
                }
            }
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        window?.makeFirstResponder(nil)  // like System Settings: the search field is not focused on open
    }
}

/// Settings panes, grouped in the sidebar like System Settings.
enum SettingsPane: String, CaseIterable, Identifiable, Hashable {
    case general, fileTypes, folders, power, performance, privacy, about
    var id: String { rawValue }

    static let groups: [[SettingsPane]] = [[.general, .fileTypes], [.folders, .power], [.performance], [.privacy, .about]]

    var title: LocalizedStringKey {
        switch self {
        case .general: "General"
        case .fileTypes: "File Types"
        case .folders: "Folders"
        case .power: "Battery and Heat"
        case .performance: "Performance"
        case .privacy: "Privacy"
        case .about: "About"
        }
    }

    var localizedTitle: String {
        switch self {
        case .general: String(localized: "General")
        case .fileTypes: String(localized: "File Types")
        case .folders: String(localized: "Folders")
        case .power: String(localized: "Battery and Heat")
        case .performance: String(localized: "Performance")
        case .privacy: String(localized: "Privacy")
        case .about: String(localized: "About")
        }
    }

    /// Words the sidebar search matches (English and Polish), besides the title.
    var keywords: [String] {
        switch self {
        case .general: ["general", "ogólne", "login", "logowanie", "language", "język", "shortcut", "skrót", "ask", "pytaj"]
        case .fileTypes: ["file types", "rodzaje", "photos", "zdjęcia", "audio", "video", "filmy", "transcription",
                          "transkrypcja", "frames", "klatki", "extensions", "rozszerzenia", "csv"]
        case .folders: ["folders", "foldery", "tree", "drzewko", "exclude", "wyklucz", "patterns", "wzorce"]
        case .power: ["battery", "bateria", "heat", "temperatura", "pause", "pauza", "power", "zasilanie"]
        case .performance: ["performance", "wydajność", "memory", "pamięć", "ram", "models", "modele", "chart", "wykres"]
        case .privacy: ["privacy", "prywatność", "history", "historia", "compact", "zagęść", "permissions", "uprawnienia"]
        case .about: ["about", "informacje", "version", "wersja"]
        }
    }

    var symbol: String {
        switch self {
        case .general: "gearshape.fill"
        case .fileTypes: "doc.on.doc.fill"
        case .folders: "folder.fill"
        case .power: "bolt.fill"
        case .performance: "gauge.with.dots.needle.67percent"
        case .privacy: "hand.raised.fill"
        case .about: "info"
        }
    }

    var color: Color {
        switch self {
        case .general: .gray
        case .fileTypes: .orange
        case .folders: .blue
        case .power: .green
        case .performance: .teal
        case .privacy: .indigo
        case .about: Color(nsColor: .systemGray)
        }
    }
}

enum SettingsSubpage: Hashable {
    case tree, patterns
}

struct SettingsView: View {
    @EnvironmentObject var state: AppState
    /// Development: `--settings-tab folders|performance|…` opens that pane (screenshots).
    @State private var pane: SettingsPane? = {
        let args = CommandLine.arguments
        let name = args.firstIndex(of: "--settings-tab").flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil }
        return name.flatMap(SettingsPane.init(rawValue:)) ?? .general
    }()
    @State private var search = ""
    @State private var forward: [SettingsSubpage] = []  // pages left with ←, for →
    @State private var path: [SettingsSubpage] = {
        CommandLine.arguments.contains("--settings-tree") ? [.tree] : []  // development screenshots
    }()

    private func visible(_ group: [SettingsPane]) -> [SettingsPane] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return group }
        return group.filter { p in
            p.localizedTitle.lowercased().contains(q)
                || p.keywords.contains { $0.contains(q) }
        }
    }

    private var windowTitle: String {
        switch path.last {
        case .tree: String(localized: "Choose What to Index")
        case .patterns: String(localized: "Exclusion Patterns")
        case nil: (pane ?? .general).localizedTitle
        }
    }

    var body: some View {
        NavigationSplitView {
            List(selection: $pane) {
                StatusRow()
                ForEach(Array(SettingsPane.groups.enumerated()), id: \.offset) { _, group in
                    let panes = visible(group)
                    if !panes.isEmpty {
                        Section {
                            ForEach(panes) { p in
                                Label {
                                    Text(p.title)
                                } icon: {
                                    SettingsIcon(symbol: p.symbol, color: p.color)
                                }
                                .tag(p)
                            }
                        }
                    }
                }
            }
            .frame(minWidth: 240)
            .navigationSplitViewColumnWidth(min: 240, ideal: 250, max: 300)
            .searchable(text: $search, placement: .sidebar, prompt: Text("Search"))
            .toolbar(removing: .sidebarToggle)
        } detail: {
            NavigationStack(path: $path) {
                Group {
                    switch pane ?? .general {
                    case .general: GeneralPane()
                    case .fileTypes: FileTypesPane()
                    case .folders: FoldersPane()
                    case .power: PowerPane()
                    case .performance: PerformanceTab()
                    case .privacy: PrivacyPane()
                    case .about: AboutPane()
                    }
                }
                .modifier(NavArrows(path: $path, forward: $forward))
                .navigationDestination(for: SettingsSubpage.self) { page in
                    Group {
                        switch page {
                        case .tree: TreePage()
                        case .patterns: PatternsPage()
                        }
                    }
                    .navigationBarBackButtonHidden(true)
                    .modifier(NavArrows(path: $path, forward: $forward))
                }
            }
        }
        .onChange(of: pane) { _, _ in
            path = []
            forward = []
        }
        .navigationTitle(windowTitle)
        .frame(minWidth: 780, idealWidth: 840, minHeight: 580, idealHeight: 680)
        .task { await state.loadSettings() }
    }
}

/// ← → in the toolbar, always present like in System Settings (also keeps the title on the left).
private struct NavArrows: ViewModifier {
    @Binding var path: [SettingsSubpage]
    @Binding var forward: [SettingsSubpage]

    func body(content: Content) -> some View {
        content.toolbar {
            ToolbarItemGroup(placement: .navigation) {
                Button {
                    if let page = path.popLast() { forward.append(page) }
                } label: { Image(systemName: "chevron.left") }
                .disabled(path.isEmpty)
                .help("Back")
                Button {
                    if let page = forward.popLast() { path.append(page) }
                } label: { Image(systemName: "chevron.right") }
                .disabled(forward.isEmpty)
                .help("Forward")
            }
        }
    }
}

/// Top of the sidebar (where System Settings shows the Apple Account): Lantern and what it is doing.
private struct StatusRow: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkle.magnifyingglass")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(Circle().fill(LinearGradient(colors: [.purple, .blue], startPoint: .topLeading, endPoint: .bottomTrailing)))
                .symbolEffect(.pulse, options: .repeating, isActive: state.phase == .indexing)
            VStack(alignment: .leading, spacing: 1) {
                Text("Lantern").font(.headline)
                Text(status).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .padding(.vertical, 6)
        .selectionDisabled()
    }

    private var status: String {
        guard let s = state.status else { return String(localized: "Starting…") }
        if s.worker.running, let done = s.worker.done, let total = s.worker.total {
            return String(localized: "Indexing \(done) of \(total)")
        }
        if s.paused != nil { return String(localized: "Paused") }
        return String(localized: "\(Format.count(s.indexedFiles)) files in the index")
    }
}

// MARK: - Panes

private struct GeneralPane: View {
    @EnvironmentObject var state: AppState
    @AppStorage(ShortcutHints.key) private var hints = ShortcutHints.row.rawValue
    @State private var language = AppLanguage.current
    @State private var askRestart = false
    @AppStorage(SearchShortcut.key) private var shortcut = SearchShortcut.optionSpace.rawValue
    @State private var spotlightOn = SpotlightShortcut.isOn
    @State private var addingLanguage = false

    var body: some View {
        SettingsPage {
            PaneHeader(symbol: "gearshape.fill", color: .gray, title: "General",
                       text: "Startup, language, the search shortcut and when Lantern indexes new files.")
            Section {
                Toggle(isOn: Binding(get: { state.launchAtLogin }, set: { state.launchAtLogin = $0 })) {
                    IconLabel(title: "Open Lantern at login", symbol: "power", color: .gray)
                }
                Picker(selection: $shortcut) {
                    Text(verbatim: SearchShortcut.optionSpace.label).tag(SearchShortcut.optionSpace.rawValue)
                    Text("\(SearchShortcut.commandSpace.label) (instead of Spotlight)").tag(SearchShortcut.commandSpace.rawValue)
                    Text(verbatim: SearchShortcut.controlOptionSpace.label).tag(SearchShortcut.controlOptionSpace.rawValue)
                } label: {
                    IconLabel(title: "Search shortcut", symbol: "keyboard", color: .gray)
                }
                .onChange(of: shortcut) { _, _ in state.registerShortcut() }
                if shortcut == SearchShortcut.commandSpace.rawValue && spotlightOn {
                    // macOS gives ⌘Space to Spotlight until its shortcut is turned off; Lantern does not change it.
                    VStack(alignment: .leading, spacing: 8) {
                        Label("⌘ Space still opens Spotlight", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        Text("Turn off Spotlight's shortcut in System Settings → Keyboard → Keyboard Shortcuts… → Spotlight. Lantern takes over ⌘ Space as soon as it is free.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Open Keyboard Settings…") { NSWorkspace.shared.open(SpotlightShortcut.keyboardSettings) }
                    }
                    .padding(.vertical, 4)
                }
                Picker(selection: $hints) {
                    Text("On the selected result").tag(ShortcutHints.row.rawValue)
                    Text("Bar at the bottom").tag(ShortcutHints.bar.rawValue)
                    Text("Hidden").tag(ShortcutHints.off.rawValue)
                } label: {
                    IconLabel(title: "Shortcut hints in the panel", symbol: "command", color: .gray)
                }
                Picker(selection: $language) {
                    ForEach(AppLanguage.allCases) { Text($0.title).tag($0) }
                } label: {
                    IconLabel(title: "Language", symbol: "globe", color: .blue)
                }
                .onChange(of: language) { _, new in
                    new.apply()
                    askRestart = true
                }
            } footer: {
                Text("Lantern follows the macOS language unless you choose one here (also in System Settings → General → Language & Region → Applications).")
                    .font(.caption)
            }
            Section {
                ForEach(state.languages?.effective ?? [], id: \.self) { code in
                    HStack {
                        Text(LanguageName.of(code))
                        Spacer()
                        if state.languages?.extra.contains(code) == true {
                            Button { state.changeLanguage("remove", code) } label: {
                                Image(systemName: "minus.circle.fill").foregroundStyle(.secondary)
                            }
                            .buttonStyle(.borderless)
                            .help("Remove")
                        } else if state.languages?.system.contains(code) == true {
                            Text(verbatim: "macOS").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                HStack {
                    Spacer()
                    Button("Add Language…") { addingLanguage = true }
                }
            } header: {
                Text("Preferred Languages")
            } footer: {
                Text("Names of apps and settings and the text in images are read in these languages. The macOS ones come from System Settings → General → Language & Region; English is always included.")
                    .font(.caption)
            }
            if let s = state.settings {
                Section {
                    Picker(selection: Binding(get: { s.indexMode }, set: { state.setConfig("index_mode", $0) })) {
                        Text("Ask before indexing").tag("ask")
                        Text("Index automatically").tag("auto")
                    } label: {
                        IconLabel(title: "New and changed files", symbol: "doc.badge.clock", color: .blue)
                    }
                } footer: {
                    Text("Ask: Lantern finds new files and waits for your approval in the menu bar.").font(.caption)
                }
            } else {
                Section { Text(state.connectionError ?? String(localized: "Connecting to the daemon…")).foregroundStyle(.secondary) }
            }
        }
        .task {
            await state.loadLanguages()
            while !Task.isCancelled {  // follow changes made in System Settings while this pane is open
                spotlightOn = SpotlightShortcut.isOn
                if shortcut == SearchShortcut.commandSpace.rawValue && !spotlightOn && state.shortcutNeedsRetry {
                    state.registerShortcut()
                }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
        .sheet(isPresented: $addingLanguage) {
            LanguagePicker(existing: state.languages?.effective ?? []) { code in
                state.changeLanguage("add", code)
            }
        }
        .alert("Restart Lantern to change the language?", isPresented: $askRestart) {
            Button("Restart Now") { AppLanguage.relaunch() }
            Button("Later", role: .cancel) {}
        } message: {
            Text("Indexing continues where it stopped.")
        }
    }
}

private struct FileTypesPane: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        SettingsPage {
            PaneHeader(symbol: "doc.on.doc.fill", color: .orange, title: "File Types",
                       text: "What Lantern reads besides text, code and PDFs: photos (and the text in them), recordings and videos, and which extensions it skips.")
            if let s = state.settings {
                Section {
                    Toggle(isOn: binding("index_photos", s.indexPhotos)) {
                        IconLabel(title: "Photos", symbol: "photo.fill", color: .pink)
                    }
                    Toggle(isOn: binding("index_image_text", s.indexImageText)) {
                        IconLabel(title: "Text in photos and screenshots (OCR)", symbol: "text.viewfinder", color: .teal)
                    }
                    .disabled(!s.indexPhotos)
                    Toggle(isOn: binding("index_audio_video", s.indexAudioVideo)) {
                        IconLabel(title: "Recordings and videos", symbol: "waveform", color: .red)
                    }
                    Toggle(isOn: binding("transcribe", s.transcribe)) {
                        IconLabel(title: "Transcribe speech (Whisper)", symbol: "text.bubble.fill", color: .orange)
                    }
                    .disabled(!s.indexAudioVideo)  // dimmed, not indented: like dependent rows in System Settings
                    Toggle(isOn: binding("index_video_frames", s.indexVideoFrames)) {
                        IconLabel(title: "Search inside video frames", symbol: "film.fill", color: .purple)
                    }
                    .disabled(!s.indexAudioVideo)  // dimmed, not indented: like dependent rows in System Settings
                } footer: {
                    Text("Switching a type off removes its files from the index at the next scan. Text in images is read on this Mac by macOS itself (Live Text engine); screenshots can contain private text, which then becomes searchable.").font(.caption)
                }
                Section {
                    ExtensionField()
                        .id("extensions")  // `--scroll-to extensions`: development screenshots
                } header: {
                    Text("Ignored extensions")
                } footer: {
                    Text("Type an extension and a comma, or use +. Files with these extensions are never indexed.")
                        .font(.caption)
                }
            }
        }
    }

    private func binding(_ key: String, _ value: Bool) -> Binding<Bool> {
        Binding(get: { value }, set: { state.setConfig(key, $0 ? "true" : "false") })
    }
}

private struct FoldersPane: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        SettingsPage {
            PaneHeader(symbol: "folder.fill", color: .blue, title: "Folders",
                       text: "The folders Lantern searches. Choose exactly which files inside them it may read.")
            Section {
                ForEach(state.status?.roots ?? []) { root in
                    HStack(spacing: 10) {
                        Image(nsImage: NSWorkspace.shared.icon(forFile: root.path))
                            .resizable().frame(width: 22, height: 22)
                            .opacity(root.available ? 1 : 0.4)
                        VStack(alignment: .leading, spacing: 1) {
                            Text((root.path as NSString).lastPathComponent)
                            Text(Format.shortPath(root.path))
                                .font(.caption).foregroundStyle(.secondary)
                                .lineLimit(1).truncationMode(.middle)
                        }
                        Spacer()
                        if !root.available {
                            Text("unavailable").font(.caption).foregroundStyle(.secondary)
                        }
                        Button(role: .destructive) { state.removeFolder(root.path) } label: {
                            Image(systemName: "minus.circle.fill").foregroundStyle(.secondary)
                        }
                        .buttonStyle(.borderless)
                        .help("Remove the folder and its data from the index")
                    }
                }
                HStack {
                    Spacer()
                    Button("Add Folder…") { state.chooseFolder() }
                }
            } footer: {
                if state.status?.waitingForSettings == true {
                    Label("Indexing starts when you close Settings, so you can still choose what to skip below.",
                          systemImage: "clock")
                        .font(.caption)
                }
            }
            Section {
                NavigationLink(value: SettingsSubpage.tree) {
                    IconLabel(title: "Choose What to Index", symbol: "checklist", color: .blue)
                }
                NavigationLink(value: SettingsSubpage.patterns) {
                    IconLabel(title: "Exclusion Patterns", symbol: "line.3.horizontal.decrease", color: .gray)
                }
            } footer: {
                Text("Always skipped: ~/Library (except iCloud Drive), files with secrets, .git, node_modules, iCloud files that are not on this Mac.")
                    .font(.caption)
            }
        }
    }
}

/// Folder tree subpage: search in the toolbar, like the search fields of System Settings subpages.
private struct TreePage: View {
    @State private var query: String = {  // `--tree-query x`: development screenshots
        let args = CommandLine.arguments
        return args.firstIndex(of: "--tree-query").flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil } ?? ""
    }()
    @State private var showAll = false

    var body: some View {
        FolderTree(query: query, showAll: showAll)
            .navigationTitle("Choose What to Index")
            .searchable(text: $query, placement: .toolbar, prompt: Text("Find a file or folder"))
            .safeAreaInset(edge: .bottom) {  // a labelled option at the bottom, like Finder's view options
                HStack {
                    Toggle("Show files Lantern can't index", isOn: $showAll)
                        .toggleStyle(.checkbox)
                    Spacer()
                }
                .font(.callout)
                .padding(.horizontal, 20)
                .padding(.vertical, 10)
                .background(.bar)
            }
    }
}

private struct PatternsPage: View {
    @EnvironmentObject var state: AppState
    @State private var newPattern = ""

    private var patterns: [String] { state.settings?.exclude.filter { !$0.hasPrefix("/") && !$0.hasPrefix("~") } ?? [] }

    var body: some View {
        SettingsPage {
            Section {
                ForEach(patterns, id: \.self) { pattern in
                    HStack {
                        Text(pattern).font(.system(.body, design: .monospaced))
                        Spacer()
                        Button { state.removeExclude(pattern) } label: {
                            Image(systemName: "minus.circle.fill").foregroundStyle(.secondary)
                        }
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
            } footer: {
                Text("Patterns work like .gitignore and apply inside every indexed folder.").font(.caption)
            }
        }
        .navigationTitle("Exclusion Patterns")
    }
}

private struct PowerPane: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        SettingsPage {
            PaneHeader(symbol: "bolt.fill", color: .green, title: "Battery and Heat",
                       text: "Indexing uses the GPU. Lantern can wait for the charger, respect Low Power Mode and pause while your Mac is hot.")
            if let s = state.settings {
                Section {
                    Toggle(isOn: Binding(get: { s.pauseOnBattery }, set: { state.setConfig("pause_on_battery", $0 ? "true" : "false") })) {
                        IconLabel(title: "Pause on battery power", symbol: "battery.50percent", color: .green)
                    }
                    Toggle(isOn: Binding(get: { s.pauseOnLowPower }, set: { state.setConfig("pause_on_low_power", $0 ? "true" : "false") })) {
                        IconLabel(title: "Pause in Low Power Mode", symbol: "leaf.fill", color: .yellow)
                    }
                    Toggle(isOn: Binding(get: { s.pauseOnThermal }, set: { state.setConfig("pause_on_thermal", $0 ? "true" : "false") })) {
                        IconLabel(title: "Pause when the Mac is hot", symbol: "thermometer.medium", color: .orange)
                    }
                } footer: {
                    Text("Search always works, also while indexing is paused.").font(.caption)
                }
            }
        }
    }
}

private struct PrivacyPane: View {
    @EnvironmentObject var state: AppState
    @State private var confirmClear = false
    @State private var confirmCompact = false
    @State private var compacting = false
    @State private var compactResult: String?

    var body: some View {
        SettingsPage {
            PaneHeader(symbol: "hand.raised.fill", color: .indigo, title: "Privacy",
                       text: "Everything stays on this Mac. Lantern only reads your files and never sends anything.")
            Section {
                Button { confirmClear = true } label: {
                    IconLabel(title: "Clear Search History…", symbol: "clock.arrow.circlepath", color: .red)
                }
                .buttonStyle(.plain)
                Button { confirmCompact = true } label: {
                    HStack {
                        IconLabel(title: "Compact Index…", symbol: "archivebox.fill", color: .brown)
                        Spacer()
                        if compacting { ProgressView().controlSize(.small) }
                    }
                }
                .buttonStyle(.plain)
                .disabled(compacting)
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: DaemonClient.dataDirectory)])
                } label: {
                    IconLabel(title: "Show Data Folder in Finder", symbol: "folder.fill", color: .blue)
                }
                .buttonStyle(.plain)
            } header: {
                Text("Data")
            } footer: {
                if let compactResult { Text(compactResult).font(.caption) }
            }
            Section {
                Button {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!)
                } label: {
                    IconLabel(title: "Open Privacy Settings…", symbol: "hand.raised.fill", color: .indigo)
                }
                .buttonStyle(.plain)
            } header: {
                Text("File access")
            } footer: {
                Text("Desktop, Documents, Downloads and iCloud Drive need macOS permission. Folders without access are skipped, and their previously indexed files are kept.")
                    .font(.caption)
            }
            // Facts, not settings: small print at the bottom of the pane, as System Settings does.
            Section {} footer: {
                Text("Lantern works offline and sends no telemetry. Indexed files are only read, never changed, moved or deleted. The index lives in this Mac's Application Support folder and is not synced to iCloud.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .confirmationDialog("Compact the index?", isPresented: $confirmCompact) {
            Button("Compact") {
                compacting = true
                compactResult = nil
                Task {
                    compactResult = await state.compact()
                    compacting = false
                }
            }
        } message: {
            Text("Data of removed files and cleared history is physically erased from the index file, and the space is freed. It takes a few seconds; search keeps working. Lantern also does this by itself after large deletions.")
        }
        .confirmationDialog("Delete the search history?", isPresented: $confirmClear) {
            Button("Delete History", role: .destructive) { state.clearHistory() }
        } message: {
            Text("Past queries and opened results are removed from this Mac.")
        }
    }
}

private struct AboutPane: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        SettingsPage {
            Section {
                VStack(spacing: 8) {
                    Image(systemName: "sparkle.magnifyingglass")
                        .font(.system(size: 34, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 72, height: 72)
                        .background(Circle().fill(LinearGradient(colors: [.purple, .blue], startPoint: .topLeading, endPoint: .bottomTrailing)))
                    Text("Lantern").font(.title2.weight(.semibold))
                    Text("Local search by meaning in files, code, photos and recordings.")
                        .font(.callout).foregroundStyle(.secondary)
                    Text("Version \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev")")
                        .font(.caption).foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
            }
            Section {
                LabeledContent("Models") { Text("EmbeddingGemma 2 · SigLIP 2 · Whisper").foregroundStyle(.secondary) }
                if let s = state.status {
                    LabeledContent("Index") { Text("\(Format.count(s.indexedFiles)) files · \(Format.count(s.chunks)) fragments").foregroundStyle(.secondary) }
                    LabeledContent("Daemon") { Text("pid \(s.pid) · \(s.memoryMb) MB").foregroundStyle(.secondary) }
                }
            }
        }
    }
}

/// A language's name in Lantern's own language ("pl" -> "Polish" / "polski").
enum LanguageName {
    static func of(_ code: String) -> String {
        let name = Locale.current.localizedString(forIdentifier: code) ?? code
        return name.prefix(1).uppercased() + name.dropFirst()
    }
}

/// Adds a preferred language, like the + under Preferred Languages in System Settings.
private struct LanguagePicker: View {
    let existing: [String]
    let add: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""

    /// Languages macOS has a locale for, by their name in Lantern's language.
    private static let all: [String] = {
        let codes = Set(Locale.availableIdentifiers.map { Locale(identifier: $0).language.languageCode?.identifier ?? "" })
        return codes.filter { !$0.isEmpty && Locale.current.localizedString(forLanguageCode: $0) != nil }
            .sorted { LanguageName.of($0).localizedCompare(LanguageName.of($1)) == .orderedAscending }
    }()

    private var shown: [String] {
        let q = query.trimmingCharacters(in: .whitespaces)
        return Self.all.filter { code in
            !existing.contains(code) && (q.isEmpty || LanguageName.of(code).localizedCaseInsensitiveContains(q))
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            TextField("", text: $query, prompt: Text("Search languages"))
                .textFieldStyle(.roundedBorder)
                .padding(12)
            List(shown, id: \.self) { code in
                Button {
                    add(code)
                    dismiss()
                } label: {
                    HStack {
                        Text(LanguageName.of(code))
                        Spacer()
                        Text(Locale(identifier: code).localizedString(forIdentifier: code) ?? "")
                            .foregroundStyle(.secondary)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(12)
        }
        .frame(width: 380, height: 440)
    }
}
