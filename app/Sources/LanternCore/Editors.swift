import Foundation

/// How to open a file at a line in the user's default app for that file type (the app macOS would open it with).
/// Editors without a known line mechanism get a plain open: Lantern never switches to a different app.
public enum LineJump: Equatable {
    case url(URL)
    case command(executable: String, arguments: [String], environment: [String: String])
    case plain

    /// - appURL: the default application bundle; executable: its main executable (JetBrains launchers).
    public static func make(bundleId: String, appURL: URL, executable: URL?, file: String, line: Int) -> LineJump {
        switch bundleId {
        case "com.microsoft.VSCode", "com.microsoft.VSCodeInsiders", "com.vscodium":
            return editorURL(scheme: bundleId == "com.vscodium" ? "vscodium" : "vscode", file: file, line: line)
        case "com.todesktop.230313mzl4w4u92":  // Cursor (VS Code fork, same URL format)
            return editorURL(scheme: "cursor", file: file, line: line)
        case "com.apple.dt.Xcode":
            // xed needs Xcode as the developer directory; xcode-select often points at the Command Line Tools.
            return .command(
                executable: "/usr/bin/xed", arguments: ["-l", String(line), file],
                environment: ["DEVELOPER_DIR": appURL.appendingPathComponent("Contents/Developer").path])
        default:
            if bundleId.hasPrefix("com.jetbrains.") || bundleId == "com.google.android.studio", let executable {
                return .command(executable: executable.path, arguments: ["--line", String(line), file], environment: [:])
            }
            return .plain
        }
    }

    private static func editorURL(scheme: String, file: String, line: Int) -> LineJump {
        var c = URLComponents()
        c.scheme = scheme
        c.host = "file"
        c.path = "\(file):\(line)"
        return c.url.map(LineJump.url) ?? .plain
    }
}
