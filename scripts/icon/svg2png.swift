import AppKit
// Renders the icon SVG to a PNG of the given size, with macOS's icon drop shadow under the tile (measured on Apple's
// own icons, System Settings and Calculator: alpha 41 just below the tile fading to 7 over 27 px at 1024 px; these
// values reproduce it exactly). CoreSVG ignores SVG filters, so the
// shadow is drawn here.
let a = CommandLine.arguments
guard let img = NSImage(contentsOfFile: a[1]) else { print("cannot load"); exit(1) }
let size = Int(a[3])!
let scale = CGFloat(size) / 1024
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let shadow = NSShadow()
shadow.shadowOffset = NSSize(width: 0, height: -8 * scale)
shadow.shadowBlurRadius = 32 * scale
shadow.shadowColor = NSColor.black.withAlphaComponent(0.25)
if size >= 64 { shadow.set() }  // tiny sizes stay crisp
img.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: a[2]))
