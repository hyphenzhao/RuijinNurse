import Foundation
import Speech
import AVFoundation

/// Wraps Apple's SFSpeechRecognizer for Chinese voice input.
/// ObservableObject — use @StateObject in SwiftUI views.
///
/// NOTE: Voice recognition may not work in iOS Simulator.
/// Test on a real iPad/iPhone for full functionality.
@MainActor
class SpeechRecognizer: ObservableObject {
    @Published var recognizedText = ""
    @Published var isRecording = false
    @Published var isAuthorized = false
    @Published var lastError: String?

    private let speechRecognizer: SFSpeechRecognizer? = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private let audioEngine = AVAudioEngine()

    init() {
        checkAvailability()
        checkAuthorization()
    }

    private func checkAvailability() {
        guard let sr = speechRecognizer else {
            lastError = "中文语音识别不可用"
            print("[SpeechRecognizer] zh-CN locale not available")
            return
        }
        print("[SpeechRecognizer] Available: \(sr.isAvailable), On-device supported: \(sr.supportsOnDeviceRecognition)")
    }

    /// Request speech recognition authorization
    func requestAuthorization() async {
        let status = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
        isAuthorized = status == .authorized
        print("[SpeechRecognizer] Auth status: \(status.rawValue) (\(status == .authorized ? "granted" : "denied"))")
        if !isAuthorized {
            lastError = "语音识别未授权，请在系统设置中开启"
        }
    }

    private func checkAuthorization() {
        let status = SFSpeechRecognizer.authorizationStatus()
        isAuthorized = status == .authorized
    }

    /// Start recording and transcribing speech
    func startRecording() throws {
        guard let speechRecognizer = speechRecognizer, speechRecognizer.isAvailable else {
            throw NSError(domain: "SpeechRecognizer", code: -3,
                userInfo: [NSLocalizedDescriptionKey: "语音识别服务不可用（模拟器不支持，请在真机上测试）"])
        }

        guard isAuthorized else {
            throw NSError(domain: "SpeechRecognizer", code: -1,
                userInfo: [NSLocalizedDescriptionKey: "语音识别未授权，请在系统设置中开启"])
        }

        // Cancel any ongoing task
        recognitionTask?.cancel()
        recognitionTask = nil

        // Configure audio session
        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.record, mode: .measurement, options: .duckOthers)
        try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        // Create recognition request — don't require on-device (simulator compatible)
        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        guard let recognitionRequest = recognitionRequest else {
            throw NSError(domain: "SpeechRecognizer", code: -2,
                userInfo: [NSLocalizedDescriptionKey: "无法创建语音识别请求"])
        }
        recognitionRequest.shouldReportPartialResults = true
        // On-device recognition requires Apple Silicon / Neural Engine
        // Set false for simulator compatibility; iOS will use on-device when available
        recognitionRequest.requiresOnDeviceRecognition = false

        // Install tap on microphone input
        let inputNode = audioEngine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)
        print("[SpeechRecognizer] Audio format: \(recordingFormat)")
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)
        }

        // Start audio engine
        audioEngine.prepare()
        try audioEngine.start()
        print("[SpeechRecognizer] Audio engine started")

        // Start recognition
        lastError = nil
        recognitionTask = speechRecognizer.recognitionTask(with: recognitionRequest) { [weak self] result, error in
            guard let self = self else { return }
            if let error = error {
                print("[SpeechRecognizer] Recognition error: \(error.localizedDescription)")
                self.lastError = error.localizedDescription
                self.stopRecording()
                return
            }
            if let result = result {
                let text = result.bestTranscription.formattedString
                print("[SpeechRecognizer] Partial: \(text)")
                self.recognizedText = text
            }
            if result?.isFinal == true {
                print("[SpeechRecognizer] Final result")
                self.stopRecording()
            }
        }

        isRecording = true
        recognizedText = ""
        print("[SpeechRecognizer] Recording started")
    }

    /// Stop recording — recognized text is available in recognizedText
    func stopRecording() {
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionTask?.finish()
        recognitionTask = nil
        recognitionRequest = nil
        isRecording = false

        // Deactivate audio session
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        print("[SpeechRecognizer] Recording stopped. Text: \(recognizedText)")
    }
}
