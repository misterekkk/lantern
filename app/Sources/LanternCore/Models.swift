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
    public var kinds: [String: Int]?
    public var photoModelLoaded: Bool?
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
    public var imageRank: Int?
    public var kind: String
    public var startMs: Int?
    public var endMs: Int?

    public var id: Int { chunkId }
    public var isMedia: Bool { startMs != nil }
    public var isVisual: Bool { kind == "image" || kind == "frame" }
}

public struct SearchResponse: Decodable, Equatable, Sendable {
    public var results: [SearchResult]
    public var photos: [SearchResult]
    public var photosFirst: Bool
    public var eventId: Int?
    public var vectorMs: Double
    public var keywordMs: Double

    enum CodingKeys: String, CodingKey { case results, photos, photosFirst, eventId, vectorMs, keywordMs }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        results = try c.decode([SearchResult].self, forKey: .results)
        photos = try c.decodeIfPresent([SearchResult].self, forKey: .photos) ?? []  // daemons before Phase 7
        photosFirst = try c.decodeIfPresent(Bool.self, forKey: .photosFirst) ?? false
        eventId = try c.decodeIfPresent(Int.self, forKey: .eventId)
        vectorMs = try c.decode(Double.self, forKey: .vectorMs)
        keywordMs = try c.decode(Double.self, forKey: .keywordMs)
    }

    /// Results grouped like Spotlight: photos and files, the more confident section first (decided by the daemon).
    public var sections: [ResultSection] {
        let files = ResultSection(kind: .files, results: results)
        let photos = ResultSection(kind: .photos, results: photos)
        return (photosFirst ? [photos, files] : [files, photos]).filter { !$0.results.isEmpty }
    }
}

public struct ResultSection: Equatable, Sendable, Identifiable {
    public enum Kind: String, Sendable { case files, photos }
    public var kind: Kind
    public var results: [SearchResult]
    public var id: Kind { kind }
}

/// Mirrors `lantern.service.settings.Settings`.
public struct DaemonSettings: Decodable, Equatable, Sendable {
    public var indexMode: String
    public var pauseOnBattery: Bool
    public var pauseOnThermal: Bool
    public var transcribe: Bool
    public var indexPhotos: Bool
    public var indexVideoFrames: Bool
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
