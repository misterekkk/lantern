import AppKit
import ApplicationServices
import LanternCore

/// Menu commands of the frontmost app and open windows as results, like Spotlight's (off by default; needs the
/// Accessibility permission, asked for only when switched on in Settings → Privacy).
/// Read when the panel opens: the panel does not activate Lantern, so the frontmost app is still the user's.
@MainActor
final class MenuCommands {
    static let shared = MenuCommands()
    static let key = "menuCommands"

    struct Command {
        let title: String
        let path: [String]  // menu titles above it: ["File"], ["View", "Zoom"]
        let element: AXUIElement
        let shortcut: String?
    }

    struct Window {
        let title: String
        let app: NSRunningApplication
        let element: AXUIElement
    }

    private(set) var app: NSRunningApplication?
    private var commands: [Command] = []
    private var windows: [Window] = []

    var enabled: Bool { UserDefaults.standard.bool(forKey: Self.key) }
    static var trusted: Bool { AXIsProcessTrusted() }

    static func requestAccess() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
    }

    static let accessibilitySettings =
        URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!

    func capture() {
        commands = []
        windows = []
        app = nil
        guard enabled, Self.trusted else { return }
        if let front = NSWorkspace.shared.frontmostApplication, front.bundleIdentifier != Bundle.main.bundleIdentifier {
            app = front
            let root = AXUIElementCreateApplication(front.processIdentifier)
            AXUIElementSetMessagingTimeout(root, 0.4)
            if let bar: AXUIElement = Self.attribute(root, kAXMenuBarAttribute) {
                for top in Self.children(bar).dropFirst() {  // the Apple menu is not the app's
                    walk(top, path: [Self.string(top, kAXTitleAttribute) ?? ""], depth: 0)
                }
            }
        }
        for running in NSWorkspace.shared.runningApplications where running.activationPolicy == .regular
            && running.bundleIdentifier != Bundle.main.bundleIdentifier {
            let root = AXUIElementCreateApplication(running.processIdentifier)
            AXUIElementSetMessagingTimeout(root, 0.15)
            let found: [AXUIElement] = Self.attribute(root, kAXWindowsAttribute) ?? []
            for window in found {
                if let title = Self.string(window, kAXTitleAttribute), !title.isEmpty {
                    windows.append(Window(title: title, app: running, element: window))
                }
            }
        }
    }

    private func walk(_ element: AXUIElement, path: [String], depth: Int) {
        guard depth < 5, commands.count < 2000 else { return }
        for child in Self.children(element) {
            if Self.string(child, kAXRoleAttribute) == kAXMenuRole {
                walk(child, path: path, depth: depth + 1)
                continue
            }
            let title = Self.string(child, kAXTitleAttribute) ?? ""
            if !Self.children(child).isEmpty {
                walk(child, path: path + [title], depth: depth + 1)  // a submenu
            } else if !title.isEmpty, (Self.attribute(child, kAXEnabledAttribute) as Bool?) ?? true {
                commands.append(Command(title: title, path: path, element: child, shortcut: Self.shortcut(child)))
            }
        }
    }

    /// Every word of the query starts a word of the title (folded: case, accents, ł); then the menu path counts.
    private static func matches(_ query: String, _ title: String, _ context: [String]) -> Double? {
        let words = { (text: String) in TextFold.fold(text).split(whereSeparator: { !$0.isLetter && !$0.isNumber }) }
        let q = words(query)
        guard !q.isEmpty, q.joined().count >= 2 else { return nil }
        let inTitle = words(title)
        let inContext = context.flatMap(words)
        guard q.allSatisfy({ t in (inTitle + inContext).contains { $0.hasPrefix(t) } }) else { return nil }
        let titleHits = q.filter { t in inTitle.contains { $0.hasPrefix(t) } }.count
        return Double(titleHits) / Double(q.count) + (inTitle.first?.hasPrefix(q[0]) == true ? 0.5 : 0)
    }

    func sections(for query: String) -> [(section: ResultSection, score: Double)] {
        var out: [(section: ResultSection, score: Double)] = []
        let appPath = app?.bundleURL?.path
        let appName = app?.localizedName ?? ""

        var menuHits: [(index: Int, command: Command, score: Double)] = []
        for (i, command) in commands.enumerated() {
            if let score = Self.matches(query, command.title, command.path), score >= 1 {  // all words in the title
                menuHits.append((i, command, score))
            }
        }
        menuHits.sort { $0.score != $1.score ? $0.score > $1.score : $0.command.title.count < $1.command.title.count }
        if !menuHits.isEmpty {
            var rows: [SearchResult] = []
            for hit in menuHits.prefix(4) {
                let location = ([appName] + hit.command.path).filter { !$0.isEmpty }.joined(separator: " › ")
                let detail = [location, hit.command.shortcut].compactMap { $0 }.joined(separator: "  ")
                var row = SearchResult(path: "menu:\(hit.index)", chunkId: 0, symbol: hit.command.title,
                                       preview: detail, kind: "menu")
                row.iconPath = appPath
                rows.append(row)
            }
            out.append((ResultSection(kind: .menu, results: rows), 0.88))
        }

        var windowHits: [(index: Int, window: Window, score: Double)] = []
        for (i, window) in windows.enumerated() {
            if let score = Self.matches(query, window.title, [window.app.localizedName ?? ""]) {
                windowHits.append((i, window, score))
            }
        }
        windowHits.sort { $0.score > $1.score }
        if !windowHits.isEmpty {
            var rows: [SearchResult] = []
            for hit in windowHits.prefix(4) {
                var row = SearchResult(path: "window:\(hit.index)", chunkId: 0, symbol: hit.window.title,
                                       preview: hit.window.app.localizedName ?? "", kind: "window")
                row.iconPath = hit.window.app.bundleURL?.path
                rows.append(row)
            }
            out.append((ResultSection(kind: .windows, results: rows), 0.8))
        }
        return out
    }

    /// ↩: press the menu item, or bring the window to the front.
    func perform(_ path: String) {
        if path.hasPrefix("menu:"), let i = Int(path.dropFirst(5)), commands.indices.contains(i) {
            let item = commands[i].element
            app?.activate()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) { AXUIElementPerformAction(item, kAXPressAction as CFString) }
        } else if path.hasPrefix("window:"), let i = Int(path.dropFirst(7)), windows.indices.contains(i) {
            let window = windows[i]
            window.app.activate()
            AXUIElementPerformAction(window.element, kAXRaiseAction as CFString)
            AXUIElementSetAttributeValue(window.element, kAXMainAttribute as CFString, kCFBooleanTrue)
        }
    }

    /// Presses the first menu item of `app` whose title starts with one of `titles` ("New Window", "Nowe okno"),
    /// searching the menus two levels deep. Needs the Accessibility permission; false when nothing was pressed.
    @discardableResult
    static func press(_ titles: [String], in app: NSRunningApplication) -> Bool {
        guard trusted else { return false }
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 0.5)
        guard let bar: AXUIElement = attribute(root, kAXMenuBarAttribute) else { return false }
        let wanted = titles.map { $0.lowercased() }
        func find(_ element: AXUIElement, depth: Int) -> AXUIElement? {
            for child in children(element) {
                if let title = string(child, kAXTitleAttribute)?.lowercased(),
                   wanted.contains(where: { title.hasPrefix($0) }),
                   (attribute(child, kAXEnabledAttribute) as Bool?) ?? true {
                    return child
                }
                if depth < 3, let hit = find(child, depth: depth + 1) { return hit }
            }
            return nil
        }
        for top in children(bar).dropFirst() {
            if let item = find(top, depth: 0) {
                return AXUIElementPerformAction(item, kAXPressAction as CFString) == .success
            }
        }
        return false
    }

    // MARK: Accessibility helpers

    private static func attribute<T>(_ element: AXUIElement, _ name: String) -> T? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value as? T
    }

    private static func string(_ element: AXUIElement, _ name: String) -> String? { attribute(element, name) }

    private static func children(_ element: AXUIElement) -> [AXUIElement] {
        attribute(element, kAXChildrenAttribute) ?? []
    }

    private static func shortcut(_ element: AXUIElement) -> String? {
        guard let key: String = attribute(element, kAXMenuItemCmdCharAttribute), !key.isEmpty else { return nil }
        let mods: Int = attribute(element, kAXMenuItemCmdModifiersAttribute) ?? 0
        var text = ""
        if mods & 4 != 0 { text += "⌃" }
        if mods & 2 != 0 { text += "⌥" }
        if mods & 1 != 0 { text += "⇧" }
        if mods & 8 == 0 { text += "⌘" }
        return text + key.uppercased()
    }
}
