import LanternCore
import SwiftUI

/// Ignored file extensions as pills. Type `csv`, `.csv` or `*.csv`: a comma (or space, Return) makes it a pill;
/// the + button adds what is typed or opens the suggestions, which filter as you type.
struct ExtensionField: View {
    @EnvironmentObject var state: AppState
    @State private var text = ""
    @State private var all: [ExtensionSuggestions.Item] = []
    @State private var showSuggestions = false
    @State private var invalid: String?
    @FocusState private var focused: Bool

    private var ignored: [String] { state.settings?.excludeExtensions ?? [] }
    private var suggestions: [ExtensionSuggestions.Item] {
        ExtensionInput.suggestions(for: text, from: all, ignored: ignored)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            FlowLayout(spacing: 6) {
                ForEach(ignored, id: \.self) { ext in
                    Pill(text: ext) { state.removeExtension(ext) }
                }
            }
            HStack(spacing: 6) {
                TextField("", text: $text, prompt: Text("e.g. csv, log, json"))
                    .textFieldStyle(.roundedBorder)
                    .labelsHidden()
                    .focused($focused)
                    .onChange(of: text) { _, new in consume(new) }
                    .onSubmit { addTyped() }
                    .popover(isPresented: Binding(
                        get: { showSuggestions && focused && !suggestions.isEmpty }, set: { showSuggestions = $0 }),
                             arrowEdge: .bottom) {
                        SuggestionList(items: suggestions) { add($0.ext) }
                    }
                Button {
                    if text.trimmingCharacters(in: .whitespaces).isEmpty {
                        focused = true
                        showSuggestions = true
                    } else {
                        addTyped()
                    }
                } label: { Image(systemName: "plus") }
                .help("Add an extension")
            }
            if let invalid {
                Text("“\(invalid)” is not a file extension.").font(.caption).foregroundStyle(.red)
            }
        }
        .task { await loadSuggestions() }
    }

    private func consume(_ new: String) {
        showSuggestions = !new.isEmpty
        let parts = ExtensionInput.split(new)
        guard !parts.completed.isEmpty || !parts.invalid.isEmpty else { return }
        parts.completed.forEach(add)
        invalid = parts.invalid.first
        text = parts.remainder
    }

    private func addTyped() {
        let typed = text.trimmingCharacters(in: .whitespaces)
        guard !typed.isEmpty else { return }
        if let ext = ExtensionInput.normalize(typed) {
            add(ext)
            text = ""
            invalid = nil
        } else {
            invalid = typed
        }
    }

    private func add(_ ext: String) {
        if !ignored.contains(ext) { state.addExtension(ext) }
        text = ""
        showSuggestions = false
    }

    private func loadSuggestions() async {
        all = (try? await state.client.call("extension_suggestions", as: ExtensionSuggestions.self))?.suggestions ?? []
    }
}

private struct Pill: View {
    let text: String
    let remove: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            Text(text).font(.system(.callout, design: .monospaced))
            Button(action: remove) { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("Index this extension again")
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(Capsule().fill(.quaternary))
    }
}

private struct SuggestionList: View {
    let items: [ExtensionSuggestions.Item]
    let pick: (ExtensionSuggestions.Item) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(items, id: \.ext) { item in
                Button { pick(item) } label: {
                    HStack {
                        Text(item.ext).font(.system(.body, design: .monospaced))
                        Spacer(minLength: 16)
                        if item.indexed > 0 {
                            Text("\(Format.count(item.indexed)) indexed").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
            }
        }
        .padding(.vertical, 6)
        .frame(minWidth: 220)
    }
}

/// Wrapping row layout for the pills.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0, maxX: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            maxX = max(maxX, x - spacing)
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxX, height: subviews.isEmpty ? 0 : y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX && x + size.width > bounds.maxX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
