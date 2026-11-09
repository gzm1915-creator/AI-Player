using System;
using System.Linq;
using UnityEngine;

namespace AUTelemetryProbe;

internal sealed class PlayerPursuit
{
    internal readonly InternalNavigationCommand Command;
    internal readonly long StartedAtMs;
    internal long PlannedAtMs;
    internal Vector2 PlannedPosition;
    private double _peakSpeed;
    internal PursuitInfo Info { get; } = new();

    internal PlayerPursuit(InternalNavigationCommand command, long now)
    { Command = command; StartedAtMs = now; Info.TargetPlayerId = command.TargetNumber; }

    internal static string? Validate(InternalNavigationCommand command, bool initial, out ObservedPlayer? target)
    {
        target = null;
        var local = PlayerControl.LocalPlayer;
        if (!RuntimeControl.AutoNavigationEnabled || local?.Data?.RoleType.ToString() != "Impostor" ||
            local.Data.IsDead || !local.CanMove || local.inVent || local.walkingToVent || local.isKilling ||
            MeetingHud.Instance != null || ExileController.Instance != null || Minigame.Instance != null ||
            GameManager.Instance?.GameHasStarted != true || !ShipStatus.Instance)
            return "追踪因角色、移动许可或游戏阶段变化而停止";
        if (command.ExpectedSessionId != TelemetryCapture.CurrentSessionId ||
            command.ExpectedMatchId != TaskStartSignalPatch.SignalVersion ||
            (initial && (!command.IssuedAtUtc.HasValue || (DateTimeOffset.UtcNow - command.IssuedAtUtc.Value).TotalSeconds is < -1 or > 2)))
            return "追踪指令已过期或属于其他对局";
        var frame = PlayerAwareness.Scan();
        if (frame.Diagnostics.Status != "ok") return "玩家检测不可用，停止追踪";
        target = frame.Players.FirstOrDefault(p => p.Player.PlayerId == command.TargetNumber && p.Player.Data?.Role?.IsImpostor != true);
        if (target == null) return "目标离开当前检测范围或被遮挡，停止追踪";
        var targetId = target.Player.PlayerId;
        var safety = KillSafetyObservation.Current.FirstOrDefault(s => s.TargetPlayerId == targetId);
        if (PlayerAwareness.HasWitness(frame, target.Player.PlayerId, target.Position, safety?.WitnessMemorySeconds ?? safety?.RequiredSeconds ?? 5))
            return "当前或近期目击者可能在附近，停止追踪";
        return null;
    }

    internal bool NeedsPlan(ObservedPlayer target, long now)
    {
        var speed = Math.Sqrt(target.Velocity.x * target.Velocity.x + target.Velocity.y * target.Velocity.y);
        _peakSpeed = Math.Max(speed, _peakSpeed);
        Info.TargetSpeed = Math.Round(speed, 2);
        Info.TargetSlowed = speed < 0.35 || (_peakSpeed > 0.6 && speed < _peakSpeed * 0.55);
        Info.TargetPosition = new PositionInfo { Space = "unity_world", X = target.Position.x, Y = target.Position.y };
        var moved = Vector2.Distance(PlannedPosition, target.Position);
        return Info.Replans == 0 || (now - PlannedAtMs >= 300 && moved >= 0.20f) ||
            (Info.TargetSlowed && now - PlannedAtMs >= 120 && moved >= 0.06f);
    }

    internal void Planned(ObservedPlayer target, long now)
    {
        PlannedAtMs = now; PlannedPosition = target.Position;
        Info.Replans++; Info.Active = true; Info.State = "tracking";
    }
    internal bool Expired(long now) => now - StartedAtMs > Math.Clamp(Command.MaxDurationMs, 1000, 45000);
}

internal sealed class PursuitInfo
{
    public bool Active { get; set; }
    public string State { get; set; } = "idle";
    public int TargetPlayerId { get; set; } = -1;
    public PositionInfo? TargetPosition { get; set; }
    public double TargetSpeed { get; set; }
    public bool TargetSlowed { get; set; }
    public int Replans { get; set; }
}
