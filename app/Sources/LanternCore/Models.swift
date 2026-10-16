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

    public init(
        path: String, chunkId: Int, startLine: Int? = nil, endLine: Int? = nil, symbol: String? = nil,
        preview: String = "", score: Double = 0, vectorRank: Int? = nil, keywordRank: Int? = nil,
        imageRank: Int? = nil, kind: String, startMs: Int? = nil, endMs: Int? = nil
    ) {
        self.path = path; self.chunkId = chunkId; self.startLine = startLine; self.endLine = endLine
        self.symbol = symbol; self.preview = preview; self.score = score; self.vectorRank = vectorRank
        self.keywordRank = keywordRank; self.imageRank = imageRank; self.kind = kind
        self.startMs = startMs; self.endMs = endMs
    }

    /// Unique across sections (a definition and a file result can share a chunk).
    public var id: String { "\(kind)|\(chunkId)|\(startLine ?? -1)|\(path)" }
    public var isMedia: Bool { startMs != nil }
    public var isVisual: Bool { kind == "image" || kind == "frame" }
    public var isDefinition: Bool { kind == "definition" }
    /// Line to open the file at in a code editor (definitions and code/text chunks).
    public var openLine: Int? { isMedia || isVisual ? nil : startLine }
}

/// A code definition from the symbols table: `def add_two(x, y)` in skrypt.py, line 200.
public struct Definition: Decodable, Equatable, Sendable {
    public var path: String
    public var line: Int
    public var endLine: Int
    public var name: String
    public var kind: String
    public var signature: String
    public var parent: String?
    public var match: String
    public var chunkId: Int?

    /// As a result row: title = name, preview = signature, opened at `line`.
    public var asResult: SearchResult {
        SearchResult(
            path: path, chunkId: chunkId ?? -1, startLine: line, endLine: endLine, symbol: name,
            preview: signature, kind: "definition")
    }
}

public struct SearchResponse: Decodable, Equatable, Sendable {
    public var results: [SearchResult]
    public var photos: [SearchResult]
    public var photosFirst: Bool
    public var definitions: [Definition]
    public var eventId: Int?
    public var vectorMs: Double
    public var keywordMs: Double

    enum CodingKeys: String, CodingKey { case results, photos, photosFirst, definitions, eventId, vectorMs, keywordMs }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        results = try c.decode([SearchResult].self, forKey: .results)
        photos = try c.decodeIfPresent([SearchResult].self, forKey: .photos) ?? []  // daemons before Phase 7
        photosFirst = try c.decodeIfPresent(Bool.self, forKey: .photosFirst) ?? false
        definitions = try c.decodeIfPresent([Definition].self, forKey: .definitions) ?? []  // before Phase 8
        eventId = try c.decodeIfPresent(Int.self, forKey: .eventId)
        vectorMs = try c.decode(Double.self, forKey: .vectorMs)
        keywordMs = try c.decode(Double.self, forKey: .keywordMs)
    }

    /// Results grouped like Spotlight: code definitions (exact answers) first, then photos and files with the more
    /// confident one first (decided by the daemon). Same order as the daemon logs, so history ranks match.
    public var sections: [ResultSection] {
        let definitions = ResultSection(kind: .definitions, results: definitions.map(\.asResult))
        let files = ResultSection(kind: .files, results: results)
        let photos = ResultSection(kind: .photos, results: photos)
        return ([definitions] + (photosFirst ? [photos, files] : [files, photos])).filter { !$0.results.isEmpty }
    }
}

public struct ResultSection: Equatable, Sendable, Identifiable {
    public enum Kind: String, Sendable { case definitions, files, photos }
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
