import SwiftUI

struct PopoverView: View {
    @ObservedObject var monitor: StatusMonitor
    @State private var now = Date()

    private let uiTimer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerSection
            Divider()

            if monitor.pipeline.isProcessing {
                processingSection
            } else {
                idleSection
            }

            if let count = monitor.watcher.filesInQueue, count > 0 {
                Divider()
                queueSection(count: count)
            }

            if !monitor.history.isEmpty {
                Divider()
                historySection
            }

            Divider()
            actionsSection
        }
        .frame(width: 300)
        .onReceive(uiTimer) { now = $0 }
    }

    // MARK: - Header

    private var headerSection: some View {
        HStack {
            Text("Memo Transcriber")
                .font(.system(size: 13, weight: .semibold))
            Spacer()
            HStack(spacing: 6) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 7, height: 7)
                Text(statusText)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var statusColor: Color {
        if monitor.pipeline.isProcessing { return .blue }
        if monitor.watcher.isRunning { return .green }
        return .red
    }

    private var statusText: String {
        if monitor.pipeline.isProcessing { return "Processing" }
        if monitor.watcher.isRunning { return "Running" }
        return "Stopped"
    }

    // MARK: - Processing

    private var processingSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(monitor.pipeline.originalName ?? monitor.pipeline.file ?? "Unknown file")
                .font(.system(size: 12, weight: .medium))
                .lineLimit(1)
                .truncationMode(.middle)

            HStack {
                Text(monitor.pipeline.stepLabel)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)

                if monitor.pipeline.state == "transcribing",
                   let ci = monitor.pipeline.chunkIndex,
                   let ct = monitor.pipeline.chunkTotal {
                    Text("(\(ci)/\(ct))")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }

                Spacer()

                if let started = monitor.pipeline.startedAt {
                    Text(elapsedString(from: started))
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(.secondary)
                }
            }

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color.primary.opacity(0.1))
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color.accentColor)
                        .frame(width: geo.size.width * monitor.pipeline.progress / 100)
                        .animation(.easeInOut(duration: 0.5), value: monitor.pipeline.progress)
                }
            }
            .frame(height: 4)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    // MARK: - Idle

    private var idleSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Idle")
                .font(.system(size: 12))
                .foregroundColor(.secondary)

            if let next = monitor.watcher.nextPollAt {
                let diff = next.timeIntervalSince(now)
                if diff > 0 {
                    Text("Next scan \(formatCountdown(diff))")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary.opacity(0.7))
                } else {
                    Text("Scanning soon")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary.opacity(0.7))
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    // MARK: - Queue

    private func queueSection(count: Int) -> some View {
        HStack {
            Image(systemName: "tray.full")
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Text("\(count) file\(count == 1 ? "" : "s") queued")
                .font(.system(size: 11))
                .foregroundColor(.secondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    // MARK: - History

    private var historySection: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Recent")
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.secondary)
                .textCase(.uppercase)
                .padding(.horizontal, 14)
                .padding(.top, 8)
                .padding(.bottom, 4)

            ForEach(monitor.history.prefix(5)) { entry in
                HStack(spacing: 8) {
                    Image(systemName: entry.isDone ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .font(.system(size: 10))
                        .foregroundColor(entry.isDone ? .green : .red)

                    VStack(alignment: .leading, spacing: 1) {
                        Text(entry.originalName)
                            .font(.system(size: 11))
                            .lineLimit(1)
                            .truncationMode(.middle)

                        if entry.isDone, let dur = entry.processingSeconds {
                            Text("Processed in \(formatProcessingTime(dur))")
                                .font(.system(size: 10))
                                .foregroundColor(.secondary)
                        } else if let err = entry.error {
                            Text(err)
                                .font(.system(size: 10))
                                .foregroundColor(.red.opacity(0.8))
                                .lineLimit(1)
                        }
                    }

                    Spacer()

                    if let completed = entry.completedAt {
                        Text(timeAgo(from: completed))
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 3)
            }
            .padding(.bottom, 4)
        }
    }

    // MARK: - Actions

    private var actionsSection: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                actionButton("Inbox", icon: "tray.and.arrow.down") {
                    openFolder("inbox")
                }
                Divider().frame(height: 28)
                actionButton("Transcripts", icon: "doc.text") {
                    openFolder("transcripts")
                }
            }

            Divider()

            Button(action: { NSApplication.shared.terminate(nil) }) {
                HStack {
                    Text("Quit Memo Transcriber")
                        .font(.system(size: 12))
                    Spacer()
                    Text("\u{2318}Q")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 6)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    private func actionButton(_ title: String, icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 2) {
                Image(systemName: icon)
                    .font(.system(size: 12))
                Text(title)
                    .font(.system(size: 10))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: - Helpers

    private func openFolder(_ name: String) {
        let path = (NSHomeDirectory() as NSString)
            .appendingPathComponent("LocalMemoTranscriber/\(name)")
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    private func elapsedString(from date: Date) -> String {
        let elapsed = max(0, now.timeIntervalSince(date))
        let totalSeconds = Int(elapsed)
        let hours = totalSeconds / 3600
        let minutes = (totalSeconds % 3600) / 60
        let seconds = totalSeconds % 60
        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, seconds)
        }
        return String(format: "%d:%02d", minutes, seconds)
    }

    private func formatCountdown(_ seconds: TimeInterval) -> String {
        let s = Int(seconds)
        if s < 60 { return "in \(s)s" }
        let m = s / 60
        if m < 60 { return "in \(m)m \(s % 60)s" }
        return "in \(m / 60)h \(m % 60)m"
    }

    private func formatProcessingTime(_ seconds: Double) -> String {
        let s = Int(seconds)
        if s < 60 { return "\(s)s" }
        let m = s / 60
        if m < 60 { return "\(m)m \(s % 60)s" }
        return "\(m / 60)h \(m % 60)m"
    }

    private func timeAgo(from date: Date) -> String {
        let diff = now.timeIntervalSince(date)
        if diff < 60 { return "just now" }
        if diff < 3600 { return "\(Int(diff / 60))m ago" }
        if diff < 86400 { return "\(Int(diff / 3600))h ago" }
        return "\(Int(diff / 86400))d ago"
    }
}
