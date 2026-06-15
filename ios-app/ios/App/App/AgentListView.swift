import SwiftUI

struct AgentListView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        List {
            Section("🤖 智能体") {
                if vm.isLoadingAgents {
                    HStack {
                        ProgressView()
                            .scaleEffect(0.7)
                        Text("加载中...")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(.vertical, 4)
                } else if vm.agents.isEmpty {
                    Text(vm.modelsLoaded ? "暂无可用智能体" : "连接服务器后自动加载")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                } else {
                    ForEach(vm.agents) { agent in
                        Button {
                            vm.selectedModelKey = "agent:\(agent.slug)"
                            vm.messages.append(
                                ChatMessage(role: .system,
                                            content: "🤖 已切换到：\(agent.name)",
                                            thinking: nil,
                                            timestamp: Date())
                            )
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(agent.name)
                                        .font(.subheadline)
                                        .fontWeight(.medium)
                                    if vm.selectedModelKey == "agent:\(agent.slug)" {
                                        Text("当前使用中")
                                            .font(.caption2)
                                            .foregroundColor(.blue)
                                    } else {
                                        Text(agent.ollamaModel ?? "")
                                            .font(.caption2)
                                            .foregroundColor(.secondary)
                                    }
                                }
                                Spacer()
                                if vm.selectedModelKey == "agent:\(agent.slug)" {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundColor(.blue)
                                }
                            }
                            .contentShape(Rectangle())
                            .padding(.vertical, 2)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Standalone model selection
            if !vm.availableModels.isEmpty {
                Section("📦 模型") {
                    ForEach(vm.availableModels) { choice in
                        Button {
                            vm.selectedModelKey = choice.key
                            vm.messages.append(
                                ChatMessage(role: .system,
                                            content: "📦 已切换到：\(choice.label)",
                                            thinking: nil,
                                            timestamp: Date())
                            )
                        } label: {
                            HStack {
                                Text(choice.label)
                                    .font(.subheadline)
                                Spacer()
                                if vm.selectedModelKey == choice.key {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundColor(.blue)
                                }
                            }
                            .contentShape(Rectangle())
                            .padding(.vertical, 2)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("智能体")
    }
}
