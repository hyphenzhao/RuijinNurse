import SwiftUI
import AVKit
import Speech

// MARK: - Messages List

struct ChatMessagesView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    // Intro video on first session
                    if vm.showIntroVideo, let introURL = vm.introVideoURL {
                        IntroVideoView(videoURL: introURL)
                            .id("intro")
                    }

                    ForEach(vm.messages) { msg in
                        MessageBubbleView(message: msg)
                            .id(msg.id)
                    }

                    // Streaming indicator
                    if vm.isStreaming {
                        StreamingBubbleView(thinking: vm.streamingThinking, answer: vm.streamingAnswer)
                            .id("streaming")
                    }
                }
                .padding(12)
            }
            .background(Color.clear)
            .onChange(of: vm.messages.count) { _ in
                scrollToBottom(proxy: proxy)
            }
            .onChange(of: vm.streamingAnswer) { _ in
                scrollToBottom(proxy: proxy)
            }
            .onChange(of: vm.isStreaming) { streaming in
                if streaming {
                    scrollToBottom(proxy: proxy)
                }
            }
        }
    }

    private func scrollToBottom(proxy: ScrollViewProxy) {
        if vm.isStreaming {
            withAnimation {
                proxy.scrollTo("streaming", anchor: .bottom)
            }
        } else if let last = vm.messages.last?.id {
            withAnimation {
                proxy.scrollTo(last, anchor: .bottom)
            }
        }
    }
}

// MARK: - Video Player Helper (with error observation)

/// Holds an AVPlayer and observes its status, exposing errors and loading state.
/// Use as @StateObject in views that embed VideoPlayer.
///
/// Downloads remote videos to a local temp file first, then plays from disk.
/// This works around servers that don't correctly handle HTTP Range requests
/// (e.g. Django runserver), which would otherwise break AVPlayer streaming.
@MainActor
class VideoPlayerModel: ObservableObject {
    @Published var player: AVPlayer?
    @Published var errorMessage: String?
    @Published var isLoading = true
    @Published var isReady = false
    @Published var downloadProgress: Double = 0

    private var statusObs: NSKeyValueObservation?
    private var errorObs: NSKeyValueObservation?
    private var downloadTask: URLSessionDownloadTask?
    private let logPrefix: String
    private var loadingURL: URL?  // Track which URL is currently loading to prevent re-entrant calls

    init(logPrefix: String = "[VideoPlayer]") {
        self.logPrefix = logPrefix
    }

    /// Load a remote video.  Downloads to a local temp file first so that
    /// we don't depend on the server correctly handling HTTP Range requests.
    func load(url: URL) {
        // If already loaded this URL and ready, do nothing
        if let player = player, isReady, loadingURL == url {
            print("\(logPrefix) ⏭️ Already playing this URL, skip")
            return
        }

        // If currently downloading the same URL, do nothing
        if isLoading && loadingURL == url {
            print("\(logPrefix) ⏭️ Already downloading this URL, skip")
            return
        }

        // If already cached locally, play directly
        let cacheURL = Self.cacheURL(for: url)
        if FileManager.default.fileExists(atPath: cacheURL.path) {
            print("\(logPrefix) 📦 Using cached file: \(cacheURL.path)")
            loadingURL = url
            playLocalFile(cacheURL)
            return
        }

        // Cancel any previous download
        downloadTask?.cancel()
        downloadTask = nil

        print("\(logPrefix) 🌐 Downloading: \(url.absoluteString)")
        isLoading = true
        errorMessage = nil
        isReady = false
        downloadProgress = 0
        loadingURL = url

        let task = URLSession.shared.downloadTask(with: URLRequest(url: url, timeoutInterval: 300)) { [weak self] tempURL, response, error in
            guard let self = self else { return }

            if let error = error {
                DispatchQueue.main.async {
                    // Only report if still loading this URL (not superseded)
                    guard self.loadingURL == url else { return }
                    print("\(self.logPrefix) ❌ Download error: \(error.localizedDescription)")
                    self.errorMessage = error.localizedDescription
                    self.isLoading = false
                }
                return
            }

            guard let tempURL = tempURL else {
                DispatchQueue.main.async {
                    guard self.loadingURL == url else { return }
                    print("\(self.logPrefix) ❌ Download returned no data")
                    self.errorMessage = "下载失败：无数据"
                    self.isLoading = false
                }
                return
            }

            // Check HTTP status
            if let httpResp = response as? HTTPURLResponse {
                print("\(self.logPrefix) 📡 HTTP \(httpResp.statusCode), Content-Type: \(httpResp.allHeaderFields["Content-Type"] ?? "unknown"), Size: \(httpResp.expectedContentLength)")
                guard httpResp.statusCode == 200 else {
                    DispatchQueue.main.async {
                        guard self.loadingURL == url else { return }
                        print("\(self.logPrefix) ❌ HTTP error \(httpResp.statusCode)")
                        self.errorMessage = "HTTP \(httpResp.statusCode)"
                        self.isLoading = false
                    }
                    return
                }
            }

            // Move to cache
            do {
                try? FileManager.default.removeItem(at: cacheURL)
                try FileManager.default.createDirectory(at: cacheURL.deletingLastPathComponent(), withIntermediateDirectories: true)
                try FileManager.default.copyItem(at: tempURL, to: cacheURL)
                print("\(self.logPrefix) 📁 Cached to: \(cacheURL.path)")
            } catch {
                DispatchQueue.main.async {
                    guard self.loadingURL == url else { return }
                    print("\(self.logPrefix) ❌ Cache error: \(error)")
                    self.errorMessage = "缓存文件失败"
                    self.isLoading = false
                }
                return
            }

            DispatchQueue.main.async {
                guard self.loadingURL == url else { return }
                self.playLocalFile(cacheURL)
            }
        }

        // Track download progress
        let observation = task.progress.observe(\.fractionCompleted) { [weak self] progress, _ in
            DispatchQueue.main.async {
                self?.downloadProgress = progress.fractionCompleted
            }
        }
        // Keep observation alive
        objc_setAssociatedObject(task, "progressObs", observation, .OBJC_ASSOCIATION_RETAIN)

        downloadTask = task
        task.resume()
    }

    /// Play a local file (no Range-request dependency)
    private func playLocalFile(_ url: URL) {
        print("\(logPrefix) ▶️ Playing local: \(url.path)")

        let asset = AVURLAsset(url: url)
        let playerItem = AVPlayerItem(asset: asset)
        let p = AVPlayer(playerItem: playerItem)
        player = p
        isLoading = true

        // Observe status
        statusObs = playerItem.observe(\.status, options: [.new, .initial]) { [weak self] item, _ in
            DispatchQueue.main.async {
                self?.handleStatus(item.status, item: item)
            }
        }

        // Observe error
        errorObs = playerItem.observe(\.error, options: [.new]) { [weak self] item, _ in
            DispatchQueue.main.async {
                if let err = item.error {
                    print("\(self?.logPrefix ?? "") ❌ PlayerItem error: \(err.localizedDescription)")
                    self?.errorMessage = err.localizedDescription
                    self?.isLoading = false
                }
            }
        }

        p.play()
    }

    private func handleStatus(_ status: AVPlayerItem.Status, item: AVPlayerItem) {
        switch status {
        case .readyToPlay:
            print("\(logPrefix) ✅ Ready to play")
            isLoading = false
            isReady = true
        case .failed:
            let err = item.error
            print("\(logPrefix) ❌ Failed: \(err?.localizedDescription ?? "unknown")")
            if let nsErr = err as NSError? {
                print("\(logPrefix)    Domain: \(nsErr.domain), Code: \(nsErr.code)")
            }
            errorMessage = err?.localizedDescription ?? "播放失败"
            isLoading = false
            isReady = false
        case .unknown:
            print("\(logPrefix) ⏳ Status: unknown — loading...")
        @unknown default:
            print("\(logPrefix) ⚠️ Unknown status")
        }
    }

    func stop() {
        player?.pause()
        player = nil
        downloadTask?.cancel()
        downloadTask = nil
        loadingURL = nil
        statusObs?.invalidate()
        errorObs?.invalidate()
    }

    /// Pause player only — keep download running (for scroll-away in lists)
    func pauseOnly() {
        player?.pause()
        statusObs?.invalidate()
        errorObs?.invalidate()
    }

    /// Cache directory for downloaded videos
    private static func cacheURL(for remoteURL: URL) -> URL {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first!
        let dir = caches.appendingPathComponent("VideoCache", isDirectory: true)
        let filename = remoteURL.lastPathComponent
        return dir.appendingPathComponent(filename)
    }

    /// Clear all cached videos
    static func clearCache() {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first!
        let dir = caches.appendingPathComponent("VideoCache", isDirectory: true)
        try? FileManager.default.removeItem(at: dir)
    }

    deinit {
        statusObs?.invalidate()
        errorObs?.invalidate()
    }
}

// MARK: - Intro Video

struct IntroVideoView: View {
    let videoURL: URL
    @StateObject private var model = VideoPlayerModel(logPrefix: "[IntroVideo]")

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                Text("🤖").font(.title3)
                Text("📺 入院介绍视频")
                    .font(.subheadline)
                    .fontWeight(.medium)
            }

            // Video area
            if let error = model.errorMessage {
                // Error state
                VStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.largeTitle)
                        .foregroundColor(.orange)
                    Text("视频加载失败")
                        .font(.subheadline)
                        .fontWeight(.medium)
                    Text(error)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 8)
                    Button("🔄 重试") {
                        model.load(url: videoURL)
                    }
                    .font(.caption)
                    .foregroundColor(.blue)
                }
                .frame(height: 220)
                .frame(maxWidth: .infinity)
                .background(Color(.systemGray6))
                .cornerRadius(12)
            } else if model.isLoading, model.player == nil {
                // Downloading / loading — show progress
                Rectangle()
                    .fill(Color(.systemGray6))
                    .frame(height: 220)
                    .cornerRadius(12)
                    .overlay(
                        VStack(spacing: 12) {
                            ProgressView(value: model.downloadProgress > 0 ? model.downloadProgress : nil)
                                .tint(.blue)
                                .frame(width: 120)
                            if model.downloadProgress > 0 {
                                Text("下载中 \(Int(model.downloadProgress * 100))%")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            } else {
                                Text("正在连接服务器...")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                    )
            } else if let player = model.player {
                VideoPlayer(player: player)
                    .frame(height: 220)
                    .cornerRadius(12)
                    .overlay(
                        // Loading overlay (semi-transparent) while buffering
                        Group {
                            if model.isLoading {
                                ZStack {
                                    Color.black.opacity(0.3)
                                    ProgressView().tint(.white)
                                }
                                .cornerRadius(12)
                            }
                        }
                    )
            }

            Text("欢迎来到瑞金医院功能神外智能宣讲。请先观看入院介绍视频，如有问题可在下方输入提问。")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(12)
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .onAppear {
            model.load(url: videoURL)
        }
        .onDisappear {
            model.stop()
        }
    }
}

// MARK: - Message Bubble

struct MessageBubbleView: View {
    let message: ChatMessage
    @EnvironmentObject var ttsManager: TTSManager

    /// Compute media items once — avoids inline `let` issues in ViewBuilder
    private var mediaItems: [MediaItem] {
        guard message.role == .assistant else { return [] }
        return findEmbeddedMedia(in: message.content)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            if message.role == .assistant || message.role == .system {
                avatar
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 4) {
                // Collapsible thinking section
                if let think = message.thinking, !think.isEmpty {
                    ThinkingBubbleView(content: think)
                }

                if !message.content.isEmpty {
                    if message.role == .assistant {
                        MarkdownContentView(content: message.content)
                            .padding(10)
                            .background(bubbleColor)
                            .cornerRadius(12)

                        // Read-aloud button
                        HStack {
                            Spacer()
                            Button(action: { ttsManager.speak(message.content, messageId: message.id.hashValue) }) {
                                HStack(spacing: 4) {
                                    Image(systemName: ttsManager.isSpeaking && ttsManager.currentTextHash == message.id.hashValue
                                          ? "stop.circle.fill" : "speaker.wave.2")
                                        .font(.caption)
                                    Text(ttsManager.isSpeaking && ttsManager.currentTextHash == message.id.hashValue
                                         ? "停止" : "朗读")
                                        .font(.caption)
                                }
                                .foregroundColor(.blue)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                            }
                            .buttonStyle(.plain)
                        }
                        .padding(.top, 2)

                        // Inline media cards
                        if !mediaItems.isEmpty {
                            ForEach(mediaItems) { item in
                                MediaCardView(item: item)
                            }
                        }
                    } else {
                        Text(message.content)
                            .font(.subheadline)
                            .padding(10)
                            .background(bubbleColor)
                            .cornerRadius(12)
                            .textSelection(.enabled)
                    }
                }
            }
            .frame(maxWidth: message.role == .user ? 280 : .infinity, alignment: message.role == .user ? .trailing : .leading)

            if message.role == .user {
                avatar
            }

            if message.role == .assistant {
                Spacer(minLength: 40)
            }
        }
        .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
    }

    private var bubbleColor: Color {
        switch message.role {
        case .user: return Color.blue.opacity(0.15)
        case .assistant: return Color(.systemBackground)
        case .system: return Color(.systemGray5)
        }
    }

    @ViewBuilder
    private var avatar: some View {
        switch message.role {
        case .assistant:
            Text("🤖")
                .font(.title3)
        case .user:
            Text("👤")
                .font(.title3)
        case .system:
            EmptyView()
        }
    }
}

// MARK: - Markdown Content (iOS 15+ native)

struct MarkdownContentView: View {
    let content: String

    /// Check if content has markdown block syntax (tables, headings, lists, code)
    private var hasBlockSyntax: Bool {
        content.contains("|") ||           // likely a table
        content.contains("```") ||         // code block
        content.contains("\n#") ||         // heading
        content.contains("\n- ") ||        // bullet list
        content.contains("\n* ") ||        // bullet list alt
        content.contains("\n> ")           // blockquote
    }

    private var attributedContent: AttributedString {
        if #available(iOS 15.0, *) {
            if hasBlockSyntax {
                // .full mode: renders tables, headings, code blocks, lists
                // Use  \n for hard line breaks (standard markdown)
                let processed = content.replacingOccurrences(of: "\n", with: "  \n")
                if let md = try? AttributedString(
                    markdown: processed,
                    options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .full)
                ) {
                    return md
                }
            } else {
                // .inlineOnly mode: preserves all line breaks, renders **bold** *italic* etc.
                if let md = try? AttributedString(
                    markdown: content,
                    options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
                ) {
                    return md
                }
            }
        }
        return AttributedString(content)
    }

    var body: some View {
        Text(attributedContent)
            .font(.subheadline)
            .textSelection(.enabled)
    }
}

// MARK: - Thinking Bubble (expand/collapse)

struct ThinkingBubbleView: View {
    let content: String
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button(action: { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }) {
                HStack(spacing: 4) {
                    Text("💭 思考过程")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Spacer()
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
            .buttonStyle(.plain)

            if expanded {
                Text(content)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .italic()
                    .padding(8)
                    .background(Color(.systemGray6))
                    .cornerRadius(8)
            }
        }
        .padding(8)
        .background(Color(.systemGray6).opacity(0.5))
        .cornerRadius(8)
    }
}

// MARK: - Streaming Bubble (dots animation)

struct StreamingBubbleView: View {
    let thinking: String
    let answer: String
    @State private var thinkingExpanded = true  // Auto-expand during streaming

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            Text("🤖").font(.title3)

            VStack(alignment: .leading, spacing: 4) {
                if !thinking.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Button(action: { withAnimation(.easeInOut(duration: 0.2)) { thinkingExpanded.toggle() } }) {
                            HStack(spacing: 4) {
                                Text("💭 思考中...")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                Spacer()
                                Image(systemName: thinkingExpanded ? "chevron.up" : "chevron.down")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .buttonStyle(.plain)

                        if thinkingExpanded {
                            Text(thinking)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .italic()
                                .padding(8)
                                .background(Color(.systemGray6))
                                .cornerRadius(8)
                        }
                    }
                    .padding(8)
                    .background(Color(.systemGray6).opacity(0.5))
                    .cornerRadius(8)
                }

                if answer.isEmpty && thinking.isEmpty {
                    HStack(spacing: 3) {
                        ForEach(0..<3) { i in
                            Circle()
                                .fill(Color.secondary)
                                .frame(width: 6, height: 6)
                                .opacity(0.6)
                        }
                    }
                    .padding(10)
                    .background(Color(.systemBackground))
                    .cornerRadius(12)
                } else if !answer.isEmpty {
                    MarkdownContentView(content: answer)
                        .padding(10)
                        .background(Color(.systemBackground))
                        .cornerRadius(12)
                } else {
                    Text("思考中...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(10)
                        .background(Color(.systemBackground))
                        .cornerRadius(12)
                }
            }

            Spacer(minLength: 40)
        }
    }
}

// MARK: - Media Card

struct MediaCardView: View {
    let item: MediaItem
    @EnvironmentObject var vm: ChatViewModel
    @StateObject private var videoModel = VideoPlayerModel(logPrefix: "[MediaCard]")

    /// Resolve a media URL string against the configured server URL.
    ///
    /// - Relative paths like `static/promotions/video.mp4` are resolved against `serverURL`.
    /// - Absolute URLs whose path starts with `/static/` are REWRITTEN to use `serverURL`
    ///   as the host.  This fixes hardcoded external URLs (e.g. `http://home.hfnjc.net:8890/...`)
    ///   that point to the same static files but are unreachable from the iPad.
    /// - Other absolute URLs are left unchanged.
    private func resolveMediaURL(_ urlString: String) -> URL? {
        guard let serverBase = URL(string: vm.serverURL) else { return URL(string: urlString) }

        // Try absolute URL first
        if let url = URL(string: urlString), url.scheme != nil {
            // Rewrite /static/promotions/… paths to use our server, regardless of host
            if url.path.hasPrefix("/static/") {
                var components = URLComponents(url: serverBase, resolvingAgainstBaseURL: false)
                components?.path = url.path
                if let query = url.query { components?.query = query }
                if let rewritten = components?.url {
                    print("[MediaCard] 🔄 Rewrote: \(url.host ?? "?") → \(serverBase.host ?? "?")")
                    return rewritten
                }
            }
            return url
        }

        // Relative URL — resolve against server base
        let trimmed = urlString.hasPrefix("/") ? String(urlString.dropFirst()) : urlString
        return serverBase.appendingPathComponent(trimmed)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                Image(systemName: item.type == .video ? "play.rectangle" : (item.type == .image ? "photo" : "doc"))
                    .font(.caption)
                    .foregroundColor(.blue)
                Text(item.type == .video ? "视频资料" : (item.type == .image ? "图片资料" : "PDF 文档"))
                    .font(.caption)
                    .foregroundColor(.blue)
                    .fontWeight(.medium)
            }

            if item.type == .video, let url = resolveMediaURL(item.url) {
                // Error state
                if let error = videoModel.errorMessage {
                    VStack(spacing: 6) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.title2)
                            .foregroundColor(.orange)
                        Text("加载失败")
                            .font(.caption)
                            .fontWeight(.medium)
                        Text(error)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 4)
                        Button("🔄 重试") {
                            videoModel.load(url: url)
                        }
                        .font(.caption2)
                        .foregroundColor(.blue)
                    }
                    .frame(height: 180)
                    .frame(maxWidth: .infinity)
                    .background(Color(.systemGray6))
                    .cornerRadius(8)
                } else if videoModel.player != nil {
                    VideoPlayer(player: videoModel.player!)
                        .frame(height: 180)
                        .cornerRadius(8)
                } else {
                    // Downloading — show progress
                    Rectangle()
                        .fill(Color(.systemGray6))
                        .frame(height: 180)
                        .cornerRadius(8)
                        .overlay(
                            VStack(spacing: 8) {
                                ProgressView(value: videoModel.downloadProgress > 0 ? videoModel.downloadProgress : nil)
                                    .tint(.blue)
                                    .frame(width: 100)
                                if videoModel.downloadProgress > 0 {
                                    Text("下载中 \(Int(videoModel.downloadProgress * 100))%")
                                        .font(.caption2)
                                        .foregroundColor(.secondary)
                                } else {
                                    Text("加载中...")
                                        .font(.caption2)
                                        .foregroundColor(.secondary)
                                }
                            }
                        )
                }
            } else if item.type == .image, let url = resolveMediaURL(item.url) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFit()
                            .frame(maxHeight: 200)
                            .cornerRadius(8)
                    case .failure:
                        Text("图片加载失败")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    default:
                        ProgressView()
                    }
                }
            } else {
                Button(action: {
                    if let url = resolveMediaURL(item.url) {
                        UIApplication.shared.open(url)
                    }
                }) {
                    Label("在新窗口打开: \(item.label)", systemImage: "arrow.up.forward.app")
                        .font(.caption)
                }
            }
        }
        .padding(10)
        .background(Color.blue.opacity(0.04))
        .cornerRadius(10)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.blue.opacity(0.15), lineWidth: 1)
        )
        .onAppear {
            if item.type == .video, let url = resolveMediaURL(item.url) {
                videoModel.load(url: url)
            }
        }
        .onDisappear {
            // Keep download alive — don't cancel when scrolling away in a list
            videoModel.pauseOnly()
        }
    }
}

// MARK: - Media Detection

/// Find embedded media URLs (video, image, PDF) in text content.
/// Matches bare URLs and URLs inside markdown links: [text](url)
/// Supports both absolute URLs (http://...) and relative paths (static/promotions/...).
func findEmbeddedMedia(in text: String) -> [MediaItem] {
    var items: [MediaItem] = []
    var seen = Set<String>()
    // Match media URLs — absolute (http(s)://) or relative paths containing / with media extensions
    let pattern = #"(?:https?://|/?[\w\-]+/)[^\s<>"')\]]+?\.(mp4|png|jpe?g|gif|webp|pdf)(\?\S*)?"#
    guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return [] }

    let nsText = text as NSString
    let matches = regex.matches(in: text, range: NSRange(location: 0, length: nsText.length))

    for match in matches {
        var url = nsText.substring(with: match.range)
        // Strip trailing markdown/HTML artifacts
        url = url.trimmingCharacters(in: CharacterSet(charactersIn: ").\"'<>"))
        guard !seen.contains(url) else { continue }
        seen.insert(url)

        let ext = (url as NSString).pathExtension.lowercased()
        let type: MediaItem.MediaType = {
            switch ext {
            case "mp4", "mov", "m4v": return .video
            case "pdf": return .pdf
            default: return .image
            }
        }()
        let label = (url as NSString).lastPathComponent
        items.append(MediaItem(label: label, url: url, type: type))
    }
    return items
}

// MARK: - Chat Input

struct ChatInputView: View {
    @EnvironmentObject var vm: ChatViewModel
    @State private var inputText = ""
    @FocusState private var isFocused: Bool
    @StateObject private var speechRecognizer = SpeechRecognizer()

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            // Microphone button for voice input
            Button(action: toggleMic) {
                Image(systemName: speechRecognizer.isRecording ? "mic.fill" : "mic")
                    .foregroundColor(speechRecognizer.isRecording ? .red : .secondary)
                    .padding(10)
                    .background(
                        Circle()
                            .fill(speechRecognizer.isRecording ? Color.red.opacity(0.15) : Color(.systemGray6))
                    )
                    .scaleEffect(speechRecognizer.isRecording ? 1.2 : 1.0)
                    .animation(.easeInOut(duration: 0.3).repeatForever(autoreverses: true), value: speechRecognizer.isRecording)
            }
            .disabled(!vm.isLoggedIn || !speechRecognizer.isAuthorized)

            TextField(speechRecognizer.isRecording ? "正在聆听..." : "输入您的问题...", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .padding(10)
                .background(Color(.systemGray6))
                .cornerRadius(10)
                .focused($isFocused)
                .lineLimit(1...5)
                .disabled(!vm.isLoggedIn || speechRecognizer.isRecording)

            if vm.isStreaming {
                Button(action: { vm.stopGeneration() }) {
                    Image(systemName: "stop.fill")
                        .foregroundColor(.white)
                        .padding(10)
                        .background(Color.red)
                        .cornerRadius(10)
                }
            } else {
                Button(action: sendAction) {
                    Image(systemName: "arrow.up")
                        .foregroundColor(.white)
                        .padding(10)
                        .background(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? Color.gray : Color.blue)
                        .cornerRadius(10)
                }
                .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !vm.isLoggedIn)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color.clear)
        .onAppear {
            Task { await speechRecognizer.requestAuthorization() }
        }
    }

    private func toggleMic() {
        if speechRecognizer.isRecording {
            speechRecognizer.stopRecording()
            let recognized = speechRecognizer.recognizedText.trimmingCharacters(in: .whitespacesAndNewlines)
            if !recognized.isEmpty {
                inputText = recognized
            }
        } else {
            do {
                try speechRecognizer.startRecording()
            } catch {
                print("[ChatInputView] Mic error: \(error.localizedDescription)")
                // Show error in the text field placeholder area
                inputText = ""
            }
        }
    }

    private func sendAction() {
        // Stop recording if active
        if speechRecognizer.isRecording {
            speechRecognizer.stopRecording()
            inputText = speechRecognizer.recognizedText.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        vm.sendMessage(text)
        inputText = ""
    }
}
