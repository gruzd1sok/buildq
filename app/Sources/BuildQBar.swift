// BuildQBar — menu bar view of the buildq queue (~/.buildq). Reads queue/history files directly.
import AppKit
import SwiftUI

let home = ProcessInfo.processInfo.environment["BUILDQ_HOME"].map { URL(fileURLWithPath: $0) }
    ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".buildq")
let cli = ProcessInfo.processInfo.environment["BUILDQ_CLI"] ?? "buildq"

/// Localized string from Localizable.xcstrings; plural variants are resolved by the format arguments.
func L(_ key: String, _ args: CVarArg...) -> String {
    let format = Bundle.main.localizedString(forKey: key, value: nil, table: nil)
    return args.isEmpty ? format : String(format: format, locale: .current, arguments: args)
}

struct Ticket: Decodable, Identifiable, Equatable {
    let id: String
    let seq: Int?
    let pid: Int32
    let state: String
    let worktree: String
    let branch: String?
    let label: String
    let enqueued_ns: Double
    let started_at: Double?
    let full_log: String?
}

struct Hist: Decodable, Identifiable, Equatable {
    let worktree: String
    let label: String
    let waited: Double
    let ran: Double
    let exit: Int
    let finished_at: String
    let full_log: String?
    var id: String { finished_at + worktree + label }
}

private let durationFormatter: DateComponentsFormatter = {
    let f = DateComponentsFormatter()
    f.unitsStyle = .abbreviated
    f.allowedUnits = [.hour, .minute, .second]
    f.maximumUnitCount = 2
    return f
}()

func dur(_ s: Double) -> String { durationFormatter.string(from: max(0, s.rounded(.down))) ?? "" }

func name(_ worktree: String) -> String { URL(fileURLWithPath: worktree).lastPathComponent }

/// Follows a growing xcodebuild log incrementally on a background queue: compiled-file count and the
/// target being built. Scans raw bytes (logs reach 100+ MB), decodes only the last line with a target.
final class LogFollower {
    struct Snapshot: Equatable { var compiled = 0; var target: String?; var errors = 0 }
    let path: String
    private(set) var snap = Snapshot()
    private var offset: UInt64 = 0
    private var tail = Data()
    private static let prefixes = ["SwiftCompile ", "CompileC ", "SwiftEmitModule "].map { Array($0.utf8) }
    private static let errorNeedle = Array(": error:".utf8)
    private static let targetNeedle = Array("(in target '".utf8)

    init(path: String) { self.path = path }

    func update() {
        guard let fh = FileHandle(forReadingAtPath: path) else { return }
        defer { try? fh.close() }
        try? fh.seek(toOffset: offset)
        while let chunk = try? fh.read(upToCount: 4 << 20), !chunk.isEmpty {
            offset += UInt64(chunk.count)
            autoreleasepool { scan(tail + chunk) }
        }
    }

    private func scan(_ data: Data) {
        guard let lastNL = data.lastIndex(of: 0x0A) else { tail = data; return }
        tail = Data(data[(lastNL + 1)...])
        let n = lastNL - data.startIndex
        var lastTargetLine: Range<Int>?
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let buf = raw.bindMemory(to: UInt8.self)
            var start = 0
            while start < n {
                let base = buf.baseAddress! + start
                let end: Int
                if let nl = memchr(base, 0x0A, n - start) {
                    end = start + (UnsafeRawPointer(nl) - UnsafeRawPointer(base))
                } else {
                    end = n
                }
                let len = end - start
                if Self.prefixes.contains(where: { p in len >= p.count && memcmp(base, p, p.count) == 0 }) {
                    snap.compiled += 1
                    lastTargetLine = start..<end
                } else if len >= Self.errorNeedle.count,
                          memmem(base, len, Self.errorNeedle, Self.errorNeedle.count) != nil {
                    snap.errors += 1
                }
                start = end + 1
            }
        }
        if let r = lastTargetLine {
            let line = Array(data[(data.startIndex + r.lowerBound)..<(data.startIndex + r.upperBound)])
            if let at = line.firstRange(of: Self.targetNeedle) {
                let rest = line[at.upperBound...]
                if let q = rest.firstIndex(of: UInt8(ascii: "'")) {
                    snap.target = String(decoding: rest[..<q], as: UTF8.self)
                }
            }
        }
    }
}

struct Loaded: Equatable {
    var tickets: [Ticket] = []
    var history: [Hist] = []
    var progress = LogFollower.Snapshot()
}

/// File reading and log scanning off the main thread; the UI only receives finished snapshots.
final class Loader: @unchecked Sendable {
    private let queue = DispatchQueue(label: "buildq.loader", qos: .utility)
    private var follower: LogFollower?
    private var busy = false
    private var historyStamp: Date?
    private var history: [Hist] = []

    func load(_ done: @escaping @Sendable (Loaded) -> Void) {
        queue.async { [self] in
            if busy { return }
            busy = true
            defer { busy = false }
            var out = Loaded()
            let dir = home.appendingPathComponent("queue")
            let files = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? []
            out.tickets = files.filter { $0.pathExtension == "json" }
                .compactMap { try? JSONDecoder().decode(Ticket.self, from: Data(contentsOf: $0)) }
                .sorted { ($0.seq ?? 0, $0.enqueued_ns) < ($1.seq ?? 0, $1.enqueued_ns) }
            let hist = home.appendingPathComponent("history.log")
            let stamp = (try? FileManager.default.attributesOfItem(atPath: hist.path))?[.modificationDate] as? Date
            if stamp != historyStamp, let text = try? String(contentsOf: hist, encoding: .utf8) {
                historyStamp = stamp
                history = text.split(separator: "\n").suffix(200)
                    .compactMap { try? JSONDecoder().decode(Hist.self, from: Data($0.utf8)) }
            }
            out.history = history
            let running = out.tickets.first { $0.state == "running" || $0.state == "orphan" }
            if let path = running?.full_log {
                if follower?.path != path { follower = LogFollower(path: path) }
                follower?.update()
                out.progress = follower?.snap ?? .init()
            } else {
                follower = nil
            }
            done(out)
        }
    }
}

@MainActor
final class Model: ObservableObject {
    @Published var tickets: [Ticket] = []
    @Published var history: [Hist] = []
    @Published var now = Date().timeIntervalSince1970
    @Published var progress = LogFollower.Snapshot()
    private let loader = Loader()

    var running: Ticket? { tickets.first { $0.state == "running" || $0.state == "orphan" } }
    var waiting: [Ticket] { tickets.filter { $0.state == "waiting" } }
    var recent: [Hist] { Array(history.suffix(6).reversed()) }

    func refresh(tickClock: Bool, then: (@MainActor () -> Void)? = nil) {
        if tickClock { now = Date().timeIntervalSince1970 }
        loader.load { loaded in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    if self.tickets != loaded.tickets { self.tickets = loaded.tickets }
                    if self.history != loaded.history { self.history = loaded.history }
                    if self.progress != loaded.progress { self.progress = loaded.progress }
                    then?()
                }
            }
        }
    }

    /// Median of the last successful builds in this worktree, as a rough ETA.
    func typical(_ worktree: String) -> Double? {
        let ok = history.filter { $0.worktree == worktree && $0.exit == 0 }.suffix(3).map(\.ran).sorted()
        return ok.isEmpty ? nil : ok[ok.count / 2]
    }

    enum Light: Equatable { case idle, green, yellow, red }

    private static let finishedFormat: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f
    }()

    /// Traffic light for the menu bar icon; `filled` while something is building.
    var light: (light: Light, filled: Bool, reason: String) {
        let now = Date().timeIntervalSince1970
        if let r = running {
            let n = name(r.worktree)
            let elapsed = now - (r.started_at ?? now)
            if progress.errors > 0 { return (.red, true, L("%@: compile errors (%lld)", n, progress.errors)) }
            if r.state == "orphan" { return (.yellow, true, L("%@: agent died, stopping the build", n)) }
            if let typ = typical(r.worktree), elapsed > typ * 1.5 {
                return (.yellow, true, L("%@: longer than usual (%@ vs ~%@)", n, dur(elapsed), dur(typ)))
            }
            if !waiting.isEmpty { return (.yellow, true, L("%@ is building, %lld waiting", n, waiting.count)) }
            return (.green, true, L("%@ is building", n))
        }
        if let last = history.last, let at = Self.finishedFormat.date(from: last.finished_at) {
            let ago = now - at.timeIntervalSince1970
            if last.exit != 0 && last.exit < 128 && ago < 15 * 60 {
                return (.red, false, L("%@: build failed %@ ago", name(last.worktree), dur(ago)))
            }
            if last.exit == 0 && ago < 5 * 60 {
                return (.green, false, L("%@: built %@ ago", name(last.worktree), dur(ago)))
            }
        }
        return (.idle, false, L("Queue is empty"))
    }
}

extension Model.Light {
    var color: Color {
        switch self {
        case .idle: return .secondary
        case .green: return .green
        case .yellow: return .yellow
        case .red: return .red
        }
    }

    var nsColor: NSColor? {
        switch self {
        case .idle: return nil
        case .green: return .systemGreen
        case .yellow: return .systemYellow
        case .red: return .systemRed
        }
    }
}

// MARK: - Actions

enum Act {
    static func q(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

    static func terminal(_ command: String) {
        let esc = command.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        NSAppleScript(source: "tell application \"Terminal\"\nactivate\ndo script \"\(esc)\"\nend tell")?
            .executeAndReturnError(nil)
    }

    static func tail(_ p: String) {
        terminal("tail -n 200 -f \(q(p)) | grep --line-buffered -E 'error:|in target|FAILED|SUCCEEDED|^Ld '")
    }

    static func errors(_ p: String) { terminal("grep -E ': error:|FAILED' \(q(p)) | sort -u | head -100") }

    static func reveal(_ p: String) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: p)]) }

    static func events() { terminal("\(q(cli)) status; echo; \(q(cli)) events -n 40") }

    static func cancel(_ t: Ticket) {
        NSApp.activate(ignoringOtherApps: true)
        let waiting = t.state == "waiting"
        let a = NSAlert()
        a.messageText = waiting ? L("Remove from the queue?") : L("Stop the build?")
        a.informativeText = L("%@ — %@\n\nThe agent gets exit code 143 and sees it in the buildq output.",
                              name(t.worktree), t.label)
        a.addButton(withTitle: waiting ? L("Remove") : L("Stop"))
        a.addButton(withTitle: L("Cancel"))
        if a.runModal() == .alertFirstButtonReturn { kill(t.pid, SIGTERM) }
    }
}

// MARK: - Views

struct IconButton: View {
    let symbol: String
    let help: String
    var tint: Color = .secondary
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(hover ? tint == .secondary ? Color.primary : tint : tint)
                .frame(width: 24, height: 22)
                .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(hover ? 0.1 : 0)))
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help(help)
    }
}

struct SectionTitle: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10, weight: .semibold))
            .foregroundStyle(.secondary)
            .tracking(0.6)
    }
}

struct Card<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        content
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color.primary.opacity(0.06)))
    }
}

struct RunningCard: View {
    @ObservedObject var m: Model
    let t: Ticket

    var body: some View {
        let elapsed = m.now - (t.started_at ?? m.now)
        let typ = m.typical(t.worktree)
        Card {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline) {
                    Circle().fill(m.light.light.color).frame(width: 8, height: 8)
                    Text(name(t.worktree)).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                    Spacer()
                    Text(dur(elapsed)).font(.system(size: 13, weight: .medium, design: .rounded)).monospacedDigit()
                }
                Text(t.label).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(2)
                if let typ, typ > 0 {
                    ProgressView(value: min(elapsed / typ, 1)).progressViewStyle(.linear).tint(elapsed > typ ? .orange : .accentColor)
                } else {
                    ProgressView().progressViewStyle(.linear)
                }
                HStack(spacing: 6) {
                    Image(systemName: "shippingbox").foregroundStyle(.secondary)
                    Text(m.progress.target ?? L("preparing")).lineLimit(1)
                    Text("·").foregroundStyle(.tertiary)
                    Text(L("%lld files", m.progress.compiled)).monospacedDigit()
                    if m.progress.errors > 0 {
                        Text("·").foregroundStyle(.tertiary)
                        Text(L("errors: %lld", m.progress.errors)).foregroundStyle(.red)
                    }
                }
                .font(.system(size: 11))
                HStack {
                    Group {
                        if t.state == "orphan" {
                            Text(L("Agent died, stopping the build"))
                        } else if let typ {
                            Text(elapsed > typ ? L("longer than usual (%@)", dur(typ)) : L("usually ~%@", dur(typ)))
                        } else {
                            Text(L("first build in this worktree"))
                        }
                    }
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                    Spacer()
                    if let p = t.full_log {
                        IconButton(symbol: "text.alignleft", help: L("Follow the log in Terminal")) { Act.tail(p) }
                        IconButton(symbol: "exclamationmark.triangle", help: L("Errors only")) { Act.errors(p) }
                        IconButton(symbol: "folder", help: L("Show the log in Finder")) { Act.reveal(p) }
                    }
                    IconButton(symbol: "stop.circle", help: L("Stop the build"), tint: .red) { Act.cancel(t) }
                }
                if let b = t.branch, !b.isEmpty {
                    Text(b).font(.system(size: 10, design: .monospaced)).foregroundStyle(.tertiary).lineLimit(1)
                }
            }
        }
    }
}

struct WaitingRow: View {
    let t: Ticket
    let position: Int
    let now: Double
    let typical: Double?
    @State private var hover = false

    var body: some View {
        HStack(spacing: 10) {
            Text("\(position)")
                .font(.system(size: 11, weight: .semibold, design: .rounded))
                .frame(width: 20, height: 20)
                .background(Circle().fill(Color.primary.opacity(0.08)))
            VStack(alignment: .leading, spacing: 2) {
                Text(name(t.worktree)).font(.system(size: 12, weight: .medium)).lineLimit(1)
                Text(t.label).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if hover {
                IconButton(symbol: "xmark.circle", help: L("Remove from the queue"), tint: .red) { Act.cancel(t) }
            } else {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(L("waiting %@", dur(now - t.enqueued_ns / 1e9))).font(.system(size: 11)).monospacedDigit()
                    if let typical { Text("~\(dur(typical))").font(.system(size: 10)).foregroundStyle(.tertiary) }
                }
                .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4).padding(.horizontal, 6)
        .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(hover ? 0.06 : 0)))
        .onHover { hover = $0 }
    }
}

struct HistoryRow: View {
    let h: Hist
    @State private var hover = false

    var status: (String, Color) {
        if h.exit == 0 { return ("checkmark.circle.fill", .green) }
        if h.exit >= 128 { return ("stop.circle.fill", .orange) }
        return ("xmark.circle.fill", .red)
    }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: status.0).foregroundStyle(status.1).font(.system(size: 14))
            VStack(alignment: .leading, spacing: 2) {
                Text(name(h.worktree)).font(.system(size: 12, weight: .medium)).lineLimit(1)
                Text(h.label).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if hover, let p = h.full_log, FileManager.default.fileExists(atPath: p) {
                IconButton(symbol: "exclamationmark.triangle", help: L("Errors only")) { Act.errors(p) }
                IconButton(symbol: "folder", help: L("Show the log in Finder")) { Act.reveal(p) }
            } else {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(dur(h.ran)).font(.system(size: 11)).monospacedDigit()
                    Text(String(h.finished_at.suffix(8).prefix(5))).font(.system(size: 10)).foregroundStyle(.tertiary)
                }
                .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4).padding(.horizontal, 6)
        .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(hover ? 0.06 : 0)))
        .onHover { hover = $0 }
        .help(L("Exit code %lld · waited %@ · ran %@", h.exit, dur(h.waited), dur(h.ran)))
    }
}

struct Panel: View {
    @ObservedObject var m: Model

    var summary: String {
        var parts: [String] = []
        if m.running != nil { parts.append(L("1 building")) }
        if !m.waiting.isEmpty { parts.append(L("%lld waiting", m.waiting.count)) }
        return parts.isEmpty ? L("idle") : parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(L("Build queue")).font(.system(size: 14, weight: .semibold))
                    Spacer()
                    Text(summary).font(.system(size: 11)).foregroundStyle(.secondary)
                }
                HStack(spacing: 6) {
                    Circle().fill(m.light.light.color).frame(width: 7, height: 7)
                    Text(m.light.reason).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                }
            }

            if let r = m.running {
                RunningCard(m: m, t: r)
            } else {
                Card {
                    HStack(spacing: 8) {
                        Image(systemName: "hammer").foregroundStyle(.secondary)
                        Text(L("Nothing is building")).font(.system(size: 12)).foregroundStyle(.secondary)
                    }
                }
            }

            if !m.waiting.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    SectionTitle(text: L("Waiting"))
                    ForEach(Array(m.waiting.enumerated()), id: \.element.id) { i, t in
                        WaitingRow(t: t, position: i + (m.running == nil ? 1 : 2), now: m.now, typical: m.typical(t.worktree))
                    }
                }
            }

            if !m.recent.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    SectionTitle(text: L("Recent"))
                    ForEach(m.recent) { HistoryRow(h: $0) }
                }
            }

            Divider()
            HStack {
                Button(L("Event log")) { Act.events() }
                Spacer()
                Button(L("Quit")) { NSApp.terminate(nil) }
            }
            .buttonStyle(.plain)
            .font(.system(size: 12))
            .foregroundStyle(.secondary)
        }
        .padding(16)
        .frame(width: 360)
    }
}

// MARK: - App

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    let popover = NSPopover()
    let model = Model()
    var timer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        let host = NSHostingController(rootView: Panel(m: model))
        host.sizingOptions = .preferredContentSize
        popover.contentViewController = host
        popover.behavior = .transient
        popover.animates = true
        item.button?.target = self
        item.button?.action = #selector(toggle)
        setIcon()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
    }

    // Closed: poll every 3s just for the icon; open: every second so the clock ticks.
    private var ticks = 0
    func refresh() {
        ticks += 1
        guard popover.isShown || ticks % 3 == 0 else { return }
        model.refresh(tickClock: popover.isShown) { [weak self] in
            self?.setIcon()
        }
    }

    private var iconKey: String?
    func setIcon() {
        let l = model.light
        item.button?.toolTip = l.reason
        let key = "\(l.light)-\(l.filled)"
        guard iconKey != key else { return }
        iconKey = key
        var img = NSImage(systemSymbolName: l.filled ? "hammer.fill" : "hammer", accessibilityDescription: "buildq")
        if let c = l.light.nsColor {
            img = img?.withSymbolConfiguration(NSImage.SymbolConfiguration(paletteColors: [c]))
            img?.isTemplate = false
        } else {
            img?.isTemplate = true
        }
        item.button?.image = img
    }

    @objc func toggle() {
        guard let button = item.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            model.now = Date().timeIntervalSince1970
            NSApp.activate(ignoringOtherApps: true)
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
            model.refresh(tickClock: true)
        }
    }
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let delegate = AppDelegate()
    app.delegate = delegate
    app.run()
}
