import Foundation
import Testing
@testable import LanternCore

@Suite struct ModelsTests {
    @Test func decodesDaemonStatus() throws {
        // Shape of Daemon.api_status (daemon/lantern/service/core.py).
        let json = """
        {"pid": 42, "memory_mb": 490, "mode": "ask", "approved": false, "awaiting_approval": true, "paused": null,
         "worker": {"running": true, "event": "progress", "stage": "index", "done": 3, "total": 10, "path": "/a/b.py"},
         "queued": {"code": 7, "audio": 1}, "failed_jobs": 0, "files": {"indexed": 120, "pending": 8},
         "chunks": 900, "roots": [{"path": "/Users/x/Projekty", "last_scan": 1760000000, "available": true}],
         "last_run": {}, "warnings": ["12:00:00 something"]}
        """
        let s = try DaemonClient.decoder.decode(DaemonStatus.self, from: Data(json.utf8))
        #expect(s.awaitingApproval && s.queuedTotal == 8 && s.indexedFiles == 120)
        #expect(s.worker.done == 3 && s.roots.first?.available == true && s.lastRun?.indexed == nil)
    }

    @Test func decodesSearchResponseWithMediaResult() throws {
        let json = """
        {"results": [{"path": "/Users/x/Filmy/vlog.mp4", "chunk_id": 7, "start_line": null, "end_line": null,
          "symbol": null, "preview": "sadzonki pomidorów", "score": 0.03, "vector_rank": 1, "vector_score": 0.7,
          "keyword_rank": 1, "keyword_score": 5.0, "keyword_coverage": 1.0, "kind": "transcript",
          "start_ms": 108000, "end_ms": 133000}], "event_id": 5, "vector_ms": 20.5, "keyword_ms": 0.4}
        """
        let r = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(r.results.first?.isMedia == true && r.eventId == 5)
        #expect(Format.title(r.results[0]) == "vlog.mp4  ·  1:48")
        #expect(Format.sources(r.results[0]) == "meaning · words")  // English source; Polish comes from pl.lproj
    }
}

@Suite struct ProtocolTests {
    @Test func remoteErrorsAreSurfaced() {
        let reply = Data(#"{"id": 1, "error": {"type": "ValueError", "message": "unknown method 'x'"}}"#.utf8)
        #expect(throws: DaemonError.remote(type: "ValueError", message: "unknown method 'x'")) {
            try DaemonClient.decodeReply(reply)
        }
    }

    @Test func missingSocketMeansNotRunning() async {
        let client = DaemonClient(socketPath: "/tmp/lantern-test-\(UUID().uuidString.prefix(8)).sock")
        await #expect(throws: DaemonError.notRunning) { try await client.callRaw("ping") }
    }
}

@Suite struct FormatTests {
    @Test func clock() {
        #expect(Format.clock(105_000) == "1:45")
        #expect(Format.clock(3_725_000) == "1:02:05")
    }

    @Test func photoResultsShowTheImageSource() throws {
        let json = """
        {"results": [{"path": "/Users/x/Zdjecia/pies.heic", "chunk_id": 9, "preview": "", "score": 0.03,
          "image_rank": 1, "kind": "image"}], "event_id": 1, "vector_ms": 9, "keyword_ms": 0.3}
        """
        let r = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8)).results[0]
        #expect(r.isVisual && Format.sources(r) == "image" && Format.title(r) == "pies.heic")
    }

    @Test func photosComeAsASectionOrderedByTheDaemon() throws {
        let json = """
        {"results": [{"path": "/a/notes.md", "chunk_id": 1, "preview": "", "score": 0.02, "kind": "text"}],
         "photos": [{"path": "/a/pies.jpg", "chunk_id": 2, "preview": "", "score": 3.1, "image_rank": 1,
           "kind": "image"}], "photos_first": true, "event_id": 1, "vector_ms": 9, "keyword_ms": 0.3}
        """
        var resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(resp.sections.map(\.kind) == [.photos, .files])
        resp.photosFirst = false
        #expect(resp.sections.map(\.kind) == [.files, .photos])
        resp.photos = []
        #expect(resp.sections.map(\.kind) == [.files])
    }

    @Test func responsesWithoutPhotosStillDecode() throws {
        let json = #"{"results": [], "event_id": null, "vector_ms": 1, "keyword_ms": 1}"#
        let resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(resp.photos.isEmpty && !resp.photosFirst && resp.sections.isEmpty)
    }

    @Test func definitionsComeFirstAndOpenAtTheirLine() throws {
        let json = """
        {"results": [{"path": "/a/uzycie.py", "chunk_id": 1, "preview": "", "score": 0.02, "kind": "code",
           "start_line": 3, "end_line": 3}],
         "definitions": [{"path": "/a/skrypt.py", "line": 200, "end_line": 201, "name": "add_two",
           "kind": "function", "signature": "def add_two(x, y)", "parent": null, "match": "exact", "score": 4,
           "chunk_id": 7}],
         "photos": [], "photos_first": false, "event_id": 1, "vector_ms": 9, "keyword_ms": 0.3}
        """
        let resp = try DaemonClient.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        #expect(resp.sections.map(\.kind) == [.definitions, .files])
        let d = resp.sections[0].results[0]
        #expect(d.isDefinition && d.openLine == 200 && Format.title(d) == "add_two  ·  skrypt.py")
        #expect(Format.sources(d) == "definition" && d.id != resp.results[0].id)
    }

    @Test func lineJumpUsesTheDefaultEditorsOwnMechanism() {
        let app = URL(fileURLWithPath: "/Applications/X.app")
        let vscode = LineJump.make(bundleId: "com.microsoft.VSCode", appURL: app, executable: nil,
                                   file: "/Users/x/My Code/a.py", line: 200)
        #expect(vscode == .url(URL(string: "vscode://file/Users/x/My%20Code/a.py:200")!))
        let xcode = LineJump.make(bundleId: "com.apple.dt.Xcode", appURL: URL(fileURLWithPath: "/Applications/Xcode.app"),
                                  executable: nil, file: "/a.swift", line: 5)
        #expect(xcode == .command(executable: "/usr/bin/xed", arguments: ["-l", "5", "/a.swift"],
                                  environment: ["DEVELOPER_DIR": "/Applications/Xcode.app/Contents/Developer"]))
        let idea = LineJump.make(bundleId: "com.jetbrains.pycharm", appURL: app,
                                 executable: URL(fileURLWithPath: "/Applications/PyCharm.app/Contents/MacOS/pycharm"),
                                 file: "/a.py", line: 9)
        #expect(idea == .command(executable: "/Applications/PyCharm.app/Contents/MacOS/pycharm",
                                 arguments: ["--line", "9", "/a.py"], environment: [:]))
        #expect(LineJump.make(bundleId: "org.python.IDLE", appURL: app, executable: nil, file: "/a.py", line: 1) == .plain)
    }

    @Test func shortPath() {
        #expect(Format.shortPath("/Users/x/Dokumenty/a.md", home: "/Users/x") == "~/Dokumenty/a.md")
        #expect(Format.shortPath("/Volumes/Dysk/a.md", home: "/Users/x") == "/Volumes/Dysk/a.md")
    }
}
