import Foundation
import SwiftUI

// MARK: - Models

struct PipelineState {
    var state: String = "idle"
    var updatedAt: Date?
    var startedAt: Date?
    var file: String?
    var originalName: String?
    var durationSeconds: Double?
    var chunkIndex: Int?
    var chunkTotal: Int?
    var device: String?

    var isProcessing: Bool { state != "idle" }

    var stepLabel: String {
        switch state {
        case "moving": return "Moving file"
        case "normalizing": return "Normalizing audio"
        case "detecting_silence": return "Detecting silence"
        case "loading_model": return "Loading model"
        case "transcribing": return "Transcribing"
        case "writing_output": return "Writing output"
        case "archiving": return "Archiving"
        default: return state.capitalized
        }
    }

    var progress: Double {
        let weights: [(String, Double)] = [
            ("moving", 2),
            ("normalizing", 5),
            ("detecting_silence", 3),
            ("loading_model", 15),
            ("transcribing", 70),
            ("writing_output", 3),
            ("archiving", 2),
        ]

        var base: Double = 0
        for (step, weight) in weights {
            if step == state {
                var stepProgress = weight
                if state == "transcribing",
                   let total = chunkTotal, total > 0,
                   let index = chunkIndex {
                    stepProgress = weight * Double(index) / Double(total)
                }
                return min(100, base + stepProgress)
            }
            base += weight
        }
        return 0
    }
}

struct WatcherState {
    var state: String = "unknown"
    var updatedAt: Date?
    var pid: Int?
    var pollIntervalSeconds: Int?
    var filesInQueue: Int?
    var nextPollAt: Date?

    var isRunning: Bool {
        ["started", "scanning", "sleeping", "processing"].contains(state)
    }
}

struct HistoryEntry: Identifiable {
    let id = UUID()
    let originalName: String
    let basename: String?
    let completedAt: Date?
    let durationSeconds: Double?
    let processingSeconds: Double?
    let status: String
    let error: String?

    var isDone: Bool { status == "done" }
}

// MARK: - StatusMonitor

@MainActor
final class StatusMonitor: ObservableObject {
    @Published var pipeline = PipelineState()
    @Published var watcher = WatcherState()
    @Published var history: [HistoryEntry] = []
    @Published var configFound = false

    private var timer: Timer?
    private let statusFilePath: String
    private(set) var inboxPath: String
    private(set) var transcriptsPath: String

    var menuBarIcon: String {
        if pipeline.isProcessing {
            return "waveform"
        } else if watcher.isRunning {
            return "mic.fill"
        } else {
            return "mic.slash"
        }
    }

    init() {
        let home = NSHomeDirectory()
        let configPath = (home as NSString).appendingPathComponent("LocalMemoTranscriber/config.env")
        var statusPath = (home as NSString).appendingPathComponent("LocalMemoTranscriber/status.json")
        var inbox = (home as NSString).appendingPathComponent("LocalMemoTranscriber/inbox")
        var transcripts = (home as NSString).appendingPathComponent("LocalMemoTranscriber/transcripts")

        if let contents = try? String(contentsOfFile: configPath, encoding: .utf8) {
            configFound = true
            for line in contents.components(separatedBy: .newlines) {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("#") || trimmed.isEmpty { continue }
                let cleaned = trimmed.hasPrefix("export ")
                    ? String(trimmed.dropFirst(7)).trimmingCharacters(in: .whitespaces)
                    : trimmed

                func extractValue(_ prefix: String) -> String? {
                    guard cleaned.hasPrefix(prefix) else { return nil }
                    var value = String(cleaned.dropFirst(prefix.count))
                    value = value.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                    value = value.replacingOccurrences(of: "$HOME", with: home)
                    return (value as NSString).expandingTildeInPath
                }

                if let v = extractValue("STATUS_FILE=") { statusPath = v }
                if let v = extractValue("WATCH_DIR=") { inbox = v }
                if let v = extractValue("TRANSCRIPTS_DIR=") { transcripts = v }
            }
        }

        self.statusFilePath = statusPath
        self.inboxPath = inbox
        self.transcriptsPath = transcripts
        startPolling()
    }

    private func startPolling() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.refresh()
            }
        }
    }

    private func refresh() {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: statusFilePath)),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }

        if let p = json["pipeline"] as? [String: Any] {
            pipeline.state = p["state"] as? String ?? "idle"
            pipeline.updatedAt = parseDate(p["updated_at"] as? String)
            pipeline.startedAt = parseDate(p["started_at"] as? String)
            pipeline.file = p["file"] as? String
            pipeline.originalName = p["original_name"] as? String
            pipeline.durationSeconds = p["duration_seconds"] as? Double
            pipeline.chunkIndex = p["chunk_index"] as? Int
            pipeline.chunkTotal = p["chunk_total"] as? Int
            pipeline.device = p["device"] as? String
        }

        if let w = json["watcher"] as? [String: Any] {
            watcher.state = w["state"] as? String ?? "unknown"
            watcher.updatedAt = parseDate(w["updated_at"] as? String)
            watcher.pid = w["pid"] as? Int
            watcher.pollIntervalSeconds = w["poll_interval_seconds"] as? Int
            watcher.filesInQueue = w["files_in_queue"] as? Int
            watcher.nextPollAt = parseDate(w["next_poll_at"] as? String)
        }

        if let h = json["history"] as? [[String: Any]] {
            history = h.prefix(10).compactMap { entry in
                guard let name = entry["original_name"] as? String,
                      let status = entry["status"] as? String else { return nil }
                return HistoryEntry(
                    originalName: name,
                    basename: entry["basename"] as? String,
                    completedAt: parseDate(entry["completed_at"] as? String),
                    durationSeconds: entry["duration_seconds"] as? Double,
                    processingSeconds: entry["processing_seconds"] as? Double,
                    status: status,
                    error: entry["error"] as? String
                )
            }
        }
    }

    private func parseDate(_ string: String?) -> Date? {
        guard let string else { return nil }
        // "2026-03-25T14:43:45" — the format status.py writes
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        df.locale = Locale(identifier: "en_US_POSIX")
        if let date = df.date(from: string) { return date }
        // Fallback: ISO 8601 with timezone
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        return iso.date(from: string)
    }
}
