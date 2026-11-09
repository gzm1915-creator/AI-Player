using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace AUTelemetryProbe;

internal static class CrimeSceneMemory
{
    private static string _identity = "";
    private static readonly List<CrimeSceneInfo> Scenes = new();
    private static void EnsureIdentity()
    {
        var identity = $"{TelemetryCapture.CurrentSessionId}/{TaskStartSignalPatch.SignalVersion}";
        if (identity == _identity) return;
        _identity = identity; Scenes.Clear();
    }
    internal static void Remember(PlayerControl killer, PlayerControl victim)
    {
        EnsureIdentity();
        if (killer == null || victim == null || killer != PlayerControl.LocalPlayer || killer.Data?.Role?.IsImpostor != true || victim.Data?.IsDead != true ||
            Scenes.Any(s => s.TargetPlayerId == victim.PlayerId)) return;
        var position = victim.GetTruePosition();
        if (float.IsNaN(position.x) || float.IsNaN(position.y) || float.IsInfinity(position.x) || float.IsInfinity(position.y)) return;
        Scenes.Add(new CrimeSceneInfo { TargetPlayerId = victim.PlayerId, X = position.x, Y = position.y, AtUtc = DateTimeOffset.UtcNow });
    }
    internal static List<CrimeSceneInfo> Capture() { EnsureIdentity(); return Scenes.ToList(); }
    internal static bool Allows(Vector2 from, Vector2 to)
    {
        EnsureIdentity();
        return PlayerControl.LocalPlayer?.Data?.Role?.IsImpostor != true ||
            Scenes.All(s => CrimeSceneGeometry.Allows(from, to, new Vector2(s.X, s.Y), s.AvoidRadius));
    }
}
internal sealed class CrimeSceneInfo
{
    public byte TargetPlayerId { get; init; }
    public float X { get; init; }
    public float Y { get; init; }
    public float AvoidRadius { get; init; } = 3;
    public DateTimeOffset AtUtc { get; init; }
    public string Source => "native_local_murder";
    public string Visibility => "impostor_private";
}
