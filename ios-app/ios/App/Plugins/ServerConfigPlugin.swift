import Foundation
import Capacitor

/// ServerConfigPlugin — Stores and retrieves the Django server URL.
///
/// This plugin allows the user to configure which RuijinNurse server
/// the app connects to.  The URL is persisted in UserDefaults.
@objc(ServerConfigPlugin)
public class ServerConfigPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "ServerConfigPlugin"
    public let jsName = "ServerConfig"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "getServerUrl", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setServerUrl", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getHealthStatus", returnType: CAPPluginReturnPromise),
    ]

    private let serverUrlKey = "ruijin_server_url"

    /// Return the currently configured server URL.
    @objc func getServerUrl(_ call: CAPPluginCall) {
        let url = UserDefaults.standard.string(forKey: serverUrlKey) ?? "http://localhost:8000"
        call.resolve(["url": url])
    }

    /// Persist a new server URL.
    @objc func setServerUrl(_ call: CAPPluginCall) {
        guard let url = call.getString("url") else {
            call.reject("url is required")
            return
        }
        UserDefaults.standard.set(url, forKey: serverUrlKey)
        call.resolve(["url": url])
    }

    /// Quickly check whether the configured server is reachable.
    @objc func getHealthStatus(_ call: CAPPluginCall) {
        let urlString = UserDefaults.standard.string(forKey: serverUrlKey) ?? "http://localhost:8000"
        guard let healthURL = URL(string: "\(urlString)/api/v1/health/") else {
            call.resolve(["ok": false, "error": "Invalid server URL"])
            return
        }

        let start = Date()
        var request = URLRequest(url: healthURL, timeoutInterval: 5)
        request.httpMethod = "GET"

        URLSession.shared.dataTask(with: request) { data, response, error in
            let latency = Int(Date().timeIntervalSince(start) * 1000)

            if let error = error {
                call.resolve([
                    "ok": false,
                    "error": error.localizedDescription,
                    "latency": latency,
                ])
                return
            }

            guard let httpResponse = response as? HTTPURLResponse else {
                call.resolve(["ok": false, "error": "No response", "latency": latency])
                return
            }

            // 200 = healthy, 401 = healthy but needs auth (expected for JWT endpoints)
            let ok = (200...299).contains(httpResponse.statusCode) || httpResponse.statusCode == 401
            call.resolve([
                "ok": ok,
                "latency": latency,
                "statusCode": httpResponse.statusCode,
            ])
        }.resume()
    }
}
