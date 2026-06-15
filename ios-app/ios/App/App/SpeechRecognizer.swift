import Foundation
import Speech
import AVFoundation

/// Wraps Apple's SFSpeechRecognizer for Chinese voice input.
/// ObservableObject — use @StateObject in SwiftUI views.
@MainActor
class SpeechRecognizer: ObservableObject {
    @Published var recognizedText = ""
    @Published var isRecording = false
    @Published var isAuthorized = false

    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))!
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private let audioEngine = AVAudioEngine()

    init() {
        speechRecognizer.defaultTaskHint = .dictation
        speechRecognizer.supportsOnDeviceRecognition = true
        checkAuthorization()
    }

    /// Request speech recognition authorization
    func requestAuthorization() async {
        let status = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
        isAuthorized = status == .authorized
        if !isAuthorized {
            print("[SpeechRecognizer] Authorization denied: \(status.rawValue)")
        }
    }

    private func checkAuthorization() {
        isAuthorized = SFSpeechRecognizer.authorizationStatus() == .authorized
    }

    /// Start recording and transcribing speech
    func startRecording() throws {
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

        // Create recognition request
        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        guard let recognitionRequest = recognitionRequest else {
            throw NSError(domain: "SpeechRecognizer", code: -2,
                userInfo: [NSLocalizedDescriptionKey: "无法创建语音识别请求"])
        }
        recognitionRequest.shouldReportPartialResults = true
        recognitionRequest.requiresOnDeviceRecognition = true  // Offline-capable

        // Install tap on microphone input
        let inputNode = audioEngine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)
        }

        // Start audio engine
        audioEngine.prepare()
        try audioEngine.start()

        // Start recognition
        recognitionTask = speechRecognizer.recognitionTask(with: recognitionRequest) { [weak self] result, error in
            guard let self = self else { return }
            if let result = result {
                self.recognizedText = result.bestTranscription.formattedString
            }
            if error != nil || result?.isFinal == true {
                self.stopRecording()
            }
        }

        isRecording = true
        recognizedText = ""
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
    }
}
