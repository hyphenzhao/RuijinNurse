using UnityEngine;

public class InputDebugOverlay : MonoBehaviour
{
    string last;
    void Update()
    {
        if (Input.anyKeyDown) last = "Some key pressed";
        if (Input.GetKeyDown(KeyCode.Alpha1)) last = "Alpha1";
        if (Input.GetKeyDown(KeyCode.Alpha2)) last = "Alpha2";
        if (Input.GetKeyDown(KeyCode.Alpha3)) last = "Alpha3";
        if (Input.GetKeyDown(KeyCode.Space)) last = "Space";
    }
    void OnGUI()
    {
        GUI.Label(new Rect(10, 10, 400, 25), $"Update OK. Last key: {last}");
    }
}