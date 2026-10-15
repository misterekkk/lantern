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
        #expect(Format.sources(r.results[0]) == "meaning and words")  // English source; Polish comes from pl.lproj
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

    @Test func shortPath() {
        #expect(Format.shortPath("/Users/x/Dokumenty/a.md", home: "/Users/x") == "~/Dokumenty/a.md")
        #expect(Format.shortPath("/Volumes/Dysk/a.md", home: "/Users/x") == "/Volumes/Dysk/a.md")
    }
}
