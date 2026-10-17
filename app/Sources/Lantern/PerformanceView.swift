import Charts
import LanternCore
import SwiftUI

/// Live memory of the daemon and the index worker (sampled every second by the daemon), current work and models.
struct PerformanceTab: View {
    @EnvironmentObject var state: AppState
    @State private var snap: PerfSnapshot?
    @State private var fetched = Date()
    @State private var window: Double = 60

    var body: some View {
        SettingsPage {
            Section {
                if let snap {
                    Picker("Range", selection: $window) {
                        Text("60 s").tag(60.0)
                        Text("5 min").tag(300.0)
                        Text("10 min").tag(600.0)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    // Clock time on the x axis, in the system's own time format (24 h in Polish, AM/PM in US English).
                    Chart(snap.points.filter { $0.t >= -window }, id: \.t) { p in
                        LineMark(x: .value("Time", fetched.addingTimeInterval(p.t)), y: .value("MB", p.mb))
                            .foregroundStyle(by: .value("Process", p.series == "daemon"
                                ? String(localized: "Daemon") : String(localized: "Indexing")))
                            .interpolationMethod(.monotone)
                    }
                    .chartXScale(domain: fetched.addingTimeInterval(-window)...fetched)
                    .chartXAxis {
                        AxisMarks(values: .stride(by: .second, count: Int(window / 4))) { _ in
                            AxisGridLine()
                            AxisValueLabel(format: window <= 60
                                ? .dateTime.hour().minute().second() : .dateTime.hour().minute())
                        }
                    }
                    .chartYAxisLabel("MB")
                    .chartForegroundStyleScale([String(localized: "Daemon"): Color.teal, String(localized: "Indexing"): Color.purple])
                    .frame(height: 180)
                    .padding(.top, 6)
                } else {
                    ProgressView().frame(maxWidth: .infinity, minHeight: 160)
                }
            } header: {
                Text("Memory")
            } footer: {
                Text("Physical memory as in Activity Monitor. Peaks include short spikes between samples.")
                    .font(.caption)
            }

            if let snap {
                Section("Now") {
                    LabeledContent("Daemon") {
                        Text(mb(snap.daemon.mb) + peak(snap.daemon.peakMb))
                    }
                    LabeledContent("Indexing") {
                        if snap.worker.running {
                            Text(mb(snap.worker.mb) + peak(snap.worker.peakMb))
                        } else {
                            Text(snap.worker.lastPeakMb.map { String(localized: "Not running · last run peak \($0) MB") }
                                 ?? String(localized: "Not running"))
                        }
                    }
                    if snap.worker.running, let done = snap.worker.done, let total = snap.worker.total {
                        LabeledContent(stageLabel(snap.worker.stage)) {
                            Text("\(done) / \(total)" + (snap.worker.rate.map { String(localized: " · \($0, specifier: "%.1f") files/s") } ?? ""))
                        }
                        if let path = snap.worker.path {
                            Text(Format.shortPath(path)).font(.caption).foregroundStyle(.secondary)
                                .lineLimit(1).truncationMode(.middle)
                        }
                    }
                }
                Section("Models") {
                    ForEach(snap.models) { m in
                        ModelRow(model: m)
                    }
                }
            }
        }
        .navigationSubtitle(subtitle)
        .task {
            while !Task.isCancelled {
                if let s = try? await state.client.call("perf", as: PerfSnapshot.self, timeout: 5) {
                    snap = s
                    fetched = Date()
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }

    private var subtitle: String {
        guard let snap else { return "" }
        if snap.worker.running, let mb = snap.worker.mb {
            return String(localized: "Indexing · \(mb) MB")
        }
        return snap.daemon.mb.map { String(localized: "Idle · \($0) MB") } ?? ""
    }

    private func mb(_ v: Int?) -> String { v.map { "\($0) MB" } ?? "–" }
    private func peak(_ v: Int?) -> String { v.map { String(localized: " · peak \($0) MB") } ?? "" }

    private func stageLabel(_ stage: String?) -> String {
        stage == "transcribe" ? String(localized: "Transcribing") : String(localized: "Indexing files")
    }
}

private struct ModelRow: View {
    @EnvironmentObject var state: AppState
    let model: PerfSnapshot.Model

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            SettingsIcon(symbol: model.key == "whisper" ? "waveform" : model.key.hasPrefix("siglip") ? "photo.fill" : "brain",
                         color: model.key == "whisper" ? .orange : model.key.hasPrefix("siglip") ? .pink : .purple)
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(title).font(.body.weight(.medium))
                Spacer()
                if model.loadedDaemon { Badge(text: String(localized: "Loaded in the daemon"), active: true) }
                if model.loadedWorker { Badge(text: workerText, active: true) }
                if !model.downloaded {
                    if state.status?.modelDownload?.running == true {
                        ProgressView().controlSize(.small)
                    } else {
                        Badge(text: String(localized: "Not downloaded"), active: false)
                        Button("Download") { state.downloadModels([model.key]) }
                            .controlSize(.small)
                    }
                }
            }
            Text("\(model.repo) @ \(String(model.revision.prefix(8)))" + (model.sizeMb.map { " · \($0) MB" } ?? ""))
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
        }
    }

    private var title: String {
        switch model.key {
        case "embeddinggemma": String(localized: "EmbeddingGemma 2 (text, audio, images)")
        case "whisper": String(localized: "Whisper large-v3-turbo (transcription)")
        case "siglip_image": String(localized: "SigLIP 2 (photos)")
        case "siglip_text": String(localized: "SigLIP 2 (photo queries)")
        default: model.key
        }
    }

    private var workerText: String {
        let parts = model.workerParts.map { part in
            switch part {
            case "audio": String(localized: "audio")
            case "vision": String(localized: "images")
            default: String(localized: "text")
            }
        }
        return parts.isEmpty ? String(localized: "Indexing") : String(localized: "Indexing: \(parts.joined(separator: ", "))")
    }
}

private struct Badge: View {
    let text: String
    let active: Bool

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Capsule().fill(active ? AnyShapeStyle(Color.accentColor.opacity(0.2)) : AnyShapeStyle(.quaternary)))
    }
}
