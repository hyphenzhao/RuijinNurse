# iOS App 更新需求 — 智能体知识图谱系统适配

> 创建日期: 2026-06-16
> 状态: 待实现

---

## 背景

Django 后端新增了智能体知识图谱系统，包含：
- 新版智能体（`agent_type='kg'`）绑定知识图谱，支持流程指南卡 + 专业知识 + 文献检索
- 旧版智能体（`agent_type='legacy'`）保持原有行为
- 智能体「设为默认」功能

iOS 端需要适配以下变更。

---

## 1. 数据模型更新

### 文件: `ios-app/ios/App/App/Models.swift`

`Agent` struct 新增两个字段：

```swift
struct Agent: Identifiable, Codable {
    // ... 现有字段保持不变 ...

    let agentType: String?     // "legacy" 或 "kg"
    let isDefault: Bool?       // 是否为默认智能体

    enum CodingKeys: String, CodingKey {
        // ... 现有 coding keys ...
        case agentType = "agent_type"
        case isDefault = "is_default"
    }
}
```

---

## 2. 默认智能体自动选中

### 文件: `ios-app/ios/App/App/ChatViewModel.swift`

#### 2.1 `loadAgents()` 方法

当前逻辑：选中列表第一个 agent 作为默认。

新逻辑：
```swift
func loadAgents() async {
    // ... 现有加载逻辑 ...
    let list: [Agent] = try await apiGet("/api/v1/agents/")
    agents = list.filter { $0.isActive ?? true }

    // ⭐ 优先选中默认智能体
    if let defaultAgent = agents.first(where: { $0.isDefault == true }) {
        selectedModelKey = "agent:\(defaultAgent.slug)"
    } else if let first = agents.first {
        selectedModelKey = "agent:\(first.slug)"
    }
    // ...
}
```

#### 2.2 `logout()` 方法

重置时 `selectedModelKey = ""` 保持不变。

---

## 3. 智能体列表 UI

### 文件: `ios-app/ios/App/App/AgentListView.swift`

#### 3.1 默认智能体标识

在默认智能体的行上显示 ⭐ 标识：

```swift
ForEach(vm.agents) { agent in
    Button {
        vm.selectedModelKey = "agent:\(agent.slug)"
        // ...
    } label: {
        HStack {
            if agent.isDefault == true {
                Image(systemName: "star.fill")
                    .foregroundColor(.yellow)
                    .font(.caption)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(agent.name)
                    .font(.subheadline)
                    .fontWeight(.medium)
                // ...
            }
            // ...
        }
    }
}
```

#### 3.2 设为默认交互（可选）

两种方案任选其一：

**方案 A（推荐）**：长按弹出上下文菜单
```swift
.contextMenu {
    if agent.isDefault != true {
        Button {
            Task { await vm.setDefaultAgent(slug: agent.slug) }
        } label: {
            Label("设为默认", systemImage: "star")
        }
    }
}
```

**方案 B**：左滑操作
```swift
.swipeActions(edge: .leading) {
    if agent.isDefault != true {
        Button {
            Task { await vm.setDefaultAgent(slug: agent.slug) }
        } label: {
            Label("设为默认", systemImage: "star")
        }
        .tint(.yellow)
    }
}
```

---

## 4. 新增 API 调用

### 文件: `ios-app/ios/App/App/ChatViewModel.swift`

```swift
/// 将指定智能体设为默认
func setDefaultAgent(slug: String) async {
    do {
        let _: [String: String] = try await apiPost(
            "/api/v1/agents/\(slug)/default/",
            body: Data(),
            auth: true
        )
        // 刷新列表
        await loadAgents()
        addSystemMessage("⭐ 已将 \(slug) 设为默认智能体")
    } catch {
        addSystemMessage("⚠️ 设置默认智能体失败: \(error.localizedDescription)")
    }
}
```

> 注意：`POST /api/v1/agents/{slug}/default/` 的请求体可以为空 `{}`

---

## 5. 兼容性说明

- **旧版 Agent** 的 `agentType` 为 `"legacy"` 或 `null`，行为与现在完全一致
- **新版 Agent** 的 `agentType` 为 `"kg"`，iOS 端无需感知其知识图谱检索逻辑（由后端透明处理）
- `isDefault` 为 `null` 时视为 `false`
- `selectedModelKey` 的格式（`"agent:<slug>"` / `"model:<name>"`）**不变**

---

## 6. API 端点变更汇总

| 端点 | 方法 | 变更 |
|------|------|------|
| `GET /api/v1/agents/` | GET | 返回值新增 `agent_type`, `is_default` 字段 |
| `POST /api/v1/agents/{slug}/default/` | POST | **新增** — 设为默认智能体 |
