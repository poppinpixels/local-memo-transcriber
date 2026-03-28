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

struct QueueFile: Identifiable {
    let id = UUID()
    let name: String
    let sizeBytes: Int64
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
    @Published var queueFiles: [QueueFile] = []
    @Published var configFound = false
    @Published var scanRunning = false
    @Published var animFrame = 0

    private var timer: Timer?
    private var animTimer: Timer?
    private let statusFilePath: String
    private(set) var inboxPath: String
    private(set) var transcriptsPath: String
    private var configPath: String = ""
    private var scriptDir: String = ""
    private var tmpDir: String = ""
    private var venvPython: String = ""

    private static let animIcons = [
        "waveform.path.ecg",
        "waveform",
        "waveform.path",
        "waveform",
    ]

    var menuBarIcon: String {
        if pipeline.isProcessing || scanRunning {
            return Self.animIcons[animFrame % Self.animIcons.count]
        } else if watcher.isRunning {
            return "mic.fill"
        } else {
            return "mic.slash"
        }
    }

    init() {
        let home = NSHomeDirectory()
        let cfgPath = (home as NSString).appendingPathComponent("LocalMemoTranscriber/config.env")
        var statusPath = (home as NSString).appendingPathComponent("LocalMemoTranscriber/status.json")
        var inbox = (home as NSString).appendingPathComponent("LocalMemoTranscriber/inbox")
        var transcripts = (home as NSString).appendingPathComponent("LocalMemoTranscriber/transcripts")
        var tmp = (home as NSString).appendingPathComponent("LocalMemoTranscriber/tmp")
        var venv = (home as NSString).appendingPathComponent("LocalMemoTranscriber/venv")

        if let contents = try? String(contentsOfFile: cfgPath, encoding: .utf8) {
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
                if let v = extractValue("TMP_DIR=") { tmp = v }
                if let v = extractValue("VENV_DIR=") { venv = v }
            }
        }

        self.statusFilePath = statusPath
        self.inboxPath = inbox
        self.transcriptsPath = transcripts
        self.configPath = cfgPath
        self.tmpDir = tmp
        self.venvPython = (venv as NSString).appendingPathComponent("bin/python")

        // Derive script dir: config.env lives alongside the scripts
        // when installed, but the repo scripts may be elsewhere.
        // Use the symlink target of the config if possible, otherwise
        // look for watch_and_transcribe.sh next to config.env.
        var sDir = (cfgPath as NSString).deletingLastPathComponent
        let candidate = (sDir as NSString).appendingPathComponent("watch_and_transcribe.sh")
        if !FileManager.default.fileExists(atPath: candidate) {
            // Fallback: check common repo location
            let repoGuess = (home as NSString).appendingPathComponent("projects/local-memo-transcriber")
            if FileManager.default.fileExists(atPath: (repoGuess as NSString).appendingPathComponent("watch_and_transcribe.sh")) {
                sDir = repoGuess
            }
        }
        self.scriptDir = sDir

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

        updateAnimation()
        scanInbox()

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

    // MARK: - Inbox

    private static let supportedExtensions: Set<String> = ["m4a", "mp3", "wav", "mp4", "aac"]

    private func scanInbox() {
        let fm = FileManager.default
        guard let contents = try? fm.contentsOfDirectory(atPath: inboxPath) else {
            queueFiles = []
            return
        }
        queueFiles = contents.compactMap { name in
            guard !name.hasPrefix(".") else { return nil }
            let ext = (name as NSString).pathExtension.lowercased()
            guard Self.supportedExtensions.contains(ext) else { return nil }
            let full = (inboxPath as NSString).appendingPathComponent(name)
            let size = (try? fm.attributesOfItem(atPath: full)[.size] as? Int64) ?? 0
            return QueueFile(name: name, sizeBytes: size)
        }
    }

    // MARK: - Animation

    private func updateAnimation() {
        let shouldAnimate = pipeline.isProcessing || scanRunning
        if shouldAnimate && animTimer == nil {
            animTimer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.animFrame += 1
                }
            }
        } else if !shouldAnimate && animTimer != nil {
            animTimer?.invalidate()
            animTimer = nil
            animFrame = 0
        }
    }

    // MARK: - Scan Now

    func scanNow() {
        guard !scanRunning else { return }

        // Don't interrupt an active transcription — it corrupts state.
        if pipeline.isProcessing { return }

        scanRunning = true
        updateAnimation()

        // Only remove the lock if the watcher process is dead or sleeping.
        // Never kill a watcher that is actively processing.
        let lockDir = (tmpDir as NSString).appendingPathComponent(".watcher.lock")
        let pidFile = (lockDir as NSString).appendingPathComponent("pid")
        if FileManager.default.fileExists(atPath: pidFile),
           let pidStr = try? String(contentsOfFile: pidFile, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
           let pid = Int32(pidStr) {
            if kill(pid, 0) != 0 {
                // Process is dead — clean up stale lock.
                try? FileManager.default.removeItem(atPath: pidFile)
                try? FileManager.default.removeItem(atPath: lockDir)
            } else {
                // Process alive — send SIGTERM so it exits its sleep loop.
                kill(pid, SIGTERM)
                // Wait for it to clean up.
                for _ in 0..<10 {
                    usleep(300_000)
                    if !FileManager.default.fileExists(atPath: lockDir) { break }
                }
                // Force-clean if trap didn't fire.
                if FileManager.default.fileExists(atPath: lockDir) {
                    try? FileManager.default.removeItem(atPath: pidFile)
                    try? FileManager.default.removeItem(atPath: lockDir)
                }
            }
        }

        let script = (scriptDir as NSString).appendingPathComponent("watch_and_transcribe.sh")
        let config = configPath

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/bash")
            process.arguments = [script, "--config", config, "--once"]
            process.standardOutput = nil
            process.standardError = nil
            try? process.run()
            process.waitUntilExit()
            DispatchQueue.main.async {
                self?.scanRunning = false
                self?.updateAnimation()
                self?.refresh()
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
