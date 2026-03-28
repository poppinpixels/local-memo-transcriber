import SwiftUI

struct PopoverView: View {
    @ObservedObject var monitor: StatusMonitor
    @State private var now = Date()

    private let uiTimer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    private var isBusy: Bool {
        monitor.pipeline.isProcessing || monitor.scanRunning
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerSection
            Divider()

            statusSection
            Divider()

            if !monitor.queueFiles.isEmpty {
                queueSection
                Divider()
            }

            if !monitor.history.isEmpty {
                historySection
                Divider()
            }

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
        if monitor.scanRunning { return .orange }
        if monitor.watcher.isRunning { return .green }
        return .red
    }

    private var statusText: String {
        if monitor.pipeline.isProcessing { return "Processing" }
        if monitor.scanRunning { return "Scanning" }
        if monitor.watcher.isRunning { return "Running" }
        return "Stopped"
    }

    // MARK: - Status (unified processing / scanning / idle)

    private var statusSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            if monitor.pipeline.isProcessing {
                processingContent
            } else if monitor.scanRunning {
                scanningContent
            } else {
                idleContent
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var processingContent: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(monitor.pipeline.originalName ?? monitor.pipeline.file ?? "Unknown file")
                .font(.system(size: 12, weight: .medium))
                .lineLimit(2)
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
    }

    private var scanningContent: some View {
        HStack(spacing: 8) {
            ProgressView()
                .scaleEffect(0.7)
                .frame(width: 14, height: 14)
            Text("Scanning inbox for new files...")
                .font(.system(size: 12))
                .foregroundColor(.secondary)
        }
    }

    private var idleContent: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Idle")
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
                Spacer()
                Button(action: { monitor.scanNow() }) {
                    HStack(spacing: 4) {
                        Image(systemName: "play.fill")
                            .font(.system(size: 8))
                        Text("Scan Now")
                            .font(.system(size: 11, weight: .medium))
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.accentColor.opacity(0.15))
                    .cornerRadius(4)
                }
                .buttonStyle(.plain)
            }

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
    }

    // MARK: - Queue

    private var queueSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Queue (\(monitor.queueFiles.count))")
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.secondary)
                .textCase(.uppercase)
                .padding(.horizontal, 14)
                .padding(.top, 8)
                .padding(.bottom, 4)

            ForEach(monitor.queueFiles) { file in
                let active = isCurrentlyProcessing(file.name)
                HStack(spacing: 8) {
                    Image(systemName: active ? "waveform" : "doc.fill")
                        .font(.system(size: 10))
                        .foregroundColor(active ? .blue : .secondary)

                    Text(file.name)
                        .font(.system(size: 11))
                        .lineLimit(1)
                        .truncationMode(.middle)

                    Spacer()

                    if active {
                        Text(monitor.pipeline.stepLabel)
                            .font(.system(size: 10, weight: .medium))
                            .foregroundColor(.blue)
                    } else if monitor.scanRunning {
                        Text("Waiting")
                            .font(.system(size: 10))
                            .foregroundColor(.orange)
                    } else {
                        Text(formatBytes(file.sizeBytes))
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

    private func isCurrentlyProcessing(_ name: String) -> Bool {
        monitor.pipeline.isProcessing && monitor.pipeline.originalName == name
    }

    private func formatBytes(_ bytes: Int64) -> String {
        if bytes < 1024 { return "\(bytes) B" }
        if bytes < 1_048_576 { return String(format: "%.1f KB", Double(bytes) / 1024) }
        return String(format: "%.1f MB", Double(bytes) / 1_048_576)
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
                    openPath(monitor.inboxPath)
                }
                Divider().frame(height: 28)
                actionButton("Transcripts", icon: "doc.text") {
                    openPath(monitor.transcriptsPath)
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

    private func openPath(_ path: String) {
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
