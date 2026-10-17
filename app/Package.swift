// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Lantern",
    platforms: [.macOS(.v14)],
    targets: [
        // Daemon API client, models and formatting: no UI, unit-tested.
        .target(name: "LanternCore", path: "Sources/LanternCore"),
        // Menu bar app: status, approval, search panel (⌥Space), settings, daemon supervision.
        .executableTarget(name: "Lantern", dependencies: ["LanternCore"], path: "Sources/Lantern"),
        // Media decoding for the daemon with AVFoundation (no ffmpeg needed): audio as 16 kHz mono, key frames.
        .executableTarget(name: "lantern-media", path: "Sources/LanternMedia"),
        .testTarget(name: "LanternCoreTests", dependencies: ["LanternCore"], path: "Tests/LanternCoreTests"),
    ],
    swiftLanguageModes: [.v5]
)
