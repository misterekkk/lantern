// lantern-media: decodes recordings and videos for the daemon with AVFoundation (macOS's own decoders), so the
// downloadable app needs no ffmpeg. Measured against ffmpeg in docs/DECISIONS.md ("Phase 14").
//
//   lantern-media audio <file>
//       the first audio track as 16 kHz mono float32 little-endian on stdout (ffmpeg -ac 1 -ar 16000 -f f32le)
//   lantern-media frames <file> <folder> [threshold 0.3] [max gap s 30] [width 448] [max frames 400]
//       key frames as JPEGs in <folder>: the first frame, every cut whose scene score passes the threshold (ffmpeg's
//       select scene score: min(mean abs difference, its change) in percent), and at least one frame per max gap.
//       stdout: "duration <s>", then one "frame <s> <file>" line per frame.
//   lantern-media still <file> <seconds> <out.jpg> [width 320]
//       one upright frame at that moment (thumbnails for the Raycast extension).
//
// Exit 2: no such track or a file AVFoundation cannot read (the daemon then tries ffmpeg if it is installed).
import AVFoundation
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(2)
}

func loadTrack(_ asset: AVURLAsset, _ type: AVMediaType) -> AVAssetTrack? {
    let done = DispatchSemaphore(value: 0)
    nonisolated(unsafe) var found: AVAssetTrack?
    Task {
        found = try? await asset.loadTracks(withMediaType: type).first
        done.signal()
    }
    done.wait()
    return found
}

func duration(_ asset: AVURLAsset) -> Double {
    let done = DispatchSemaphore(value: 0)
    nonisolated(unsafe) var seconds = 0.0
    Task {
        seconds = (try? await asset.load(.duration)).map(CMTimeGetSeconds) ?? 0
        done.signal()
    }
    done.wait()
    return seconds.isFinite ? seconds : 0
}

func audio(_ url: URL) {
    let asset = AVURLAsset(url: url)
    guard let track = loadTrack(asset, .audio), let reader = try? AVAssetReader(asset: asset) else {
        fail("no audio track or unreadable")
    }
    let output = AVAssetReaderTrackOutput(track: track, outputSettings: [
        AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
        AVLinearPCMBitDepthKey: 32, AVLinearPCMIsFloatKey: true, AVLinearPCMIsBigEndianKey: false,
        AVLinearPCMIsNonInterleaved: false,
    ])
    reader.add(output)
    guard reader.startReading() else { fail("cannot read audio") }
    let out = FileHandle.standardOutput
    while let buffer = output.copyNextSampleBuffer(), let block = CMSampleBufferGetDataBuffer(buffer) {
        var length = 0
        var pointer: UnsafeMutablePointer<CChar>?
        CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length,
                                    dataPointerOut: &pointer)
        if let pointer { out.write(Data(bytes: pointer, count: length)) }
    }
    if reader.status != .completed { fail("audio decoding stopped: \(reader.error?.localizedDescription ?? "")") }
}

/// Samples of a BGRA frame on a fixed grid (every channel, as ffmpeg sums every plane), for the scene score.
func grid(_ pixels: CVPixelBuffer, columns: Int = 96, rows: Int = 54) -> [UInt8] {
    CVPixelBufferLockBaseAddress(pixels, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(pixels, .readOnly) }
    let width = CVPixelBufferGetWidth(pixels), height = CVPixelBufferGetHeight(pixels)
    let stride = CVPixelBufferGetBytesPerRow(pixels)
    guard let base = CVPixelBufferGetBaseAddress(pixels)?.assumingMemoryBound(to: UInt8.self) else { return [] }
    var out = [UInt8]()
    out.reserveCapacity(columns * rows * 3)
    for r in 0..<rows {
        let y = (r * height + height / 2) / rows
        for c in 0..<columns {
            let p = base + y * stride + ((c * width + width / 2) / columns) * 4
            out.append(p[0]); out.append(p[1]); out.append(p[2])
        }
    }
    return out
}

func preferredTransform(_ track: AVAssetTrack) -> CGAffineTransform {
    let done = DispatchSemaphore(value: 0)
    nonisolated(unsafe) var transform = CGAffineTransform.identity
    Task {
        transform = (try? await track.load(.preferredTransform)) ?? .identity
        done.signal()
    }
    done.wait()
    return transform
}

/// The frame upright (the track's transform, as ffmpeg's autorotate), `width` wide, as a JPEG.
func writeJPEG(_ pixels: CVPixelBuffer, width: Int, transform: CGAffineTransform, to url: URL, context: CIContext) -> Bool {
    // Core Image's y axis points up: the track transform is conjugated with a flip so it rotates the picture the
    // same way AVFoundation's players do.
    let h = CGFloat(CVPixelBufferGetHeight(pixels))
    let flip = CGAffineTransform(a: 1, b: 0, c: 0, d: -1, tx: 0, ty: h)
    var image = CIImage(cvPixelBuffer: pixels).transformed(by: flip.concatenating(transform).concatenating(flip.inverted()))
    image = image.transformed(by: CGAffineTransform(translationX: -image.extent.minX, y: -image.extent.minY))
    let scale = min(1, Double(width) / Double(image.extent.width))
    let scaled = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
    guard let cg = context.createCGImage(scaled, from: scaled.extent.integral),
          let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil)
    else { return false }
    CGImageDestinationAddImage(dest, cg, [kCGImageDestinationLossyCompressionQuality: 0.8] as CFDictionary)
    return CGImageDestinationFinalize(dest)
}

nonisolated(unsafe) var debugScores = false  // "scores": every frame's scene score, for calibrating against ffmpeg

func frames(_ url: URL, folder: URL, threshold: Double, maxGap: Double, width: Int, maxFrames: Int) {
    let asset = AVURLAsset(url: url)
    guard let track = loadTrack(asset, .video), let reader = try? AVAssetReader(asset: asset) else {
        fail("no video track or unreadable")
    }
    let transform = preferredTransform(track)  // phone videos: saved upright
    let output = AVAssetReaderTrackOutput(track: track, outputSettings: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
    ])
    output.alwaysCopiesSampleData = false
    reader.add(output)
    guard reader.startReading() else { fail("cannot read video") }
    print("duration \(duration(asset))")
    let context = CIContext(options: [.useSoftwareRenderer: false])
    var previous: [UInt8]?
    var previousMAFD = 0.0
    var lastSelected = -Double.infinity
    var count = 0
    while count < maxFrames, let buffer = output.copyNextSampleBuffer() {
        guard let pixels = CMSampleBufferGetImageBuffer(buffer) else { continue }
        let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(buffer))
        let samples = grid(pixels)
        var scene = 0.0
        if let previous, previous.count == samples.count, !samples.isEmpty {
            var sad = 0
            for i in 0..<samples.count { sad += abs(Int(samples[i]) - Int(previous[i])) }
            let mafd = Double(sad) * 100 / Double(samples.count) / 256
            // ×2: measured against ffmpeg's score on 9,400 frames of three videos, this grid gives half its value
            // (median ratio 0.49–0.56) at a correlation of 0.97–0.98, so the same threshold finds the same cuts.
            scene = min(max(2 * min(mafd, abs(mafd - previousMAFD)) / 100, 0), 1)
            previousMAFD = mafd
        }
        previous = samples
        if debugScores { print("score \(t) \(scene)"); continue }
        guard lastSelected == -.infinity || scene > threshold || t - lastSelected >= maxGap else { continue }
        let file = folder.appendingPathComponent(String(format: "f%05d.jpg", count + 1))
        if writeJPEG(pixels, width: width, transform: transform, to: file, context: context) {
            print("frame \(t) \(file.path)")
            lastSelected = t
            count += 1
        }
    }
    if reader.status == .failed { fail("video decoding stopped: \(reader.error?.localizedDescription ?? "")") }
}

func still(_ url: URL, at seconds: Double, to out: URL, width: Int) {
    let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url))
    generator.appliesPreferredTrackTransform = true
    generator.maximumSize = CGSize(width: width, height: width * 4)
    generator.requestedTimeToleranceBefore = CMTime(seconds: 0.5, preferredTimescale: 600)
    generator.requestedTimeToleranceAfter = CMTime(seconds: 0.5, preferredTimescale: 600)
    let done = DispatchSemaphore(value: 0)
    nonisolated(unsafe) var image: CGImage?
    Task {
        image = try? await generator.image(at: CMTime(seconds: max(0, seconds), preferredTimescale: 600)).image
        done.signal()
    }
    done.wait()
    guard let image, let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.jpeg.identifier as CFString, 1, nil)
    else { fail("no frame at \(seconds) s") }
    CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: 0.8] as CFDictionary)
    if !CGImageDestinationFinalize(dest) { fail("cannot write \(out.path)") }
}

let args = CommandLine.arguments
guard args.count >= 3 else {
    fail("usage: lantern-media audio <file> | frames <file> <folder> [threshold gap width max] | still <file> <s> <out.jpg> [width]")
}
let url = URL(fileURLWithPath: args[2])
switch args[1] {
case "audio":
    audio(url)
case "scores":
    debugScores = true
    frames(url, folder: URL(fileURLWithPath: "/dev/null"), threshold: 1, maxGap: .infinity, width: 1, maxFrames: .max)
case "still" where args.count >= 5:
    still(url, at: Double(args[3]) ?? 0, to: URL(fileURLWithPath: args[4]),
          width: args.count > 5 ? Int(args[5]) ?? 320 : 320)
case "frames" where args.count >= 4:
    let num = { (i: Int, d: Double) in args.count > i ? Double(args[i]) ?? d : d }
    frames(url, folder: URL(fileURLWithPath: args[3]), threshold: num(4, 0.3), maxGap: num(5, 30),
           width: Int(num(6, 448)), maxFrames: Int(num(7, 400)))
default:
    fail("unknown command \(args[1])")
}
