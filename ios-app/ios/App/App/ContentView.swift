import SwiftUI
import WebKit

struct ContentView: View {
    @EnvironmentObject var vm: ChatViewModel
    @State private var showConfig = false
    @State private var showLogin = false

    var body: some View {
        if vm.needsSetup {
            // First launch — show setup screen
            SetupView()
        } else {
            // Main chat interface
            mainView
        }
    }

    // MARK: - Main Chat View

    var mainView: some View {
        NavigationSplitView {
            AgentListView()
        } content: {
            VStack(spacing: 0) {
                connectionBanner
                ChatMessagesView()
                ChatInputView()
            }
        } detail: {
            mediaPanel
        }
        .navigationTitle("")
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                HStack(spacing: 8) {
                    Circle()
                        .fill(connectionColor)
                        .frame(width: 10, height: 10)

                    Button {
                        showLogin = true
                    } label: {
                        Image(systemName: vm.isLoggedIn ? "person.circle.fill" : "person.circle")
                    }

                    Button {
                        showConfig = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }
            ToolbarItem(placement: .navigationBarLeading) {
                Text("🏥 瑞金神外护理助手")
                    .font(.headline)
            }
        }
        .sheet(isPresented: $showConfig) {
            ServerConfigView()
        }
        .sheet(isPresented: $showLogin) {
            LoginView()
        }
    }

    // MARK: - Media Panel (Unity WebGL)

    @ViewBuilder
    private var mediaPanel: some View {
        VStack(spacing: 0) {
            Text("📺 参考内容")
                .font(.headline)
                .padding(.top, 8)
                .padding(.bottom, 8)

            if vm.connectionState == .connected, let unityURL = vm.unityWebGLURL {
                UnityWebView(url: unityURL)
                    .cornerRadius(12)
                    .padding(8)
            } else {
                Rectangle()
                    .fill(Color(.systemGray6))
                    .cornerRadius(12)
                    .overlay(
                        VStack(spacing: 8) {
                            Image(systemName: "play.rectangle")
                                .font(.largeTitle)
                                .foregroundColor(.secondary)
                            Text("连接服务器后自动加载内容")
                                .foregroundColor(.secondary)
                                .font(.caption)
                        }
                    )
                    .padding(8)
            }
        }
    }

    // MARK: - Connection Banner

    @ViewBuilder
    private var connectionBanner: some View {
        if vm.connectionState != .connected {
            bannerText
        }
    }

    @ViewBuilder
    private var bannerText: some View {
        switch vm.connectionState {
        case .disconnected:
            banner("⚠️ 未连接服务器 — 请在设置中配置", color: .yellow)
        case .connecting:
            banner("⏳ 正在连接...", color: .yellow)
        case .error(let msg):
            banner(msg, color: .red)
        default:
            EmptyView()
        }
    }

    private func banner(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.caption)
            .foregroundColor(color == .yellow ? .black : .white)
            .padding(8)
            .frame(maxWidth: .infinity)
            .background(color.opacity(color == .yellow ? 0.25 : 0.9))
    }

    private var connectionColor: Color {
        switch vm.connectionState {
        case .connected: return .green
        case .connecting: return .yellow
        case .disconnected: return .gray
        case .error: return .red
        }
    }
}

// MARK: - Setup View (First Launch)

struct SetupView: View {
    @EnvironmentObject var vm: ChatViewModel
    @State private var urlInput: String = ""
    @State private var username: String = ""
    @State private var password: String = ""
    @State private var statusMsg: String = ""
    @State private var isConnecting = false
    @State private var isLoggingIn = false

    var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                // Header
                VStack(spacing: 8) {
                    Text("🏥")
                        .font(.system(size: 56))
                    Text("瑞金神外护理助手")
                        .font(.title)
                        .fontWeight(.bold)
                    Text("Ruijin Nurse")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
                .padding(.top, 60)
                .padding(.bottom, 30)

                // Form
                Form {
                    Section("服务器地址") {
                        TextField("http://192.168.1.100:8000", text: $urlInput)
                            .keyboardType(.URL)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                        Text("请输入 RuijinNurse 服务器的完整地址")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    Section("登录信息") {
                        TextField("用户名", text: $username)
                            .autocapitalization(.none)
                        SecureField("密码", text: $password)
                    }

                    if !statusMsg.isEmpty {
                        Section {
                            Text(statusMsg)
                                .font(.caption)
                                .foregroundColor(statusMsg.hasPrefix("✅") ? .green : .red)
                        }
                    }

                    Section {
                        Button(action: connectAndLogin) {
                            HStack {
                                if isConnecting || isLoggingIn {
                                    ProgressView()
                                }
                                Text("连接并登录")
                                    .frame(maxWidth: .infinity)
                            }
                        }
                        .disabled(urlInput.isEmpty || username.isEmpty || password.isEmpty || isConnecting)
                    }
                }
            }
            .navigationBarHidden(true)
            .onAppear {
                urlInput = vm.serverURL
            }
        }
        .navigationViewStyle(.stack)
    }

    private func connectAndLogin() {
        isConnecting = true
        statusMsg = ""
        let url = urlInput.trimmingCharacters(in: .init(charactersIn: "/"))
        vm.serverURL = url

        Task {
            // Step 1: Check connection
            await vm.checkConnection()
            guard vm.connectionState == .connected else {
                // Show the REAL error from ChatViewModel
                switch vm.connectionState {
                case .error(let msg):
                    statusMsg = "❌ \(msg)"
                case .disconnected:
                    statusMsg = "❌ 无法连接服务器，请检查地址和网络"
                default:
                    statusMsg = "❌ 连接失败"
                }
                isConnecting = false
                return
            }
            isConnecting = false

            // Step 2: Login
            isLoggingIn = true
            let err = await vm.login(user: username, pass: password)
            isLoggingIn = false

            if let err = err {
                statusMsg = "❌ \(err)"
            } else {
                statusMsg = "✅ 连接成功！"
                vm.needsSetup = false
            }
        }
    }
}

// MARK: - Unity WebGL WKWebView Wrapper

struct UnityWebView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        config.defaultWebpagePreferences = prefs

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.scrollView.isScrollEnabled = true
        webView.isOpaque = false
        webView.backgroundColor = .black
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}
}
