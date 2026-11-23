// swift-tools-version: 6.0
import PackageDescription

// Everything the Mac app knows that is not a view: how it starts hearth's engine and asks it
// for a screen, what the answers hold, and how figures are written for a person. It builds and
// tests with `swift test`, with no app and no engine.
let package = Package(
    name: "HearthKit",
    platforms: [.macOS(.v15)],
    products: [.library(name: "HearthKit", targets: ["HearthKit"])],
    targets: [
        .target(name: "HearthKit"),
        .testTarget(name: "HearthKitTests", dependencies: ["HearthKit"]),
    ]
)
