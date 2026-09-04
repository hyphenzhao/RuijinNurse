import Foundation
import SwiftUI
import UIKit

/// Central state for the chat application.
/// Handles API communication, authentication, and chat state.
@MainActor
class ChatViewModel: ObservableObject {

    // MARK: - Server config
    @Published var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: "ruijin_server_url") }
    }
    @Published var connectionState: ConnectionState = .disconnected

    // MARK: - Setup
    @Published var needsSetup: Bool

    // MARK: - Auth
    @Published var isLoggedIn = false
    @Published var username = ""
    private var jwtAccess: String?
    private var jwtRefresh: String?

    // MARK: - Agents & Model
    @Published var agents: [Agent] = []
    @Published var selectedModelKey: String = ""
    @Published var modelsLoaded = false
    @Published var isLoadingAgents = false
    @Published var availableModels: [ModelChoice] = []
    @Published var defaultModelKey: String = "model:qwen3:4b"

    // MARK: - Chat
    @Published var messages: [ChatMessage] = []
    @Published var isStreaming = false {
        didSet {
            // Keep the screen awake while streaming: the model's "thinking" phase
            // sends no data for minutes, and if the iPad auto-locks the app gets
            // backgrounded and the stream dies.
            UIApplication.shared.isIdleTimerDisabled = isStreaming
        }
    }
    @Published var streamingThinking = ""
    @Published var streamingAnswer = ""
    @Published var showIntroVideo = true   // Show intro video on launch (matches web)
    @Published var autoReadEnabled = true   // Auto-read enabled by default
    /// Message ids appended during a failed stream (partial answer + error) — removed on retry.
    private var failedMessageIDs: [UUID] = []
    /// Question text of the stream currently in flight — attached to the error message on failure.
    private var currentQuestion: String?
    /// Consecutive auto-retry attempts for the current question (max 2).
    private var autoRetryCount = 0

    /// Set by AppDelegate — used for auto-reading responses after generation completes
    weak var ttsManager: TTSManager?

    private let sseClient = SSEClient()

    // MARK: - Init
    init() {
        let savedURL = UserDefaults.standard.string(forKey: "ruijin_server_url") ?? "http://localhost:8000"
        let savedAccess = Keychain.get("jwt_access")
        let savedRefresh = Keychain.get("jwt_refresh")
        let loggedIn = savedAccess != nil

        self.serverURL = savedURL
        self.jwtAccess = savedAccess
        self.jwtRefresh =  savedRefresh
        self.isLoggedIn = loggedIn
        self.needsSetup = savedURL == "http://localhost:8000" || !loggedIn
        setupSSECallbacks()
        if !needsSetup {
            // Show intro video (matches web version's 入院介绍视频)
            showIntroVideo = true
            // Welcome text (same as web: data-intro-welcome)
            addSystemMessage("欢迎来到瑞金医院功能神外智能宣讲。请先观看入院介绍视频，如有问题可在下方输入提问。")
            Task {
                await checkConnection()
                if connectionState == .connected {
                    async let _agents: () = loadAgents()
                    async let _models: () = loadModels()
                    _ = await (_agents, _models)
                }
            }
        }
    }

    // MARK: - Connection
    func checkConnection() async {
        connectionState = .connecting
        do {
            let resp: HealthResponse = try await apiGet("/api/v1/health/", auth: false)
            connectionState = resp.ok ? .connected : .error("服务器响应异常")
            if resp.ok && !isLoggedIn {
                addSystemMessage("✅ 已连接到服务器，请登录后开始使用。")
            }
        } catch {
            connectionState = .error("无法连接: \(error.localizedDescription)")
            addSystemMessage("⚠️ 无法连接服务器: \(error.localizedDescription)")
        }
    }

    // MARK: - Auth
    func login(user: String, pass: String) async -> String? {
        do {
            let body = try JSONEncoder().encode(["username": user, "password": pass])
            let resp: LoginResponse = try await apiPost("/api/v1/auth/login/", body: body, auth: false)
            jwtAccess = resp.access
            jwtRefresh = resp.refresh
            Keychain.set(resp.access, forKey: "jwt_access")
            Keychain.set(resp.refresh, forKey: "jwt_refresh")
            isLoggedIn = true
            username = resp.user?.username ?? user
            connectionState = .connected
            async let _agents: () = loadAgents()
            async let _models: () = loadModels()
            _ = await (_agents, _models)
            addSystemMessage("✅ 登录成功，欢迎 \(username)")
            return nil
        } catch {
            let msg = "登录失败: \(error.localizedDescription)"
            addSystemMessage("❌ \(msg)")
            return msg
        }
    }

    func logout() {
        jwtAccess = nil
        jwtRefresh = nil
        Keychain.remove("jwt_access")
        Keychain.remove("jwt_refresh")
        isLoggedIn = false
        username = ""
        agents = []
        availableModels = []
        selectedModelKey = ""
        needsSetup = true
        messages = []
        showIntroVideo = true
        autoReadEnabled = false
        ttsManager?.stop()
        addSystemMessage("👋 已登出")
    }

    // MARK: - Agents & Models

    func loadAgents() async {
        isLoadingAgents = true
        do {
            let list: [Agent] = try await apiGet("/api/v1/agents/")
            agents = list.filter { $0.isActive ?? true }
            if let first = agents.first {
                selectedModelKey = "agent:\(first.slug)"
            }
            modelsLoaded = true
            if agents.isEmpty {
                addSystemMessage("ℹ️ 服务器暂无可用智能体，使用默认模型。")
            }
        } catch {
            addSystemMessage("⚠️ 加载智能体列表失败: \(error.localizedDescription)")
        }
        isLoadingAgents = false
    }

    /// Fetch available Ollama models from the server configuration
    func loadModels() async {
        do {
            // Get server Ollama config first
            let settings: SettingsResponse = try await apiGet("/api/v1/settings/")
            // Fetch available models from that Ollama instance
            let resp: ModelsListResponse = try await apiGet(
                "/api/v1/models/?host=\(settings.ollamaHost)&port=\(settings.ollamaPort)"
            )
            // Use server-provided choices (key/label pairs)
            if let choices = resp.choices, !choices.isEmpty {
                availableModels = choices
                if agents.isEmpty {
                    defaultModelKey = choices[0].key
                }
            } else if let models = resp.models, !models.isEmpty {
                // Fallback: construct choices from model name list
                availableModels = models.map { ModelChoice(key: "model:\($0)", label: $0) }
                defaultModelKey = availableModels[0].key
            }
        } catch {
            addSystemMessage("⚠️ 加载模型列表失败: \(error.localizedDescription)")
        }
    }

    /// URL for the Unity WebGL content served by Django
    var unityWebGLURL: URL? {
        guard let base = URL(string: serverURL) else { return nil }
        return base.appendingPathComponent("static/promotions/unity/Build/index.html")
    }

    /// URL for the intro video served by Django
    var introVideoURL: URL? {
        guard let base = URL(string: serverURL) else { return nil }
        return base.appendingPathComponent("static/promotions/hospital_notification.mp4")
    }

    // MARK: - Chat
    func sendMessage(_ text: String, isAutoRetry: Bool = false) {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        guard isLoggedIn, let token = jwtAccess else {
            addSystemMessage("⚠️ 请先登录")
            return
        }
        if !isAutoRetry {
            autoRetryCount = 0
        }

        let userMsg = ChatMessage(role: .user, content: text, thinking: nil, timestamp: Date())
        messages.append(userMsg)

        currentQuestion = text
        isStreaming = true
        streamingThinking = ""
        streamingAnswer = ""
        showIntroVideo = false  // Hide intro video after user starts chatting

        let model = selectedModelKey.isEmpty ? defaultModelKey : selectedModelKey
        print("[ChatViewModel] ✉️ 发送问题 (model=\(model)): \(text.prefix(50))\(text.count > 50 ? "…" : "")")
        let body: [String: Any] = ["text": text, "model": model]
        guard let bodyData = try? JSONSerialization.data(withJSONObject: body) else { return }

        let headers = ["Authorization": "Bearer \(token)"]
        guard let url = URL(string: "\(serverURL)/api/v1/chat/stream/") else { return }

        sseClient.connect(url: url, headers: headers, body: bodyData)
    }

    func stopGeneration() {
        sseClient.disconnect()
        ttsManager?.stop()
        if !streamingAnswer.isEmpty || !streamingThinking.isEmpty {
            let finalContent = streamingAnswer.isEmpty ? "（已停止生成）" : streamingAnswer
            let msg = ChatMessage(role: .assistant, content: finalContent, thinking: streamingThinking.isEmpty ? nil : streamingThinking, timestamp: Date())
            messages.append(msg)
        }
        isStreaming = false
        streamingAnswer = ""
        streamingThinking = ""
    }

    /// Re-send a question after its stream failed.
    /// Removes the partial answer + error message first, then restarts generation.
    func retryFailedQuestion(_ question: String) {
        guard !isStreaming else { return }
        sseClient.disconnect()
        ttsManager?.stop()
        messages.removeAll { failedMessageIDs.contains($0.id) }
        failedMessageIDs = []
        isStreaming = false
        streamingAnswer = ""
        streamingThinking = ""
        autoRetryCount = 0
        addSystemMessage("🔄 已重新发起提问")
        sendMessage(question)
    }

    // MARK: - Private
    private func setupSSECallbacks() {
        sseClient.onThinking = { [weak self] text in
            self?.streamingThinking += text
        }
        sseClient.onDelta = { [weak self] text in
            self?.streamingAnswer += text
        }
        sseClient.onDone = { [weak self] final, cleanDone in
            guard let self = self else { return }
            let content = final.isEmpty ? self.streamingAnswer : final
            let think = self.streamingThinking.isEmpty ? nil : self.streamingThinking
            let question = self.currentQuestion
            self.isStreaming = false
            self.streamingAnswer = ""
            self.streamingThinking = ""
            self.currentQuestion = nil
            // A completion invalidates any pending manual-retry state
            self.failedMessageIDs = []

            let emptyAnswer = content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            let needsAutoRetry = (emptyAnswer || !cleanDone) && question != nil && self.autoRetryCount < 2

            // Show whatever content we did receive (even partial)
            if !emptyAnswer {
                let msg = ChatMessage(role: .assistant, content: content, thinking: think, timestamp: Date())
                self.messages.append(msg)
            }

            if needsAutoRetry, let question = question {
                self.autoRetryCount += 1
                let reason = emptyAnswer ? "本次未收到回答内容" : "连接在完成前中断"
                print("[ChatViewModel] 🔁 自动重试 \(self.autoRetryCount)/2（\(reason)）")
                self.addSystemMessage("⚠️ \(reason)，自动重试（第 \(self.autoRetryCount)/2 次）…")
                self.sendMessage(question, isAutoRetry: true)
                return
            }

            self.autoRetryCount = 0
            if emptyAnswer {
                self.addSystemMessage("⚠️ 模型未返回内容，请点击重试按钮或重新提问。")
            } else {
                // Auto-read: speak the entire response once generation is complete
                if self.autoReadEnabled, let tts = self.ttsManager {
                    tts.speak(content)
                }
            }
        }
        sseClient.onError = { [weak self] error in
            guard let self = self else { return }
            print("[ChatViewModel] ❌ 流错误: \(error)")
            let partial = self.streamingAnswer
            let think = self.streamingThinking.isEmpty ? nil : self.streamingThinking
            let question = self.currentQuestion
            self.isStreaming = false
            self.streamingAnswer = ""
            self.streamingThinking = ""
            self.currentQuestion = nil
            self.ttsManager?.stop()
            self.failedMessageIDs = []

            // Auto-recover: re-ask the same question (like EventSource reconnect on the web)
            if let question = question, self.autoRetryCount < 2 {
                self.autoRetryCount += 1
                print("[ChatViewModel] 🔁 自动重试 \(self.autoRetryCount)/2（错误: \(error)）")
                self.addSystemMessage("⚠️ 连接中断，自动重试（第 \(self.autoRetryCount)/2 次）…")
                self.sendMessage(question, isAutoRetry: true)
                return
            }

            // Give up after 2 attempts — show partial + error + manual retry button
            self.autoRetryCount = 0
            if !partial.isEmpty {
                let msg = ChatMessage(role: .assistant, content: partial, thinking: think, timestamp: Date())
                self.messages.append(msg)
                self.failedMessageIDs.append(msg.id)
            }
            var errMsg = ChatMessage(role: .system, content: "❌ 错误: \(error)", thinking: nil, timestamp: Date())
            errMsg.retryQuestion = question  // attach the question for the retry button
            self.messages.append(errMsg)
            self.failedMessageIDs.append(errMsg.id)
        }
    }

    private func addSystemMessage(_ text: String) {
        messages.append(ChatMessage(role: .system, content: text, thinking: nil, timestamp: Date()))
    }

    // MARK: - URLSession (bypass system proxy for LAN access)
    private lazy var urlSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]  // Bypass WPAD/PAC proxy
        config.timeoutIntervalForRequest = 10
        return URLSession(configuration: config)
    }()

    // MARK: - Token Refresh

    /// Try to refresh the JWT access token. Returns true on success, false on failure (calls logout).
    private func refreshTokenIfNeeded() async -> Bool {
        guard let refresh = jwtRefresh else { return false }
        do {
            let body = try JSONEncoder().encode(["refresh": refresh])
            let resp: RefreshResponse = try await apiPost("/api/v1/auth/refresh/", body: body, auth: false, isRetry: true)
            jwtAccess = resp.access
            Keychain.set(resp.access, forKey: "jwt_access")
            return true
        } catch {
            await MainActor.run { logout() }
            addSystemMessage("⚠️ 会话已过期，请重新登录")
            return false
        }
    }

    // MARK: - API Helpers
    private func apiGet<T: Decodable>(_ path: String, auth: Bool = true, isRetry: Bool = false) async throws -> T {
        var req = URLRequest(url: URL(string: "\(serverURL)\(path)")!)
        req.httpMethod = "GET"
        if auth, let token = jwtAccess {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        req.timeoutInterval = 10
        let (data, response) = try await urlSession.data(for: req)

        // Auto-refresh token on 401 (only once to avoid infinite loops)
        if let http = response as? HTTPURLResponse, http.statusCode == 401, auth, !isRetry {
            let refreshed = await refreshTokenIfNeeded()
            if refreshed {
                return try await apiGet(path, auth: auth, isRetry: true)
            }
            throw NSError(domain: "API", code: 401,
                userInfo: [NSLocalizedDescriptionKey: "会话已过期，请重新登录"])
        }

        try checkHTTPStatus(response, data: data)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func apiPost<T: Decodable>(_ path: String, body: Data, auth: Bool = true, isRetry: Bool = false) async throws -> T {
        var req = URLRequest(url: URL(string: "\(serverURL)\(path)")!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if auth, let token = jwtAccess {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        req.httpBody = body
        req.timeoutInterval = 10
        let (data, response) = try await urlSession.data(for: req)

        // Auto-refresh token on 401 (only once to avoid infinite loops)
        if let http = response as? HTTPURLResponse, http.statusCode == 401, auth, !isRetry {
            let refreshed = await refreshTokenIfNeeded()
            if refreshed {
                return try await apiPost(path, body: body, auth: auth, isRetry: true)
            }
            throw NSError(domain: "API", code: 401,
                userInfo: [NSLocalizedDescriptionKey: "会话已过期，请重新登录"])
        }

        try checkHTTPStatus(response, data: data)
        return try JSONDecoder().decode(T.self, from: data)
    }

    /// Throw a readable error for non-2xx responses
    private func checkHTTPStatus(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            // Try to extract DRF detail message
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                throw NSError(domain: "API", code: http.statusCode,
                    userInfo: [NSLocalizedDescriptionKey: detail])
            }
            throw NSError(domain: "API", code: http.statusCode,
                userInfo: [NSLocalizedDescriptionKey: "服务器返回 \(http.statusCode)"])
        }
    }
}

// MARK: - Simple Keychain helper (UserDefaults-based for simplicity)
private struct Keychain {
    static func get(_ key: String) -> String? {
        UserDefaults.standard.string(forKey: "ruijin_\(key)")
    }
    static func set(_ value: String, forKey key: String) {
        UserDefaults.standard.set(value, forKey: "ruijin_\(key)")
    }
    static func remove(_ key: String) {
        UserDefaults.standard.removeObject(forKey: "ruijin_\(key)")
    }
}
