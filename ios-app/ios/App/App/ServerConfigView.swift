import SwiftUI

struct ServerConfigView: View {
    @EnvironmentObject var vm: ChatViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var urlInput: String = ""
    @State private var statusMsg: String = ""
    @State private var isTesting = false

    var body: some View {
        NavigationView {
            Form {
                Section("服务器地址") {
                    TextField("http://192.168.1.100:8000", text: $urlInput)
                        .keyboardType(.URL)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }

                Section {
                    Button(action: testConnection) {
                        HStack {
                            if isTesting {
                                ProgressView()
                            }
                            Text("测试连接")
                        }
                    }
                    .disabled(isTesting || urlInput.isEmpty)

                    if !statusMsg.isEmpty {
                        Text(statusMsg)
                            .font(.caption)
                            .foregroundColor(statusMsg.hasPrefix("✅") ? .green : .red)
                    }
                }

                Section("当前状态") {
                    HStack {
                        Text("连接状态")
                        Spacer()
                        Text(connectionText)
                            .foregroundColor(vm.connectionState == .connected ? .green : .secondary)
                    }
                    HStack {
                        Text("登录状态")
                        Spacer()
                        Text(vm.isLoggedIn ? "已登录 (\(vm.username))" : "未登录")
                            .foregroundColor(vm.isLoggedIn ? .green : .secondary)
                    }
                }

                Section {
                    Button("保存并关闭") {
                        vm.serverURL = urlInput
                        dismiss()
                        Task { await vm.checkConnection() }
                    }
                    .disabled(urlInput.isEmpty)
                }
            }
            .navigationTitle("⚙️ 服务器设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
            .onAppear {
                urlInput = vm.serverURL
            }
        }
    }

    private func testConnection() {
        isTesting = true
        statusMsg = ""
        let testURL = urlInput.trimmingCharacters(in: .init(charactersIn: "/"))
        vm.serverURL = testURL
        Task {
            await vm.checkConnection()
            isTesting = false
            switch vm.connectionState {
            case .connected:
                statusMsg = "✅ 连接成功"
            case .error(let msg):
                statusMsg = "❌ \(msg)"
            default:
                statusMsg = "❌ 连接失败"
            }
        }
    }

    private var connectionText: String {
        switch vm.connectionState {
        case .connected: return "已连接"
        case .connecting: return "连接中..."
        case .disconnected: return "未连接"
        case .error: return "异常"
        }
    }
}

// MARK: - Login View

struct LoginView: View {
    @EnvironmentObject var vm: ChatViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var user = ""
    @State private var pass = ""
    @State private var errorMsg: String?
    @State private var isLoading = false

    var body: some View {
        NavigationView {
            Form {
                Section("登录信息") {
                    TextField("用户名", text: $user)
                        .autocapitalization(.none)
                    SecureField("密码", text: $pass)
                }

                if let errorMsg = errorMsg {
                    Section {
                        Text(errorMsg)
                            .foregroundColor(.red)
                            .font(.caption)
                    }
                }

                Section {
                    Button(action: doLogin) {
                        HStack {
                            if isLoading {
                                ProgressView()
                            }
                            Text("登录")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .disabled(isLoading || user.isEmpty || pass.isEmpty)
                }

                if vm.isLoggedIn {
                    Section {
                        Button("登出", role: .destructive) {
                            vm.logout()
                            dismiss()
                        }
                    }
                }
            }
            .navigationTitle("🔐 登录")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
        }
    }

    private func doLogin() {
        isLoading = true
        errorMsg = nil
        Task {
            let err = await vm.login(user: user, pass: pass)
            errorMsg = err
            isLoading = false
            if err == nil {
                dismiss()
            }
        }
    }
}
