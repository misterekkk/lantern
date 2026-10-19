import LanternCore
import SwiftUI

/// Content of the menu bar window, laid out like macOS's own menu extras (Battery): the name and a status dot with
/// its word, the numbers in one quiet line, progress while indexing, cards when a decision is needed, and
/// "Lantern Settings…" at the bottom. Pause / resume appears only while it means something. Quitting is in Settings.
struct MenuView: View {
    @EnvironmentObject var state: AppState
    @State private var wantWhisper = true
    @State private var wantPhotos = true

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
                if let problem = s.indexProblem {
                    problemCard(problem).transition(.move(edge: .top).combined(with: .opacity))
                } else if s.modelsMissing == true || s.modelDownload?.running == true {
                    modelsCard(s).transition(.move(edge: .top).combined(with: .opacity))
                } else if s.waitingForSettings == true {
                    settingsCard(s).transition(.move(edge: .top).combined(with: .opacity))
                } else if s.awaitingApproval {
                    approvalCard(s).transition(.move(edge: .top).combined(with: .opacity))
                }
                if s.worker.running {
                    progress(s.worker).transition(.opacity)
                }
                if s.indexProblem == nil {  // an index that cannot be read has no counts worth showing ("0 files")
                    Text(statsLine(s))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .contentTransition(.numericText())
                }
                // worded by the app in Lantern's language; a daemon from before 0.2.1 sends only English text
                if let warning = s.warningEvents.map({ $0.last.flatMap(Format.warning) }) ?? s.warnings.last {
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
        .animation(.smooth(duration: 0.28), value: state.status?.modelsMissing)
        .animation(.smooth(duration: 0.28), value: state.status?.modelDownload?.running)
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
            Image(nsImage: phaseImage).resizable().frame(width: 10, height: 10)
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
            case "low_disk": "Paused: disk almost full"
            case "memory": "Paused: the Mac is short of memory"
            default: "Paused"
            }
        case .problem: "The index needs attention"
        case .setup: "Setup not finished"
        case .awaitingApproval: "Waiting for approval"
        case .indexing: "Indexing"
        case .idle: "Ready"
        }
    }

    /// macOS's own status images, as System Settings draws them (Network, Sharing): green ready, amber busy or waiting
    /// for you, grey inactive, red not working. SwiftUI's .yellow is systemYellow (#FFCC00), brighter than their amber.
    private var phaseImage: NSImage {
        let name: NSImage.Name = switch state.phase {
        case .idle: NSImage.statusAvailableName
        case .indexing, .awaitingApproval, .setup: NSImage.statusPartiallyAvailableName
        case .problem: NSImage.statusUnavailableName
        case .starting, .paused: NSImage.statusNoneName
        case .offline: NSImage.statusUnavailableName
        }
        return NSImage(named: name) ?? NSImage()
    }

    /// First launch of the prebuilt .dmg (or while a model is downloading): one-click download into ~/.cache/huggingface/hub.
    private func modelsCard(_ s: DaemonStatus) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let dl = s.modelDownload, dl.running {
                Text("Downloading models…")
                    .font(.callout.weight(.semibold))
                let approx = max(dl.approxMb ?? 1200, 1)
                let got = min(dl.downloadedMb ?? 0, approx)
                ProgressView(value: Double(got), total: Double(approx))
                    .animation(.smooth(duration: 0.4), value: got)
                Text("\(dl.step ?? 1)/\(dl.total ?? 1) · \(got) / ~\(approx) MB")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("Download the search model")
                    .font(.callout.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Text("One-time download from Hugging Face (~1.2 GB); after that Lantern works completely offline.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Toggle("Whisper — transcripts of recordings (~1.6 GB)", isOn: $wantWhisper)
                    .font(.caption)
                Toggle("SigLIP 2 — better photo search (~1 GB)", isOn: $wantPhotos)
                    .font(.caption)
                    .task {  // the optional models follow this Mac's memory: on by default from 16 GB, off on 8 GB
                        if state.setup == nil { await state.loadSetup() }
                        if state.setup?.profile == "light" { wantWhisper = false; wantPhotos = false }
                    }
                if state.setup?.profile == "light" {  // 8 GB: off by default, and why (phase 17)
                    Text("On a Mac with \(state.setup?.ramGb ?? 8) GB these two can push other apps into swap; Lantern starts them only when there is room.")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if s.modelDownload?.error != nil {  // the daemon's English detail stays in logs/models.log
                    Text("The model download failed. Check the connection and try again; details are in logs/models.log.")
                        .font(.caption).foregroundStyle(.red)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Button("Download") {
                    var groups = ["required"]
                    if wantWhisper { groups.append("whisper") }
                    if wantPhotos { groups.append("photos") }
                    state.downloadModels(groups)
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(.blue.opacity(0.08)))
    }

    /// An index from a newer Lantern, or a damaged one: what happened and what to do, instead of "Off".
    private func problemCard(_ problem: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(problem == "newer" ? "This index is from a newer Lantern" : "The index is damaged")
                .font(.callout.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            Text(problem == "newer"
                 ? "Update Lantern to keep using it, or rebuild it for this version (your folders stay; everything is indexed again)."
                 : "Lantern can rebuild it: your folders stay, and everything is indexed again. Your files are not touched.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            // side by side when they fit; one under the other when the words are longer (Polish)
            ViewThatFits(in: .horizontal) {
                HStack { problemButtons(problem) }
                VStack(alignment: .leading, spacing: 6) { problemButtons(problem) }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(.red.opacity(0.08)))
    }

    @ViewBuilder private func problemButtons(_ problem: String) -> some View {
        if problem == "newer" {
            Button("Get the Latest Version") {
                NSWorkspace.shared.open(URL(string: "https://github.com/misterekkk/lantern/releases/latest")!)
            }
            .buttonStyle(.borderedProminent)
            .fixedSize()
        }
        Button("Rebuild the Index") { state.rebuildIndex() }
            .buttonStyle(.bordered)
            .fixedSize()
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
                Text("\(w.stage == "transcribe" ? String(localized: "Transcribing") : w.stage == "people" ? String(localized: "Counting people in photos") : String(localized: "Indexing files")) \(done)/\(total) · \((w.path.map { ($0 as NSString).lastPathComponent }) ?? "")")
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
