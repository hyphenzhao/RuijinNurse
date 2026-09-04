import SwiftUI
import WebKit

/// Renders markdown in a WKWebView using the same marked.js + DOMPurify
/// pipeline as the web UI, so formatting (headings, lists, tables, code)
/// matches the website exactly.
///
/// Reports its content height back through a binding so the SwiftUI bubble
/// can size itself (the webview itself never scrolls).
struct MarkdownWebView: UIViewRepresentable {
    let content: String
    /// Base URL used to resolve relative links (e.g. `static/...`) in the content.
    let baseURL: URL?
    @Binding var height: CGFloat

    func makeCoordinator() -> Coordinator {
        Coordinator { newHeight in
            DispatchQueue.main.async {
                if abs(height - newHeight) > 0.5 {
                    height = newHeight
                }
            }
        }
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        let controller = WKUserContentController()
        controller.add(context.coordinator, name: "heightReporter")
        config.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        webView.scrollView.isScrollEnabled = false
        webView.scrollView.bounces = false
        webView.scrollView.pinchGestureRecognizer?.isEnabled = false
        webView.navigationDelegate = context.coordinator
        context.coordinator.webView = webView
        context.coordinator.baseURL = baseURL

        webView.loadHTMLString(Self.buildTemplate(), baseURL: baseURL)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard content != context.coordinator.lastRenderedContent else { return }
        if context.coordinator.pageLoaded {
            context.coordinator.lastRenderedContent = content
            webView.evaluateJavaScript("window.renderContent(\(Self.jsString(content)));", completionHandler: nil)
        } else {
            // Page still loading — render as soon as it finishes
            context.coordinator.pendingContent = content
        }
    }

    // MARK: - Coordinator

    class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        let onHeightChange: (CGFloat) -> Void
        weak var webView: WKWebView?
        var baseURL: URL?
        var pageLoaded = false
        var pendingContent: String?
        var lastRenderedContent: String?

        init(onHeightChange: @escaping (CGFloat) -> Void) {
            self.onHeightChange = onHeightChange
        }

        deinit {
            webView?.configuration.userContentController
                .removeScriptMessageHandler(forName: "heightReporter")
        }

        // MARK: WKScriptMessageHandler

        func userContentController(_ userContentController: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard message.name == "heightReporter" else { return }
            let h: CGFloat
            if let d = message.body as? Double { h = CGFloat(d) }
            else if let i = message.body as? Int { h = CGFloat(i) }
            else { return }
            onHeightChange(h)
        }

        // MARK: WKNavigationDelegate

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            pageLoaded = true
            let content = pendingContent ?? lastRenderedContent ?? ""
            if pendingContent != nil { pendingContent = nil }
            webView.evaluateJavaScript("window.renderContent(\(MarkdownWebView.jsString(content)));", completionHandler: nil)
        }

        /// Recover when the system kills the WebContent process (memory pressure
        /// from the Unity scene etc.) — otherwise the bubble would stay blank.
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            print("[MarkdownWebView] WebContent 进程被终止，重新加载渲染器")
            pageLoaded = false
            webView.loadHTMLString(MarkdownWebView.buildTemplate(), baseURL: baseURL)
        }

        /// Open links externally instead of navigating inside the bubble.
        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            if navigationAction.navigationType == .linkActivated,
               let url = navigationAction.request.url,
               url.scheme == "http" || url.scheme == "https" {
                UIApplication.shared.open(url)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }
    }

    // MARK: - HTML Template

    private static func jsString(_ s: String) -> String {
        // Wrap in a JSON array so escaping is handled correctly
        if let data = try? JSONSerialization.data(withJSONObject: [s]),
           let json = String(data: data, encoding: .utf8) {
            return String(json.dropFirst().dropLast())  // strip [ ]
        }
        return "''"
    }

    /// Build the HTML page. JS libraries are loaded from the app bundle so
    /// rendering works even with no server connection.
    private static func buildTemplate() -> String {
        let mdJS = bundleFile("markdown-it.min", "js") ?? ""
        let purifyJS = bundleFile("purify.min", "js") ?? ""
        let hasLibs = !mdJS.isEmpty && !purifyJS.isEmpty

        // Mirrors promotion.html: markdown-it with html:false, linkify:true,
        // breaks:true, then DOMPurify.sanitize.
        let renderScript = hasLibs ? """
        var md = window.markdownit({ html: false, linkify: true, breaks: true });
        // Coalesce rapid streaming updates to at most one render per frame
        // (streaming can deliver 50+ deltas per second — full re-parse per
        // delta would freeze the UI on long answers).
        var pendingText = null, frameScheduled = false;
        function renderContent(text) {
          pendingText = text;
          if (!frameScheduled) {
            frameScheduled = true;
            requestAnimationFrame(function() {
              frameScheduled = false;
              var t = pendingText;
              pendingText = null;
              doRender(t);
            });
          }
        }
        function doRender(text) {
          text = (text || '').replace(/!\\[[^\\]]*\\]\\([^)]*\\)/g, '');
          var html = md.render(text);
          html = html.replace(/href="static\\//g, 'href="/static/');
          html = html.replace(/src="static\\//g, 'src="/static/');
          html = DOMPurify.sanitize(html);
          document.getElementById('content').innerHTML = html;
          reportHeight();
        }
        """ : """
        // Fallback: libraries missing from bundle — show plain text
        var pendingText = null, frameScheduled = false;
        function renderContent(text) {
          pendingText = text;
          if (!frameScheduled) {
            frameScheduled = true;
            requestAnimationFrame(function() {
              frameScheduled = false;
              var el = document.getElementById('content');
              el.style.whiteSpace = 'pre-wrap';
              el.textContent = pendingText || '';
              pendingText = null;
              reportHeight();
            });
          }
        }
        """

        return #"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
        <style>
        html, body { margin: 0; padding: 0; background: transparent; }
        body {
          font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
          font-size: 15px;
          color: #212529;
          word-break: break-word;
          -webkit-text-size-adjust: 100%;
        }
        #content { white-space: normal; }
        .markdown-body { line-height: 1.5; }
        .markdown-body p { margin: 0 0 .35rem; }
        .markdown-body p:last-child { margin-bottom: 0; }
        .markdown-body ul, .markdown-body ol { padding-left: 1.25rem; margin: .25rem 0 .4rem; }
        .markdown-body li { margin-bottom: .1rem; }
        .markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4,
        .markdown-body h5, .markdown-body h6 { font-size: 1rem; font-weight: 600; margin: .75rem 0 .5rem; }
        .markdown-body a { color: #0d6efd; word-break: break-all; }
        .markdown-body code { background: #eef2f7; padding: .1rem .3rem; border-radius: .25rem; font-size: .9em; }
        .markdown-body pre { background: #111827; color: #f9fafb; padding: .75rem; border-radius: .5rem; overflow-x: auto; }
        .markdown-body pre code { background: transparent; padding: 0; color: inherit; }
        .markdown-body table { border-collapse: collapse; margin: .5rem 0; max-width: 100%; display: block; overflow-x: auto; }
        .markdown-body th, .markdown-body td { border: 1px solid #dee2e6; padding: .35rem .5rem; }
        .markdown-body th { background: #f8f9fa; font-weight: 600; }
        .markdown-body img { max-width: 100%; border-radius: .5rem; }
        .markdown-body blockquote { margin: .5rem 0; padding: .25rem .75rem; border-left: 3px solid #dee2e6; color: #6c757d; }
        .markdown-body hr { border: none; border-top: 1px solid #dee2e6; margin: .75rem 0; }
        </style>
        </head>
        <body>
        <div class="markdown-body" id="content"></div>
        <script>\#(mdJS)</script>
        <script>\#(purifyJS)</script>
        <script>
        \#(renderScript)
        function reportHeight() {
          requestAnimationFrame(function() {
            var h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
            if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.heightReporter) {
              window.webkit.messageHandlers.heightReporter.postMessage(h);
            }
          });
        }
        window.addEventListener('resize', reportHeight);
        document.addEventListener('load', function(e) { if (e.target.tagName === 'IMG') reportHeight(); }, true);
        </script>
        </body>
        </html>
        """#
    }

    private static func bundleFile(_ name: String, _ ext: String) -> String? {
        guard let url = Bundle.main.url(forResource: name, withExtension: ext,
                                        subdirectory: "markdown-assets"),
              let s = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        return s
    }
}
