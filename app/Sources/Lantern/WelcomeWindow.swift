import AppKit
import LanternCore
import SwiftUI

/// First launch (phase 17): a welcome window in the manner of macOS's Setup Assistant. Each step asks in plain words,
/// with the technical detail in small print below; Back and Continue at the bottom; steps cross-fade with a short
/// slide in the direction of travel. Models and defaults follow this Mac's memory (`setup` API).
@MainActor final class WelcomeWindowController {
    static let shared = WelcomeWindowController()
    private var window: NSWindow?
    private var model: WelcomeModel?
    fileprivate var finished = false

    func show() {
        if window == nil {
            finished = false
            let model = WelcomeModel()
            self.model = model
            let host = NSHostingController(rootView: WelcomeView(model: model).environmentObject(AppState.shared))
            let w = NSWindow(contentViewController: host)
            w.styleMask = [.titled, .closable, .fullSizeContentView]
            w.titlebarAppearsTransparent = true
            w.titleVisibility = .hidden
            w.isMovableByWindowBackground = true
            w.isReleasedWhenClosed = false
            w.setContentSize(NSSize(width: 640, height: 580))
            w.center()
            window = w
            NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: w, queue: .main) { _ in
                Task { @MainActor in
                    let controller = WelcomeWindowController.shared
                    if !controller.finished { AppState.shared.notifySetupUnfinished() }
                    controller.window = nil
                }
            }
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    /// Development: `--welcome-snapshot <dir>` writes every step as a PNG (layout checks without screen recording).
    func snapshots(to dir: URL) async {
        show()
        guard let model else { return }
        for step in WelcomeModel.Step.allCases {
            model.step = step
            try? await Task.sleep(nanoseconds: 700_000_000)  // past the transition
            // SwiftUI renders the view itself (cacheDisplay and layer rendering leave SwiftUI content out; system
            // controls may come out as placeholders, the layout and the texts are what this checks)
            let renderer = ImageRenderer(content: WelcomeView(model: model).environmentObject(AppState.shared)
                .background(Color(nsColor: .windowBackgroundColor)))
            renderer.scale = window?.backingScaleFactor ?? 2
            guard let image = renderer.cgImage else { continue }
            try? NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?
                .write(to: dir.appendingPathComponent("welcome-\(step.rawValue)-\(step).png"))
        }
        print("welcome snapshots written to \(dir.path)")
        fflush(stdout)
    }

    fileprivate func finish() {
        finished = true
        window?.close()
    }
}

@MainActor final class WelcomeModel: ObservableObject {
    enum Step: Int, CaseIterable { case welcome, usage, models, folders, launcher, done }

    @Published var step: Step = .welcome
    @Published var forward = true
    @Published var mode = "ready"
    @Published var models: Set<String> = []
    @Published var folders: Set<String> = Set(AppState.commonFolders)
    @Published var launcher: SearchLauncher = .lantern
    @Published var confirmHeavy: SetupInfo.Model?
    private var defaultsApplied = false

    /// First launch: what suits this Mac. Opened again from Settings: what is set now, so Continue changes nothing
    /// the user did not touch (no folder they removed comes back, the mode stays).
    func applyDefaults(_ setup: SetupInfo, roots: [String], currentMode: String?) {
        guard !defaultsApplied else { return }
        defaultsApplied = true
        launcher = SearchLauncher.current
        if roots.isEmpty {
            mode = setup.suggestedMode
            models = Set(setup.models.filter { $0.recommended || $0.downloaded }.map(\.key))
        } else {
            mode = currentMode ?? setup.suggestedMode
            models = Set(setup.models.filter(\.downloaded).map(\.key))
            folders = Set(AppState.commonFolders.filter { f in roots.contains { f == $0 || f.hasPrefix($0 + "/") } })
        }
    }

    func go(_ to: Step) {
        forward = to.rawValue > step.rawValue
        withAnimation(.smooth(duration: 0.3)) { step = to }
    }

    func next() { if let s = Step(rawValue: step.rawValue + 1) { go(s) } }
    func back() { if let s = Step(rawValue: step.rawValue - 1) { go(s) } }

    /// Whisper and SigLIP on an 8 GB Mac: asked once, with what they need.
    func toggle(_ model: SetupInfo.Model, profile: String) {
        if models.contains(model.key) {
            if model.group != "required" { models.remove(model.key) }  // the search model cannot be left out
        } else if model.heavy && profile == "light" {
            confirmHeavy = model
        } else {
            models.insert(model.key)
        }
    }
}

struct WelcomeView: View {
    @ObservedObject var model: WelcomeModel
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                page
                    .id(model.step)
                    .transition(.asymmetric(
                        insertion: .opacity.combined(with: .offset(x: model.forward ? 36 : -36)),
                        removal: .opacity.combined(with: .offset(x: model.forward ? -36 : 36))))
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
            footer
        }
        .frame(width: 640, height: 580)
        .task {
            // the daemon may still be starting on first launch: ask until it answers
            for _ in 0..<60 where state.setup == nil {
                await state.loadSetup()
                if state.setup == nil { try? await Task.sleep(nanoseconds: 1_000_000_000) }
            }
            if let s = state.setup {
                model.applyDefaults(s, roots: state.status?.roots.map(\.path) ?? [], currentMode: state.settings?.mode)
            }
        }
        .alert("This Mac may slow down", isPresented: Binding(get: { model.confirmHeavy != nil },
                                                              set: { if !$0 { model.confirmHeavy = nil } }),
               presenting: model.confirmHeavy) { heavy in
            Button("Use It Anyway") { model.models.insert(heavy.key) }
            Button("Not Now", role: .cancel) {}
        } message: { heavy in
            Text("It needs about \(heavy.needsGb.formatted()) GB of memory while it works. On a Mac with \(state.setup?.ramGb ?? 8) GB that can push other apps into swap. Lantern starts it only when there is room, but it may still be slow.")
        }
    }

    @ViewBuilder private var page: some View {
        switch model.step {
        case .welcome: welcome
        case .usage: usage
        case .models: models
        case .folders: folders
        case .launcher: launcher
        case .done: done
        }
    }

    // MARK: - Steps

    private var welcome: some View {
        StepPage(icon: AnyView(Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 96, height: 96)),
                 title: "Welcome to Lantern",
                 text: "Find anything on your Mac by what it says or shows: documents, code, PDFs, photos and recordings.",
                 fine: "Lantern works offline. The only connection it makes is the one-time download of its models from Hugging Face; your files, searches and their results never leave this Mac.") {
            VStack(alignment: .leading, spacing: 14) {
                Feature(symbol: "text.magnifyingglass", color: .blue, title: "Search by meaning",
                        text: "Type what you remember, in your own words; Lantern finds the file that says it.")
                Feature(symbol: "photo.on.rectangle.angled", color: .orange, title: "Photos, places and people",
                        text: "“Photos from Kraków in 2024”, “no people”, a recording at the moment it was said.")
                Feature(symbol: "lock.shield", color: .green, title: "Private by design",
                        text: "Everything is indexed and searched on this Mac. Nothing is uploaded.")
            }
        }
    }

    private var usage: some View {
        StepPage(icon: AnyView(Symbol(name: "clock.arrow.circlepath", color: .purple)),
                 title: "How often will you search?",
                 text: "Lantern can stay ready all the time, or wake up when you need it.",
                 fine: "Always ready keeps the search model in memory (about 0.5 GB), so results appear at once. When needed releases it after 10 minutes without a search: apps and settings still answer at once, and the first search by meaning after a break takes a moment longer. You can change this in Settings → General.") {
            VStack(spacing: 10) {
                Choice(selected: model.mode == "ready", symbol: "bolt.fill", title: "Often, throughout the day",
                       text: "Always ready", recommended: state.setup?.suggestedMode == "ready") { model.mode = "ready" }
                Choice(selected: model.mode == "on_demand", symbol: "leaf.fill", title: "Now and then",
                       text: "When needed, lighter on memory",
                       recommended: state.setup?.suggestedMode == "on_demand") { model.mode = "on_demand" }
            }
            .frame(maxWidth: 420)
        }
    }

    @ViewBuilder private var models: some View {
        if let setup = state.setup {
            modelsStep(setup)
        } else {
            StepPage(icon: AnyView(Symbol(name: "arrow.down.circle", color: .blue)), title: "Download the models",
                     text: "Checking this Mac…", fine: "") {
                ProgressView().controlSize(.small)
            }
        }
    }

    private func modelsStep(_ setup: SetupInfo) -> some View {
        let setup: SetupInfo? = setup
        let chosen = setup?.models.filter { model.models.contains($0.key) && !$0.downloaded } ?? []
        let mb = chosen.reduce(0) { $0 + ($1.key == "siglip_image" ? $1.groupMb ?? $1.approxMb : $1.approxMb) }
        return StepPage(icon: AnyView(Symbol(name: "arrow.down.circle", color: .blue)),
                        title: "Download the models",
                        text: setup?.profile == "light"
                            ? "Recommended for this Mac: the search model. The others need more memory than an 8 GB Mac can usually spare."
                            : "Recommended for this Mac: all of them.",
                        fine: "This Mac has \(setup?.ramGb ?? 0) GB of memory. Models are downloaded once from huggingface.co into ~/.cache/huggingface and then used offline. Heavy work starts only while the Mac has room for it; you can add or remove models later in Settings → Performance.") {
            VStack(spacing: 8) {
                ForEach(setup?.models.filter { $0.key != "siglip_text" } ?? []) { m in
                    ModelRow(model: m, on: model.models.contains(m.key), light: setup?.profile == "light") {
                        model.toggle(m, profile: setup?.profile ?? "full")
                    }
                }
                if mb > 0 {
                    Text("To download: about \(String(format: "%.1f", Double(mb) / 1000)) GB")
                        .font(.callout).foregroundStyle(.secondary).padding(.top, 4)
                }
            }
            .frame(maxWidth: 460)
        }
    }

    private var folders: some View {
        StepPage(icon: AnyView(Symbol(name: "folder.fill", color: .cyan)),
                 title: "Choose what to search",
                 text: "Lantern indexes these folders and keeps up with changes.",
                 fine: "Lantern only reads your files: it never changes, moves or deletes them. Secrets (.env, keys), app data in ~/Library and iCloud files that are not on this Mac are skipped. You can add folders or leave parts out later in Settings → Folders.") {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(AppState.commonFolders, id: \.self) { path in
                    Toggle(isOn: Binding(get: { model.folders.contains(path) }, set: { on in
                        if on { model.folders.insert(path) } else { model.folders.remove(path) }
                    })) {
                        Label(Format.shortPath(path), systemImage: "folder").labelStyle(.titleAndIcon)
                    }
                    .toggleStyle(.checkbox)
                }
            }
            .frame(maxWidth: 360, alignment: .leading)
        }
    }

    private var launcher: some View {
        StepPage(icon: AnyView(Symbol(name: "command", color: .indigo)),
                 title: "Where do you want to search?",
                 text: "Use Lantern’s own panel, or keep the launcher you know.",
                 fine: "Lantern’s panel opens with ⌥Space. With Raycast or Alfred, Lantern turns its shortcut off and answers inside them (the Raycast extension comes from the Raycast Store; the Alfred workflow is inside Lantern). Change it any time in Settings → General.") {
            VStack(spacing: 10) {
                Choice(selected: model.launcher == .lantern, symbol: "magnifyingglass", title: "Lantern’s panel",
                       text: "⌥Space", recommended: false) { model.launcher = .lantern }
                Choice(selected: model.launcher == .raycast, symbol: "sparkle", title: "Raycast",
                       text: SearchLauncher.raycast.isInstalled ? "Installed" : "Not installed",
                       recommended: false) { model.launcher = .raycast }
                    .disabled(!SearchLauncher.raycast.isInstalled)
                Choice(selected: model.launcher == .alfred, symbol: "hat.widebrim", title: "Alfred",
                       text: SearchLauncher.alfred.isInstalled ? "Installed" : "Not installed",
                       recommended: false) { model.launcher = .alfred }
                    .disabled(!SearchLauncher.alfred.isInstalled)
                if model.launcher == .raycast {
                    Label("Install “Lantern” from the Raycast Store, then give “Search Lantern” a hotkey in Raycast’s settings (Extensions → Lantern). For ⌥Space, move Raycast’s own hotkey first: it uses ⌥Space by default.",
                          systemImage: "info.circle")
                        .font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)
                        .transition(.opacity)
                } else if model.launcher == .alfred {
                    Label("Lantern installs its Alfred workflow when you continue; type “l” and your query in Alfred, or give the workflow a hotkey. Workflows need Alfred’s Powerpack.",
                          systemImage: "info.circle")
                        .font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)
                        .transition(.opacity)
                }
            }
            .animation(.smooth(duration: 0.25), value: model.launcher)
            .frame(maxWidth: 420)
        }
    }

    private var done: some View {
        StepPage(icon: AnyView(Symbol(name: "checkmark.circle.fill", color: .green)),
                 title: "You’re all set",
                 text: "Lantern is getting your files ready in the background. You can search right away; results fill in as it goes.",
                 fine: "Progress is in the menu bar. Indexing pauses on battery, when the Mac is hot, or short of memory, and resumes by itself.") {
            if let dl = state.status?.modelDownload, dl.running {
                let total = dl.totalMb
                let got = min(dl.downloadedMb ?? 0, total)
                VStack(alignment: .leading, spacing: 6) {
                    Text("Downloading models…").font(.callout.weight(.semibold))
                    ProgressView(value: Double(got), total: Double(total))
                    Text(verbatim: "\(got) / \(total) MB").font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: 360)
            }
        }
    }

    // MARK: - Footer

    private var footer: some View {
        HStack {
            if model.step != .welcome && model.step != .done {
                Button("Back") { model.back() }
                    .controlSize(.large)
            }
            Spacer()
            HStack(spacing: 6) {
                ForEach(WelcomeModel.Step.allCases, id: \.self) { s in
                    Circle().fill(s == model.step ? Color.primary.opacity(0.7) : Color.primary.opacity(0.18))
                        .frame(width: 6, height: 6)
                }
            }
            .animation(.smooth(duration: 0.3), value: model.step)
            Spacer()
            primary
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 20)
    }

    @ViewBuilder private var primary: some View {
        let title: LocalizedStringKey = model.step == .done ? "Start Using Lantern" : model.step == .models && needsDownload ? "Download and Continue" : "Continue"
        let button = Button(title) { advance() }.keyboardShortcut(.defaultAction).controlSize(.large)
        if #available(macOS 26, *) {
            button.buttonStyle(.glassProminent)
        } else {
            button.buttonStyle(.borderedProminent)
        }
    }

    private var needsDownload: Bool {
        state.setup?.models.contains { model.models.contains($0.key) && !$0.downloaded } ?? false
    }

    private func advance() {
        switch model.step {
        case .models:
            if let setup = state.setup, needsDownload {
                var keys = model.models
                if keys.contains("siglip_image") { keys.insert("siglip_text") }  // the photo model's two halves
                let wanted = setup.models.filter { keys.contains($0.key) && !$0.downloaded }
                state.downloadModels(SetupInfo.groups(for: Set(wanted.map(\.key)), in: setup.models))
            }
            model.next()
        case .done:
            apply()
            WelcomeWindowController.shared.finish()
        default:
            model.next()
        }
    }

    /// What the steps chose: the mode, the folders, the launcher; recorded as done (not with `--welcome`).
    private func apply() {
        state.setConfig("mode", model.mode)
        if !(model.launcher == .lantern && SearchLauncher.current == .lantern) {
            UserDefaults.standard.set(model.launcher.rawValue, forKey: SearchLauncher.key)
            state.registerShortcut()
            if model.launcher == .alfred, let workflow = SearchLauncher.alfredWorkflow {
                NSWorkspace.shared.open(workflow)  // Alfred asks to import it
            }
        }
        let indexed = state.status?.roots.map(\.path) ?? []
        for folder in model.folders.sorted() where !indexed.contains(where: { folder == $0 || folder.hasPrefix($0 + "/") }) {
            state.addFolder(folder)
        }
        if !CommandLine.arguments.contains("--welcome") {
            UserDefaults.standard.set(true, forKey: AppState.welcomeDoneKey)
        }
    }
}

// MARK: - Building blocks

extension SetupInfo.Model {
    /// What the model is for, in the app's language (the daemon's labels are English).
    var title: LocalizedStringKey {
        switch key {
        case "embeddinggemma": "Search model"
        case "whisper": "Transcripts of recordings"
        case "siglip_image": "Better photo search"
        default: LocalizedStringKey(label)
        }
    }

    /// Its own name and size; names are not translated. SigLIP's two halves are downloaded together.
    var detail: String {
        let name = switch key {
        case "embeddinggemma": "EmbeddingGemma 2"
        case "whisper": "Whisper large-v3-turbo"
        case "siglip_image": "SigLIP 2"
        default: key
        }
        let mb = key == "siglip_image" ? groupMb ?? approxMb : approxMb
        return "\(name) · ~\((Double(mb) / 1000).formatted(.number.precision(.fractionLength(1)))) GB"
    }
}

/// One step: a symbol, a title, a line of text, the step's own content, and the small print.
private struct StepPage<Content: View>: View {
    let icon: AnyView
    let title: LocalizedStringKey
    let text: LocalizedStringKey
    let fine: LocalizedStringKey
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) {
            icon.padding(.top, 44).padding(.bottom, 16)
            Text(title).font(.system(size: 26, weight: .bold)).multilineTextAlignment(.center)
            Text(text)
                .font(.title3)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 480)
                .padding(.top, 8)
                .fixedSize(horizontal: false, vertical: true)
            content.padding(.top, 22)
            Spacer(minLength: 12)
            Text(fine)
                .font(.footnote)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 520)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct Symbol: View {
    let name: String
    let color: Color

    var body: some View {
        Image(systemName: name)
            .font(.system(size: 54, weight: .regular))
            .foregroundStyle(color.gradient)
            .frame(width: 96, height: 72)
    }
}

private struct Feature: View {
    let symbol: String
    let color: Color
    let title: LocalizedStringKey
    let text: LocalizedStringKey

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: symbol).font(.system(size: 22)).foregroundStyle(color).frame(width: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                Text(text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: 440, alignment: .leading)
    }
}

/// A large selectable option, like the choices in Setup Assistant.
private struct Choice: View {
    let selected: Bool
    let symbol: String
    let title: LocalizedStringKey
    let text: LocalizedStringKey
    let recommended: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: symbol).font(.system(size: 20)).foregroundStyle(selected ? .white : .accentColor)
                    .frame(width: 36, height: 36)
                    .background(Circle().fill(selected ? Color.accentColor : Color.accentColor.opacity(0.12)))
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(title).font(.headline)
                        if recommended {
                            Text("Recommended").font(.caption.weight(.medium)).foregroundStyle(.secondary)
                                .padding(.horizontal, 6).padding(.vertical, 1)
                                .background(Capsule().fill(.quaternary))
                        }
                    }
                    Text(text).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                    .font(.title3).foregroundStyle(selected ? Color.accentColor : Color.secondary)
            }
            .padding(12)
            .contentShape(Rectangle())
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(selected ? Color.accentColor.opacity(0.10) : Color.primary.opacity(0.04)))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(selected ? Color.accentColor.opacity(0.6) : Color.primary.opacity(0.08)))
        }
        .buttonStyle(.plain)
        .animation(.smooth(duration: 0.2), value: selected)
    }
}

private struct ModelRow: View {
    let model: SetupInfo.Model
    let on: Bool
    let light: Bool
    let action: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Toggle("", isOn: Binding(get: { on || model.downloaded }, set: { _ in action() }))
                .toggleStyle(.checkbox)
                .labelsHidden()
                .disabled(model.downloaded || model.group == "required")
            VStack(alignment: .leading, spacing: 2) {
                Text(model.title).font(.callout)
                Text(verbatim: model.detail).font(.caption).foregroundStyle(.secondary)
                if model.downloaded {
                    Text("Downloaded").font(.caption).foregroundStyle(.secondary)
                } else if model.heavy && light {
                    Text("Needs about \(model.needsGb.formatted()) GB of memory while it works")
                        .font(.caption).foregroundStyle(.orange)
                }
            }
            Spacer()
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.primary.opacity(0.04)))
    }
}
