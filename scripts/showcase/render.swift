// Renders an animated SVG/HTML page frame by frame with WebKit (offline): every CSS animation is paused and set to the
// frame's time, then the view is snapshotted. Usage: swift render.swift <page> <out-dir> <width> <height> <seconds> <fps>
import AppKit
import WebKit

let args = CommandLine.arguments
let page = URL(fileURLWithPath: args[1]), out = URL(fileURLWithPath: args[2])
let width = Double(args[3])!, height = Double(args[4])!, seconds = Double(args[5])!, fps = Double(args[6])!
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

final class Renderer: NSObject, WKNavigationDelegate {
    let view: WKWebView
    var frame = 0
    let total: Int
    init(total: Int) {
        let config = WKWebViewConfiguration()
        view = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: height), configuration: config)
        self.total = total
        super.init()
        view.navigationDelegate = self
        view.setValue(false, forKey: "drawsBackground")
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.next() }
    }
    func next() {
        guard frame < total else { exit(0) }
        let ms = Double(frame) * 1000 / fps
        let js = "document.getAnimations().forEach(a => { a.pause(); a.currentTime = \(ms); }); true"
        view.evaluateJavaScript(js) { _, error in
            if let error { FileHandle.standardError.write("\(error)\n".data(using: .utf8)!); exit(1) }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.03) {
                let conf = WKSnapshotConfiguration()
                conf.afterScreenUpdates = true
                self.view.takeSnapshot(with: conf) { image, _ in
                    guard let image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                          let png = rep.representation(using: .png, properties: [:]) else { exit(2) }
                    try? png.write(to: out.appendingPathComponent(String(format: "f%05d.png", self.frame)))
                    self.frame += 1
                    self.next()
                }
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let window = NSWindow(contentRect: NSRect(x: -10000, y: -10000, width: width, height: height), styleMask: [.borderless],
                      backing: .buffered, defer: false)
let renderer = Renderer(total: Int(seconds * fps))
window.contentView = renderer.view
window.orderBack(nil)
renderer.view.loadFileURL(page, allowingReadAccessTo: page.deletingLastPathComponent())
app.run()
