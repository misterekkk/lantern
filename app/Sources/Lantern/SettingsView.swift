import AppKit
import LanternCore
import SwiftUI

/// Own settings window: SwiftUI's Settings scene does not reliably come to the front in a menu-bar-only app.
@MainActor
final class SettingsWindowController {
    static let shared = SettingsWindowController()
    private var window: NSWindow?

    func show() {
        if window == nil {
            let host = NSHostingController(rootView: SettingsView().environmentObject(AppState.shared))
            let w = NSWindow(contentViewController: host)
            w.title = "Ustawienia Lantern"
            w.styleMask = [.titled, .closable, .miniaturizable]
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

/// Settings window (⌘,): general, folders and exclusions, privacy, about.
struct SettingsView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        TabView {
            GeneralTab().tabItem { Label("Ogólne", systemImage: "gearshape") }
            FoldersTab().tabItem { Label("Foldery", systemImage: "folder") }
            PrivacyTab().tabItem { Label("Prywatność", systemImage: "hand.raised") }
            AboutTab().tabItem { Label("Informacje", systemImage: "info.circle") }
        }
        .frame(width: 560, height: 420)
        .task { await state.loadSettings() }
    }
}

private struct GeneralTab: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Form {
            Section {
                Toggle("Uruchamiaj Lantern przy logowaniu", isOn: Binding(
                    get: { state.launchAtLogin }, set: { state.launchAtLogin = $0 }))
                LabeledContent("Skrót wyszukiwania") { Text("⌥ Space").font(.system(.body, design: .rounded)) }
            }
            if let s = state.settings {
                Section("Indeksowanie") {
                    Picker("Nowe i zmienione pliki", selection: Binding(
                        get: { s.indexMode }, set: { state.setConfig("index_mode", $0) })) {
                        Text("Pytaj przed indeksowaniem").tag("ask")
                        Text("Indeksuj automatycznie").tag("auto")
                    }
                    Toggle("Transkrypcja nagrań i filmów (Whisper)", isOn: Binding(
                        get: { s.transcribe }, set: { state.setConfig("transcribe", $0 ? "true" : "false") }))
                    Toggle("Wstrzymuj na zasilaniu z baterii", isOn: Binding(
                        get: { s.pauseOnBattery }, set: { state.setConfig("pause_on_battery", $0 ? "true" : "false") }))
                    Toggle("Wstrzymuj przy wysokiej temperaturze", isOn: Binding(
                        get: { s.pauseOnThermal }, set: { state.setConfig("pause_on_thermal", $0 ? "true" : "false") }))
                }
            } else {
                Text(state.connectionError ?? "Łączenie z demonem…").foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

private struct FoldersTab: View {
    @EnvironmentObject var state: AppState
    @State private var newPattern = ""

    var body: some View {
        Form {
            Section("Przeszukiwane foldery") {
                ForEach(state.status?.roots ?? []) { root in
                    HStack {
                        Image(systemName: root.available ? "folder.fill" : "externaldrive.badge.xmark")
                            .foregroundStyle(root.available ? Color.accentColor : .secondary)
                        Text(Format.shortPath(root.path)).lineLimit(1).truncationMode(.middle)
                        if !root.available { Text("niedostępny").font(.caption).foregroundStyle(.secondary) }
                        Spacer()
                        Button(role: .destructive) { state.removeFolder(root.path) } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless)
                            .help("Usuń folder i jego dane z indeksu")
                    }
                }
                Button("Dodaj folder…") { state.chooseFolder() }
            }
            Section {
                ForEach(state.settings?.exclude ?? [], id: \.self) { pattern in
                    HStack {
                        Text(pattern).font(.system(.body, design: .monospaced))
                        Spacer()
                        Button { state.removeExclude(pattern) } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless)
                    }
                }
                HStack {
                    TextField("np. ~/Downloads/archiwum albo *.csv", text: $newPattern)
                    Button("Dodaj") {
                        state.addExclude(newPattern)
                        newPattern = ""
                    }
                    .disabled(newPattern.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            } header: {
                Text("Wykluczenia")
            } footer: {
                Text("Zawsze pomijane: ~/Library (poza iCloud Drive), pliki z sekretami, .git, node_modules, pliki iCloud nieobecne na dysku.")
                    .font(.caption)
            }
        }
        .formStyle(.grouped)
    }
}

private struct PrivacyTab: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Form {
            Section {
                Label("Wszystko działa lokalnie i offline. Lantern nie wysyła żadnych danych.", systemImage: "lock.shield")
                Label("Indeksowane pliki są tylko czytane, nigdy zmieniane.", systemImage: "doc.text.magnifyingglass")
            }
            Section("Dane") {
                Button("Wyczyść historię wyszukiwań") { state.clearHistory() }
                Button("Zagęść indeks (fizycznie usuń skasowane dane)") { state.compact() }
                Button("Pokaż folder danych w Finderze") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: DaemonClient.dataDirectory)])
                }
            }
            Section("Dostęp do plików") {
                Text("Biurko, Dokumenty, Pobrane i iCloud Drive wymagają zgody macOS. Foldery bez dostępu są pomijane, a ich wcześniej zaindeksowane pliki zostają.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Button("Otwórz ustawienia prywatności") {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!)
                }
            }
        }
        .formStyle(.grouped)
    }
}

private struct AboutTab: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "sparkle.magnifyingglass")
                .font(.system(size: 48, weight: .semibold))
                .foregroundStyle(.tint)
            Text("Lantern").font(.title2.weight(.semibold))
            Text("Lokalne wyszukiwanie po znaczeniu w plikach, kodzie i nagraniach.")
                .foregroundStyle(.secondary)
            Text("Model: EmbeddingGemma 2 (MLX, 8-bit) · Whisper large-v3-turbo")
                .font(.caption)
                .foregroundStyle(.secondary)
            if let s = state.status {
                Text("Demon: pid \(s.pid), \(s.memoryMb) MB · \(s.indexedFiles) plików, \(s.chunks) fragmentów")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text("Wersja \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev")")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
