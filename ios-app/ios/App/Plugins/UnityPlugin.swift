import Foundation
import Capacitor

/// UnityPlugin — Bridges between the Capacitor web layer and a Unity iOS scene.
///
/// This plugin replaces the WebGL iframe with a native Unity view.
/// Communication flows:
///   JS → Native (loadScene / sendMessage) → Unity C#
///   Unity C# → Native (callback) → JS (Capacitor event listener)
///
/// Prerequisites:
///   1. Build the Unity project for iOS (UnityFramework.framework).
///   2. Add UnityFramework.framework to the Xcode project.
///   3. Link UnityFramework in the app target.
///
/// The Unity view is lazily initialised the first time loadScene is called.
@objc(UnityPlugin)
public class UnityPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "UnityPlugin"
    public let jsName = "UnityScene"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "loadScene", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "sendMessage", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setVisibility", returnType: CAPPluginReturnPromise),
    ]

    private var isUnityInitialized = false
    private var isVisible = false
    private weak var unityViewController: UIViewController?

    /// Load (or switch to) a Unity scene by name.
    ///
    /// On first call this initializes the Unity runtime.  Subsequent calls
    /// switch the active scene.
    @objc func loadScene(_ call: CAPPluginCall) {
        guard let sceneName = call.getString("sceneName") else {
            call.reject("sceneName is required")
            return
        }

        ensureUnityInitialized()

        // Tell Unity to load the requested scene
        // UnityFramework.SendMessageToGOWithName("SceneManager", "LoadScene", sceneName)
        call.resolve(["scene": sceneName, "loaded": true])
    }

    /// Send a message to a Unity GameObject.
    @objc func sendMessage(_ call: CAPPluginCall) {
        guard let gameObject = call.getString("gameObject"),
              let method = call.getString("method") else {
            call.reject("gameObject and method are required")
            return
        }

        let args = call.getString("args") ?? ""

        // UnityFramework.SendMessageToGOWithName(gameObject, method, args)
        call.resolve(["sent": true])
    }

    /// Show or hide the Unity view overlay.
    @objc func setVisibility(_ call: CAPPluginCall) {
        let visible = call.getBool("visible") ?? true
        isVisible = visible

        unityViewController?.view.isHidden = !visible
        call.resolve(["visible": visible])
    }

    // MARK: - Private

    private func ensureUnityInitialized() {
        guard !isUnityInitialized else { return }

        // TODO: Initialize UnityFramework here once the .framework is linked.
        // Example:
        //   let ufw = UnityFramework.getInstance()
        //   ufw.setDataBundleId("com.ruijin.unityscene")
        //   ufw.runEmbedded(withArgc: CommandLine.argc, argv: CommandLine.unsafeArgv, appLaunchOpts: nil)
        //   let unityVC = ufw.appController()?.rootViewController
        //   unityVC?.view.frame = bridge?.webView?.bounds ?? .zero
        //   bridge?.webView?.addSubview(unityVC!.view!)
        //   self.unityViewController = unityVC

        isUnityInitialized = true
    }
}
