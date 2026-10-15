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

    private var phaseLabel: String {
        switch state.phase {
        case .starting: "Uruchamianie…"
        case .offline: "Wyłączony"
        case .paused:
            state.status?.paused == "battery" ? "Pauza: bateria" : state.status?.paused == "thermal" ? "Pauza: temperatura" : "Wstrzymany"
        case .awaitingApproval: "Czeka na zgodę"
        case .indexing: "Indeksuje"
        case .idle: "Gotowy"
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
            Text("Znaleziono \(s.queuedTotal) nowych lub zmienionych plików")
                .font(.callout.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            Text(s.queued.sorted { $0.key < $1.key }.map { "\(kindLabel($0.key)): \($0.value)" }.joined(separator: " · "))
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Button("Zaindeksuj") { state.approve() }
                    .buttonStyle(.borderedProminent)
                Button("Później") { state.decline() }
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
                Text("\(w.stage == "transcribe" ? "Transkrypcja" : "Indeksowanie") \(done)/\(total) · \((w.path.map { ($0 as NSString).lastPathComponent }) ?? "")")
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
            Stat(value: "\(s.indexedFiles)", label: "plików")
            Stat(value: "\(s.chunks)", label: "fragmentów")
            Stat(value: "\(s.memoryMb) MB", label: "pamięć")
        }
    }

    private var actions: some View {
        VStack(alignment: .leading, spacing: 2) {
            MenuButton(title: "Szukaj…", systemImage: "magnifyingglass", shortcut: "⌥Space") {
                SearchPanelController.shared.show()
            }
            if state.status?.paused == "manual" {
                MenuButton(title: "Wznów indeksowanie", systemImage: "play.fill") { state.resume() }
            } else {
                MenuButton(title: "Wstrzymaj indeksowanie", systemImage: "pause.fill") { state.pause() }
                    .disabled(state.status == nil)
            }
            MenuButton(title: "Dodaj folder…", systemImage: "folder.badge.plus") { state.chooseFolder() }
                .disabled(state.status == nil)
            MenuButton(title: "Ustawienia…", systemImage: "gearshape") { SettingsWindowController.shared.show() }
            MenuButton(title: "Zakończ Lantern", systemImage: "power") { NSApp.terminate(nil) }
        }
    }

    private func kindLabel(_ kind: String) -> String {
        ["code": "kod", "text": "tekst", "audio": "audio", "video": "wideo", "transcribe": "transkrypcja"][kind] ?? kind
    }
}

private struct Stat: View {
    let value: String
    let label: String

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
    let title: String
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
