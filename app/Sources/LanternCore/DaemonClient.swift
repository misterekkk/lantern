import Darwin
import Foundation

public enum DaemonError: Error, Equatable, LocalizedError {
    case notRunning
    case protocolError(String)
    case remote(type: String, message: String)

    public var errorDescription: String? {
        switch self {
        case .notRunning: "Demon Lanterna nie działa."
        case .protocolError(let detail): "Błąd komunikacji z demonem: \(detail)"
        case .remote(_, let message): message
        }
    }
}

/// Client of the daemon API: newline-delimited JSON over a user-only Unix socket, one request per connection
/// (see daemon/lantern/api/protocol.py). Blocking socket I/O runs off the main actor.
public struct DaemonClient: Sendable {
    public let socketPath: String

    public init(socketPath: String = DaemonClient.defaultSocketPath) {
        self.socketPath = socketPath
    }

    /// LANTERN_SOCKET, else <LANTERN_HOME>/lantern.sock, else ~/Library/Application Support/Lantern/lantern.sock.
    public static var defaultSocketPath: String {
        let env = ProcessInfo.processInfo.environment
        if let socket = env["LANTERN_SOCKET"], !socket.isEmpty { return socket }
        return (dataDirectory as NSString).appendingPathComponent("lantern.sock")
    }

    public static var dataDirectory: String {
        if let home = ProcessInfo.processInfo.environment["LANTERN_HOME"], !home.isEmpty {
            return (home as NSString).expandingTildeInPath
        }
        return (NSHomeDirectory() as NSString).appendingPathComponent("Library/Application Support/Lantern")
    }

    public static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    public func call<T: Decodable>(_ method: String, _ params: [String: Any] = [:], as type: T.Type,
                                   timeout: TimeInterval = 30) async throws -> T {
        let data = try await callRaw(method, params, timeout: timeout)
        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch {
            throw DaemonError.protocolError("cannot decode \(method): \(error)")
        }
    }

    @discardableResult
    public func callRaw(_ method: String, _ params: [String: Any] = [:], timeout: TimeInterval = 30) async throws -> Data {
        let request = try JSONSerialization.data(withJSONObject: ["id": 1, "method": method, "params": params])
        let path = socketPath
        return try await Task.detached(priority: .userInitiated) {
            try Self.roundTrip(path: path, request: request, timeout: timeout)
        }.value
    }

    public func ping() async -> Bool {
        (try? await callRaw("ping", timeout: 2)) != nil
    }

    // MARK: - Socket I/O

    static func roundTrip(path: String, request: Data, timeout: TimeInterval) throws -> Data {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw DaemonError.protocolError("socket() failed") }
        defer { close(fd) }

        var tv = timeval(tv_sec: Int(timeout), tv_usec: Int32((timeout - Double(Int(timeout))) * 1_000_000))
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var one: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let maxLen = MemoryLayout.size(ofValue: addr.sun_path)
        guard path.utf8.count < maxLen else { throw DaemonError.protocolError("socket path too long") }
        withUnsafeMutableBytes(of: &addr.sun_path) { buffer in
            buffer.copyBytes(from: path.utf8)
            buffer[path.utf8.count] = 0
        }
        let connected = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { throw DaemonError.notRunning }

        var message = request
        message.append(0x0A)
        try message.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let n = write(fd, raw.baseAddress!.advanced(by: sent), raw.count - sent)
                guard n > 0 else { throw DaemonError.protocolError("write failed") }
                sent += n
            }
        }

        var reply = Data()
        var chunk = [UInt8](repeating: 0, count: 65536)
        while reply.last != 0x0A {
            let n = read(fd, &chunk, chunk.count)
            if n == 0 { break }
            guard n > 0 else { throw DaemonError.protocolError("read failed or timed out") }
            reply.append(chunk, count: n)
        }
        return try decodeReply(reply)
    }

    static func decodeReply(_ reply: Data) throws -> Data {
        guard let object = try? JSONSerialization.jsonObject(with: reply) as? [String: Any] else {
            throw DaemonError.protocolError("invalid reply")
        }
        if let error = object["error"] as? [String: Any] {
            throw DaemonError.remote(type: error["type"] as? String ?? "Error", message: error["message"] as? String ?? "")
        }
        let result = object["result"] ?? [String: Any]()
        return try JSONSerialization.data(withJSONObject: result, options: [.fragmentsAllowed])
    }
}
