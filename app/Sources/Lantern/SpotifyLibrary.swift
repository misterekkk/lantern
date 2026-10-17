import AppKit
import CryptoKit
import Foundation
import Network
import Security

/// The user's Spotify playlists and liked songs, fetched from Spotify's Web API with the user's own app key (Client
/// ID from developer.spotify.com), only while "Spotify" is on (Settings → Privacy, off by default). This is the one
/// network connection Lantern makes: to accounts.spotify.com (sign-in) and api.spotify.com (the library), at sign-in,
/// once a day and on "Sync Now". Searching is local (the daemon's copy, `music` API with source "spotify"); playing
/// goes through the Spotify app (`MusicPlayer`). Downloaded songs are never read: they are encrypted.
@MainActor
final class SpotifySync: ObservableObject {
    static let shared = SpotifySync()
    static let enabledKey = "spotifyLibrary"
    static let clientKey = "spotifyClientID"
    static let syncedKey = "spotifyLastSync"
    /// Register exactly this redirect URI in the Spotify app's settings (Spotify's dashboard wants a port; loopback
    /// over plain HTTP is allowed, `localhost` is not). `spotifyRedirectPort` changes the port if 8000 is taken.
    static var port: UInt16 {
        let saved = UserDefaults.standard.integer(forKey: "spotifyRedirectPort")
        return (1024...65535).contains(saved) ? UInt16(saved) : 8000
    }
    static var redirectURI: String { redirect(port) }
    private static let scopes = "playlist-read-private playlist-read-collaborative user-library-read"

    @Published private(set) var signedIn = Keychain.read(account: Keychain.tokenAccount) != nil
    @Published private(set) var busy = false
    @Published private(set) var message: String?

    var enabled: Bool { UserDefaults.standard.bool(forKey: Self.enabledKey) }
    /// The user's app key, kept in the keychain (an older build stored it in the defaults: moved over once).
    var clientID: String {
        if let old = UserDefaults.standard.string(forKey: Self.clientKey) {
            if !old.isEmpty { Keychain.save(old, account: Keychain.clientAccount) }
            UserDefaults.standard.removeObject(forKey: Self.clientKey)
        }
        return (Keychain.read(account: Keychain.clientAccount) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }
    @Published private(set) var hasClientID = false

    func setClientID(_ value: String) {
        let id = value.trimmingCharacters(in: .whitespacesAndNewlines)
        let previous = clientID
        if id.isEmpty { Keychain.delete(account: Keychain.clientAccount) } else { Keychain.save(id, account: Keychain.clientAccount) }
        hasClientID = !id.isEmpty
        if signedIn && id != previous { signOut() }  // tokens belong to the old key
    }
    private var accessToken: (value: String, expires: Date)?
    private var timer: Timer?
    private var login: LoopbackLogin?

    func start() {
        hasClientID = !clientID.isEmpty
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 3600, repeats: true) { _ in
            Task { @MainActor in SpotifySync.shared.syncIfDue() }
        }
        syncIfDue()
    }

    /// Once a day while it is on and signed in.
    func syncIfDue() {
        let last = UserDefaults.standard.double(forKey: Self.syncedKey)
        guard enabled, signedIn, Date().timeIntervalSince1970 - last > 24 * 3600 else { return }
        Task { await sync() }
    }

    // MARK: Sign-in (authorization code with PKCE: no client secret)

    func signIn() {
        guard !clientID.isEmpty else {
            message = String(localized: "Enter your Client ID first.")
            return
        }
        let verifier = Self.randomString(64)
        let challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
        let state = Self.randomString(16)
        let login = LoopbackLogin(state: state, port: Self.port) { [weak self] result in
            Task { @MainActor in self?.finishSignIn(result, verifier: verifier) }
        }
        guard let port = login.start() else {
            message = String(localized: "Port \(Int(Self.port)) on this Mac is in use by another app; close it and sign in again.")
            return
        }
        self.login = login
        var parts = URLComponents(string: "https://accounts.spotify.com/authorize")!
        parts.queryItems = [
            URLQueryItem(name: "client_id", value: clientID), URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "redirect_uri", value: Self.redirect(port)), URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "code_challenge", value: challenge), URLQueryItem(name: "scope", value: Self.scopes),
        ]
        NSWorkspace.shared.open(parts.url!)  // the user's browser: Lantern never sees the password
        message = String(localized: "Finish signing in in your browser.")
    }

    private static func redirect(_ port: UInt16) -> String { "http://127.0.0.1:\(port)/callback" }

    private func finishSignIn(_ result: Result<(code: String, port: UInt16), Error>, verifier: String) {
        login = nil
        switch result {
        case .failure:
            message = String(localized: "Signing in to Spotify was cancelled or failed.")
        case .success(let (code, port)):
            Task {
                do {
                    try await token(["grant_type": "authorization_code", "code": code,
                                     "redirect_uri": Self.redirect(port), "code_verifier": verifier])
                    signedIn = true
                    await sync()
                } catch {
                    message = String(localized: "Signing in to Spotify failed: \(error.localizedDescription)")
                }
            }
        }
    }

    func signOut() {
        Keychain.delete(account: Keychain.tokenAccount)
        accessToken = nil
        signedIn = false
        UserDefaults.standard.removeObject(forKey: Self.syncedKey)
        clear()
    }

    private func token(_ form: [String: String]) async throws {
        var request = URLRequest(url: URL(string: "https://accounts.spotify.com/api/token")!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var body = URLComponents()
        body.queryItems = (form.merging(["client_id": clientID]) { a, _ in a }).map { URLQueryItem(name: $0, value: $1) }
        request.httpBody = body.percentEncodedQuery?.data(using: .utf8)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let access = json["access_token"] as? String else {
            throw SpotifyError.http((response as? HTTPURLResponse)?.statusCode ?? 0)
        }
        accessToken = (access, Date().addingTimeInterval((json["expires_in"] as? Double ?? 3600) - 60))
        if let refresh = json["refresh_token"] as? String {  // Spotify may rotate it
            Keychain.save(refresh, account: Keychain.tokenAccount)
        }
    }

    private func bearer() async throws -> String {
        if let accessToken, accessToken.expires > Date() { return accessToken.value }
        guard let refresh = Keychain.read(account: Keychain.tokenAccount) else { throw SpotifyError.signedOut }
        try await token(["grant_type": "refresh_token", "refresh_token": refresh])
        return accessToken!.value
    }

    // MARK: Library

    func sync() async {
        guard enabled, signedIn, !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let library = try await fetchLibrary()
            let encoder = JSONEncoder()
            let songs = try JSONSerialization.jsonObject(with: encoder.encode(library.songs))
            let lists = try JSONSerialization.jsonObject(with: encoder.encode(library.playlists))
            _ = try await AppState.shared.client.callRaw("music", ["source": "spotify", "songs": songs,
                                                                   "playlists": lists], timeout: 300)
            UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: Self.syncedKey)
            message = String(localized: "Synced \(library.songs.count) songs and \(library.playlists.count) playlists.")
        } catch SpotifyError.signedOut {
            signedIn = false
            message = String(localized: "Sign in to Spotify again.")
        } catch {
            message = String(localized: "Spotify sync failed: \(error.localizedDescription)")
        }
    }

    /// The playlists the user follows (songs only for their own and collaborative ones: the Web API returns no
    /// items for others since February 2026) and the liked songs, as one more playlist.
    private func fetchLibrary() async throws -> (songs: [MusicSong], playlists: [MusicPlaylist]) {
        var songs: [String: MusicSong] = [:]
        var playlists: [MusicPlaylist] = []
        func add(_ track: [String: Any]?) -> String? {
            guard let track, (track["type"] as? String ?? "track") == "track", let id = track["id"] as? String,
                  let name = track["name"] as? String else { return nil }  // local files and episodes have no id
            let key = "sp-" + id
            if songs[key] == nil {
                let artists = (track["artists"] as? [[String: Any]] ?? []).compactMap { $0["name"] as? String }
                let album = (track["album"] as? [String: Any])?["name"] as? String ?? ""
                songs[key] = MusicSong(id: key, title: name, artist: artists.joined(separator: ", "), album: album,
                                       track: track["track_number"] as? Int ?? 0, year: 0, downloaded: false)
            }
            return key
        }
        let liked = try await pages("https://api.spotify.com/v1/me/tracks?limit=50").compactMap {
            add($0["track"] as? [String: Any])
        }
        if !liked.isEmpty {
            playlists.append(MusicPlaylist(id: "sp-liked", name: String(localized: "Liked Songs"), songs: liked))
        }
        for list in try await pages("https://api.spotify.com/v1/me/playlists?limit=50") {
            guard let id = list["id"] as? String, let name = list["name"] as? String else { continue }
            var items: [String] = []
            if let href = (list["items"] as? [String: Any] ?? list["tracks"] as? [String: Any])?["href"] as? String {
                // Others' playlists answer without items (or with an error): their name is still searchable.
                let rows = (try? await pages(href + (href.contains("?") ? "&" : "?") + "limit=100")) ?? []
                items = rows.compactMap { add(($0["item"] ?? $0["track"]) as? [String: Any]) }
            }
            playlists.append(MusicPlaylist(id: "sp-" + id, name: name, songs: items))
        }
        return (Array(songs.values), playlists)
    }

    /// Every page of a paged Web API list.
    private func pages(_ first: String) async throws -> [[String: Any]] {
        var next: String? = first
        var all: [[String: Any]] = []
        var retried = false
        while let url = next.flatMap(URL.init(string:)) {
            var request = URLRequest(url: url)
            request.setValue("Bearer \(try await bearer())", forHTTPHeaderField: "Authorization")
            let (data, response) = try await URLSession.shared.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if status == 401 && !retried {  // expired early: refresh once more
                accessToken = nil
                retried = true
                continue
            }
            guard status == 200, let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw SpotifyError.http(status)
            }
            all += json["items"] as? [[String: Any]] ?? []
            next = json["next"] as? String
        }
        return all
    }

    /// Switched off or signed out: the daemon forgets the Spotify list.
    func clear() {
        Task { _ = try? await AppState.shared.client.callRaw("music", ["source": "spotify", "clear": true]) }
    }

    private static func randomString(_ length: Int) -> String {
        let chars = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        return String((0..<length).map { _ in chars[Int.random(in: 0..<chars.count)] })
    }
}

enum SpotifyError: LocalizedError {
    case http(Int), signedOut, cancelled

    var errorDescription: String? {
        switch self {
        case .http(let status): String(localized: "Spotify answered with HTTP \(status).")
        case .signedOut: String(localized: "Not signed in to Spotify.")
        case .cancelled: String(localized: "Cancelled.")
        }
    }
}

/// Waits on 127.0.0.1 (never other interfaces) for the one redirect back from Spotify's sign-in page, then stops.
private final class LoopbackLogin: @unchecked Sendable {
    private let state: String
    private let port: UInt16
    private let done: (Result<(code: String, port: UInt16), Error>) -> Void
    private var listener: NWListener?
    private var finished = false

    init(state: String, port: UInt16, done: @escaping (Result<(code: String, port: UInt16), Error>) -> Void) {
        self.state = state
        self.port = port
        self.done = done
    }

    /// nil when the port cannot be used (another app listens on it).
    func start() -> UInt16? {
        let params = NWParameters.tcp
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
        guard let listener = try? NWListener(using: params) else { return nil }
        self.listener = listener
        let ready = DispatchSemaphore(value: 0)
        var started = false
        listener.stateUpdateHandler = { state in
            if case .ready = state { started = true; ready.signal() }
            if case .failed = state { ready.signal() }
        }
        listener.newConnectionHandler = { [weak self] connection in self?.handle(connection) }
        listener.start(queue: .global(qos: .userInitiated))
        _ = ready.wait(timeout: .now() + 3)
        guard started else {
            listener.cancel()
            return nil
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 300) { [weak self] in  // give up after 5 minutes
            self?.finish(.failure(SpotifyError.cancelled))
        }
        return listener.port?.rawValue
    }

    private func handle(_ connection: NWConnection) {
        connection.start(queue: .global(qos: .userInitiated))
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16384) { [weak self] data, _, _, _ in
            guard let self else { return }
            let request = data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            let target = request.split(separator: " ").dropFirst().first.map(String.init) ?? ""
            let items = URLComponents(string: "http://127.0.0.1" + target)?.queryItems ?? []
            let value = { (name: String) in items.first { $0.name == name }?.value }
            let ok = target.hasPrefix("/callback") && value("state") == self.state && value("code") != nil
            let page = ok ? String(localized: "Lantern is connected to Spotify. You can close this tab.")
                : String(localized: "Signing in to Spotify did not finish. You can close this tab.")
            let body = "<!doctype html><meta charset=utf-8><title>Lantern</title>"
                + "<body style=\"font:16px -apple-system;margin:3em\">\(page)</body>"
            let reply = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n"
                + "Content-Length: \(body.utf8.count)\r\n\r\n\(body)"
            connection.send(content: reply.data(using: .utf8), completion: .contentProcessed { _ in connection.cancel() })
            guard target.hasPrefix("/callback") else { return }  // e.g. the browser asking for a favicon
            if ok, let code = value("code"), let port = self.listener?.port?.rawValue {
                self.finish(.success((code, port)))
            } else {
                self.finish(.failure(SpotifyError.cancelled))
            }
        }
    }

    private func finish(_ result: Result<(code: String, port: UInt16), Error>) {
        guard !finished else { return }
        finished = true
        listener?.cancel()
        done(result)
    }
}

/// The refresh token and the Client ID, in the login keychain (only this app's items).
private enum Keychain {
    static let tokenAccount = "refresh_token"
    static let clientAccount = "client_id"

    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "com.lantern.app.spotify",
         kSecAttrAccount as String: account]
    }

    static func save(_ value: String, account: String) {
        SecItemDelete(query(account) as CFDictionary)
        var item = query(account)
        item[kSecValueData as String] = Data(value.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(item as CFDictionary, nil)
    }

    static func read(account: String) -> String? {
        var item = query(account)
        item[kSecReturnData as String] = true
        item[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(item as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete(account: String) { SecItemDelete(query(account) as CFDictionary) }
}

private extension Data {
    var base64URL: String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
