import LanternCore
import SwiftUI

/// Content of the menu bar window, laid out like macOS's own menu extras (Battery): the name and a status dot with
/// its word, the numbers in one quiet line, progress while indexing, cards when a decision is needed, and
/// "Lantern Settings…" at the bottom. Pause / resume appears only while it means something. Quitting is in Settings.
struct MenuView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if let error = state.connectionError, state.status == nil {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let s = state.status {
                if s.waitingForSettings == true {
                    settingsCard(s).transition(.move(edge: .top).combined(with: .opacity))
                } else if s.awaitingApproval {
                    approvalCard(s).transition(.move(edge: .top).combined(with: .opacity))
                }
                if s.worker.running {
                    progress(s.worker).transition(.opacity)
                }
                Text(statsLine(s))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .contentTransition(.numericText())
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
            MenuRow(title: "Lantern Settings…") {
                MenuBarMenu.close()
                SettingsWindowController.shared.show()
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 12)
        .padding(.bottom, 8)
        .frame(width: 300)
        .animation(.smooth(duration: 0.28), value: state.status?.awaitingApproval)
        .animation(.smooth(duration: 0.28), value: state.status?.waitingForSettings)
        .animation(.smooth(duration: 0.28), value: state.status?.worker.running)
        .animation(.smooth(duration: 0.28), value: state.phase)
    }

    private var header: some View {
        HStack(spacing: 8) {
            Text("Lantern").font(.headline)
            Spacer()
            if state.phase == .indexing || state.status?.paused == "manual" {
                let paused = state.status?.paused == "manual"
                Button {
                    paused ? state.resume() : state.pause()
                } label: {
                    Image(systemName: paused ? "play.fill" : "pause.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .frame(width: 22, height: 22)
                        .background(Circle().fill(Color.primary.opacity(0.08)))
                }
                .buttonStyle(.plain)
                .help(paused ? Text("Resume Indexing") : Text("Pause Indexing"))
                .transition(.opacity)
            }
            // The dot alone, in the corner (its colour says it); the word is in the tooltip and for VoiceOver.
            Circle().fill(phaseColor).frame(width: 8, height: 8)
                .help(Text(phaseLabel))
                .accessibilityLabel(Text(phaseLabel))
        }
        .animation(.smooth(duration: 0.25), value: state.phase)
    }

    private func statsLine(_ s: DaemonStatus) -> String {
        String(localized: "\(Format.count(s.indexedFiles)) files · \(Format.count(s.chunks)) chunks · \(s.memoryMb) MB")
    }

    private var phaseLabel: LocalizedStringKey {
        switch state.phase {
        case .starting: "Starting…"
        case .offline: "Off"
        case .paused:
            switch state.status?.paused {
            case "battery": "Paused: battery"
            case "low_power": "Paused: Low Power Mode"
            case "thermal": "Paused: heat"
            default: "Paused"
            }
        case .awaitingApproval: "Waiting for approval"
        case .indexing: "Indexing"
        case .idle: "Ready"
        }
    }

    /// Apple's status colours (as in System Settings → Network): green ready, yellow busy or waiting for you, grey
    /// inactive, red not working.
    private var phaseColor: Color {
        switch state.phase {
        case .idle: .green
        case .indexing, .awaitingApproval: .yellow
        case .starting, .paused: .gray
        case .offline: .red
        }
    }

    /// Work set up in Settings waits until they close (the folder tree may still exclude parts of it).
    private func settingsCard(_ s: DaemonStatus) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Indexing starts when you close Settings")
                .font(.callout.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            Text("You can still choose what to skip in Choose What to Index.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button("Close Settings and Index") {
                MenuBarMenu.close()
                SettingsWindowController.shared.close()
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(.blue.opacity(0.08)))
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

    private func kindLabel(_ kind: String) -> String {
        // Same names as the panel's filter pills, capitalised like other labels in the menu.
        ["code": String(localized: "Code"), "text": String(localized: "Documents"), "pdf": String(localized: "PDFs"),
         "audio": String(localized: "Audio"), "video": String(localized: "Videos"), "image": String(localized: "Photos"),
         "transcribe": String(localized: "Transcripts")][kind] ?? kind.capitalized
    }
}

/// A plain row at the bottom, like "Battery Settings…" in macOS's Battery menu.
private struct MenuRow: View {
    let title: LocalizedStringKey
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack {
                Text(title)
                Spacer()
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 1)  // compact, like the rows of macOS's own menu extras
            .background(RoundedRectangle(cornerRadius: 6).fill(hovering ? Color.primary.opacity(0.08) : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, -8)  // the text lines up with the rest; the hover highlight reaches a little wider
        .onHover { hovering = $0 }
    }
}
