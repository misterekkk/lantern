import AppKit
import iTunesLibrary
import LanternCore

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

/// Plays songs and playlists in the Music or Spotify app through AppleScript (osascript; macOS asks once whether
/// Lantern may control the app). Only validated IDs go into the scripts. A closed app is started first and the command
/// waits until it answers (a cold start used to swallow the command: the app opened, nothing played).
enum MusicPlayer {
    enum Command { case playSong, revealSong, revealPlaylist, playPlaylist }

    /// Waits up to ~8 s for the app to answer, then runs `body` inside `tell application`.
    private static func script(app: String, ready: String, body: String) -> String {
        """
        tell application "\(app)"
            if it is not running then launch
            repeat 40 times
                try
                    \(ready)
                    exit repeat
                end try
                delay 0.2
            end repeat
            \(body)
        end tell
        """
    }

    private static func untilPlaying(_ play: String) -> String {
        """
        repeat 4 times
            \(play)
            repeat 12 times
                delay 0.25
                if player state is playing then return
            end repeat
            delay 1.5
        end repeat
        """
    }

    static func run(_ command: Command, song: String? = nil, playlist: String? = nil) {
        let valid = { (id: String?) in id.map { $0.range(of: "^[0-9A-F]{16}$", options: .regularExpression) != nil } }
        guard valid(song) ?? true, valid(playlist) ?? true else { return }
        // `playlist`, not `user playlist`: playlists added from the Apple Music catalogue are not user playlists.
        // A song plays from the library: Music (macOS 26) ignores the playlist given with a single track (tested:
        // `play track i of p` and `play p` then a jump both continue with the library), while `play p` keeps the
        // playlist. So the playlist row is the way to listen to a playlist.
        let container = playlist.map { "set p to first playlist whose persistent ID is \"\($0)\"" }
            ?? "set p to library playlist 1"
        // Songs added from the Apple Music catalogue to a playlist only are not in the library playlist (on the user's
        // Mac: 74 of 643), so the song is looked up in the library first, then in its playlist.
        let library = song.map { id in
            let fallback = playlist.map { """
                on error
                    set p to first playlist whose persistent ID is "\($0)"
                    set t to (first track of p whose persistent ID is "\(id)")
                """ } ?? ""
            return """
                try
                    set t to (first track of library playlist 1 whose persistent ID is "\(id)")
                \(fallback)
                end try
                """
        } ?? ""
        let track = song.map { _ in "t" }
        let body: String
        switch command {
        case .playSong:
            // Measured on a cold start: the first `play` only loads the song and leaves it paused, a second one plays
            // it; replaying too fast keeps it stopped. So: play, wait up to 3 s, pause 1.5 s, again (4 times).
            guard let track else { return }
            body = "\(library)\n" + Self.untilPlaying("play \(track)")
        case .revealSong:
            guard let track else { return }
            body = "\(library)\nreveal \(track)\nactivate"
        case .revealPlaylist:  // best effort: Music (macOS 26) may keep its current view; it comes to the front anyway
            guard playlist != nil else { return }
            body = "\(container)\nreveal p\nactivate"
        case .playPlaylist:
            guard playlist != nil else { return }
            body = "\(container)\n" + Self.untilPlaying("play p")
        }
        osascript(script(app: "Music", ready: "get name of library playlist 1", body: body), app: "Music")
    }

    /// Spotify rows: ids are "sp-" + a Spotify ID; the Spotify app takes spotify: URIs (its AppleScript `play track …
    /// in context …`; opening a URI shows it without playing).
    static func spotify(_ command: Command, song: String? = nil, playlist: String? = nil) {
        let valid = { (id: String?) in
            id.map { $0.range(of: "^sp-[A-Za-z0-9]{1,40}$", options: .regularExpression) != nil }
        }
        guard valid(song) ?? true, valid(playlist) ?? true else { return }
        let track = song.map { "spotify:track:" + $0.dropFirst(3) }
        // Liked Songs have no playlist URI: Spotify plays them as spotify:user:<id>:collection (spotify:collection:tracks
        // only shows them; tested). The id is read from Spotify's own local prefs; without it the view opens instead.
        let liked = SpotifyAccount.collection
        let list = playlist.flatMap { $0 == "sp-liked" ? liked : "spotify:playlist:" + $0.dropFirst(3) }
        let ready = "get player state"
        switch command {
        case .playSong:
            guard let track else { return }
            let context = list.map { " in context \"\($0)\"" } ?? ""
            osascript(script(app: "Spotify", ready: ready, body: "play track \"\(track)\"\(context)"), app: "Spotify")
        case .revealSong:
            if let track, let url = URL(string: track) { NSWorkspace.shared.open(url) }
        case .revealPlaylist:
            let view = playlist == "sp-liked" ? "spotify:collection:tracks" : list
            if let url = URL(string: view ?? "spotify:collection:tracks") { NSWorkspace.shared.open(url) }
        case .playPlaylist:
            guard let list else {  // Liked Songs without a known account: show them
                if playlist == "sp-liked", let url = URL(string: "spotify:collection:tracks") { NSWorkspace.shared.open(url) }
                return
            }
            osascript(script(app: "Spotify", ready: ready, body: "play track \"\(list)\""), app: "Spotify")
        }
    }

    /// Runs the script in the background; a refused permission (-1743) shows where to allow it, other errors go to
    /// the app's log (Console, "Lantern").
    /// The command still running, if any: a new one replaces it, so a late `play` never interrupts the new choice.
    nonisolated(unsafe) private static var running: Process?

    private static func osascript(_ source: String, app: String) {
        if let running, running.isRunning { running.terminate() }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", source]
        let errors = Pipe()
        process.standardError = errors
        process.terminationHandler = { proc in
            guard proc.terminationStatus != 0, proc.terminationReason == .exit else { return }  // replaced: not an error
            let message = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            NSLog("Lantern: %@ script failed: %@", app, message)
            AppLog.write("\(app) script failed: \(message.trimmingCharacters(in: .whitespacesAndNewlines))")
            if message.contains("-1743") {
                Task { @MainActor in MusicPlayer.askForAutomation(app) }
            }
        }
        try? process.run()
        running = process
    }

    @MainActor private static func askForAutomation(_ app: String) {
        let alert = NSAlert()
        alert.messageText = String(localized: "Lantern may not control \(app)")
        alert.informativeText = String(localized: "Allow it in System Settings → Privacy & Security → Automation → Lantern → \(app).")
        alert.addButton(withTitle: String(localized: "Open Privacy Settings…"))
        alert.addButton(withTitle: String(localized: "Cancel"))
        if alert.runModal() == .alertFirstButtonReturn,
           let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation") {
            NSWorkspace.shared.open(url)
        }
    }
}

/// logs/app.log in Lantern's data folder: errors of the app itself (scripts), next to the daemon's log. No content of
/// files or searches is written.
enum AppLog {
    static func write(_ line: String) {
        let folder = URL(fileURLWithPath: DaemonClient.dataDirectory).appendingPathComponent("logs")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent("app.log")
        let stamp = ISO8601DateFormatter().string(from: Date())
        let data = Data("\(stamp) \(line)\n".utf8)
        if let handle = try? FileHandle(forWritingTo: file) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            try? data.write(to: file)
        }
    }
}

/// The signed-in Spotify account, from the Spotify app's local prefs (`autologin.canonical_username`), read only and
/// only to play Liked Songs; nothing is sent anywhere.
enum SpotifyAccount {
    static var collection: String? {
        let prefs = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Spotify/prefs")
        guard let text = try? String(contentsOf: prefs, encoding: .utf8) else { return nil }
        for line in text.split(separator: "\n") where line.hasPrefix("autologin.canonical_username=") {
            let user = line.dropFirst("autologin.canonical_username=".count).trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            guard user.range(of: "^[A-Za-z0-9._-]{1,64}$", options: .regularExpression) != nil else { return nil }
            return "spotify:user:\(user):collection"
        }
        return nil
    }
}
