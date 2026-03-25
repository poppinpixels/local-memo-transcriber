// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MemoTranscriber",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "MemoTranscriber",
            path: "MemoTranscriber",
            exclude: ["Info.plist", "Assets.xcassets"]
        ),
    ]
)
