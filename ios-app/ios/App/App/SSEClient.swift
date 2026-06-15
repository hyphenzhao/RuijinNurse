import Foundation

/// Simple SSE (Server-Sent Events) client for streaming chat responses.
class SSEClient: NSObject {
    private var task: URLSessionDataTask?
    private var buffer = ""
    private var responseData = Data()  // Accumulate for error extraction
    private var accumulatedAnswer = "" // Fallback for done event
    private var pendingEvent = ""       // Persist across buffer chunks
    private var doneReceived = false     // Track if done event was processed
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
        accumulatedAnswer = ""
        pendingEvent = ""
        doneReceived = false
        task = session.dataTask(with: request)
        task?.resume()
    }

    func disconnect() {
        task?.cancel()
        task = nil
        accumulatedAnswer = ""
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
        } else {
            // Connection closed cleanly — flush any remaining data in buffer
            flushBuffer()
            // Fallback: if done event was never received, force-complete with accumulated text
            if !doneReceived {
                DispatchQueue.main.async { [weak self] in
                    self?.onDone?(self?.accumulatedAnswer ?? "")
                }
            }
        }
        DispatchQueue.main.async { [weak self] in
            self?.task = nil
        }
    }

    /// Process any remaining data in the buffer when connection closes.
    /// Handles the case where the final SSE event lacks a trailing \n\n.
    private func flushBuffer() {
        guard !buffer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        // Force-process what's in the buffer as complete lines
        let lines = buffer.components(separatedBy: "\n")
        buffer = ""

        for line in lines where !line.isEmpty {
            if line.hasPrefix("event: ") {
                pendingEvent = String(line.dropFirst(7))
            } else if line.hasPrefix("data: ") {
                let data = String(line.dropFirst(6))
                processSSEMessage(event: pendingEvent, data: data)
                pendingEvent = ""
            }
        }

        // If pendingEvent was set but no data followed, treat as done
        if pendingEvent == "done" {
            processSSEMessage(event: "done", data: "{}")
            pendingEvent = ""
        }
    }

    private func processBuffer() {
        let lines = buffer.components(separatedBy: "\n")
        // Keep the last incomplete line in buffer
        buffer = lines.last ?? ""

        for i in 0..<(lines.count - 1) {
            let line = lines[i]

            if line.hasPrefix("event: ") {
                pendingEvent = String(line.dropFirst(7))
            } else if line.hasPrefix("data: ") {
                let data = String(line.dropFirst(6))

                // Use pendingEvent (persists across buffer chunks)
                processSSEMessage(event: pendingEvent, data: data)

                // Reset for next SSE message
                pendingEvent = ""
            }
        }
    }

    private func processSSEMessage(event: String, data: String) {
        // Handle "done" event specially — always trigger completion,
        // even if JSON is malformed (e.g. split across network chunks)
        if event == "done" {
            doneReceived = true
            var final = ""
            if let jsonData = data.data(using: .utf8),
               let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] {
                final = dict["final"] as? String ?? ""
            }
            DispatchQueue.main.async { [weak self] in
                self?.onDone?(final.isEmpty ? (self?.accumulatedAnswer ?? "") : final)
            }
            return
        }

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
                    self?.accumulatedAnswer += content
                    self?.onDelta?(content)
                }
            default:
                break
            }
        }
    }
}
