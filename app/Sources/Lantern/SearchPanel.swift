import AppKit
import LanternCore
import SwiftUI

/// Spotlight-like floating panel (⌥Space): appears over the current app without switching apps, closes on Esc or
/// when it loses focus. Borderless with a clear background, so the window shadow follows the rounded glass shape;
/// SwiftUI sizes the window, and the top edge stays put while the results list grows downwards.
final class SearchPanel: NSPanel {
    var anchorTop: CGFloat?
    var anchorMidX: CGFloat?

    init<Content: View>(rootView: Content) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 720, height: 72),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .floating
        isMovableByWindowBackground = false  // moved by SearchPanelController.drag…, which adds the magnets
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        isOpaque = false
        backgroundColor = .clear
        // Liquid Glass draws its own soft shadow; a window shadow on top would outline the window rectangle
        // around the glass' halo. Older systems (material background) keep the window shadow.
        if #available(macOS 26.0, *) { hasShadow = false } else { hasShadow = true }
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let host = NSHostingController(rootView: rootView)
        host.sizingOptions = [.preferredContentSize]
        host.view.wantsLayer = true
        host.view.layer?.backgroundColor = .clear
        contentViewController = host
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    override func setFrame(_ frameRect: NSRect, display flag: Bool) {
        var rect = frameRect
        // SwiftUI resizes the window as content changes (results, preview): keep the top edge and the horizontal
        // centre fixed, otherwise a width change would leave the panel off-centre.
        if let anchorTop { rect.origin.y = anchorTop - rect.height }
        if let anchorMidX { rect.origin.x = (anchorMidX - rect.width / 2).rounded() }
        super.setFrame(rect, display: flag)
        invalidateShadow()  // the shadow is computed from the (rounded) content
    }

    /// LANTERN_PANEL_PINNED=1 keeps the panel open when it loses focus (screenshots during development).
    static let pinned = ProcessInfo.processInfo.environment["LANTERN_PANEL_PINNED"] == "1"

    override func resignKey() {
        super.resignKey()
        if !Self.pinned { SearchPanelController.shared.hide() }  // like Spotlight: clicking elsewhere dismisses it
    }
}

@MainActor
final class SearchPanelController {
    static let shared = SearchPanelController()
    let model = SearchModel()
    private lazy var panel = SearchPanel(rootView: SearchView(model: model, close: { [weak self] in self?.hide() }))

    var isVisible: Bool { panel.isVisible }

    func toggle() { isVisible ? hide() : show() }

    func show(query: String? = nil) {
        if let query { model.query = query }
        MenuCommands.shared.capture()  // before the panel shows: the frontmost app is still the user's
        position()
        panel.alphaValue = 0
        panel.makeKeyAndOrderFront(nil)
        model.appearToken += 1  // subtle scale-in of the content
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.16
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            panel.animator().alphaValue = 1
        }
        model.focusToken += 1  // focuses the text field
        if query != nil { model.search() }
        // Load the photo query model now, while the user types (~0.3 s, released by the daemon after inactivity).
        Task { try? await AppState.shared.client.callRaw("warmup", timeout: 10) }
    }

    func hide() {
        guard panel.isVisible else { return }
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = 0.12
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            Task { @MainActor in
                self?.panel.orderOut(nil)
                self?.model.reset()  // the next opening starts empty, like Spotlight
            }
        })
    }

    /// The screen with the mouse; centred horizontally with the top edge in the upper third (where Spotlight
    /// appears), or where the user dragged it last time (kept as an offset from that spot).
    private func position() {
        let screen = NSScreen.screens.first { NSMouseInRect(NSEvent.mouseLocation, $0.frame, false) } ?? NSScreen.main
        guard let frame = screen?.visibleFrame else { return }
        // The offset is kept as a share of the screen, so a position chosen on a big display means the same spot on a
        // smaller one.
        let offset = UserDefaults.standard.array(forKey: Self.offsetKey) as? [Double] ?? [0, 0]
        place(midX: frame.midX + offset[0] * frame.width, top: Self.homeTop(frame) + offset[1] * frame.height, in: frame)
    }

    // MARK: Dragging, with magnets at the centre line and at the home spot

    private static let offsetKey = "panelOffsetShare"  // [x, y] as shares of the screen (an older point offset is ignored)
    // Magnets with hysteresis: they catch within 15 pt and let go only past 20 pt, so the panel never flickers at the
    // edge and leaving a magnet is a clear, deliberate pull.
    private static let catchDistance: CGFloat = 15
    private static let releaseDistance: CGFloat = 20
    private var dragStart: (mouse: NSPoint, midX: CGFloat, top: CGFloat)?
    private var snapped = (x: false, home: false)

    private static func homeTop(_ frame: NSRect) -> CGFloat { frame.minY + frame.height * 0.78 }

    private func place(midX: CGFloat, top: CGFloat, in frame: NSRect) {
        let half = panel.frame.width / 2
        panel.anchorMidX = min(max(midX, frame.minX + half), frame.maxX - half)
        panel.anchorTop = min(max(top, frame.minY + 120), frame.maxY)
        panel.setFrame(panel.frame, display: true)  // applies both anchors
    }

    /// The header was dragged: follow the mouse, but stick to the vertical centre line of the screen and to the
    /// home spot (a short haptic tap on the trackpad when it catches), so they are easy to find again.
    func dragChanged() {
        let mouse = NSEvent.mouseLocation
        guard let frame = (panel.screen ?? NSScreen.main)?.visibleFrame else { return }
        let start = dragStart ?? (mouse, panel.anchorMidX ?? panel.frame.midX, panel.anchorTop ?? panel.frame.maxY)
        dragStart = start
        var midX = start.midX + mouse.x - start.mouse.x
        var top = start.top + mouse.y - start.mouse.y
        let xLimit = snapped.x ? Self.releaseDistance : Self.catchDistance
        let onCentre = abs(midX - frame.midX) < xLimit
        if onCentre { midX = frame.midX }
        let yLimit = snapped.home ? Self.releaseDistance : Self.catchDistance
        let atHome = onCentre && abs(top - Self.homeTop(frame)) < yLimit
        if atHome { top = Self.homeTop(frame) }
        if (onCentre && !snapped.x) || (atHome && !snapped.home) {
            // levelChange: the firmest of the three system patterns (alignment felt too light)
            NSHapticFeedbackManager.defaultPerformer.perform(.levelChange, performanceTime: .now)
        }
        snapped = (onCentre, atHome)
        place(midX: midX, top: top, in: frame)
    }

    func dragEnded() {
        dragStart = nil
        guard let frame = (panel.screen ?? NSScreen.main)?.visibleFrame, let midX = panel.anchorMidX,
              let top = panel.anchorTop else { return }
        let offset = [Double((midX - frame.midX) / frame.width), Double((top - Self.homeTop(frame)) / frame.height)]
        if offset == [0, 0] {
            UserDefaults.standard.removeObject(forKey: Self.offsetKey)  // back home
        } else {
            UserDefaults.standard.set(offset, forKey: Self.offsetKey)
        }
    }

    var windowNumber: Int { panel.windowNumber }

    /// Displays were added, removed or rearranged: a panel left on a screen that is gone (or now smaller) moves back
    /// onto the screen with the mouse, at its usual place.
    func screensChanged() {
        guard panel.isVisible else { return }
        position()
    }

    /// The daemon answered for the first time: a query typed while it was still starting gets its results now.
    func daemonCameUp() {
        if panel.isVisible && !model.query.isEmpty { model.search() }
    }

    /// Development: render the panel's views into a PNG (window-server effects such as glass are not included).
    func snapshot(to url: URL) {
        guard let view = panel.contentView, let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}

@MainActor
final class SearchModel: ObservableObject {
    @Published var query = "" { didSet { if query != oldValue { scheduleSearch() } } }
    @Published private(set) var sections: [ResultSection] = []
    /// All results in display order; `selection` indexes this list, so ↑↓ moves across sections.
    var results: [SearchResult] { sections.flatMap(\.results) }
    @Published var selection = 0
    @Published private(set) var message: String?
    @Published private(set) var searching = false
    @Published var focusToken = 0
    @Published var appearToken = 0
    /// Quick look shown in place of the results (⌘Y); Esc returns to the list.
    @Published var preview: PreviewContent?
    /// Filter pills: categories with matches for this query, and the chosen one (nil = all).
    @Published private(set) var categories: [ResultCategory] = []
    @Published var category: ResultCategory? { didSet { if category != oldValue { search() } } }
    /// Inline completion from the daemon (earlier queries, definitions, file names); → accepts it.
    @Published private(set) var completion: String?
    /// The query the results are for, when a spelling guess replaced a word ("Showing results for …").
    @Published private(set) var correctedQuery: String?
    /// The words the results matched: the corrected query when there is one (bold words in rows and previews).
    var searchedQuery: String { correctedQuery ?? query }

    /// A kind's name on the pills, icons and section headers: Lantern's own words, in Lantern's language. Only the
    /// results themselves carry names from macOS ("Ustawienia systemowe › Bateria").
    func title(_ category: ResultCategory?) -> Text {
        category.map { Text($0.titleKey) } ?? Text("All")
    }
    var completionRemainder: String? { QueryCompletion.remainder(query: query, completion: completion) }
    /// Pills to show: the categories with matches, plus the chosen one even if it has none now.
    var pillCategories: [ResultCategory] {
        var list = categories
        if let category, !list.contains(category) { list.append(category) }
        return ResultCategory.allCases.filter(list.contains)
    }
    var showsPills: Bool { !query.isEmpty && (pillCategories.count > 1 || category != nil) }
    /// How the scope is shown (pills under the field, or Spotlight-like icons beside it); set by the view.
    var scopeStyle: ScopeStyle = .pills
    /// Icons beside the field: every kind before typing (choose a scope first, like Spotlight's modes), then only
    /// the kinds with matches, plus the chosen one.
    var iconCategories: [ResultCategory] {
        let all = ResultCategory.allCases.filter { $0 != .clipboard || ClipboardHistory.shared.retention != nil }
        return query.trimmingCharacters(in: .whitespaces).isEmpty ? all : pillCategories
    }
    private var eventId: Int?
    private var pending: Task<Void, Never>?
    private var spellCheck: Task<Void, Never>?
    /// "Search for … instead" was chosen for this query: no correction for it.
    private var keepAsTyped: String?

    private func scheduleSearch() {
        pending?.cancel()
        spellCheck?.cancel()
        pending = Task {
            try? await Task.sleep(nanoseconds: 140_000_000)  // debounce typing
            guard !Task.isCancelled else { return }
            search()
        }
    }

    /// After a pause in typing, ask the spell checker about the words of the query; when it has guesses, search
    /// again with them. The daemon applies one only if the index knows the guess, otherwise the results stay.
    private func scheduleSpelling(_ q: String) {
        spellCheck?.cancel()
        guard q != keepAsTyped, category != .code, category != .clipboard else { return }
        spellCheck = Task {
            try? await Task.sleep(nanoseconds: 450_000_000)
            guard !Task.isCancelled, q == query.trimmingCharacters(in: .whitespaces) else { return }
            let guesses = Spelling.guesses(for: q)
            if !guesses.isEmpty { search(spelling: guesses) }
        }
    }

    /// "Search for … instead": the query exactly as typed.
    func searchAsTyped() {
        keepAsTyped = query.trimmingCharacters(in: .whitespaces)
        spellCheck?.cancel()
        search()
    }

    func search(spelling: [String: [String]]? = nil) {
        let q = query.trimmingCharacters(in: .whitespaces)
        if category == .clipboard {  // kept by the app, not the daemon: no request
            sections = ClipboardHistory.shared.section(for: q, limit: 20).map { [$0] } ?? []
            message = sections.isEmpty ? String(localized: "Nothing copied matches.") : nil
            selection = 0
            return
        }
        guard !q.isEmpty else {
            sections = []
            message = nil
            categories = []
            completion = nil
            correctedQuery = nil
            if category != nil && scopeStyle == .pills { category = nil }  // icons: a scope may be chosen first
            return
        }
        searching = true
        Task {
            defer { searching = false }
            do {
                var params: [String: Any] = ["query": q, "limit": 8, "log": SearchHistory.enabled]
                if let category { params["category"] = category.rawValue }
                if let spelling { params["spelling"] = spelling }
                let resp = try await AppState.shared.client.call("search", params, as: SearchResponse.self, timeout: 10)
                guard q == query.trimmingCharacters(in: .whitespaces) else { return }  // a newer query is running
                if spelling != nil && resp.correctedQuery == nil { return }  // no guess the index knows: keep these
                let searched = resp.correctedQuery ?? q
                let clipboard = category == nil ? ClipboardHistory.shared.section(for: searched, limit: 3) : nil
                // Copied text that contains the whole query is a near-certain answer; a short query less so.
                var extra = clipboard.map { [(section: $0, score: searched.count >= 4 ? 0.9 : 0.55)] } ?? []
                if category == nil { extra += MenuCommands.shared.sections(for: searched) }
                extra += WebSearch.section(for: searched, filtered: category != nil).map { ($0, ResultLayout.webScore) }
                // The corrected results replace the shown ones at once: animated, the swap could stop half-way
                // (both lists faded on top of each other) until the next key press.
                var swap = Transaction()
                swap.disablesAnimations = spelling != nil
                withTransaction(swap) {
                    sections = ResultLayout.learnedFirst(
                        ResultLayout.merge(resp.sections, scores: resp.sectionScores, extra: extra), key: resp.learned)
                    categories = resp.categories + (clipboard != nil ? [.clipboard] : [])
                    completion = resp.completion
                    correctedQuery = resp.correctedQuery
                    eventId = resp.eventId
                    selection = 0
                    preview = nil
                    message = sections.isEmpty ? String(localized: "No results for “\(q)”.") : nil
                }
                if spelling == nil { scheduleSpelling(q) }
            } catch {
                guard spelling == nil else { return }  // the second, corrected search is optional (older daemons)
                sections = []
                message = (error as? LocalizedError)?.errorDescription ?? "\(error)"
            }
        }
    }

    /// A fresh panel: no query, no filter, no preview.
    func reset() {
        pending?.cancel()
        spellCheck?.cancel()
        keepAsTyped = nil
        correctedQuery = nil
        query = ""
        category = nil
        preview = nil
        sections = []
        categories = []
        completion = nil
        message = nil
        selection = 0
    }

    /// → at the end of the field: take the suggested ending.
    func acceptCompletion() -> Bool {
        guard completionRemainder != nil, let completion else { return false }
        query = completion
        focusToken += 1  // puts the caret at the end of the accepted text
        return true
    }

    /// Tab / ⇧Tab: next or previous filter pill ("All" first).
    func cycleCategory(_ delta: Int) {
        let options: [ResultCategory?] = [nil] + (scopeStyle == .icons ? iconCategories : pillCategories)
        let current = options.firstIndex(of: category) ?? 0
        category = options[(current + delta + options.count) % options.count]
    }

    /// ⌘1 = All, ⌘2… = the pills in order. Icons: ⌘N is always the same kind (as in Spotlight), again = off.
    func selectCategory(number: Int) {
        if scopeStyle == .icons {
            let kinds = ResultCategory.allCases
            guard kinds.indices.contains(number - 1) else { return }
            category = category == kinds[number - 1] ? nil : kinds[number - 1]
            return
        }
        let options: [ResultCategory?] = [nil] + pillCategories
        if options.indices.contains(number - 1) { category = options[number - 1] }
    }

    func move(_ delta: Int) {
        guard !results.isEmpty else { return }
        selection = (selection + delta + results.count) % results.count
    }

    var selected: SearchResult? { results.indices.contains(selection) ? results[selection] : nil }

    /// One entry point for every shortcut and click (OpenAction): same meaning for code, text and recordings.
    func act(_ action: OpenAction) {
        guard let hit = selected else { return }
        let url = URL(fileURLWithPath: hit.path)
        if let music = MusicResult.parse(hit.path) {
            // One rule for every kind: ↩ (and ⌥↩) consume — a song or a playlist plays; ⌘↩ inspects — it is shown
            // in its app. ⌘Y has nothing to preview.
            guard action != .preview else { return }
            let player = music.source == "spotify" ? MusicPlayer.spotify : MusicPlayer.run
            let isSong = music.kind == "song"
            if action == .reveal {
                player(isSong ? .revealSong : .revealPlaylist, isSong ? music.id : nil, music.playlist)
            } else {
                player(isSong ? .playSong : .playPlaylist, isSong ? music.id : nil, isSong ? music.playlist : music.id)
            }
            recordOpened(hit)
            SearchPanelController.shared.hide()
            return
        }
        if hit.isAction {  // apps, settings, answers, dictionary, shortcuts, web: nothing to preview or reveal
            guard action == .atLocation || action == .fromStart else {
                if action == .reveal && hit.isApp { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                return
            }
            if hit.kind == "clipboard" {
                ClipboardHistory.shared.copy(hit.path)
            } else if hit.kind == "menu" || hit.kind == "window" {
                SearchPanelController.shared.hide()
                MenuCommands.shared.perform(hit.path)
                return
            } else if hit.isApp {
                if action == .fromStart {
                    NewWindow.open(url)  // ⌥↩: a new window
                } else {
                    NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration())
                }
            } else if hit.isAnswer {  // ↩ copies the answer
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(String(hit.path.dropFirst("answer:".count)), forType: .string)
            } else if let link = URL(string: hit.path) {
                NSWorkspace.shared.open(link)
            }
            if hit.kind != "web" && hit.kind != "clipboard" { recordOpened(hit) }  // copied text is never logged
            SearchPanelController.shared.hide()
            return
        }
        switch action {
        case .preview:
            togglePreview(hit)
            return
        case .reveal:
            NSWorkspace.shared.activateFileViewerSelecting([url])
        case .fromStart:
            NSWorkspace.shared.open(url)  // default app, top of the file / beginning of the recording
        case .atLocation:
            if hit.isMedia {
                openRecording(hit)
                return
            }
            EditorOpener.open(url, line: hit.openLine)
        }
        recordOpened(hit)
        SearchPanelController.shared.hide()
    }

    /// ↩ on a recording: QuickTime at the moment (plan B); if that fails or QuickTime cannot play the format,
    /// Lantern's own preview from the same moment (plan A).
    private func openRecording(_ hit: SearchResult) {
        recordOpened(hit)
        guard MediaOpen.quickTimePlays(hit.path) else {
            // mkv, webm…: neither QuickTime nor Lantern's preview (same AVFoundation engine) can play them, so the
            // default app (VLC, IINA…) opens the file from the beginning.
            NSWorkspace.shared.open(URL(fileURLWithPath: hit.path))
            SearchPanelController.shared.hide()
            return
        }
        SearchPanelController.shared.hide()
        MediaOpener.openInQuickTime(hit) { [weak self] ok in
            guard !ok, let self else { return }
            self.preview = .media(hit)
            SearchPanelController.shared.show()
        }
    }

    private func togglePreview(_ hit: SearchResult) {
        if preview != nil {
            preview = nil
            return
        }
        if hit.isMedia {
            preview = .media(hit)
        } else if hit.isPDF {
            preview = .pdf(hit)
        } else if hit.isVisual {
            preview = .image(hit)
        } else if let line = hit.startLine {
            Task {
                let params: [String: Any] = ["path": hit.path, "start_line": line, "end_line": hit.endLine ?? line]
                if let p = try? await AppState.shared.client.call("preview", params, as: TextPreview.self) {
                    preview = .text(p, hit)
                }
            }
        }
    }

    private func recordOpened(_ hit: SearchResult) {
        // Which row was chosen: learned choices ("sl" + ↩ -> Slack) and training data for a future ranking model.
        guard SearchHistory.enabled, let eventId, hit.chunkId >= 0 else { return }
        Task {
            try? await AppState.shared.client.callRaw("opened", ["event_id": eventId, "chunk_id": hit.chunkId,
                                                                 "key": hit.id])
        }
    }
}

/// Settings → Privacy → Search history (on by default): searches and chosen results are kept in the local index
/// (`search_events`, never file contents) to learn the usual choice for a query and, later, to rank results.
enum SearchHistory {
    static let key = "searchHistory"
    static var enabled: Bool { UserDefaults.standard.object(forKey: key) as? Bool ?? true }
}

enum ShortcutHints: String, CaseIterable {
    case row, bar, off
    static let key = "shortcutHints"
}

/// Keycap in Spotlight's style: a soft capsule with the key's symbol.
private struct KeyCap: View {
    let keys: String
    var body: some View {
        Text(keys)
            .font(.system(size: 11, weight: .medium))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 5, style: .continuous).fill(.primary.opacity(0.1)))
    }
}

/// The two actions of the selected result: label in grey, key in a capsule.
private struct RowHint: View {
    let result: SearchResult
    var body: some View {
        HStack(spacing: 10) {
            if !result.isAction {  // nothing to preview
                HStack(spacing: 4) {
                    Text("preview").foregroundStyle(.secondary)
                    KeyCap(keys: "⌘Y")
                }
            }
            HStack(spacing: 4) {
                Text(result.isMedia || result.kind == "song" || result.kind == "playlist" ? "play"
                     : result.isAnswer || result.kind == "clipboard" ? "copy" : "open")
                    .foregroundStyle(.secondary)
                KeyCap(keys: "↩")
            }
        }
        .font(.system(size: 11))
        .transition(.opacity)
    }
}

/// Filter pills under the field: All + the kinds of results that match.
private struct ScopeBar: View {
    @ObservedObject var model: SearchModel

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                pill(nil)
                ForEach(model.pillCategories) { pill($0) }
            }
            .padding(.horizontal, 20)
        }
        .padding(.bottom, 10)
    }

    private func pill(_ category: ResultCategory?) -> some View {
        let selected = model.category == category
        return Button { model.category = category } label: {
            model.title(category)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(selected ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
                .padding(.horizontal, 11)
                .padding(.vertical, 5)
                .background(Capsule().fill(selected ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.primary.opacity(0.08))))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

/// Optional bar of shortcuts at the bottom (Settings → General); aligned with the result text, hairline above.
private struct ShortcutBar: View {
    let previewing: Bool
    let pills: Bool

    var body: some View {
        VStack(spacing: 0) {
            Divider().opacity(0.5)
            HStack(spacing: 16) {
                item("↩", "open here")
                item("⌥↩", "from the start")
                item("⌘Y", previewing ? "back" : "preview")
                if pills { item("⇥", "filter") }
                item("⌘↩", "Finder")
                Spacer()
                item("esc", previewing ? "back" : "close")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 9)
        }
    }

    private func item(_ keys: String, _ label: LocalizedStringKey) -> some View {
        HStack(spacing: 5) {
            KeyCap(keys: keys)
            Text(label).font(.system(size: 11)).foregroundStyle(.secondary)
        }
    }
}

/// Sections with each result's index in the flat display order (the selection index).
private func numbered(_ sections: [ResultSection]) -> [(section: ResultSection, rows: [(index: Int, result: SearchResult)])] {
    var next = 0
    return sections.map { section in
        let rows = section.results.map { result in
            defer { next += 1 }
            return (index: next, result: result)
        }
        return (section: section, rows: rows)
    }
}

/// "Showing results for faktura za prąd · Search for faktrua za prad instead", like a web search engine's line.
private struct CorrectionLine: View {
    let corrected: String
    let typed: String
    let searchTyped: () -> Void

    var body: some View {
        // The text lives in an overlay, so it never sizes the panel: as content it asked for its full width, the
        // panel grew, re-centred and laid out again, endlessly (the app hung).
        Color.clear
            .frame(height: 24)
            .overlay(alignment: .leading) {
                HStack(spacing: 4) {
                    Text("Showing results for \(Text(corrected).bold())")
                        .foregroundStyle(.secondary)
                        .layoutPriority(1)
                    Button(action: searchTyped) {
                        Text("Search for “\(typed)” instead")
                    }
                    .buttonStyle(.link)
                }
                .lineLimit(1)
                .truncationMode(.middle)
                .font(.system(size: 12))
                .padding(.horizontal, 12)
            }
            .clipped()
    }
}

private struct SectionHeader: View {
    let kind: ResultSection.Kind
    let first: Bool

    private var label: Text {
        switch kind {
        case .answer: Text("Answer")
        case .apps: Text("Applications")
        case .shortcuts: Text("Shortcuts")
        case .settings: Text("System Settings")
        case .definitions: Text("Definitions")
        case .photos: Text("Photos")
        case .files: Text("Files")
        case .dictionary: Text("Dictionary")
        case .clipboard: Text("Clipboard")
        case .menu: Text("Menu Items")
        case .windows: Text("Windows")
        case .music: Text("Music")
        case .web: Text("Web")
        }
    }

    var body: some View {
        label
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .padding(.top, first ? 4 : 10)
            .padding(.bottom, 2)
    }
}

struct SearchView: View {
    @ObservedObject var model: SearchModel
    let close: () -> Void
    /// Keyboard shortcut hints: "row" (on the selected result, like Spotlight), "bar" (a bar at the bottom) or "off".
    @AppStorage(ShortcutHints.key) private var hints = ShortcutHints.row.rawValue
    @FocusState private var focused: Bool
    @AppStorage(ScopeStyle.key) private var scopeStyleRaw = ScopeStyle.launchDefault.rawValue
    @State private var shown = false
    @Namespace private var selectionSpace
    @Namespace private var scopeSpace
    private var scopeStyle: ScopeStyle { ScopeStyle(rawValue: scopeStyleRaw) ?? .pills }
    /// Long queries fold the scope icons into one button, so the text keeps its room; a click unfolds them.
    @State private var fieldWidth: CGFloat = 0
    @State private var iconsFolded = false
    @State private var iconsOpened = false
    @State private var foldBack: Task<Void, Never>?
    private static var unfoldedOnce = false  // `--unfold-scopes` unfolds them once
    private var visibleIcons: [ResultCategory] { model.iconCategories.filter { $0 != model.category } }
    /// More kinds than fit calmly beside the field fold into one button by default (pointing at it unfolds them).
    private static let maxUnfolded = 3
    private var showsFoldedButton: Bool {
        (iconsFolded || visibleIcons.count > Self.maxUnfolded) && !iconsOpened && visibleIcons.count > 1
    }

    /// Unfolded icons fold again by themselves when none is chosen: after a while, or soon after the pointer leaves.
    private func scheduleFoldBack(after seconds: Double) {
        foldBack?.cancel()
        foldBack = Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            guard !Task.isCancelled, iconsOpened else { return }
            withAnimation(.smooth(duration: 0.3)) { iconsOpened = false }
            updateFolding()
        }
    }

    private func fieldResized(_ width: CGFloat) {
        fieldWidth = width
        updateFolding()
    }

    /// Fold when the typed text (with its grey completion) reaches the icons; unfold only when all of them fit again
    /// with room to spare, so the row does not flicker at the edge.
    private func updateFolding() {
        guard scopeStyle == .icons, fieldWidth > 0 else { return }
        let text = model.query + (model.completionRemainder ?? "")
        let width = (text as NSString).size(withAttributes: [.font: NSFont.systemFont(ofSize: 24)]).width
        let iconsWidth = CGFloat(max(visibleIcons.count - 1, 0)) * 44  // every icon is 34 pt + 10 pt spacing
        if !iconsFolded && !iconsOpened && width > fieldWidth - 8 {
            iconsFolded = true
        } else if iconsFolded && !iconsOpened && width + iconsWidth < fieldWidth - 40 {
            iconsFolded = false
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            ScopeGlassContainer {
                HStack(spacing: 10) {
                    Image(systemName: "sparkle.magnifyingglass")
                        .font(.system(size: 22, weight: .medium))
                        .foregroundStyle(.secondary)
                        .padding(.trailing, 2)
                        .gesture(PanelDrag.gesture)
                    if scopeStyle == .icons, let category = model.category {
                        ScopeChip(category: category, title: model.title(category), space: scopeSpace) {
                            model.category = nil
                        }
                    }
                    QueryField(model: model, focused: $focused)
                        .layoutPriority(-1)  // gives way to a widened scope icon (its name is never cut)
                        .background(GeometryReader { g in
                            // Measured here, not through a preference: the glass container does not pass those up.
                            Color.clear
                                .onAppear { fieldResized(g.size.width) }
                                .onChange(of: g.size.width) { _, width in fieldResized(width) }
                        })
                    if model.searching {
                        ProgressView().controlSize(.small)
                    }
                    if scopeStyle == .icons && model.preview == nil {
                        if showsFoldedButton {
                            FoldedScopes {
                                withAnimation(.smooth(duration: 0.3)) { iconsOpened = true }
                            }
                            .transition(.scale(scale: 0.6, anchor: .trailing).combined(with: .opacity))
                        } else {
                            let icons = visibleIcons
                            HStack(spacing: 10) {
                            ForEach(Array(icons.enumerated()), id: \.element) { index, category in
                                ScopeIcon(category: category, title: model.title(category), space: scopeSpace) {
                                    model.category = category
                                }
                                // Unfolding: each icon slides out from the folded button on the right, one after
                                // another (the nearest first), quiet and without bounce.
                                .transition(.asymmetric(
                                    insertion: .scale(scale: 0.4, anchor: .trailing).combined(with: .opacity)
                                        .combined(with: .offset(x: 18))
                                        .animation(.smooth(duration: 0.28).delay(0.03 * Double(icons.count - 1 - index))),
                                    removal: .scale(scale: 0.6).combined(with: .opacity)))
                            }
                            }
                            .onHover { inside in
                                if inside {
                                    foldBack?.cancel()
                                } else if iconsOpened {
                                    scheduleFoldBack(after: 1.2)
                                }
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
            .background(Color.clear.contentShape(Rectangle()).gesture(PanelDrag.gesture))  // drag by the header
            .animation(.smooth(duration: 0.3), value: model.category)  // quiet, no bounce (app/CLAUDE.md)
            .animation(.smooth(duration: 0.3), value: model.iconCategories)
            .animation(.smooth(duration: 0.3), value: showsFoldedButton)
            .onChange(of: iconsOpened) { _, open in
                if open { scheduleFoldBack(after: 4) } else { foldBack?.cancel() }
            }
            .onChange(of: showsFoldedButton) { _, folded in
                // `--unfold-scopes`: development screenshots of the unfolding, 1.5 s after the icons fold
                guard folded, !Self.unfoldedOnce, CommandLine.arguments.contains("--unfold-scopes") else { return }
                Self.unfoldedOnce = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    withAnimation(.smooth(duration: 0.3)) { iconsOpened = true }
                }
            }
            .onChange(of: model.query) { _, _ in
                if iconsOpened { iconsOpened = false }  // typing on folds them again
                updateFolding()
            }
            .onChange(of: model.completion) { _, _ in updateFolding() }
            .onChange(of: model.category) { _, _ in
                iconsOpened = false
                updateFolding()
            }

            if scopeStyle == .pills && model.showsPills && model.preview == nil {
                ScopeBar(model: model)
                    .transition(.opacity)
            }

            if !model.results.isEmpty || model.message != nil {
                Divider().opacity(0.5)
                if let preview = model.preview {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(Format.title(preview.result))
                            .font(.system(size: 13, weight: .semibold))
                            .padding(.horizontal, 16)
                            .padding(.top, 10)
                        PreviewView(content: preview, query: model.searchedQuery)
                    }
                    .transition(.opacity)
                } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 2) {
                            Color.clear.frame(height: 0).id(ListEdge.top)
                            if let corrected = model.correctedQuery {
                                CorrectionLine(corrected: corrected, typed: model.query) { model.searchAsTyped() }
                            }
                            ForEach(Array(numbered(model.sections).enumerated()), id: \.element.section.id) { i, group in
                                if model.sections.count > 1 {
                                    SectionHeader(kind: group.section.kind, first: i == 0)
                                }
                                ForEach(group.rows, id: \.result.id) { index, result in
                                    ResultRow(result: result, selected: index == model.selection, space: selectionSpace,
                                              query: model.searchedQuery, showsHint: hints == ShortcutHints.row.rawValue)
                                        .transition(.opacity)
                                        .onTapGesture(count: 2) {
                                            model.selection = index
                                            // ⌥ double-click: from the top / beginning, like ⌥↩
                                            model.act(NSEvent.modifierFlags.contains(.option) ? .fromStart : .atLocation)
                                        }
                                        .onTapGesture { model.selection = index }
                                }
                            }
                            if let message = model.message {
                                Text(message)
                                    .foregroundStyle(.secondary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(16)
                            }
                            Color.clear.frame(height: 0).id(ListEdge.bottom)
                        }
                        .padding(8)
                    }
                    .frame(maxHeight: 340)
                    // Keep the scroller inside the rounded panel, clear of its corners (as AppKit insets it in
                    // rounded windows): a margin at both ends and a little from the right edge.
                    .contentMargins(.vertical, 14, for: .scrollIndicators)
                    .contentMargins(.trailing, 4, for: .scrollIndicators)
                    // Rows are identified by their result (ForEach), never by position: a positional `.id(index)`
                    // let a row from the previous search-as-you-type response stay on screen in the new one.
                    .onChange(of: model.selection) { _, new in
                        // The first and last rows scroll all the way to the edge (section header included, no
                        // leftover gap), like list views in macOS; the rest just come into view.
                        if new == 0 {
                            proxy.scrollTo(ListEdge.top, anchor: .top)
                        } else if new == model.results.count - 1 {
                            proxy.scrollTo(ListEdge.bottom, anchor: .bottom)
                        } else if model.results.indices.contains(new) {
                            proxy.scrollTo(model.results[new].id)
                        }
                    }
                    .animation(.snappy(duration: 0.18), value: model.selection)
                    .animation(.smooth(duration: 0.22), value: model.results.map(\.id))
                }
                }
                if hints == ShortcutHints.bar.rawValue {
                    ShortcutBar(previewing: model.preview != nil, pills: model.showsPills)
                }
            }
        }
        .frame(width: 720)
        .fixedSize(horizontal: false, vertical: true)
        .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))  // nothing drawn past the rounded edge
        .modifier(PanelBackground())
        .scaleEffect(shown ? 1 : 0.97, anchor: .top)
        .onChange(of: model.appearToken) { _, _ in
            shown = false
            withAnimation(.snappy(duration: 0.2)) { shown = true }
        }
        .onKeyPress(.upArrow) { model.preview = nil; model.move(-1); return .handled }
        .onKeyPress(.downArrow) { model.preview = nil; model.move(1); return .handled }
        .onKeyPress(.escape) {  // like Spotlight: a preview closes first, then the filter goes, then the panel
            if model.preview != nil {
                model.preview = nil
            } else if model.category != nil {
                model.category = nil
            } else {
                close()
            }
            return .handled
        }
        .onKeyPress(.return, phases: .down) { press in
            model.act(press.modifiers.contains(.command) ? .reveal : press.modifiers.contains(.option) ? .fromStart : .atLocation)
            return .handled
        }
        .onKeyPress(keys: ["y"], phases: .down) { press in
            guard press.modifiers.contains(.command) else { return .ignored }
            model.act(.preview)
            return .handled
        }
        // Filter pills, like Tab narrowing the scope in Spotlight. ⇧Tab arrives as the "backtab" character (U+0019).
        .onKeyPress(keys: [.tab, KeyEquivalent("\u{19}")], phases: .down) { press in
            guard scopeStyle == .icons || model.showsPills, model.preview == nil else { return .ignored }
            let back = press.key == KeyEquivalent("\u{19}") || press.modifiers.contains(.shift)
            model.cycleCategory(back ? -1 : 1)
            return .handled
        }
        .onKeyPress(keys: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"], phases: .down) { press in
            guard press.modifiers.contains(.command), scopeStyle == .icons || model.showsPills,
                  let n = Int(String(press.characters)) else {
                return .ignored
            }
            model.selectCategory(number: n == 0 ? 10 : n)
            return .handled
        }
        .onKeyPress(.rightArrow, phases: .down) { _ in model.acceptCompletion() ? .handled : .ignored }
        // Icons: ⌫ in an empty field takes the scope back out of it.
        .onKeyPress(.delete, phases: .down) { _ in
            guard scopeStyle == .icons, model.query.isEmpty, model.category != nil else { return .ignored }
            model.category = nil
            return .handled
        }
        .animation(.snappy(duration: 0.18), value: model.showsPills)
        .animation(.snappy(duration: 0.18), value: model.category)
        .animation(.snappy(duration: 0.2), value: model.preview)
        .onChange(of: model.focusToken) { _, _ in focused = true }
        .onChange(of: scopeStyleRaw, initial: true) { _, _ in model.scopeStyle = scopeStyle }
        .onAppear { focused = true }
    }
}

/// The query field. Focusing a text field selects all of its text; a prefilled query gets the cursor at the end
/// instead (TextSelection, macOS 15+).
private struct QueryField: View {
    @ObservedObject var model: SearchModel
    var focused: FocusState<Bool>.Binding
    private let prompt: LocalizedStringKey = "Search Lantern"  // short, like "Spotlight Search"

    var body: some View {
        ZStack(alignment: .leading) {
            // The suggested ending in grey right after the typed text (→ accepts it), like Spotlight's completion.
            if let rest = model.completionRemainder {
                HStack(spacing: 0) {
                    Text(model.query).opacity(0)
                    Text(rest).foregroundStyle(.tertiary)
                }
                .font(.system(size: 24, weight: .regular))
                .lineLimit(1)
                .allowsHitTesting(false)
            }
            if #available(macOS 15.0, *) {
                SelectionAwareField(model: model, focused: focused, prompt: prompt)
            } else {
                TextField(prompt, text: $model.query).modifier(QueryStyle(model: model, focused: focused))
            }
        }
    }
}

@available(macOS 15.0, *)
private struct SelectionAwareField: View {
    @ObservedObject var model: SearchModel
    var focused: FocusState<Bool>.Binding
    let prompt: LocalizedStringKey
    @State private var selection: TextSelection?

    var body: some View {
        TextField(prompt, text: $model.query, selection: $selection)
            .modifier(QueryStyle(model: model, focused: focused))
            .onChange(of: focused.wrappedValue) { _, isFocused in
                if isFocused { DispatchQueue.main.async { caretToEnd() } }
            }
            .onChange(of: model.focusToken) { _, _ in DispatchQueue.main.async { caretToEnd() } }
    }

    private func caretToEnd() {
        selection = TextSelection(insertionPoint: model.query.endIndex)
    }
}

private struct QueryStyle: ViewModifier {
    @ObservedObject var model: SearchModel
    var focused: FocusState<Bool>.Binding

    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .font(.system(size: 24, weight: .regular))
            .focused(focused)
            .onSubmit { model.act(.atLocation) }
    }
}

private struct ResultRow: View {
    let result: SearchResult
    let selected: Bool
    let space: Namespace.ID
    var query = ""
    var showsHint = false

    /// The passage with the words that match the query in bold (`QueryHighlight`), like Spotlight marks matches.
    private var preview: AttributedString {
        var text = AttributedString(result.preview)
        for match in QueryHighlight.ranges(query: query, in: result.preview) {
            guard let range = Range(match, in: text) else { continue }
            text[range].inlinePresentationIntent = .stronglyEmphasized
            text[range].foregroundColor = .primary
        }
        return text
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            if result.isVisual {
                ResultThumbnail(path: result.path, atMs: result.kind == "frame" ? result.startMs : nil, size: 52)
            } else if result.isSetting || result.kind == "clipboard" {
                SettingIcon(symbol: result.settingSymbol, color: result.settingColor)
            } else if let iconPath = result.iconPath {  // the app that handles it: Calculator, Dictionary, browser…
                Image(nsImage: NSWorkspace.shared.icon(forFile: iconPath))
                    .resizable()
                    .frame(width: 32, height: 32)
            } else {
                Image(nsImage: NSWorkspace.shared.icon(forFile: result.path))
                    .resizable()
                    .frame(width: 32, height: 32)
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(Format.title(result))
                        .font(.system(size: 14, weight: .semibold))
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    if selected && showsHint {  // what ↩ and ⌘Y do with this result, like Spotlight's "tab" chip
                        RowHint(result: result)
                    } else {
                        Text(Format.sources(result))
                            .font(.system(size: 11))
                            .foregroundStyle(.tertiary)
                    }
                }
                Text(Format.subtitle(result))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if !result.preview.isEmpty && !["answer", "web", "clipboard", "menu", "window"].contains(result.kind) {
                    Text(preview)
                        .font(result.isDefinition ? .system(size: 12, design: .monospaced) : .system(size: 12))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background {
            if selected {  // one highlight that slides between rows
                // concentric with the panel: panel radius (26) minus the list inset (8)
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(Color.accentColor.opacity(0.22))
                    .matchedGeometryEffect(id: "selection", in: space)
            }
        }
        .contentShape(Rectangle())
    }
}

/// Liquid Glass on macOS 26, translucent material on older systems.
private struct PanelBackground: ViewModifier {
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *) {
            content.glassEffect(.regular, in: .rect(cornerRadius: 26))
        } else {
            content
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 26, style: .continuous))
        }
    }
}


// MARK: - Scope icons (prototype of a Spotlight-like scope control)

/// Where the scope control lives: "icons" beside the query field, like the mode buttons of Spotlight on macOS 26
/// (default), or "pills" under it. `--scope-style pills` (development) or the `scopeStyle` default switches it.
enum ScopeStyle: String {
    case pills, icons
    static let key = "scopeStyle"
    static var launchDefault: ScopeStyle {
        let args = CommandLine.arguments
        let value = args.firstIndex(of: "--scope-style").flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil }
        return value.flatMap(ScopeStyle.init(rawValue:)) ?? .icons
    }
}

extension ResultCategory {
    /// SF Symbols, in the style of Spotlight's mode buttons.
    var symbol: String {
        switch self {
        case .apps: "square.grid.2x2"
        case .settings: "gearshape"
        case .shortcuts: "square.3.layers.3d"
        case .clipboard: "doc.on.clipboard"
        case .code: "chevron.left.forwardslash.chevron.right"
        case .docs: "doc.text"
        case .pdf: "doc.richtext"
        case .photos: "photo"
        case .media: "play.rectangle"
        case .music: "music.note"
        }
    }

    var titleKey: LocalizedStringKey {
        switch self {
        case .apps: "Applications"
        case .settings: "System Settings"
        case .shortcuts: "Shortcuts"
        case .clipboard: "Clipboard"
        case .code: "Code"
        case .docs: "Documents"
        case .pdf: "PDFs"
        case .photos: "Photos"
        case .media: "Recordings and videos"
        case .music: "Music"
        }
    }

    /// ⌘N: fixed per kind, so the shortcut never changes with the results.
    var number: Int { (Self.allCases.firstIndex(of: self) ?? 0) + 1 }
    /// The key after ⌘: 1…9, then 0 for the tenth kind.
    var key: String { number == 10 ? "0" : "\(number)" }
}

/// A round glass button for one kind. On hover it widens into a capsule with the name; choosing it moves it into
/// the query field (matched geometry), where it becomes the chip.
private struct ScopeIcon: View {
    let category: ResultCategory
    let title: Text
    let space: Namespace.ID
    let select: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: select) {
            HStack(spacing: 6) {
                Image(systemName: category.symbol)
                    .font(.system(size: 14, weight: .semibold))
                if hovering {
                    title
                        .font(.system(size: 12, weight: .medium))
                        .fixedSize()
                        // In with the widening capsule, out at once: the name never lingers over the next icon.
                        .transition(.asymmetric(insertion: .opacity.animation(.easeOut(duration: 0.18).delay(0.05)),
                                                removal: .opacity.animation(.easeIn(duration: 0.06))))
                }
            }
            .foregroundStyle(.primary)
            .padding(.horizontal, hovering ? 12 : 0)
            .frame(minWidth: 34, minHeight: 34)
            .clipShape(Capsule())  // nothing is drawn outside the capsule while it narrows
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .fixedSize()  // a widened icon keeps its full name ("Recordings and videos")
        .modifier(ScopeGlass(selected: false))
        .onHover { inside in withAnimation(.snappy(duration: 0.22)) { hovering = inside } }
        .onAppear {  // `--hover-scope docs`: development screenshots of the hover state
            let args = CommandLine.arguments
            if let i = args.firstIndex(of: "--hover-scope"), args.indices.contains(i + 1), args[i + 1] == category.rawValue {
                hovering = true
            }
        }
        .help(title + Text(verbatim: "  ⌘\(category.key)"))
    }
}

/// All the scope icons folded into one, while a long query needs the room. Resting the pointer on it unfolds them
/// (a click too); ⌘1–⌘9 always work.
private struct FoldedScopes: View {
    let open: () -> Void
    @State private var intent: Task<Void, Never>?

    var body: some View {
        Button(action: open) {
            Image(systemName: "line.3.horizontal.decrease")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.primary)
                .frame(width: 34, height: 34)
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .modifier(ScopeGlass(selected: false))
        .help(Text("Filters") + Text(verbatim: "  ⌘1–⌘9"))
        .onHover { inside in
            intent?.cancel()
            guard inside else { return }
            intent = Task { @MainActor in  // a short pause: a pointer just passing over does not unfold them
                try? await Task.sleep(nanoseconds: 120_000_000)
                if !Task.isCancelled { open() }
            }
        }
    }
}

/// The chosen kind inside the query field; a click (or ⌫ in an empty field) takes it back out.
private struct ScopeChip: View {
    let category: ResultCategory
    let title: Text
    let space: Namespace.ID
    let clear: () -> Void

    var body: some View {
        Button(action: clear) {
            HStack(spacing: 6) {
                Image(systemName: category.symbol)
                    .font(.system(size: 13, weight: .semibold))
                title
                    .font(.system(size: 13, weight: .semibold))
                    .fixedSize()
                Text(verbatim: "⌘\(category.key)")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                    .fixedSize()
            }
            .padding(.horizontal, 11)
            .frame(height: 32)
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .modifier(ScopeGlass(selected: true))
        // Grows in at the start of the field while the icon folds away on the right: nothing crosses the text.
        .transition(.scale(scale: 0.6, anchor: .leading).combined(with: .opacity))
    }
}

/// Liquid Glass on macOS 26 (interactive: it reacts to the pointer), a soft fill before.
private struct ScopeGlass: ViewModifier {
    let selected: Bool

    func body(content: Content) -> some View {
        if #available(macOS 26.0, *) {
            content
                .background(Capsule().fill(.primary.opacity(selected ? 0 : 0.07)))  // visible on the empty panel too
                .glassEffect(
                    selected ? .regular.tint(Color.accentColor.opacity(0.45)).interactive() : .regular.interactive(),
                    in: Capsule())
        } else {
            content.background(Capsule().fill(selected ? AnyShapeStyle(Color.accentColor.opacity(0.3))
                                                       : AnyShapeStyle(.primary.opacity(0.08))))
        }
    }
}

/// Lets neighbouring glass shapes blend and morph into each other as they move (macOS 26).
private struct ScopeGlassContainer<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        if #available(macOS 26.0, *) {
            GlassEffectContainer(spacing: 8) { content }
        } else {
            content
        }
    }
}

/// A System Settings icon: the pane's SF Symbol in white on its colour, in a rounded square like the sidebar's.
private struct SettingIcon: View {
    let symbol: String?
    let color: String?

    private var fill: Color {
        switch color?.lowercased() {
        case "blue": .blue
        case "red": .red
        case "green": .green
        case "orange": .orange
        case "yellow": .yellow
        case "purple": .purple
        case "pink": .pink
        case "teal": .teal
        case "indigo": .indigo
        case "black": Color(white: 0.15)
        default: .gray
        }
    }

    var body: some View {
        RoundedRectangle(cornerRadius: 7, style: .continuous)
            .fill(fill.gradient)
            .frame(width: 30, height: 30)
            .overlay {
                Image(systemName: symbol.flatMap { NSImage(systemSymbolName: $0, accessibilityDescription: nil) != nil ? $0 : nil }
                      ?? "gearshape.fill")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
            }
            .frame(width: 32, height: 32)
    }
}

/// "Search the web" as the last row, like Spotlight's: Lantern only opens the default browser with the chosen engine
/// (Settings → General); it sends nothing itself.
enum WebSearch: String, CaseIterable, Identifiable {
    case google, duckduckgo, bing, ecosia, off

    static let key = "webSearch"
    static var current: WebSearch {
        UserDefaults.standard.string(forKey: key).flatMap(WebSearch.init(rawValue:)) ?? .google
    }

    var id: String { rawValue }

    var name: String {
        switch self {
        case .google: "Google"
        case .duckduckgo: "DuckDuckGo"
        case .bing: "Bing"
        case .ecosia: "Ecosia"
        case .off: String(localized: "Off")
        }
    }

    func url(_ query: String) -> URL? {
        let base = switch self {
        case .google: "https://www.google.com/search"
        case .duckduckgo: "https://duckduckgo.com/"
        case .bing: "https://www.bing.com/search"
        case .ecosia: "https://www.ecosia.org/search"
        case .off: ""
        }
        guard self != .off, var parts = URLComponents(string: base) else { return nil }
        parts.queryItems = [URLQueryItem(name: "q", value: query)]
        return parts.url
    }

    @MainActor static func section(for query: String, filtered: Bool) -> [ResultSection] {
        let engine = current
        guard !filtered, let url = engine.url(query) else { return [] }
        var row = SearchResult(path: url.absoluteString, chunkId: 0,
                               symbol: String(localized: "Search the web for “\(query)”"), preview: engine.name,
                               kind: "web")
        row.iconPath = NSWorkspace.shared.urlForApplication(toOpen: URL(string: "https://example.com")!)?.path
        return [ResultSection(kind: .web, results: [row])]
    }
}

/// Dragging the panel by its header (the icon and the empty space; the text field keeps its own selection drags).
@MainActor
enum PanelDrag {
    static var gesture: some Gesture {
        DragGesture(minimumDistance: 2)
            .onChanged { _ in SearchPanelController.shared.dragChanged() }
            .onEnded { _ in SearchPanelController.shared.dragEnded() }
    }
}

/// Invisible markers at both ends of the results list (scroll targets for the first and last rows).
private enum ListEdge: Hashable { case top, bottom }
