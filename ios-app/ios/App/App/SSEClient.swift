import Foundation

/// Simple SSE (Server-Sent Events) client for streaming chat responses.
class SSEClient: NSObject {
    private var task: URLSessionDataTask?
    private var bufferData = Data()      // Raw bytes not yet decoded (multi-byte UTF-8 chars may split across chunks)
    private var buffer = ""              // Decoded text awaiting line parsing
    private var responseData = Data()    // Accumulate for error extraction
    private var accumulatedAnswer = ""   // Fallback for done event
    private var pendingEvent = ""        // Persist across buffer chunks
    private var doneReceived = false     // Track if done event was processed
    private var httpStatus = 200         // Response status — non-2xx becomes an error

    // MARK: Diagnostics
    private var lastDataTime = Date()
    private var deltaCount = 0
    private var thinkingCount = 0
    private var thinkingStarted = false
    private var firstChunkReceived = false
    private var unknownEventCount = 0

    private static let timeFormatter: DateFormatter = {
        let df = DateFormatter()
        df.dateFormat = "HH:mm:ss.SSS"
        return df
    }()

    private func sseLog(_ msg: String) {
        print("[SSEClient] \(Self.timeFormatter.string(from: Date())) \(msg)")
    }

    var isStreaming: Bool { task != nil }

    var onThinking: ((String) -> Void)?
    var onDelta: ((String) -> Void)?
    /// (finalText, cleanDone) — cleanDone=false means the connection ended
    /// before a done event arrived (abnormal termination).
    var onDone: ((String, Bool) -> Void)?
    var onError: ((String) -> Void)?

    func connect(url: URL, headers: [String: String], body: Data) {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }
        request.httpBody = body
        // Long idle timeout: the server sends NO data while the model "thinks"
        // (can exceed 2 minutes on CPU inference) — iOS treats this as an idle
        // timeout, so keep it generous or the stream gets killed mid-thinking.
        request.timeoutInterval = 3600

        let config: URLSessionConfiguration = {
            let c = URLSessionConfiguration.ephemeral
            c.connectionProxyDictionary = [:]  // Bypass WPAD/PAC proxy for LAN
            c.timeoutIntervalForRequest = 3600
            c.timeoutIntervalForResource = 86400
            return c
        }()
        let session = URLSession(
            configuration: config,
            delegate: self,
            delegateQueue: nil
        )

        responseData = Data()
        bufferData = Data()
        buffer = ""
        accumulatedAnswer = ""
        pendingEvent = ""
        doneReceived = false
        httpStatus = 200
        lastDataTime = Date()
        deltaCount = 0
        thinkingCount = 0
        thinkingStarted = false
        firstChunkReceived = false
        unknownEventCount = 0
        sseLog("▶️ 连接 \(url.host ?? "")\(url.path), bodySize=\(body.count)")
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
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        if let http = response as? HTTPURLResponse {
            httpStatus = http.statusCode
            sseLog("📡 HTTP \(http.statusCode), Content-Type: \(http.allHeaderFields["Content-Type"] ?? "?")")
        }
        // Keep receiving — the (short) DRF error body carries the readable message
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        let now = Date()

        // Log long silences — these are what idle timeouts (server proxies) kill
        if !firstChunkReceived {
            firstChunkReceived = true
            sseLog("📦 首个数据块 (\(data.count) bytes)")
        } else {
            let gap = now.timeIntervalSince(lastDataTime)
            if gap > 30 {
                sseLog("⏳ 静默 \(Int(gap))s 后恢复数据流 (\(data.count) bytes)")
            }
        }
        lastDataTime = now

        responseData.append(data)
        bufferData.append(data)

        // Incremental UTF-8 decode: Chinese characters are 3 bytes and may be
        // split across TCP chunks. Decoding a chunk that ends mid-character
        // would fail and previously DROPPED the whole chunk (losing events).
        guard let text = decodeIncrementalUTF8() else { return }
        buffer += text
        processBuffer()
    }

    /// Decode as much complete UTF-8 text as possible from bufferData.
    /// Leaves any incomplete trailing sequence (≤3 bytes) in bufferData for the next chunk.
    private func decodeIncrementalUTF8() -> String? {
        guard !bufferData.isEmpty else { return nil }

        if let text = String(data: bufferData, encoding: .utf8) {
            bufferData.removeAll()
            return text
        }

        // A multi-byte char is at most 4 bytes, so at most 3 trailing bytes
        // can belong to an incomplete sequence.
        for drop in 1...3 where drop < bufferData.count {
            let valid = bufferData.dropLast(drop)
            if let text = String(data: valid, encoding: .utf8) {
                bufferData.removeFirst(bufferData.count - drop)
                return text
            }
        }
        return nil  // Whole buffer is one incomplete sequence — wait for more bytes
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error = error {
            let nsError = error as NSError
            if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
                sseLog("⏹️ 主动断开 (cancelled)")
                return // intentional disconnect
            }
            sseLog("❌ 连接以错误结束: \(nsError.domain) code=\(nsError.code) \(error.localizedDescription)")

            // Try to extract DRF error from response body
            var errorMsg = error.localizedDescription
            if let json = try? JSONSerialization.jsonObject(with: responseData) as? [String: Any],
               let detail = json["detail"] as? String {
                errorMsg = detail
            } else if httpStatus >= 400 {
                errorMsg = "服务器错误 \(httpStatus)"
            }

            DispatchQueue.main.async { [weak self] in
                self?.onError?(errorMsg)
            }
        } else if httpStatus >= 400 {
            // Connection closed cleanly but the server rejected the request
            // (e.g. 401 mid-stream) — surface a readable error, not an empty reply.
            sseLog("⚠️ HTTP \(httpStatus) 关闭连接, bytes=\(responseData.count)")
            var errorMsg = "服务器错误 \(httpStatus)"
            if let json = try? JSONSerialization.jsonObject(with: responseData) as? [String: Any],
               let detail = json["detail"] as? String {
                errorMsg = detail
            }
            DispatchQueue.main.async { [weak self] in
                self?.onError?(errorMsg)
            }
        } else {
            // Connection closed cleanly — flush any remaining data in buffer
            sseLog("✅ 连接正常关闭: doneReceived=\(doneReceived), bytes=\(responseData.count), 答案长度=\(accumulatedAnswer.count)")
            flushBuffer()
            // Fallback: if done event was never received, force-complete with
            // accumulated text but flag the stream as abnormally terminated.
            if !doneReceived {
                DispatchQueue.main.async { [weak self] in
                    self?.onDone?(self?.accumulatedAnswer ?? "", false)
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
        // Force-decode any leftover raw bytes — no more chunks are coming
        if let tail = String(data: bufferData, encoding: .utf8), !tail.isEmpty {
            buffer += tail
        }
        bufferData = Data()
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
            var thinkLen = 0
            var parseOK = false
            if let jsonData = data.data(using: .utf8),
               let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] {
                final = dict["final"] as? String ?? ""
                thinkLen = (dict["thinking"] as? String)?.count ?? 0
                parseOK = true
            }
            sseLog("🏁 done 事件: parseOK=\(parseOK), final=\(final.count) chars, thinking=\(thinkLen) chars, delta 总数=\(deltaCount), thinking 总数=\(thinkingCount)")
            DispatchQueue.main.async { [weak self] in
                self?.onDone?(final.isEmpty ? (self?.accumulatedAnswer ?? "") : final, true)
            }
            return
        }

        guard let jsonData = data.data(using: .utf8),
              let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any]
        else {
            sseLog("⚠️ 事件解析失败: event=\(event), data=\(String(data.prefix(120)))")
            return
        }

        DispatchQueue.main.async { [weak self] in
            switch event {
            case "status":
                break  // Connection preamble — no content to extract
            case "thinking":
                if let content = dict["content"] as? String {
                    if let self = self {
                        self.thinkingCount += 1
                        if !self.thinkingStarted {
                            self.thinkingStarted = true
                            self.sseLog("🧠 thinking 开始")
                        }
                    }
                    self?.onThinking?(content)
                }
            case "delta":
                if let content = dict["content"] as? String {
                    self?.accumulatedAnswer += content
                    if let self = self {
                        self.deltaCount += 1
                        if self.deltaCount == 1 || self.deltaCount % 50 == 0 {
                            self.sseLog("💧 delta #\(self.deltaCount) (累计 \(self.accumulatedAnswer.count) chars)")
                        }
                    }
                    self?.onDelta?(content)
                }
            default:
                if let self = self, self.unknownEventCount < 3 {
                    self.unknownEventCount += 1
                    self.sseLog("❓ 未知事件类型: \(event)")
                }
            }
        }
    }
}
