import SwiftUI

@main
struct MemoTranscriberApp: App {
    @StateObject private var monitor = StatusMonitor()

    var body: some Scene {
        MenuBarExtra {
            PopoverView(monitor: monitor)
        } label: {
            Image(systemName: monitor.menuBarIcon)
        }
        .menuBarExtraStyle(.window)
    }
}
