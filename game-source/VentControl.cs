using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace AUTelemetryProbe;

internal static class VentControl
{
    private static readonly List<VentInfo> CachedVents = new();
    private static long _nextVentScanMs;
    private static string _cachedMap = string.Empty;

    internal static VentRoleInfo ReadRoleInfo()
    {
        var player = PlayerControl.LocalPlayer;
        return new VentRoleInfo
        {
            RoleType = player?.Data?.RoleType.ToString() ?? "unknown",
            CanVent = player?.Data?.Role?.CanVent == true,
            InVent = player?.inVent == true,
            CurrentVentId = player?.inVent == true ? Vent.currentVent?.Id ?? -1 : -1,
            WalkingToVent = player?.walkingToVent == true
        };
    }

    internal static List<VentInfo> Capture()
    {
        var map = ShipStatus.Instance?.Type.ToString() ?? "UNKNOWN";
        var now = Environment.TickCount64;
        if (string.Equals(map, _cachedMap, StringComparison.Ordinal) &&
            now < _nextVentScanMs)
        {
            return CachedVents;
        }

        var captured = UnityEngine.Object.FindObjectsOfType<Vent>()
            .Where(vent => vent != null)
            .OrderBy(vent => vent.Id)
            .Select(vent =>
            {
                var position = vent.transform.position;
                return new VentInfo
                {
                    Id = vent.Id,
                    Name = vent.name,
                    X = position.x,
                    Y = position.y,
                    LeftId = vent.Left?.Id ?? -1,
                    RightId = vent.Right?.Id ?? -1,
                    CenterId = vent.Center?.Id ?? -1,
                    UsableDistance = vent.UsableDistance
                };
            })
            .ToList();
        CachedVents.Clear();
        CachedVents.AddRange(captured);
        _cachedMap = map;
        _nextVentScanMs = now + 1000;
        return CachedVents;
    }

    internal static string? Enter(int targetVentId)
    {
        var player = PlayerControl.LocalPlayer;
        var role = player?.Data?.Role;
        if (player == null || player.MyPhysics == null)
        {
            return "Local player is unavailable";
        }
        if (role == null || !role.CanVent)
        {
            return $"Role {player.Data?.RoleType.ToString() ?? "unknown"} cannot use vents";
        }
        if (player.inVent)
        {
            return "Local player is already inside a vent";
        }

        var position = player.GetTruePosition();
        var vent = targetVentId >= 0
            ? FindById(targetVentId)
            : UnityEngine.Object.FindObjectsOfType<Vent>()
                .Where(candidate => candidate != null)
                .OrderBy(candidate =>
                    Vector2.Distance(position, candidate.transform.position))
                .FirstOrDefault();
        if (vent == null)
        {
            return targetVentId >= 0
                ? $"Vent {targetVentId} is unavailable"
                : "No vent was found";
        }

        var distance = vent.CanUse(player.Data, out var canUse, out var couldUse);
        if (!couldUse || !canUse || distance > vent.UsableDistance)
        {
            return $"Vent {vent.Id} is out of range ({distance:F2})";
        }

        vent.Use();
        TelemetryProbePlugin.LogSource?.LogInfo(
            $"Entered vent {vent.Id} as role " +
            $"{player.Data?.RoleType.ToString() ?? "unknown"}");
        return null;
    }

    internal static string? Move(int targetVentId)
    {
        var player = PlayerControl.LocalPlayer;
        if (player == null || !player.inVent)
        {
            return "Local player is not inside a vent";
        }

        var current = Vent.currentVent;
        var target = FindById(targetVentId);
        if (current == null)
        {
            return "Current vent is unavailable";
        }
        if (target == null)
        {
            return $"Target vent {targetVentId} is unavailable";
        }
        if (current.Id == target.Id)
        {
            return null;
        }
        if (current.Left?.Id != target.Id && current.Right?.Id != target.Id && current.Center?.Id != target.Id)
            return "目标管道不与当前管道直接连接";
        if (!current.TryMoveToVent(target, out var error))
        {
            return string.IsNullOrWhiteSpace(error)
                ? $"Cannot move from vent {current.Id} to {target.Id}"
                : error;
        }

        TelemetryProbePlugin.LogSource?.LogInfo(
            $"Moved through vent network: {current.Id} -> {target.Id}");
        return null;
    }

    internal static string? Exit()
    {
        var player = PlayerControl.LocalPlayer;
        if (player == null || !player.inVent || player.MyPhysics == null)
        {
            return "Local player is not inside a vent";
        }

        var current = Vent.currentVent;
        if (current == null)
        {
            return "Current vent is unavailable";
        }

        player.MyPhysics.RpcExitVent(current.Id);
        TelemetryProbePlugin.LogSource?.LogInfo(
            $"Exited vent {current.Id}");
        return null;
    }

    private static Vent? FindById(int ventId)
    {
        return UnityEngine.Object.FindObjectsOfType<Vent>()
            .FirstOrDefault(vent => vent != null && vent.Id == ventId);
    }
}

public sealed class VentInfo
{
    public int Id { get; init; }
    public string Name { get; init; } = string.Empty;
    public float X { get; init; }
    public float Y { get; init; }
    public int LeftId { get; init; }
    public int RightId { get; init; }
    public int CenterId { get; init; }
    public float UsableDistance { get; init; }
}

public sealed class VentRoleInfo
{
    public string RoleType { get; init; } = "unknown";
    public bool CanVent { get; init; }
    public bool InVent { get; init; }
    public int CurrentVentId { get; init; }
    public bool WalkingToVent { get; init; }
}
