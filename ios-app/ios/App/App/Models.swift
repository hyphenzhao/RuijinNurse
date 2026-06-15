import Foundation

// MARK: - API Response Types

struct LoginResponse: Codable {
    let access: String
    let refresh: String
    let user: UserInfo?
}

struct UserInfo: Codable {
    let id: Int
    let username: String
    let isStaff: Bool?

    enum CodingKeys: String, CodingKey {
        case id, username
        case isStaff = "is_staff"
    }
}

struct Agent: Identifiable, Codable {
    let id: Int
    let name: String
    let slug: String
    let ollamaHost: String?
    let ollamaPort: Int?
    let ollamaModel: String?
    let systemPrompt: String?
    let knowledge: String?
    let isActive: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name, slug
        case ollamaHost = "ollama_host"
        case ollamaPort = "ollama_port"
        case ollamaModel = "ollama_model"
        case systemPrompt = "system_prompt"
        case knowledge
        case isActive = "is_active"
    }
}

struct ModelChoice: Codable {
    let key: String
    let label: String
}

struct HealthResponse: Codable {
    let ok: Bool
    let serverTime: String?
    let authenticated: Bool?
    let version: String?

    enum CodingKeys: String, CodingKey {
        case ok
        case serverTime = "server_time"
        case authenticated
        case version
    }
}

// MARK: - Chat Message

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: MessageRole
    let content: String
    let thinking: String?
    let timestamp: Date

    enum MessageRole {
        case user, assistant, system
    }
}

// MARK: - App State

enum ConnectionState: Equatable {
    case disconnected
    case connecting
    case connected
    case error(String)
}

// MARK: - Token Refresh

struct RefreshResponse: Codable {
    let access: String
}

// MARK: - Settings

struct SettingsResponse: Codable {
    let ollamaHost: String
    let ollamaPort: Int

    enum CodingKeys: String, CodingKey {
        case ollamaHost = "ollama_host"
        case ollamaPort = "ollama_port"
    }
}

struct ModelsListResponse: Codable {
    let ok: Bool?
    let models: [String]?
    let choices: [ModelChoice]?
    let error: String?
}

// MARK: - Media Item (for inline media cards in chat)

struct MediaItem: Identifiable {
    let id = UUID()
    let label: String
    let url: String
    let type: MediaType

    enum MediaType: String {
        case video, image, pdf
    }
}

// MARK: - SSE Event

enum SSEEvent {
    case status([String: Any])
    case thinking(String)
    case delta(String)
    case done(String)
    case error(String)

    var eventName: String {
        switch self {
        case .status: return "status"
        case .thinking: return "thinking"
        case .delta: return "delta"
        case .done: return "done"
        case .error: return "error"
        }
    }
}
