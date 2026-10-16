import LanternCore
import SwiftUI

/// Content of the menu bar window: state, approval, progress, quick actions.
struct MenuView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            if let error = state.connectionError, state.status == nil {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let s = state.status {
                if s.awaitingApproval {
                    approvalCard(s).transition(.move(edge: .top).combined(with: .opacity))
                }
                if s.worker.running {
                    progress(s.worker).transition(.opacity)
                }
                stats(s)
                if let warning = s.warnings.last {
                    Label(warning, systemImage: "exclamationmark.circle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .lineLimit(3)
                }
            }
            if let message = state.lastMessage {
                Text(message).font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            actions
        }
        .padding(16)
        .frame(width: 320)
        .animation(.smooth(duration: 0.28), value: state.status?.awaitingApproval)
        .animation(.smooth(duration: 0.28), value: state.status?.worker.running)
        .animation(.smooth(duration: 0.28), value: state.phase)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkle.magnifyingglass")
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(.tint)
                .symbolEffect(.pulse, options: .repeating, isActive: state.phase == .indexing)
            Text("Lantern").font(.headline)
            Spacer()
            Text(phaseLabel)
                .contentTransition(.opacity)
                .font(.caption.weight(.medium))
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Capsule().fill(phaseColor.opacity(0.18)))
                .foregroundStyle(phaseColor)
        }
    }

    private var phaseLabel: LocalizedStringKey {
        switch state.phase {
        case .starting: "Starting…"
        case .offline: "Off"
        case .paused:
            state.status?.paused == "battery" ? "Paused: battery" : state.status?.paused == "thermal" ? "Paused: heat" : "Paused"
        case .awaitingApproval: "Waiting for approval"
        case .indexing: "Indexing"
        case .idle: "Ready"
        }
    }

    private var phaseColor: Color {
        switch state.phase {
        case .starting, .offline: .secondary
        case .paused: .orange
        case .awaitingApproval: .blue
        case .indexing: .purple
        case .idle: .green
        }
    }

    private func approvalCard(_ s: DaemonStatus) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Found \(s.queuedTotal) new or changed files")
                .font(.callout.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            Text(s.queued.sorted { $0.key < $1.key }.map { "\(kindLabel($0.key)): \($0.value)" }.joined(separator: " · "))
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Button("Index Now") { state.approve() }
                    .buttonStyle(.borderedProminent)
                Button("Later") { state.decline() }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(.blue.opacity(0.08)))
    }

    private func progress(_ w: DaemonStatus.Worker) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if let done = w.done, let total = w.total, total > 0 {
                ProgressView(value: Double(done), total: Double(total))
                    .animation(.smooth(duration: 0.4), value: done)
                Text("\(w.stage == "transcribe" ? String(localized: "Transcribing") : String(localized: "Indexing files")) \(done)/\(total) · \((w.path.map { ($0 as NSString).lastPathComponent }) ?? "")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            } else {
                ProgressView().controlSize(.small)
            }
        }
    }

    private func stats(_ s: DaemonStatus) -> some View {
        HStack(spacing: 18) {
            Stat(value: Format.count(s.indexedFiles), label: "files")
            Stat(value: Format.count(s.chunks), label: "chunks")
            Stat(value: "\(s.memoryMb) MB", label: "memory")
        }
    }

    private var actions: some View {
        VStack(alignment: .leading, spacing: 2) {
            MenuButton(title: "Search…", systemImage: "magnifyingglass", shortcut: "⌥Space") {
                SearchPanelController.shared.show()
            }
            if state.status?.paused == "manual" {
                MenuButton(title: "Resume Indexing", systemImage: "play.fill") { state.resume() }
            } else {
                MenuButton(title: "Pause Indexing", systemImage: "pause.fill") { state.pause() }
                    .disabled(state.status == nil)
            }
            MenuButton(title: "Add Folder…", systemImage: "folder.badge.plus") { state.chooseFolder() }
                .disabled(state.status == nil)
            MenuButton(title: "Settings…", systemImage: "gearshape") { SettingsWindowController.shared.show() }
            MenuButton(title: "Quit Lantern", systemImage: "power") { NSApp.terminate(nil) }
        }
    }

    private func kindLabel(_ kind: String) -> String {
        ["code": String(localized: "code"), "text": String(localized: "text"), "audio": String(localized: "audio"), "video": String(localized: "video"), "image": String(localized: "photos"), "transcribe": String(localized: "transcription")][kind] ?? kind
    }
}

private struct Stat: View {
    let value: String
    let label: LocalizedStringKey

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.system(.callout, design: .rounded).weight(.semibold))
                .contentTransition(.numericText())
                .animation(.snappy, value: value)
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
    }
}

private struct MenuButton: View {
    let title: LocalizedStringKey
    let systemImage: String
    var shortcut: String? = nil
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: systemImage)
                    .frame(width: 20, alignment: .center)  // equal icon column: titles line up
                    .foregroundStyle(.secondary)
                Text(title)
                Spacer()
                if let shortcut { Text(shortcut).foregroundStyle(.secondary).font(.caption) }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(RoundedRectangle(cornerRadius: 6).fill(hovering ? Color.primary.opacity(0.08) : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}
