using System;
using System.Collections.Generic;
using System.Linq;

namespace AUTelemetryProbe;

// A continuously observed opportunity, not a sleep after clicking the button.
internal static class KillSafetyObservation
{
    private sealed class Window
    {
        internal DateTimeOffset Since;
        internal double Required = 3 + Random.NextDouble() * 2;
    }
    private static readonly Random Random = new();
    private static readonly Dictionary<byte, Window> Windows = new();
    private static DateTimeOffset _lastScan;
    internal static List<KillSafetyInfo> Current { get; private set; } = new();

    internal static void Update(AwarenessFrame frame, DateTimeOffset now)
    {
        if (frame.Diagnostics.Status != "ok" || PlayerControl.LocalPlayer?.Data?.RoleType.ToString() != "Impostor" ||
            PlayerControl.LocalPlayer.inVent || Minigame.Instance != null)
        { Reset(); return; }
        if ((now - _lastScan).TotalSeconds is > .5 or < 0) Windows.Clear();
        _lastScan = now;
        Current = new();
        var lightsActive = EmergencyObservation.LightsActive();
        var eligible = frame.Players.Where(p => p.Player.Data?.Role?.IsImpostor != true).ToArray();
        var ids = eligible.Select(p => p.Player.PlayerId).ToHashSet();
        foreach (var id in Windows.Keys.Where(id => !ids.Contains(id)).ToArray()) Windows.Remove(id);
        foreach (var p in eligible)
        {
            var id = p.Player.PlayerId;
            if (!Windows.TryGetValue(id, out var window)) Windows[id] = window = new Window { Since = now };
            var currentWitness = PlayerAwareness.HasWitness(frame, id, p.Position, memorySeconds: -1);
            if (currentWitness) window.Since = now;
            // The same 3-5s window includes a witness just turning a corner.
            // Historical observations still remain available for ten seconds.
            var witness = PlayerAwareness.HasWitness(frame, id, p.Position, window.Required);
            // Blackout removes only the extra countdown. Witness checks remain,
            // and repaired lights begin a new normal observation period.
            if (lightsActive) window.Since = now;
            var clear = Math.Max(0, (now - window.Since).TotalSeconds);
            Current.Add(new KillSafetyInfo { TargetPlayerId = id, RequiredSeconds = lightsActive ? 0 : window.Required,
                WitnessMemorySeconds = window.Required, LightsWaitBypassed = lightsActive,
                ClearSeconds = Math.Round(clear, 2), Ready = !witness && (lightsActive || clear >= window.Required),
                Reason = witness ? "当前或刚离开的玩家可能目击，重新判断" : lightsActive ? "照明破坏已生效，免除额外读秒；仍需原生距离判定"
                    : clear < window.Required ? "连续观察周围安全，尚未满足3至5秒" : "安全观察已满足，仍须原生距离判定" });
        }
    }
    internal static void Reset() { Windows.Clear(); Current = new(); _lastScan = default; }
}
internal sealed class KillSafetyInfo
{
    public byte TargetPlayerId { get; init; }
    public double RequiredSeconds { get; init; }
    public double? WitnessMemorySeconds { get; init; }
    public bool LightsWaitBypassed { get; init; }
    public double ClearSeconds { get; init; }
    public bool Ready { get; init; }
    public string Reason { get; init; } = "";
}
