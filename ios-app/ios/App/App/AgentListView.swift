import SwiftUI

struct AgentListView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        List {
            Section("🤖 智能体") {
                if vm.agents.isEmpty {
                    Text("连接服务器后自动加载")
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
        }
        .listStyle(.sidebar)
        .navigationTitle("智能体")
    }
}
