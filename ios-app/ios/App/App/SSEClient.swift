import Foundation

/// Simple SSE (Server-Sent Events) client for streaming chat responses.
class SSEClient: NSObject {
    private var task: URLSessionDataTask?
    private var buffer = ""
    private var responseData = Data()  // Accumulate for error extraction
    var isStreaming: Bool { task != nil }

    var onThinking: ((String) -> Void)?
    var onDelta: ((String) -> Void)?
    var onDone: ((String) -> Void)?
    var onError: ((String) -> Void)?

    func connect(url: URL, headers: [String: String], body: Data) {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }
        request.httpBody = body
        request.timeoutInterval = 120

        let config: URLSessionConfiguration = {
            let c = URLSessionConfiguration.ephemeral
            c.connectionProxyDictionary = [:]  // Bypass WPAD/PAC proxy for LAN
            return c
        }()
        let session = URLSession(
            configuration: config,
            delegate: self,
            delegateQueue: nil
        )

        responseData = Data()
        buffer = ""
        task = session.dataTask(with: request)
        task?.resume()
    }

    func disconnect() {
        task?.cancel()
        task = nil
    }
}

extension SSEClient: URLSessionDataDelegate {
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        responseData.append(data)
        guard let text = String(data: data, encoding: .utf8) else { return }
        buffer += text
        processBuffer()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error = error {
            let nsError = error as NSError
            if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
                return // intentional disconnect
            }

            // Try to extract DRF error from response body
            var errorMsg = error.localizedDescription
            if let httpResponse = task.response as? HTTPURLResponse,
               let json = try? JSONSerialization.jsonObject(with: responseData) as? [String: Any],
               let detail = json["detail"] as? String {
                errorMsg = detail
            } else if let httpResponse = task.response as? HTTPURLResponse,
                      httpResponse.statusCode >= 400 {
                errorMsg = "服务器错误 \(httpResponse.statusCode)"
            }

            DispatchQueue.main.async { [weak self] in
                self?.onError?(errorMsg)
            }
        }
        DispatchQueue.main.async { [weak self] in
            self?.task = nil
        }
    }

    private func processBuffer() {
        let lines = buffer.components(separatedBy: "\n")
        // Keep the last incomplete line in buffer
        buffer = lines.last ?? ""

        var currentEvent = ""
        var currentData = ""

        for i in 0..<(lines.count - 1) {
            let line = lines[i]

            if line.hasPrefix("event: ") {
                currentEvent = String(line.dropFirst(7))
            } else if line.hasPrefix("data: ") {
                currentData = String(line.dropFirst(6))

                // We have both event and data — process
                processSSEMessage(event: currentEvent, data: currentData)

                // Reset for next
                currentEvent = ""
                currentData = ""
            }
        }
    }

    private func processSSEMessage(event: String, data: String) {
        guard let jsonData = data.data(using: .utf8),
              let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any]
        else { return }

        DispatchQueue.main.async { [weak self] in
            switch event {
            case "thinking":
                if let content = dict["content"] as? String {
                    self?.onThinking?(content)
                }
            case "delta":
                if let content = dict["content"] as? String {
                    self?.onDelta?(content)
                }
            case "done":
                let final = dict["final"] as? String ?? ""
                self?.onDone?(final)
            default:
                break
            }
        }
    }
}
