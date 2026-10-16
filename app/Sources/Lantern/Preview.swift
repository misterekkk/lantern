import AppKit
import AVKit
import LanternCore
import PDFKit
import SwiftUI

/// Quick look inside the search panel (⌘Y): the matched text range highlighted, or the recording from the moment
/// the words were spoken. The system Quick Look panel cannot start at a line or a time, so Lantern draws its own.
enum PreviewContent: Equatable {
    case text(TextPreview, SearchResult)
    case media(SearchResult)
    case image(SearchResult)
    case pdf(SearchResult)

    var result: SearchResult {
        switch self {
        case .text(_, let r), .media(let r), .image(let r), .pdf(let r): r
        }
    }
}

struct PreviewView: View {
    let content: PreviewContent

    var body: some View {
        switch content {
        case .text(let preview, _): TextPreviewView(preview: preview)
        case .media(let r): MediaPreviewView(result: r)
        case .image(let r):
            VStack(spacing: 6) {
                if let image = NSImage(contentsOfFile: r.path) {
                    Image(nsImage: image).resizable().scaledToFit().frame(maxWidth: .infinity, maxHeight: r.preview.isEmpty ? 380 : 330)
                }
                if !r.preview.isEmpty {  // the text Lantern read from the image (OCR) that matched
                    Text(r.preview).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(12)
        case .pdf(let r):
            PDFPageView(url: URL(fileURLWithPath: r.path), page: r.page ?? 1, passage: r.preview)
                .frame(height: 360)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .padding(12)
        }
    }
}

private struct TextPreviewView: View {
    let preview: TextPreview

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(preview.numbered, id: \.number) { line in
                        let hit = (preview.startLine...preview.endLine).contains(line.number)
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Text("\(line.number)")
                                .foregroundStyle(.tertiary)
                                .frame(width: 44, alignment: .trailing)
                            Text(line.text.isEmpty ? " " : line.text)
                                .foregroundStyle(hit ? .primary : .secondary)
                                .textSelection(.enabled)
                            Spacer(minLength: 0)
                        }
                        .font(.system(size: 12, design: .monospaced))
                        .padding(.vertical, 1)
                        .background(hit ? Color.accentColor.opacity(0.16) : .clear)
                        .id(line.number)
                    }
                }
                .padding(.vertical, 8)
            }
            .frame(height: 340)
            .onAppear { proxy.scrollTo(max(1, preview.startLine - 3), anchor: .top) }
        }
    }
}

private struct MediaPreviewView: View {
    let result: SearchResult

    var body: some View {
        VStack(spacing: 8) {
            if MediaOpen.quickTimePlays(result.path) {
                PlayerView(url: URL(fileURLWithPath: result.path), start: MediaOpen.startSeconds(result.startMs))
                    .frame(height: result.kind == "audio" ? 70 : 300)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            } else {
                Label("macOS cannot play this format. ⌥↩ opens it in its default app.", systemImage: "film")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120)
            }
            if !result.preview.isEmpty {
                Text(result.preview)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Spacer()
                Button("Open in QuickTime Player") {
                    MediaOpener.openInQuickTime(result) { ok in
                        if ok { SearchPanelController.shared.hide() }
                    }
                }
                .disabled(!MediaOpen.quickTimePlays(result.path))
            }
        }
        .padding(12)
    }
}

/// A PDF page (PDFKit) with the matched passage highlighted. Preview cannot be told to open a page, so Lantern shows
/// it here; ↩ opens the file in the default PDF app.
private struct PDFPageView: NSViewRepresentable {
    let url: URL
    let page: Int
    let passage: String

    func makeNSView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePage
        view.backgroundColor = .clear
        guard let doc = PDFDocument(url: url) else { return view }
        view.document = doc
        let target = doc.page(at: max(0, page - 1))
        // Highlight the start of the passage on that page (the first words; line breaks in PDFs break longer matches).
        let words = passage.split(separator: " ").prefix(5).joined(separator: " ")
        if !words.isEmpty, let target,
           let hit = doc.findString(words, withOptions: .caseInsensitive).first(where: { $0.pages.contains(target) }) {
            hit.color = NSColor.systemYellow.withAlphaComponent(0.5)
            view.highlightedSelections = [hit]
            view.go(to: hit)
        } else if let target {
            view.go(to: target)
        }
        return view
    }

    func updateNSView(_ view: PDFView, context: Context) {}
}

/// System player (the one QuickTime and Quick Look use), started at `start` seconds.
private struct PlayerView: NSViewRepresentable {
    let url: URL
    let start: Double

    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.controlsStyle = .inline
        view.showsFullScreenToggleButton = true
        let player = AVPlayer(url: url)
        view.player = player
        player.seek(to: CMTime(seconds: start, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) { _ in
            player.play()
        }
        return view
    }

    func updateNSView(_ view: AVPlayerView, context: Context) {}

    static func dismantleNSView(_ view: AVPlayerView, coordinator: ()) {
        view.player?.pause()  // stop the sound when the preview closes
        view.player = nil
    }
}

/// Plan B: QuickTime Player at the moment of the match, through its scripting dictionary (`current time`).
/// macOS asks once for permission to control QuickTime; any failure (refused, unsupported file) is reported to
/// the caller, which then shows Lantern's own preview (plan A).
enum MediaOpener {
    private static let script = """
    on run argv
        set f to POSIX file (item 1 of argv)
        set t to (item 2 of argv) as integer
        tell application "QuickTime Player"
            activate
            set d to open f
            if d is missing value then set d to document 1
            set current time of d to t
            play d
        end tell
    end run
    """

    @MainActor
    static func openInQuickTime(_ result: SearchResult, completion: @escaping @MainActor (Bool) -> Void) {
        guard MediaOpen.quickTimePlays(result.path) else {
            completion(false)
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        // Whole seconds: AppleScript reads "105.5" differently depending on the system's decimal separator.
        process.arguments = ["-e", script, result.path, String(Int(MediaOpen.startSeconds(result.startMs)))]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { p in
            let ok = p.terminationStatus == 0
            Task { @MainActor in completion(ok) }
        }
        do { try process.run() } catch { completion(false) }
    }
}
