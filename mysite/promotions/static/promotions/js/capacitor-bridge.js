/**
 * RuijinNurse Capacitor Bridge
 * ============================
 *
 * Detects whether the app is running inside a Capacitor WKWebView and
 * wires up native capabilities (TTS, Unity, Server Config) replacing the
 * browser-only implementations.
 *
 * Include this script AFTER the main page JS but BEFORE any manual
 * TTS / Unity initialization.
 *
 * Usage in promotion.html:
 *   <script src="{% static 'promotions/js/capacitor-bridge.js' %}"></script>
 */

(function () {
  'use strict';

  // ---- 1. Environment detection ----
  var IS_CAPACITOR = !!(window.Capacitor && window.Capacitor.Plugins);
  var NativeTTS = IS_CAPACITOR && window.Capacitor.Plugins.NativeTTS;
  var UnityScene = IS_CAPACITOR && window.Capacitor.Plugins.UnityScene;
  var ServerConfig = IS_CAPACITOR && window.Capacitor.Plugins.ServerConfig;

  // Expose for use by other scripts
  window.RuijinNurse = window.RuijinNurse || {};
  window.RuijinNurse.isCapacitor = IS_CAPACITOR;
  window.RuijinNurse.NativeTTS = NativeTTS || null;
  window.RuijinNurse.UnityScene = UnityScene || null;
  window.RuijinNurse.ServerConfig = ServerConfig || null;

  console.log(
    '[RuijinNurse] Capacitor detected: ' + IS_CAPACITOR +
    ' | TTS: ' + !!NativeTTS +
    ' | Unity: ' + !!UnityScene +
    ' | ServerConfig: ' + !!ServerConfig
  );

  // ---- 2. Native TTS integration ----
  if (NativeTTS) {
    /**
     * Override the global TTS function used by the chat page.
     * The original code in promotion.html calls something like:
     *   window.speechSynthesis.speak(utterance)
     * or a custom speak() function.
     *
     * We wrap the native TTS with the same interface so the
     * existing "朗读" button code works unchanged.
     */
    var ttsQueue = [];
    var ttsSpeaking = false;
    var ttsStopped = false;

    window.RuijinNurse.speakText = function (text, options) {
      options = options || {};
      var lang = options.lang || 'zh-CN';
      var rate = options.rate || 1.0;

      if (!text || !text.trim()) return;

      // Split long text into sentences for smoother playback
      var sentences = text.match(/[^。！？\n]+[。！？]?/g) || [text];

      function speakNext() {
        if (ttsStopped || sentences.length === 0) {
          ttsSpeaking = false;
          ttsStopped = false;
          return;
        }
        ttsSpeaking = true;
        var chunk = sentences.shift().trim();
        if (!chunk) {
          speakNext();
          return;
        }
        NativeTTS.speak({ text: chunk, lang: lang, rate: rate })
          .then(function () {
            // Wait for onFinished event before speaking next
          })
          .catch(function (err) {
            console.warn('[RuijinNurse] TTS error:', err);
            ttsSpeaking = false;
          });
      }

      // Listen for the "finished" event to dequeue
      if (NativeTTS.addListener) {
        NativeTTS.addListener('onFinished', function () {
          speakNext();
        });
      }

      ttsStopped = false;
      if (!ttsSpeaking) {
        speakNext();
      }
      // If already speaking, the onFinished handler will dequeue
    };

    window.RuijinNurse.stopSpeech = function () {
      ttsStopped = true;
      ttsSpeaking = false;
      ttsQueue = [];
      if (NativeTTS.stop) {
        NativeTTS.stop({});
      }
    };

    window.RuijinNurse.isSpeechSpeaking = function () {
      return ttsSpeaking;
    };
  }

  // ---- 3. Unity scene integration ----
  if (UnityScene) {
    /**
     * Replace the Unity WebGL iframe with the native Unity view.
     * Called after the page DOM is ready.
     */
    window.RuijinNurse.initUnity = function () {
      var unityContainer = document.getElementById('unityContainer');
      var unityIframe = document.getElementById('unityFrame');

      if (unityIframe) {
        // Remove the WebGL iframe
        unityIframe.remove();
        console.log('[RuijinNurse] Unity WebGL iframe removed — switching to native Unity view');
      }

      // Create a container div for the native Unity view
      if (unityContainer) {
        var nativeView = document.createElement('div');
        nativeView.id = 'unityNativeView';
        nativeView.style.cssText = 'width:100%;height:65vh;border:0;border-radius:.5rem;background:#000;';
        unityContainer.appendChild(nativeView);

        // Load the default Unity scene
        UnityScene.loadScene({ sceneName: 'MainScene' })
          .then(function (result) {
            console.log('[RuijinNurse] Unity scene loaded:', result.scene);
          })
          .catch(function (err) {
            console.error('[RuijinNurse] Unity scene load failed:', err);
          });
      }
    };
  }

  // ---- 4. Server configuration integration ----
  if (ServerConfig) {
    /**
     * Open the native server-configuration screen.
     * Call this from a "settings" button in the web UI.
     */
    window.RuijinNurse.openServerSettings = function () {
      // The native settings screen is triggered by a URL scheme or
      // a Capacitor plugin call.  For now, we just fetch the current URL.
      ServerConfig.getServerUrl({})
        .then(function (result) {
          console.log('[RuijinNurse] Current server:', result.url);
          // TODO: Open native settings screen via a scheme or modal
        });
    };
  }

  // ---- 5. Safe-area CSS injection (notch / dynamic island) ----
  if (IS_CAPACITOR) {
    var safeStyle = document.createElement('style');
    safeStyle.textContent =
      ':root {' +
      '  --safe-top: env(safe-area-inset-top, 0px);' +
      '  --safe-bottom: env(safe-area-inset-bottom, 0px);' +
      '  --safe-left: env(safe-area-inset-left, 0px);' +
      '  --safe-right: env(safe-area-inset-right, 0px);' +
      '}' +
      '.app-header { padding-top: calc(10px + var(--safe-top)) !important; }' +
      '.chat-input { padding-bottom: calc(0.75rem + var(--safe-bottom)) !important; }';
    document.head.appendChild(safeStyle);
    console.log('[RuijinNurse] Safe-area CSS injected');
  }

  // ---- 6. Network status monitoring ----
  if (IS_CAPACITOR) {
    var offlineBanner = null;
    function updateNetworkStatus(online) {
      if (!offlineBanner) {
        offlineBanner = document.createElement('div');
        offlineBanner.style.cssText =
          'display:none;position:fixed;top:0;left:0;right:0;padding:0.5rem;' +
          'background:#fff3cd;color:#856404;text-align:center;font-size:0.85rem;z-index:9999;';
        offlineBanner.textContent = '⚠️ 当前无网络连接，显示缓存内容';
        document.body.appendChild(offlineBanner);
      }
      offlineBanner.style.display = online ? 'none' : 'block';
    }

    window.addEventListener('online', function () {
      updateNetworkStatus(true);
    });
    window.addEventListener('offline', function () {
      updateNetworkStatus(false);
    });

    // Initial check
    updateNetworkStatus(navigator.onLine);
  }

  // ---- 7. JWT auth token management ----
  if (IS_CAPACITOR) {
    var Preferences = window.Capacitor.Plugins.Preferences;

    window.RuijinNurse.jwt = {
      getTokens: function () {
        if (!Preferences) {
          return Promise.resolve({ access: null, refresh: null });
        }
        return Promise.all([
          Preferences.get({ key: 'jwt_access' }),
          Preferences.get({ key: 'jwt_refresh' }),
        ]).then(function (results) {
          return {
            access: (results[0] && results[0].value) || null,
            refresh: (results[1] && results[1].value) || null,
          };
        }).catch(function () {
          return { access: null, refresh: null };
        });
      },

      setTokens: function (access, refresh) {
        if (!Preferences) return Promise.resolve();
        return Promise.all([
          Preferences.set({ key: 'jwt_access', value: access || '' }),
          Preferences.set({ key: 'jwt_refresh', value: refresh || '' }),
        ]);
      },

      clearTokens: function () {
        if (!Preferences) return Promise.resolve();
        return Promise.all([
          Preferences.remove({ key: 'jwt_access' }),
          Preferences.remove({ key: 'jwt_refresh' }),
        ]);
      },

      /**
       * Refresh the access token using the stored refresh token.
       * Returns the new access token or null.
       */
      refreshAccessToken: function (serverUrl) {
        var self = this;
        return this.getTokens().then(function (tokens) {
          if (!tokens.refresh) return null;
          return fetch((serverUrl || '') + '/api/v1/auth/refresh/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh: tokens.refresh }),
          })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data.access) {
                return self.setTokens(data.access, tokens.refresh).then(function () {
                  return data.access;
                });
              }
              return null;
            })
            .catch(function () {
              return null;
            });
        });
      },
    };
  }

  console.log('[RuijinNurse] Bridge initialized');
})();
