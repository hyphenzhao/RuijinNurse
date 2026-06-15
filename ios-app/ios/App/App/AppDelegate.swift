import UIKit
import SwiftUI

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)

        // Create the SwiftUI root view with the shared ViewModel
        let viewModel = ChatViewModel()
        let ttsManager = TTSManager()
        let contentView = ContentView()
            .environmentObject(viewModel)
            .environmentObject(ttsManager)

        let hostingController = UIHostingController(rootView: contentView)
        window.rootViewController = hostingController
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
}
