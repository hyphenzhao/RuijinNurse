import Foundation
import AVFoundation
import Capacitor

/// NativeTTSPlugin — Native iOS text-to-speech via AVSpeechSynthesizer.
///
/// Replaces the browser's window.speechSynthesis API with the
/// higher-quality native AVFoundation synthesizer.  Supports Chinese
/// voice selection and sequential queuing.
@objc(NativeTTSPlugin)
public class NativeTTSPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "NativeTTSPlugin"
    public let jsName = "NativeTTS"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "speak", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stop", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "isSpeaking", returnType: CAPPluginReturnPromise),
    ]

    private let synthesizer = AVSpeechSynthesizer()
    private var utteranceQueue: [AVSpeechUtterance] = []
    private var isSpeakingFlag = false

    public override func load() {
        synthesizer.delegate = self
    }

    /// Speak *text* aloud. If the synthesizer is already speaking, the text
    /// is queued to play after the current utterance finishes (matching
    /// the web TTS queueing behaviour).
    @objc func speak(_ call: CAPPluginCall) {
        guard let text = call.getString("text"), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            call.reject("text is required")
            return
        }

        let lang = call.getString("lang") ?? "zh-CN"
        let rate = call.getFloat("rate") ?? AVSpeechUtteranceDefaultSpeechRate

        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: lang)
        utterance.rate = rate
        utterance.pitchMultiplier = 1.0
        utterance.volume = 1.0

        if synthesizer.isSpeaking {
            utteranceQueue.append(utterance)
        } else {
            synthesizer.speak(utterance)
            isSpeakingFlag = true
        }

        call.resolve(["queued": true])
    }

    /// Stop all speech immediately and clear the queue.
    @objc func stop(_ call: CAPPluginCall) {
        synthesizer.stopSpeaking(at: .immediate)
        utteranceQueue.removeAll()
        isSpeakingFlag = false
        call.resolve(["stopped": true])
    }

    /// Return whether the synthesizer is currently speaking or has queued text.
    @objc func isSpeaking(_ call: CAPPluginCall) {
        call.resolve([
            "speaking": synthesizer.isSpeaking || !utteranceQueue.isEmpty,
        ])
    }
}

// MARK: - AVSpeechSynthesizerDelegate
extension NativeTTSPlugin: AVSpeechSynthesizerDelegate {

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                   didFinish utterance: AVSpeechUtterance) {
        // Dequeue the next utterance if any
        if let next = utteranceQueue.first {
            utteranceQueue.removeFirst()
            synthesizer.speak(next)
        } else {
            isSpeakingFlag = false
            // Notify the web layer via a Capacitor event
            notifyListeners("onFinished", data: [:])
        }
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                   didCancel utterance: AVSpeechUtterance) {
        isSpeakingFlag = false
    }
}
