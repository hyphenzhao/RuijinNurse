import SwiftUI

// MARK: - Messages List

struct ChatMessagesView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
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

// MARK: - Message Bubble

struct MessageBubbleView: View {
    let message: ChatMessage

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            if message.role == .assistant || message.role == .system {
                avatar
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 4) {
                if let think = message.thinking, !think.isEmpty {
                    Text(think)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .italic()
                        .padding(8)
                        .background(Color(.systemGray6))
                        .cornerRadius(8)
                }
                if !message.content.isEmpty {
                    Text(message.content)
                        .font(.subheadline)
                        .padding(10)
                        .background(bubbleColor)
                        .cornerRadius(12)
                        .textSelection(.enabled)
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

// MARK: - Streaming Bubble (dots animation)

struct StreamingBubbleView: View {
    let thinking: String
    let answer: String

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            Text("🤖").font(.title3)

            VStack(alignment: .leading, spacing: 4) {
                if !thinking.isEmpty {
                    Text(thinking)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .italic()
                        .padding(8)
                        .background(Color(.systemGray6))
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
                    Text(answer)
                        .font(.subheadline)
                        .padding(10)
                        .background(Color(.systemBackground))
                        .cornerRadius(12)
                        .textSelection(.enabled)
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
