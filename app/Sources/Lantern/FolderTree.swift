import AppKit
import LanternCore
import SwiftUI

/// One node of the folder tree; children are listed by the daemon when the node is expanded.
@MainActor
final class TreeNode: ObservableObject, Identifiable {
    nonisolated let path: String
    let name: String
    let isRoot: Bool
    @Published var entry: TreeEntry?
    @Published var children: [TreeNode]?
    @Published var expanded = false
    @Published var note: String?

    nonisolated var id: String { path }
    var isDir: Bool { isRoot || entry?.isDir == true }

    init(root: String) {
        path = root
        name = Format.shortPath(root)
        isRoot = true
    }

    init(entry: TreeEntry, displayName: String? = nil) {
        path = entry.path
        name = displayName ?? entry.name
        isRoot = false
        self.entry = entry
    }

    /// Lists the folder; existing child nodes are kept (with their expansion) and only get a fresh entry.
    func load() async {
        guard let listing = try? await AppState.shared.client.call("tree", ["path": path], as: TreeListing.self)
        else { return }
        let old = Dictionary(uniqueKeysWithValues: (children ?? []).map { ($0.path, $0) })
        children = listing.entries.map { e in
            if let node = old[e.path] {
                node.entry = e
                return node
            }
            return TreeNode(entry: e)
        }
        note = listing.note
    }

    func reload() async {
        guard children != nil else { return }
        await load()
        for child in children ?? [] where child.expanded { await child.reload() }
    }
}

/// Folder tree with checkboxes: expand down to single files, uncheck what Lantern must not index. With a query,
/// a flat list of matching files and folders (the daemon searches names in the indexed folders).
struct FolderTree: View {
    @EnvironmentObject var state: AppState
    let query: String
    let showAll: Bool
    @State private var roots: [TreeNode] = []
    @State private var pending: (node: TreeNode, count: Int)?
    @State private var hits: [TreeNode]?
    @State private var hitsTruncated = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                if let hits {
                    ForEach(hits) { hit in
                        TreeRow(node: hit, depth: 0, showAll: true, toggle: toggle)
                    }
                    if hits.isEmpty {
                        Text("Nothing found in the indexed folders.").foregroundStyle(.secondary).padding(12)
                    } else if hitsTruncated {
                        Text("Showing the first matches; type more to narrow down.")
                            .font(.caption).foregroundStyle(.secondary).padding(12)
                    }
                } else {
                    ForEach(roots) { root in
                        TreeRow(node: root, depth: 0, showAll: showAll, toggle: toggle)
                    }
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(.background.secondary))
            .padding(20)
        }
        .softScrollEdge()
        .task(id: state.status?.roots.map(\.path) ?? []) { syncRoots() }
        .task(id: query) { await runSearch() }
        .alert(
            pending.map { String(localized: "Remove \($0.count) indexed files of “\($0.node.name)” from the index?") } ?? "",
            isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } })
        ) {
            Button("Remove", role: .destructive) {
                if let p = pending { Task { await apply(p.node, included: false) } }
                pending = nil
            }
            Button("Cancel", role: .cancel) { pending = nil }
        } message: {
            Text("Their text, vectors and history entries are deleted. The files themselves are not touched.")
        }
    }

    private func runSearch() async {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else {
            hits = nil
            return
        }
        try? await Task.sleep(nanoseconds: 300_000_000)  // debounce typing
        guard !Task.isCancelled,
              let result = try? await state.client.call("tree_search", ["query": q], as: TreeSearch.self, timeout: 15)
        else { return }
        let roots = state.status?.roots.map(\.path) ?? []
        hits = result.entries.map { e in
            let root = roots.filter { e.path.hasPrefix($0 + "/") }.max { $0.count < $1.count }
            let shown = root.map { String(e.path.dropFirst($0.count + 1)) } ?? Format.shortPath(e.path)
            return TreeNode(entry: e, displayName: shown)
        }
        hitsTruncated = result.truncated
    }

    private func syncRoots() {
        let paths = state.status?.roots.map(\.path) ?? []
        let old = Dictionary(uniqueKeysWithValues: roots.map { ($0.path, $0) })
        roots = paths.map { old[$0] ?? TreeNode(root: $0) }
        if CommandLine.arguments.contains("--expand-tree"), let first = roots.first, !first.expanded {  // screenshots
            first.expanded = true
            Task { await first.load() }
        }
    }

    private func toggle(_ node: TreeNode) {
        guard let entry = node.entry, entry.toggleable else { return }
        if entry.state == .excluded {
            Task { await apply(node, included: true) }
            return
        }
        Task {
            let preview = try? await AppState.shared.client.call(
                "tree_preview", ["path": node.path], as: PreviewCount.self)
            let count = preview?.indexed ?? 0
            if count > 0 { pending = (node, count) } else { await apply(node, included: false) }
        }
    }

    private func apply(_ node: TreeNode, included: Bool) async {
        await state.setIncluded(node.path, included)
        for root in roots { await root.reload() }
        if hits != nil { await runSearch() }
        try? await Task.sleep(nanoseconds: 2_500_000_000)  // the daemon rescans: refresh the indexed counts
        for root in roots { await root.reload() }
        if hits != nil { await runSearch() }
    }
}

private struct PreviewCount: Decodable { var indexed: Int }
private struct TreeSearch: Decodable {
    var entries: [TreeEntry]
    var truncated: Bool
}

private struct TreeRow: View {
    @ObservedObject var node: TreeNode
    let depth: Int
    let showAll: Bool
    let toggle: (TreeNode) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Color.clear.frame(width: CGFloat(depth) * 16, height: 1)
                if node.isDir && node.entry?.state != .skipped {
                    Button { expand() } label: {
                        Image(systemName: "chevron.right")
                            .font(.system(size: 10, weight: .semibold))
                            .rotationEffect(.degrees(node.expanded ? 90 : 0))
                            .frame(width: 12)
                    }
                    .buttonStyle(.plain)
                } else {
                    Color.clear.frame(width: 12, height: 1)
                }
                checkbox
                Image(nsImage: icon).resizable().frame(width: 16, height: 16)
                Text(node.name)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .foregroundStyle(node.entry?.state == .skipped || node.entry?.state == .excluded ? .secondary : .primary)
                Spacer(minLength: 8)
                detail.font(.caption).foregroundStyle(.secondary)
            }
            .padding(.vertical, 3)
            .padding(.horizontal, 6)
            .contentShape(Rectangle())
            .onTapGesture(count: 2) { if node.isDir { expand() } }
            if node.expanded {
                if let note = node.note {
                    Text(note == "cloud" ? "In iCloud only: not listed, so nothing is downloaded." : "No access to this folder.")
                        .font(.caption).foregroundStyle(.secondary)
                        .padding(.leading, CGFloat(depth + 1) * 16 + 40)
                }
                ForEach(visibleChildren) { child in
                    TreeRow(node: child, depth: depth + 1, showAll: showAll, toggle: toggle)
                }
            }
        }
        .animation(.snappy(duration: 0.18), value: node.expanded)
    }

    private var visibleChildren: [TreeNode] {
        (node.children ?? []).filter { showAll || $0.entry?.reason != "unsupported" }
    }

    private var icon: NSImage {
        NSWorkspace.shared.icon(forFile: node.path)
    }

    @ViewBuilder private var checkbox: some View {
        if node.isRoot {
            Image(systemName: "checkmark.square.fill").foregroundStyle(.tint)
                .help("Indexed folder (remove it in the list above)")
        } else if let entry = node.entry {
            Button { toggle(node) } label: {
                Image(systemName: symbol(entry.state))
                    .foregroundStyle(entry.toggleable ? AnyShapeStyle(.tint) : AnyShapeStyle(.tertiary))
            }
            .buttonStyle(.plain)
            .disabled(!entry.toggleable)
            .help(entry.toggleable ? "" : TreeReason.explanation(entry.reason))
        }
    }

    private func symbol(_ state: TreeEntry.State) -> String {
        switch state {
        case .included: "checkmark.square.fill"
        case .partial: "minus.square.fill"
        case .excluded: "square"
        case .skipped: "square.dashed"
        }
    }

    @ViewBuilder private var detail: some View {
        if let entry = node.entry {
            if entry.state == .skipped {
                Text(TreeReason.label(entry.reason))
            } else if entry.isDir, entry.indexed > 0 {
                Text("\(Format.count(entry.indexed)) indexed")
            } else if !entry.isDir, entry.indexed > 0 {
                Image(systemName: "checkmark.circle").help("In the index")
            }
        }
    }

    private func expand() {
        node.expanded.toggle()
        if node.expanded && node.children == nil { Task { await node.load() } }
    }
}

enum TreeReason {
    static func label(_ reason: String?) -> String {
        switch reason {
        case "hidden": String(localized: "hidden")
        case "secret": String(localized: "secrets")
        case "build": String(localized: "build or cache")
        case "gitignore": String(localized: ".gitignore")
        case "extension": String(localized: "ignored extension")
        case "unsupported": String(localized: "type not indexed")
        case "photos_off": String(localized: "photos off")
        case "media_off": String(localized: "audio and video off")
        case "system": String(localized: "system folder")
        case "bundle": String(localized: "macOS package")
        case "generated": String(localized: "generated file")
        case "cloud": String(localized: "iCloud only")
        case "symlink": String(localized: "link")
        case "pattern": String(localized: "exclusion pattern")
        default: ""
        }
    }

    static func explanation(_ reason: String?) -> String {
        switch reason {
        case "secret": String(localized: "Files with secrets (keys, tokens, .env) are never indexed.")
        case "extension": String(localized: "This extension is on the ignored list below.")
        case "pattern": String(localized: "Matches one of your exclusion patterns.")
        case "gitignore": String(localized: "Listed in a .gitignore file.")
        case "cloud": String(localized: "Not on this Mac (iCloud only): reading it would download it.")
        case "photos_off", "media_off": String(localized: "This file type is switched off in General.")
        default: String(localized: "Always skipped by Lantern.")
        }
    }
}
