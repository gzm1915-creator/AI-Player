using System;
using UnityEngine;

namespace AUTelemetryProbe;

internal static class PracticeDialog
{
    internal static bool IsCompletionMessage(string text) =>
        (text.Contains("练习模式") && text.Contains("新任务")) ||
        ((text.Contains("practice", StringComparison.OrdinalIgnoreCase) ||
          text.Contains("freeplay", StringComparison.OrdinalIgnoreCase)) &&
         text.Contains("task", StringComparison.OrdinalIgnoreCase));

    internal static bool Tick()
    {
        if (!RuntimeControl.AutoNavigationEnabled || MeetingHud.Instance != null ||
            Minigame.Instance != null) return false;
        var dialogue = HudManager.Instance?.Dialogue;
        if (dialogue == null || !dialogue.gameObject.activeInHierarchy ||
            dialogue.target == null || !IsCompletionMessage(dialogue.target.text ?? string.Empty) ||
            UnityEngine.Object.FindObjectOfType<TutorialManager>() == null) return false;
        dialogue.Hide();
        return true;
    }
}
