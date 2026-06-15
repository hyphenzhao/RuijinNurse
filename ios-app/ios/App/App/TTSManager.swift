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

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    /// Speak the given text aloud. Stops any existing speech first.
    /// Strips markdown formatting before speaking so URLs and syntax aren't read aloud.
    func speak(_ text: String, messageId: Int = 0) {
        let trimmed = stripMarkdown(text).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        // Toggle: tapping same message again stops it
        if isSpeaking && currentQueueHash == messageId {
            stop()
            return
        }

        // Replace any existing speech
        if isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
            utteranceQueue.removeAll()
        }

        let sentences = splitChineseSentences(trimmed)
        utteranceQueue = sentences
        currentQueueHash = messageId
        currentTextHash = messageId

        speakNextInQueue()
    }

    /// Append text to the speech queue WITHOUT interrupting current speech.
    /// Strips markdown before speaking.
    func appendSpeech(_ text: String) {
        let trimmed = stripMarkdown(text).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let sentences = splitChineseSentences(trimmed)
        utteranceQueue.append(contentsOf: sentences)

        // Start speaking if idle
        if !synthesizer.isSpeaking && !utteranceQueue.isEmpty {
            speakNextInQueue()
        }
    }

    /// Strip markdown formatting for clean TTS reading:
    /// - [link text](url) → link text
    /// - ![image](url) → removed
    /// - **bold** → bold, *italic* → italic
    /// - `code` → code
    /// - Bare URLs → removed
    /// - # headings → plain text
    /// - | tables | → removed (unreadable)
    private func stripMarkdown(_ text: String) -> String {
        var result = text

        // Remove images: ![alt](url)
        result = result.replacingOccurrences(of: #"!\[[^\]]*\]\([^)]*\)"#,
                                             with: "", options: .regularExpression)

        // Replace links with just the text: [text](url) → text
        result = result.replacingOccurrences(of: #"\[([^\]]*)\]\([^)]*\)"#,
                                             with: "$1", options: .regularExpression)

        // Remove bare URLs
        result = result.replacingOccurrences(of: #"https?://[^\s<>"')\]]+"#,
                                             with: "", options: .regularExpression)

        // Remove markdown formatting markers (keep the text inside)
        result = result.replacingOccurrences(of: #"\*\*([^*]+)\*\*"#,
                                             with: "$1", options: .regularExpression)
        result = result.replacingOccurrences(of: #"\*([^*]+)\*"#,
                                             with: "$1", options: .regularExpression)
        result = result.replacingOccurrences(of: #"`([^`]+)`"#,
                                             with: "$1", options: .regularExpression)

        // Remove heading markers (keep text)
        result = result.replacingOccurrences(of: #"^#{1,6}\s+"#,
                                             with: "", options: .regularExpression.union(.anchorsMatchLines))

        // Remove table rows (lines with | that contain more than 2 pipes)
        let lines = result.components(separatedBy: "\n")
        result = lines.filter { line in
            let pipes = line.components(separatedBy: "|").count - 1
            return pipes < 2
        }.joined(separator: "\n")

        // Remove separator lines (---, ***, ===)
        result = result.replacingOccurrences(of: #"^[-*=_]{3,}\s*$"#,
                                             with: "", options: .regularExpression.union(.anchorsMatchLines))

        return result
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
