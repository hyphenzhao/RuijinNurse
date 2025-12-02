using System.Collections;
using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

public class MotionBridge : MonoBehaviour
{
    [Header("Required")]
    public Animator animator;

    [Header("Animator Layer")]
    [SerializeField] int layerIndex = 0;   // Base Layer

    [Header("State names (must match Animator states)")]
    public string listenState = "Listen";
    public string talkState = "Talk";
    public string greetState = "Greet";

    [Header("Defaults")]
    public string defaultState = "Greet";  // <<< DEFAULT is Greet now

    [Header("Optional clip refs (for timing one-shots if you ever want them)")]
    public AnimationClip listenClip;
    public AnimationClip talkClip;
    public AnimationClip greetClip;

    [Header("Crossfade")]
    [Range(0f, 1f)] public float fadeSeconds = 0.15f;

    float _savedSpeed = 1f;

    void Reset() { animator = GetComponent<Animator>(); }

    void Awake()
    {
        if (!animator) animator = GetComponent<Animator>();
        animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
        animator.updateMode = AnimatorUpdateMode.Normal;
        animator.speed = 1f;
        Time.timeScale = 1f;
        _savedSpeed = 1f;

        // Start in the chosen default (Greet)
        Crossfade(defaultState, log: true);
    }

    void Update()
    {
#if UNITY_EDITOR
        bool k1 = false, k2 = false, k3 = false, pause = false, slower = false, faster = false, restart = false;
#if ENABLE_INPUT_SYSTEM
        var kb = Keyboard.current;
        if (kb != null) {
            k1 = kb.digit1Key.wasPressedThisFrame || kb.numpad1Key.wasPressedThisFrame; // Talk
            k2 = kb.digit2Key.wasPressedThisFrame || kb.numpad2Key.wasPressedThisFrame; // Greet
            k3 = kb.digit3Key.wasPressedThisFrame || kb.numpad3Key.wasPressedThisFrame; // Listen
            pause   = kb.spaceKey.wasPressedThisFrame;
            slower  = kb.leftBracketKey.wasPressedThisFrame;
            faster  = kb.rightBracketKey.wasPressedThisFrame;
            restart = kb.rKey.wasPressedThisFrame;
        }
#else
        k1 = Input.GetKeyDown(KeyCode.Alpha1) || Input.GetKeyDown(KeyCode.Keypad1);
        k2 = Input.GetKeyDown(KeyCode.Alpha2) || Input.GetKeyDown(KeyCode.Keypad2);
        k3 = Input.GetKeyDown(KeyCode.Alpha3) || Input.GetKeyDown(KeyCode.Keypad3);
        pause = Input.GetKeyDown(KeyCode.Space);
        slower = Input.GetKeyDown(KeyCode.LeftBracket);
        faster = Input.GetKeyDown(KeyCode.RightBracket);
        restart = Input.GetKeyDown(KeyCode.R);
#endif
        if (k1) PlayTalk();
        if (k2) PlayGreet();
        if (k3) PlayListen();
        if (pause) TogglePause();
        if (slower) SetSpeed(Mathf.Max(0.1f, animator.speed - 0.1f));
        if (faster) SetSpeed(Mathf.Min(2.0f, animator.speed + 0.1f));
        if (restart) RestartCurrent();
#endif
    }

    // --- High-level API ---
    public void PlayListen() => Crossfade(listenState);
    public void PlayTalk() => Crossfade(talkState);

    // Greet is now a looping default; no auto-return.
    public void PlayGreet() => Crossfade(greetState);

    // Stop returns to the default (Greet by default)
    public void Stop() => Crossfade(defaultState);

    public void SetSpeed(float s)
    {
        animator.speed = Mathf.Clamp(s, 0f, 2.0f);
        if (animator.speed > 0f) _savedSpeed = animator.speed;
    }
    public void Pause() => SetSpeed(0f);
    public void Resume() => SetSpeed(Mathf.Max(_savedSpeed, 1f));
    public void TogglePause() { if (Mathf.Approximately(animator.speed, 0f)) Resume(); else Pause(); }

    public void RestartCurrent()
    {
        var st = animator.GetCurrentAnimatorStateInfo(layerIndex);
        animator.Play(st.shortNameHash, layerIndex, 0f);
    }

    // --- WebGL/JavaScript entry points ---
    public void PlayJS(string name)
    {
        name = (name ?? "").ToLowerInvariant();
        switch (name)
        {
            case "listen": PlayListen(); break;
            case "talk": PlayTalk(); break;
            case "greet": PlayGreet(); break;
            default: Debug.LogWarning($"Unknown motion '{name}'. Use: listen | talk | greet"); break;
        }
    }
    public void SetSpeedJS(string value) { if (float.TryParse(value, out var s)) SetSpeed(s); }
    public void StopJS() => Stop();
    public void PauseJS() => Pause();
    public void ResumeJS() => Resume();

    // optional: set default from JS ("greet" | "listen" | "talk"), then stop to go there
    public void SetDefaultJS(string name)
    {
        name = (name ?? "").ToLowerInvariant();
        switch (name)
        {
            case "greet": defaultState = greetState; break;
            case "listen": defaultState = listenState; break;
            case "talk": defaultState = talkState; break;
            default: Debug.LogWarning("Unknown default '" + name + "'."); break;
        }
    }

    // --- helpers ---
    void Crossfade(string state, bool log = false)
    {
        if (!animator) { Debug.LogError("MotionBridge: Animator is null."); return; }
        int hash = Animator.StringToHash(state);
        if (!animator.HasState(layerIndex, hash))
        {
            Debug.LogError($"MotionBridge: State '{state}' not found on layer {layerIndex}. Check state name & layer.");
            return;
        }
        if (log) Debug.Log($"MotionBridge: Crossfading to '{state}' on layer {layerIndex}");
        animator.CrossFadeInFixedTime(hash, fadeSeconds, layerIndex);
    }
    void Crossfade(string state) => Crossfade(state, false);

#if UNITY_EDITOR
    void OnGUI()
    {
        if (!animator) return;
        var st = animator.GetCurrentAnimatorStateInfo(layerIndex);
        var infos = animator.GetCurrentAnimatorClipInfo(layerIndex);
        string clip = (infos.Length > 0 && infos[0].clip) ? infos[0].clip.name : "(none)";
        GUI.Label(new Rect(10, 10, 900, 22),
          $"State clip: {clip}   normTime:{st.normalizedTime:0.00}   loop:{(st.loop ? "yes" : "no")}   speed:{animator.speed:0.00}   timeScale:{Time.timeScale:0.00}");
    }
#endif
}
