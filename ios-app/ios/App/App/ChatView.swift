import SwiftUI
import AVKit

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
            .background(Color(.systemGroupedBackground))
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

// MARK: - Intro Video

struct IntroVideoView: View {
    let videoURL: URL
    @State private var player = AVPlayer()

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                Text("🤖").font(.title3)
                Text("📺 入院介绍视频")
                    .font(.subheadline)
                    .fontWeight(.medium)
            }

            VideoPlayer(player: player)
                .frame(height: 220)
                .cornerRadius(12)

            Text("欢迎来到瑞金医院功能神外智能宣讲。请先观看入院介绍视频，如有问题可在下方输入提问。")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(12)
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .onAppear {
            player = AVPlayer(url: videoURL)
            player.play()
        }
        .onDisappear {
            player.pause()
        }
    }
}

// MARK: - Message Bubble

struct MessageBubbleView: View {
    let message: ChatMessage

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
                    } else {
                        Text(message.content)
                            .font(.subheadline)
                            .padding(10)
                            .background(bubbleColor)
                            .cornerRadius(12)
                            .textSelection(.enabled)
                    }

                    // Inline media cards for AI responses
                    if message.role == .assistant {
                        let items = findEmbeddedMedia(in: message.content)
                        ForEach(items) { item in
                            MediaCardView(item: item)
                        }
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

    /// Minimal preprocessing: replace \n with "  \n" (two spaces + newline)
    /// This creates hard line breaks in standard markdown while keeping
    /// block syntax (tables |, headings #, lists -, code blocks ```) intact.
    /// Simple string replacement — no regex, no line analysis, no garbled text risk.
    private var preprocessed: String {
        content.replacingOccurrences(of: "\n", with: "  \n")
    }

    private var attributedContent: AttributedString {
        if #available(iOS 15.0, *) {
            // .full renders tables, headings, lists, code blocks, bold, italic, links
            if let md = try? AttributedString(
                markdown: preprocessed,
                options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .full)
            ) {
                return md
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

            if item.type == .video, let url = URL(string: item.url) {
                VideoPlayer(player: AVPlayer(url: url))
                    .frame(height: 180)
                    .cornerRadius(8)
            } else if item.type == .image, let url = URL(string: item.url) {
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
                    if let url = URL(string: item.url) {
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
    }
}

// MARK: - Media Detection

/// Find embedded media URLs (video, image, PDF) in text content.
/// Matches bare URLs and URLs inside markdown links: [text](url)
func findEmbeddedMedia(in text: String) -> [MediaItem] {
    var items: [MediaItem] = []
    var seen = Set<String>()
    // Match http(s)://... ending with media extensions, optionally inside markdown link syntax
    let pattern = #"https?://[^\s<>"')\]]+?\.(mp4|png|jpe?g|gif|webp|pdf)(\?\S*)?"#
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

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("输入您的问题...", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .padding(10)
                .background(Color(.systemGray6))
                .cornerRadius(10)
                .focused($isFocused)
                .lineLimit(1...5)
                .disabled(!vm.isLoggedIn)

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
        .background(Color(.systemBackground))
    }

    private func sendAction() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        vm.sendMessage(text)
        inputText = ""
    }
}
