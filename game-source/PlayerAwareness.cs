using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace AUTelemetryProbe;

// A shared local observation for telemetry, pursuit and the final kill guard.
// Unseen players are never refreshed from their live transform.
internal static class PlayerAwareness
{
    internal const double MemorySeconds = 10;
    private static readonly Dictionary<byte, SeenPlayer> Memory = new();
    private static PlayerControl[] _scene = Array.Empty<PlayerControl>();
    private static DateTimeOffset _nextScan, _nextSceneScan;
    private static string _identity = "";
    internal static AwarenessFrame Current { get; private set; } = new();

    internal static AwarenessFrame Scan(bool force = false, DateTimeOffset? at = null)
    {
        var now = at ?? DateTimeOffset.UtcNow;
        var local = PlayerControl.LocalPlayer;
        var identity = $"{TelemetryCapture.CurrentSessionId}/{TaskStartSignalPatch.SignalVersion}/{local?.GetInstanceID()}";
        if (identity != _identity || MeetingHud.Instance != null || ExileController.Instance != null ||
            GameManager.Instance?.GameHasStarted != true)
        {
            Reset();
            _identity = identity;
        }
        if (!force && now < _nextScan) return Current;
        _nextScan = now.AddMilliseconds(PlayerView.ScanIntervalMs);
        var frame = new AwarenessFrame();
        if (local?.Data == null || local.Data.IsDead) KillSafetyObservation.Reset();
        Current = frame;
        var diagnostic = frame.Diagnostics;
        diagnostic.UpdatedAtUtc = now;
        try
        {
            if (local?.Data == null || local.Data.IsDead || MeetingHud.Instance != null ||
                ExileController.Instance != null || GameManager.Instance?.GameHasStarted != true)
            { diagnostic.Status = "local_player_unavailable"; return frame; }
            var camera = PlayerView.Select(Camera.allCameras, local.transform.position, local.gameObject.layer, out var details);
            diagnostic.CameraDiagnostics = details;
            diagnostic.CameraName = camera?.name;
            if (camera == null) { KillSafetyObservation.Reset(); diagnostic.Status = "camera_unavailable"; return frame; }
            var candidates = new Dictionary<int, PlayerControl>();
            foreach (var p in PlayerControl.AllPlayerControls)
                if (p != null) candidates[p.GetInstanceID()] = p;
            if (now >= _nextSceneScan)
            {
                _nextSceneScan = now.AddSeconds(1);
                _scene = UnityEngine.Object.FindObjectsOfType<PlayerControl>();
            }
            foreach (var p in _scene)
                if (p != null) candidates[p.GetInstanceID()] = p;
            diagnostic.Scanned = candidates.Count;
            var origin = local.GetTruePosition();
            if (!Finite(origin)) { KillSafetyObservation.Reset(); diagnostic.Status = "position_unavailable"; return frame; }
            foreach (var p in candidates.Values)
            {
                try
                {
                    if (p.GetInstanceID() == local.GetInstanceID()) continue;
                    if (p.Data == null || p.Data.IsDead || p.Data.Disconnected || p.inVent || !p.gameObject.activeInHierarchy)
                    { diagnostic.Inactive++; continue; }
                    var position = p.GetTruePosition();
                    if (!Finite(position)) { diagnostic.Errors++; continue; }
                    var distance = Vector2.Distance(origin, position);
                    if (distance > PlayerView.DetectionRadius) { diagnostic.OutsideRadius++; continue; }
                    if ((camera.cullingMask & (1 << p.gameObject.layer)) == 0 ||
                        !PlayerView.Contains(camera, p.transform.position, PlayerView.DetectionViewportMargin))
                    { diagnostic.OutsideView++; continue; }
                    if (PhysicsHelpers.AnythingBetween(origin, position, Constants.ShipOnlyMask, false))
                    { diagnostic.Occluded++; continue; }
                    var velocity = new Vector2(0, 0);
                    try { velocity = p.MyPhysics?.GetVelocity() ?? velocity; } catch { }
                    if (!Finite(velocity)) velocity = new Vector2(0, 0);
                    frame.Players.Add(new ObservedPlayer { Player = p, Position = position, Velocity = velocity,
                        Distance = distance, InCameraView = PlayerView.Contains(camera, p.transform.position) });
                    Memory[p.PlayerId] = new SeenPlayer { PlayerId = p.PlayerId, PlayerName = p.Data.PlayerName,
                        X = position.x, Y = position.y, VelocityX = velocity.x, VelocityY = velocity.y,
                        At = now, Teammate = local.Data.Role?.IsImpostor == true && p.Data.Role?.IsImpostor == true };
                }
                catch (Exception e) { diagnostic.Errors++; diagnostic.LastError = e.Message; }
            }
            foreach (var id in Memory.Where(pair => (now - pair.Value.At).TotalSeconds > MemorySeconds).Select(pair => pair.Key).ToArray())
                Memory.Remove(id);
            var visible = new HashSet<byte>(frame.Players.Select(p => p.Player.PlayerId));
            foreach (var seen in Memory.Values.Where(p => !visible.Contains(p.PlayerId)))
            {
                var age = Math.Max(0, (now - seen.At).TotalSeconds);
                frame.RecentPlayers.Add(new RecentPlayerInfo { PlayerId = seen.PlayerId, PlayerName = seen.PlayerName,
                    X = seen.X, Y = seen.Y, VelocityX = seen.VelocityX, VelocityY = seen.VelocityY,
                    LastSeenAtUtc = seen.At, AgeSeconds = Math.Round(age, 2),
                    UncertaintyRadius = Math.Round(Math.Min(6, age * Math.Max(1.5, Math.Sqrt(seen.VelocityX * seen.VelocityX + seen.VelocityY * seen.VelocityY))), 2),
                    IsTeammate = seen.Teammate });
            }
            diagnostic.Visible = frame.Players.Count;
            diagnostic.Status = diagnostic.Errors == 0 ? "ok" : "partial";
            KillSafetyObservation.Update(frame, now);
        }
        catch (Exception e) { KillSafetyObservation.Reset(); diagnostic.Status = "error"; diagnostic.Errors++; diagnostic.LastError = e.Message; }
        return frame;
    }

    internal static bool HasWitness(AwarenessFrame frame, byte targetId, Vector2 targetPosition, double memorySeconds = 5)
    {
        var local = PlayerControl.LocalPlayer;
        if (local == null || frame.Diagnostics.Status != "ok") return true;
        var origin = local.GetTruePosition();
        foreach (var p in frame.Players)
            if (p.Player.PlayerId != targetId && p.Player.Data?.Role?.IsImpostor != true &&
                (Vector2.Distance(p.Position, targetPosition) <= 4 || Vector2.Distance(p.Position, origin) <= 4)) return true;
        return frame.RecentPlayers.Any(p => p.PlayerId != targetId && !p.IsTeammate && p.AgeSeconds <= memorySeconds &&
            (Vector2.Distance(new Vector2(p.X, p.Y), targetPosition) <= 4 + p.UncertaintyRadius ||
             Vector2.Distance(new Vector2(p.X, p.Y), origin) <= 4 + p.UncertaintyRadius));
    }

    internal static void Reset()
    {
        KillSafetyObservation.Reset();
        Memory.Clear(); _scene = Array.Empty<PlayerControl>();
        _nextScan = _nextSceneScan = default; Current = new();
    }
    private static bool Finite(Vector2 p) => !float.IsNaN(p.x) && !float.IsInfinity(p.x) && !float.IsNaN(p.y) && !float.IsInfinity(p.y);
    private sealed class SeenPlayer
    {
        public byte PlayerId; public string PlayerName = ""; public float X, Y, VelocityX, VelocityY;
        public DateTimeOffset At; public bool Teammate;
    }
}

internal sealed class AwarenessFrame
{
    internal PlayerDetectionInfo Diagnostics { get; } = new();
    internal List<ObservedPlayer> Players { get; } = new();
    internal List<RecentPlayerInfo> RecentPlayers { get; } = new();
}
internal sealed class ObservedPlayer
{
    internal PlayerControl Player = null!;
    internal Vector2 Position, Velocity;
    internal float Distance;
    internal bool InCameraView;
}
internal sealed class RecentPlayerInfo
{
    public byte PlayerId { get; init; }
    public string PlayerName { get; init; } = "";
    public string Presence => "last_seen_only";
    public float X { get; init; }
    public float Y { get; init; }
    public float VelocityX { get; init; }
    public float VelocityY { get; init; }
    public DateTimeOffset LastSeenAtUtc { get; init; }
    public double AgeSeconds { get; init; }
    public double UncertaintyRadius { get; init; }
    public bool IsTeammate { get; init; }
}
internal sealed class PlayerDetectionInfo
{
    public float DetectionRadius { get; init; } = PlayerView.DetectionRadius;
    public float ViewportMargin { get; init; } = PlayerView.DetectionViewportMargin;
    public double RecentMemorySeconds { get; init; } = PlayerAwareness.MemorySeconds;
    public bool AwarenessSupported => true;
    public int ScanIntervalMs { get; init; } = PlayerView.ScanIntervalMs;
    public string Status { get; set; } = "waiting";
    public DateTimeOffset UpdatedAtUtc { get; set; }
    public int Scanned { get; set; }
    public int Visible { get; set; }
    public int Inactive { get; set; }
    public int OutsideRadius { get; set; }
    public int OutsideView { get; set; }
    public int Occluded { get; set; }
    public int Errors { get; set; }
    public string LastError { get; set; } = "";
    public string? CameraName { get; set; }
    public string CameraDiagnostics { get; set; } = "";
}
