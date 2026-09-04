import SwiftUI
import WebKit

struct ContentView: View {
    @EnvironmentObject var vm: ChatViewModel
    @State private var showConfig = false
    @State private var showLogin = false
    @State private var sidebarCollapsed = true
    @State private var unityLoaded = false   // Hide 3D placeholder once Unity is ready

    private let digitalHumanWidth: CGFloat = 350
    private let sidebarWidth: CGFloat = 260

    var body: some View {
        if vm.needsSetup {
            SetupView()
        } else {
            mainView
        }
    }

    // MARK: - Main Chat View (ZStack-based layered layout)

    var mainView: some View {
        ZStack(alignment: .topLeading) {

            // ═══ Layer 0: Full-screen background ═══
            backgroundLayer

            // ═══ Layer 1: Content stack ═══
            VStack(spacing: 0) {
                topBar
                connectionBanner
                mainContentArea
            }

            // ═══ Layer 2: Sidebar overlay ═══
            sidebarOverlay
        }
        .animation(.easeOut(duration: 0.25), value: sidebarCollapsed)
        .sheet(isPresented: $showConfig) { ServerConfigView() }
        .sheet(isPresented: $showLogin) { LoginView() }
    }

    // MARK: - Layer 0: Background

    @ViewBuilder
    private var backgroundLayer: some View {
        if vm.connectionState == .connected, let unityURL = vm.unityWebGLURL {
            UnityWebView(url: unityURL, onLoad: { unityLoaded = true })
                .id("unity-webview")
                .ignoresSafeArea()
        } else {
            Color(.systemGroupedBackground)
                .ignoresSafeArea()
        }
    }

    // MARK: - Layer 1a: Top Bar

    private var topBar: some View {
        HStack {
            // Sidebar toggle
            Button {
                withAnimation { sidebarCollapsed.toggle() }
            } label: {
                Image(systemName: "sidebar.left")
                    .font(.title3)
                    .foregroundColor(sidebarCollapsed ? .secondary : .blue)
            }

            Text("🏥 瑞金神外护理助手")
                .font(.headline)
            Spacer()

            // Auto-read toggle
            Button {
                vm.autoReadEnabled.toggle()
            } label: {
                Image(systemName: vm.autoReadEnabled ? "speaker.wave.2.fill" : "speaker.wave.2")
                    .font(.system(size: 18))
                    .foregroundColor(vm.autoReadEnabled ? .white : .primary)
                    .padding(6)
                    .background(vm.autoReadEnabled ? Color.blue : Color(.systemGray5))
                    .clipShape(Circle())
            }
            .disabled(!vm.isLoggedIn)

            Circle()
                .fill(connectionColor)
                .frame(width: 10, height: 10)

            Button {
                showLogin = true
            } label: {
                Image(systemName: vm.isLoggedIn ? "person.circle.fill" : "person.circle")
                    .font(.title3)
            }

            Button {
                showConfig = true
            } label: {
                Image(systemName: "gearshape")
                    .font(.title3)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(.systemBackground).opacity(0.92))
    }

    // MARK: - Layer 1b: Main Content Area

    /// Spacing around the chat overlay — tweak these to adjust position
    private let chatMarginTop: CGFloat = 50
    private let chatMarginBottom: CGFloat = 30
    private let chatMarginTrailing: CGFloat = 20

    private var mainContentArea: some View {
        HStack(spacing: 0) {
            // Left: 3D digital human placeholder
            digitalHumanPlaceholder

            // Right: Chat overlay with margins (doesn't touch screen edges)
            chatOverlay
                .padding(.top, chatMarginTop)
                .padding(.bottom, chatMarginBottom)
                .padding(.trailing, chatMarginTrailing)
        }
    }

    // 3D Digital Human placeholder — hides watermark once Unity has loaded
    private var digitalHumanPlaceholder: some View {
        Rectangle()
            .fill(Color.clear)
            .overlay(
                Group {
                    if !unityLoaded {
                        VStack(spacing: 8) {
                            Image(systemName: "person.fill.viewfinder")
                                .font(.system(size: 36))
                                .foregroundColor(.white.opacity(0.7))
                            Text("3D 数智人")
                                .font(.caption)
                                .foregroundColor(.white.opacity(0.6))
                        }
                    }
                }
            )
            .frame(width: digitalHumanWidth)
    }

    // Chat overlay — frosted glass rounded container
    private var chatOverlay: some View {
        VStack(spacing: 0) {
            ChatMessagesView()
            ChatInputView()
        }
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    // MARK: - Layer 2: Sidebar Overlay

    @ViewBuilder
    private var sidebarOverlay: some View {
        if !sidebarCollapsed {
            HStack(spacing: 0) {
                AgentListView()
                    .frame(width: sidebarWidth)
                    .background(Color(.systemBackground))

                // Dimmed backdrop — tap to dismiss
                Color.black.opacity(0.2)
                    .ignoresSafeArea()
                    .onTapGesture {
                        withAnimation(.easeOut(duration: 0.25)) {
                            sidebarCollapsed = true
                        }
                    }
            }
            .transition(.move(edge: .leading))
            .zIndex(100)
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
    var onLoad: (() -> Void)?

    func makeCoordinator() -> Coordinator {
        Coordinator(onLoad: onLoad)
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        config.defaultWebpagePreferences = prefs

        // Inject script to scale Unity canvas — tweak initial-scale to zoom
        let scaleScript = """
        (function() {
            var meta = document.createElement('meta');
            meta.name = 'viewport';
            meta.content = 'width=device-width, initial-scale=1.25, user-scalable=yes';
            document.head.appendChild(meta);
            var style = document.createElement('style');
            style.textContent = '#unity-container{width:100%!important;overflow:visible}' +
                '#unity-canvas{max-width:100%!important;height:auto!important;margin-left:0px;margin-top:-75px}' +
                'body{margin:0;overflow:hidden;background:#000}';
            document.head.appendChild(style);
        })();
        """
        let userScript = WKUserScript(source: scaleScript,
                                       injectionTime: .atDocumentEnd,
                                       forMainFrameOnly: true)
        config.userContentController.addUserScript(userScript)

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.scrollView.isScrollEnabled = true
        webView.scrollView.minimumZoomScale = 0.2
        webView.scrollView.maximumZoomScale = 2.0
        webView.isOpaque = false
        webView.backgroundColor = .black

        // Use standard HTTP caching — WKWebView automatically handles
        // ETag / If-None-Match and Last-Modified / If-Modified-Since:
        //   304 Not Modified → serves from cache (fast)
        //   200 OK           → loads new content (server updated)
        context.coordinator.loadURL = url
        webView.load(URLRequest(url: url, timeoutInterval: 30))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    class Coordinator: NSObject, WKNavigationDelegate {
        let onLoad: (() -> Void)?
        var loadURL: URL?

        init(onLoad: (() -> Void)?) {
            self.onLoad = onLoad
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            print("[UnityWebView] Failed to load: \(error.localizedDescription)")
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            print("[UnityWebView] Navigation failed: \(error.localizedDescription)")
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            print("[UnityWebView] Loaded successfully: \(webView.url?.absoluteString ?? "unknown")")
            DispatchQueue.main.async { self.onLoad?() }
        }

        /// Recover when the system kills the WebContent process (memory pressure) —
        /// reload the Unity scene instead of leaving a blank background.
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            print("[UnityWebView] WebContent 进程被终止，重新加载 Unity")
            let reloadURL = webView.url ?? loadURL
            guard let reloadURL = reloadURL else { return }
            webView.load(URLRequest(url: reloadURL, timeoutInterval: 30))
        }
    }
}
