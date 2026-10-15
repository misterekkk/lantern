import Foundation

/// Mirrors `Daemon.api_status` (daemon/lantern/service/core.py). Keys are snake_case on the wire.
public struct DaemonStatus: Decodable, Equatable, Sendable {
    public struct Worker: Decodable, Equatable, Sendable {
        public var running: Bool
        public var stage: String?
        public var done: Int?
        public var total: Int?
        public var path: String?
    }

    public struct Root: Decodable, Equatable, Sendable, Identifiable {
        public var path: String
        public var lastScan: Double?
        public var available: Bool
        public var id: String { path }
    }

    public struct LastRun: Decodable, Equatable, Sendable {
        public var indexed: Int?
        public var transcribed: Int?
        public var failed: Int?
        public var finished: String?
    }

    public var pid: Int
    public var memoryMb: Int
    public var mode: String
    public var approved: Bool
    public var awaitingApproval: Bool
    public var paused: String?
    public var worker: Worker
    public var queued: [String: Int]
    public var failedJobs: Int
    public var files: [String: Int]
    public var chunks: Int
    public var roots: [Root]
    public var lastRun: LastRun?
    public var warnings: [String]

    public var queuedTotal: Int { queued.values.reduce(0, +) }
    public var filesTotal: Int { files.values.reduce(0, +) }
    public var indexedFiles: Int { files["indexed"] ?? 0 }
}

/// Mirrors `lantern.search.hybrid.Result`.
public struct SearchResult: Decodable, Equatable, Sendable, Identifiable {
    public var path: String
    public var chunkId: Int
    public var startLine: Int?
    public var endLine: Int?
    public var symbol: String?
    public var preview: String
    public var score: Double
    public var vectorRank: Int?
    public var keywordRank: Int?
    public var kind: String
    public var startMs: Int?
    public var endMs: Int?

    public var id: Int { chunkId }
    public var isMedia: Bool { startMs != nil }
}

public struct SearchResponse: Decodable, Equatable, Sendable {
    public var results: [SearchResult]
    public var eventId: Int?
    public var vectorMs: Double
    public var keywordMs: Double
}

/// Mirrors `lantern.service.settings.Settings`.
public struct DaemonSettings: Decodable, Equatable, Sendable {
    public var indexMode: String
    public var pauseOnBattery: Bool
    public var pauseOnThermal: Bool
    public var transcribe: Bool
    public var notify: Bool
    public var debounceS: Double
    public var exclude: [String]
}

public struct ScanSummary: Decodable, Equatable, Sendable {
    public var root: String
    public var new: Int?
    public var changed: Int?
    public var removed: Int?
    public var error: String?
}
