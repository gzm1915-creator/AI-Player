using System;
using System.Collections.Generic;

namespace AUTelemetryProbe;

internal static class EmergencyObservation
{
    internal static bool LightsActive()
    {
        var ship = ShipStatus.Instance;
        if (ship == null || ship.Type.ToString() != "Ship") return false;
        try { return ship.Systems.TryGetValue(SystemTypes.Electrical, out var system) && system.TryCast<SwitchSystem>()?.IsActive == true; }
        catch { return false; }
    }
    internal static string? WindowTaskType(Minigame? window)
    {
        if (window == null) return null;
        if (window.MyTask != null) return window.MyTask.TaskType.ToString();
        // Exact native window classes, never proximity or nearest-task guesses.
        if (window.TryCast<SwitchMinigame>() != null) return "FixLights";
        if (window.TryCast<KeypadGame>() != null) return "RestoreOxy";
        if (window.TryCast<TuneRadioMinigame>() != null) return "FixComms";
        if (window.TryCast<ReactorMinigame>() != null) return "ResetReactor";
        return null;
    }

    internal static List<string>? Active()
    {
        var ship = ShipStatus.Instance;
        if (ship == null || ship.Type.ToString() != "Ship") return null;
        try
        {
            var result = new List<string>();
            if (ship.Systems.TryGetValue(SystemTypes.Electrical, out var lights) && lights.TryCast<SwitchSystem>()?.IsActive == true)
                result.Add("FixLights");
            if (ship.Systems.TryGetValue(SystemTypes.Reactor, out var reactor) && reactor.TryCast<ReactorSystemType>()?.IsActive == true)
                result.Add("ResetReactor");
            if (ship.Systems.TryGetValue(SystemTypes.LifeSupp, out var oxygen) && oxygen.TryCast<LifeSuppSystemType>()?.IsActive == true)
                result.Add("RestoreOxy");
            if (ship.Systems.TryGetValue(SystemTypes.Comms, out var comms) && comms.TryCast<HudOverrideSystemType>()?.IsActive == true)
                result.Add("FixComms");
            return result;
        }
        catch { return null; } // Unavailable must not look like a repaired system.
    }
}
