import AppKit
import LanternCore
import SwiftUI

/// Spotlight-like floating panel (⌥Space): appears over the current app without switching apps, closes on Esc or
/// when it loses focus. Borderless with a clear background, so the window shadow follows the rounded glass shape;
/// SwiftUI sizes the window, and the top edge stays put while the results list grows downwards.
final class SearchPanel: NSPanel {
    var anchorTop: CGFloat?

    init<Content: View>(rootView: Content) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 720, height: 72),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .floating
        isMovableByWindowBackground = true
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
        if let anchorTop { rect.origin.y = anchorTop - rect.height }
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
            Task { @MainActor in self?.panel.orderOut(nil) }
        })
    }

    /// Centred horizontally, top edge in the upper third of the screen with the mouse (where Spotlight appears).
    private func position() {
        let screen = NSScreen.screens.first { NSMouseInRect(NSEvent.mouseLocation, $0.frame, false) } ?? NSScreen.main
        guard let frame = screen?.visibleFrame else { return }
        panel.anchorTop = frame.minY + frame.height * 0.78
        panel.setFrameTopLeftPoint(NSPoint(x: frame.midX - panel.frame.width / 2, y: panel.anchorTop!))
    }

    var windowNumber: Int { panel.windowNumber }

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
    var completionRemainder: String? { QueryCompletion.remainder(query: query, completion: completion) }
    /// Pills to show: the categories with matches, plus the chosen one even if it has none now.
    var pillCategories: [ResultCategory] {
        var list = categories
        if let category, !list.contains(category) { list.append(category) }
        return ResultCategory.allCases.filter(list.contains)
    }
    var showsPills: Bool { !query.isEmpty && (pillCategories.count > 1 || category != nil) }
    private var eventId: Int?
    private var pending: Task<Void, Never>?

    private func scheduleSearch() {
        pending?.cancel()
        pending = Task {
            try? await Task.sleep(nanoseconds: 140_000_000)  // debounce typing
            guard !Task.isCancelled else { return }
            search()
        }
    }

    func search() {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else {
            sections = []
            message = nil
            categories = []
            completion = nil
            if category != nil { category = nil }
            return
        }
        searching = true
        Task {
            defer { searching = false }
            do {
                var params: [String: Any] = ["query": q, "limit": 8]
                if let category { params["category"] = category.rawValue }
                let resp = try await AppState.shared.client.call("search", params, as: SearchResponse.self, timeout: 10)
                guard q == query.trimmingCharacters(in: .whitespaces) else { return }  // a newer query is running
                sections = resp.sections
                categories = resp.categories
                completion = resp.completion
                eventId = resp.eventId
                selection = 0
                preview = nil
                message = sections.isEmpty ? String(localized: "No results for “\(q)”.") : nil
            } catch {
                sections = []
                message = (error as? LocalizedError)?.errorDescription ?? "\(error)"
            }
        }
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
        let options: [ResultCategory?] = [nil] + pillCategories
        let current = options.firstIndex(of: category) ?? 0
        category = options[(current + delta + options.count) % options.count]
    }

    /// ⌘1 = All, ⌘2… = the pills in order.
    func selectCategory(number: Int) {
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
        if let eventId, hit.chunkId >= 0 {  // which result was chosen: training data for a future reranker
            Task { try? await AppState.shared.client.callRaw("opened", ["event_id": eventId, "chunk_id": hit.chunkId]) }
        }
    }
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
            HStack(spacing: 4) {
                Text("preview").foregroundStyle(.secondary)
                KeyCap(keys: "⌘Y")
            }
            HStack(spacing: 4) {
                Text(result.isMedia ? "play" : "open").foregroundStyle(.secondary)
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
            Text(title(category))
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(selected ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
                .padding(.horizontal, 11)
                .padding(.vertical, 5)
                .background(Capsule().fill(selected ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.primary.opacity(0.08))))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    private func title(_ category: ResultCategory?) -> LocalizedStringKey {
        switch category {
        case nil: "All"
        case .code: "Code"
        case .docs: "Documents"
        case .pdf: "PDFs"
        case .photos: "Photos"
        case .media: "Recordings and videos"
        }
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

private struct SectionHeader: View {
    let kind: ResultSection.Kind
    let first: Bool

    var body: some View {
        Text(kind == .photos ? "Photos" : kind == .definitions ? "Definitions" : "Files")
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
    @State private var shown = false
    @Namespace private var selectionSpace

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "sparkle.magnifyingglass")
                    .font(.system(size: 22, weight: .medium))
                    .foregroundStyle(.secondary)
                QueryField(model: model, focused: $focused)
                if model.searching {
                    ProgressView().controlSize(.small)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)

            if model.showsPills && model.preview == nil {
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
                        PreviewView(content: preview, query: model.query)
                    }
                    .transition(.opacity)
                } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 2) {
                            ForEach(Array(numbered(model.sections).enumerated()), id: \.element.section.id) { i, group in
                                if model.sections.count > 1 {
                                    SectionHeader(kind: group.section.kind, first: i == 0)
                                }
                                ForEach(group.rows, id: \.result.id) { index, result in
                                    ResultRow(result: result, selected: index == model.selection, space: selectionSpace,
                                              query: model.query, showsHint: hints == ShortcutHints.row.rawValue)
                                        .id(index)
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
                        }
                        .padding(8)
                    }
                    .frame(maxHeight: 340)
                    .onChange(of: model.selection) { _, new in proxy.scrollTo(new) }
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
        .modifier(PanelBackground())
        .scaleEffect(shown ? 1 : 0.97, anchor: .top)
        .onChange(of: model.appearToken) { _, _ in
            shown = false
            withAnimation(.snappy(duration: 0.2)) { shown = true }
        }
        .onKeyPress(.upArrow) { model.preview = nil; model.move(-1); return .handled }
        .onKeyPress(.downArrow) { model.preview = nil; model.move(1); return .handled }
        .onKeyPress(.escape) {
            if model.preview != nil { model.preview = nil } else { close() }
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
            guard model.showsPills, model.preview == nil else { return .ignored }
            let back = press.key == KeyEquivalent("\u{19}") || press.modifiers.contains(.shift)
            model.cycleCategory(back ? -1 : 1)
            return .handled
        }
        .onKeyPress(keys: ["1", "2", "3", "4", "5", "6"], phases: .down) { press in
            guard press.modifiers.contains(.command), model.showsPills, let n = Int(String(press.characters)) else {
                return .ignored
            }
            model.selectCategory(number: n)
            return .handled
        }
        .onKeyPress(.rightArrow, phases: .down) { _ in model.acceptCompletion() ? .handled : .ignored }
        .animation(.snappy(duration: 0.18), value: model.showsPills)
        .animation(.snappy(duration: 0.18), value: model.category)
        .animation(.snappy(duration: 0.2), value: model.preview)
        .onChange(of: model.focusToken) { _, _ in focused = true }
        .onAppear { focused = true }
    }
}

/// The query field. Focusing a text field selects all of its text; a prefilled query gets the cursor at the end
/// instead (TextSelection, macOS 15+).
private struct QueryField: View {
    @ObservedObject var model: SearchModel
    var focused: FocusState<Bool>.Binding
    private let prompt: LocalizedStringKey = "Search Lantern — by meaning, in code, recordings…"

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
                if !result.preview.isEmpty {
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
