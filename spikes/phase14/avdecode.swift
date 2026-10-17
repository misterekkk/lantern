// Phase 14 spike: decode a file's first audio track with AVFoundation to 16 kHz mono float32 on stdout (what ffmpeg
// does for Lantern today: -map 0:a:0 -ac 1 -ar 16000 -f f32le). Exit 2: no audio track or unreadable.
import AVFoundation
import Foundation

let url = URL(fileURLWithPath: CommandLine.arguments[1])
let asset = AVURLAsset(url: url)
let sem = DispatchSemaphore(value: 0)
var tracks: [AVAssetTrack] = []
Task {
    tracks = (try? await asset.loadTracks(withMediaType: .audio)) ?? []
    sem.signal()
}
sem.wait()
guard let track = tracks.first, let reader = try? AVAssetReader(asset: asset) else {
    FileHandle.standardError.write("no audio track or unreadable\n".data(using: .utf8)!)
    exit(2)
}
let settings: [String: Any] = [
    AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
    AVLinearPCMBitDepthKey: 32, AVLinearPCMIsFloatKey: true, AVLinearPCMIsBigEndianKey: false,
    AVLinearPCMIsNonInterleaved: false,
]
let output = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
reader.add(output)
guard reader.startReading() else { exit(2) }
let out = FileHandle.standardOutput
while let buffer = output.copyNextSampleBuffer(), let block = CMSampleBufferGetDataBuffer(buffer) {
    var length = 0
    var pointer: UnsafeMutablePointer<CChar>?
    CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &pointer)
    if let pointer { out.write(Data(bytes: pointer, count: length)) }
}
exit(reader.status == .completed ? 0 : 2)
