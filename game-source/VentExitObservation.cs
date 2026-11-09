using System;
using System.Collections.Generic;
using UnityEngine;

namespace AUTelemetryProbe;

internal static class VentExitObservation
{
    internal static VentExitInfo Scan(bool requireInside = true)
    {
        var result = new VentExitInfo { AtUtc = DateTimeOffset.UtcNow };
        try
        {
            var local = PlayerControl.LocalPlayer;
            var vent = Vent.currentVent;
            if (local?.Data == null || local.Data.IsDead || local.walkingToVent ||
                (requireInside && (!local.inVent || vent == null || Vector2.Distance(local.GetTruePosition(), vent.transform.position) > 1.5f)))
            { result.Status = "position_unconfirmed"; return result; }
            var origin = requireInside ? vent!.transform.position : local.transform.position;
            var camera = PlayerView.Select(Camera.allCameras, origin, local.gameObject.layer, out _);
            if (camera == null) { result.Status = "camera_unavailable"; return result; }
            var players = new Dictionary<int, PlayerControl>();
            foreach (var p in PlayerControl.AllPlayerControls)
                if (p != null) players[p.GetInstanceID()] = p;
            foreach (var p in UnityEngine.Object.FindObjectsOfType<PlayerControl>())
                if (p != null) players[p.GetInstanceID()] = p;
            var ids = new List<int>();
            foreach (var p in players.Values)
            {
                if (p.GetInstanceID() == local.GetInstanceID()) continue;
                if (p.Data == null) { result.Status = "partial"; return result; }
                if (p.Data.IsDead || p.Data.Disconnected || p.inVent || !p.gameObject.activeInHierarchy) continue;
                if (Vector2.Distance(origin, p.GetTruePosition()) > result.Radius) continue;
                if ((camera.cullingMask & (1 << p.gameObject.layer)) == 0 || !PlayerView.Contains(camera, p.transform.position)) continue;
                if (PhysicsHelpers.AnythingBetween(origin, p.GetTruePosition(), Constants.ShipOnlyMask, false)) continue;
                ids.Add(p.PlayerId);
            }
            result.PlayerIds = ids.ToArray();
            result.NearbyCount = ids.Count;
            result.Status = "ok";
        }
        catch { result.Status = "error"; }
        return result;
    }
}
internal sealed class VentExitInfo
{
    public string Status { get; set; } = "unavailable";
    public float Radius { get; init; } = 6;
    public int NearbyCount { get; set; }
    public int[] PlayerIds { get; set; } = Array.Empty<int>();
    public DateTimeOffset AtUtc { get; init; }
}
