import Foundation
import iTunesLibrary

/// The user's Apple Music library, read on the Mac through Apple's iTunesLibrary framework: songs (title, artist,
/// album, track number) and the user's playlists with the songs in them. Read-only; it does not start the Music app.
/// macOS asks once for access to "Media & Apple Music" (NSAppleMusicUsageDescription). The daemon stores and searches
/// the list (`api_music`); playing goes through the Music app (`MusicPlayer`).
struct MusicSong: Encodable, Equatable {
    let id: String  // persistent ID as 16 hex digits, the form Music's AppleScript uses
    let title: String
    let artist: String
    let album: String
    let track: Int
    let year: Int
    let downloaded: Bool
}

struct MusicPlaylist: Encodable, Equatable {
    let id: String
    let name: String
    let songs: [String]  // song ids in playlist order
}

enum MusicLibrary {
    static func hex(_ id: NSNumber) -> String { String(format: "%016llX", id.uint64Value) }

    /// nil when access was refused or the library cannot be read.
    static func read() -> (songs: [MusicSong], playlists: [MusicPlaylist])? {
        guard let library = try? ITLibrary(apiVersion: "1.1") else { return nil }
        let songs = library.allMediaItems
            .filter { $0.mediaKind == .kindSong && !$0.title.isEmpty }
            .map { item in
                MusicSong(id: hex(item.persistentID), title: item.title, artist: item.artist?.name ?? "",
                          album: item.album.title ?? "", track: item.trackNumber, year: item.year,
                          downloaded: item.locationType == .file && item.location != nil)
            }
        let known = Set(songs.map(\.id))
        let playlists = library.allPlaylists
            .filter { $0.distinguishedKind == .kindNone && !$0.isPrimary && $0.isVisible && $0.kind != .folder }
            .map { list in
                MusicPlaylist(id: hex(list.persistentID), name: list.name,
                              songs: list.items.map { hex($0.persistentID) }.filter(known.contains))
            }
            .filter { !$0.songs.isEmpty }
        return (songs, playlists)
    }
}

/// Keeps the daemon's copy of the library current while "Apple Music" is on (Settings → Privacy, off by default):
/// at launch, when the daemon (re)starts and when the library file changes (its date is checked once a minute).
@MainActor
final class MusicSync {
    static let shared = MusicSync()
    static let key = "appleMusicLibrary"

    var enabled: Bool { UserDefaults.standard.bool(forKey: Self.key) }
    private var timer: Timer?
    private var lastStamp: Date?
    private var running = false
    private static let libraryFile = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("Music/Music/Music Library.musiclibrary/Library.musicdb")

    func start() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { _ in
            Task { @MainActor in MusicSync.shared.syncIfChanged() }
        }
        syncIfChanged(force: true)
    }

    /// The library file's date; nil when the library lives elsewhere (then it is read again every 10 minutes).
    private var stamp: Date? {
        (try? Self.libraryFile.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
    }

    func syncIfChanged(force: Bool = false) {
        guard enabled, !running else { return }
        let now = stamp ?? Date(timeIntervalSince1970: (Date().timeIntervalSince1970 / 600).rounded(.down) * 600)
        guard force || now != lastStamp else { return }
        running = true
        Task.detached(priority: .utility) {
            let library = MusicLibrary.read()
            await MainActor.run { MusicSync.shared.send(library, stamp: now) }
        }
    }

    private func send(_ library: (songs: [MusicSong], playlists: [MusicPlaylist])?, stamp: Date) {
        guard let library, enabled else {
            running = false
            return
        }
        let encoder = JSONEncoder()
        guard let songs = (try? JSONSerialization.jsonObject(with: encoder.encode(library.songs))) as? [Any],
              let lists = (try? JSONSerialization.jsonObject(with: encoder.encode(library.playlists))) as? [Any]
        else {
            running = false
            return
        }
        Task {
            defer { running = false }
            if (try? await AppState.shared.client.callRaw("music", ["songs": songs, "playlists": lists],
                                                          timeout: 120)) != nil {
                lastStamp = stamp
            }
        }
    }

    /// Switched off: the daemon forgets the list.
    func clear() {
        lastStamp = nil
        Task { _ = try? await AppState.shared.client.callRaw("music", ["clear": true]) }
    }
}

/// Plays songs and playlists in the Music app through AppleScript (osascript; macOS asks once whether Lantern may
/// control Music). Only persistent IDs (16 hex digits) go into the script.
enum MusicPlayer {
    enum Command { case playSong, revealSong, revealPlaylist, playPlaylist }

    static func run(_ command: Command, song: String? = nil, playlist: String? = nil) {
        let valid = { (id: String?) in id.map { $0.range(of: "^[0-9A-F]{16}$", options: .regularExpression) != nil } }
        guard valid(song) ?? true, valid(playlist) ?? true else { return }
        let container = playlist.map { "set p to first user playlist whose persistent ID is \"\($0)\"" }
            ?? "set p to library playlist 1"
        let track = song.map { "(first track of p whose persistent ID is \"\($0)\")" }
        let body: String
        switch command {
        case .playSong:  // in the playlist's (or the library's) context: the next songs follow
            guard let track else { return }
            body = "\(container)\nplay \(track)"
        case .revealSong:
            guard let track else { return }
            body = "\(container)\nreveal \(track)\nactivate"
        case .revealPlaylist:
            guard playlist != nil else { return }
            body = "\(container)\nreveal p\nactivate"
        case .playPlaylist:
            guard playlist != nil else { return }
            body = "\(container)\nplay p"
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", "tell application \"Music\"\n\(body)\nend tell"]
        try? process.run()
    }
}
