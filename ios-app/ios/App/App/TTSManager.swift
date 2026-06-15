import Foundation
import AVFoundation

/// Manages text-to-speech via AVSpeechSynthesizer.
/// Use as @EnvironmentObject in SwiftUI views.
@MainActor
class TTSManager: NSObject, ObservableObject {
    @Published var isSpeaking = false
    @Published var currentTextHash: Int = 0  // Track which message is being spoken

    private let synthesizer = AVSpeechSynthesizer()
    private var utteranceQueue: [String] = []
    private var currentQueueHash: Int = 0

    init() {
        synthesizer.delegate = self
    }

    /// Speak the given text aloud using a Chinese voice.
    /// Automatically splits by Chinese sentence boundaries for natural pacing.
    func speak(_ text: String, messageId: Int = 0) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        // If already speaking the SAME message, stop it
        if isSpeaking && currentTextHash == messageId {
            stop()
            return
        }

        // If speaking something else, stop and replace
        if isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
            utteranceQueue.removeAll()
        }

        // Split into sentences for natural pacing
        let sentences = splitChineseSentences(trimmed)
        utteranceQueue = sentences
        currentTextHash = messageId
        currentQueueHash = messageId

        speakNextInQueue()
    }

    /// Stop all speech immediately
    func stop() {
        synthesizer.stopSpeaking(at: .immediate)
        utteranceQueue.removeAll()
        isSpeaking = false
        currentTextHash = 0
        currentQueueHash = 0
    }

    // MARK: - Private

    private func speakNextInQueue() {
        guard !utteranceQueue.isEmpty else {
            isSpeaking = false
            currentTextHash = 0
            return
        }

        let sentence = utteranceQueue.removeFirst()
        let utterance = AVSpeechUtterance(string: sentence)
        utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        utterance.pitchMultiplier = 1.0
        utterance.volume = 1.0

        isSpeaking = true
        synthesizer.speak(utterance)
    }

    /// Split text by Chinese sentence-ending punctuation
    private func splitChineseSentences(_ text: String) -> [String] {
        var sentences: [String] = []
        var current = ""
        for char in text {
            current.append(char)
            if "。！？!?\n；;".contains(char) {
                let trimmed = current.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    sentences.append(trimmed)
                }
                current = ""
            }
        }
        let remaining = current.trimmingCharacters(in: .whitespacesAndNewlines)
        if !remaining.isEmpty {
            sentences.append(remaining)
        }
        return sentences.isEmpty ? [text] : sentences
    }
}

// MARK: - AVSpeechSynthesizerDelegate
extension TTSManager: AVSpeechSynthesizerDelegate {
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                           didFinish utterance: AVSpeechUtterance) {
        speakNextInQueue()
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                           didCancel utterance: AVSpeechUtterance) {
        isSpeaking = false
    }
}
