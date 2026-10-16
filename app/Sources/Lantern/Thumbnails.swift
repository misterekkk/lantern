import AppKit
import AVFoundation
import QuickLookThumbnailing
import SwiftUI

/// Thumbnails for photo and video-frame results: QuickLook for photos (HEIC included), AVFoundation for the frame
/// at the matched moment of a video. Cached in memory for the lifetime of the app.
@MainActor
final class ThumbnailCache {
    static let shared = ThumbnailCache()
    private var cache: [String: NSImage] = [:]

    func image(path: String, atMs: Int?, size: CGFloat) async -> NSImage? {
        let key = "\(path)#\(atMs ?? -1)#\(Int(size))"
        if let hit = cache[key] { return hit }
        let url = URL(fileURLWithPath: path)
        let scale = NSScreen.main?.backingScaleFactor ?? 2
        let image: NSImage?
        if let ms = atMs {
            image = await Self.frame(url: url, ms: ms, size: size * scale)
        } else {
            let request = QLThumbnailGenerator.Request(fileAt: url, size: CGSize(width: size, height: size), scale: scale,
                                                       representationTypes: .thumbnail)
            image = try? await QLThumbnailGenerator.shared.generateBestRepresentation(for: request).nsImage
        }
        if let image { cache[key] = image }
        return image
    }

    private nonisolated static func frame(url: URL, ms: Int, size: CGFloat) async -> NSImage? {
        let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: size, height: size)
        guard let (cg, _) = try? await generator.image(at: CMTime(value: CMTimeValue(ms), timescale: 1000)) else { return nil }
        return NSImage(cgImage: cg, size: .zero)
    }
}

struct ResultThumbnail: View {
    let path: String
    let atMs: Int?
    let size: CGFloat
    @State private var image: NSImage?

    var body: some View {
        Group {
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                RoundedRectangle(cornerRadius: 8, style: .continuous).fill(.quaternary)
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .transition(.opacity)
        .task(id: "\(path)#\(atMs ?? -1)") {
            let loaded = await ThumbnailCache.shared.image(path: path, atMs: atMs, size: size)
            withAnimation(.easeOut(duration: 0.15)) { image = loaded }
        }
    }
}
