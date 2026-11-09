using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;
using UnityEngine;

namespace AUTelemetryProbe;

// Local, ordinary Impostor only. Native buttons and murder resolution own the rules.
internal static class ImpostorControl
{
    internal static ImpostorActionInfo LastAction { get; private set; } = new();
    private static DateTimeOffset _submittedAt;

    internal static OwnRoleInfo Capture()
    {
        var player = PlayerControl.LocalPlayer;
        var role = player?.Data?.Role;
        var button = HudManager.Instance?.KillButton;
        var info = new OwnRoleInfo
        {
            RoleType = player?.Data?.RoleType.ToString() ?? "unknown",
            IsImpostor = role?.IsImpostor == true,
            IsDead = player?.Data?.IsDead != false,
            KillCooldown = player?.killTimer ?? 0,
            KillTargetId = button?.currentTarget?.PlayerId,
            CanKill = role?.CanUseKillButton == true && button?.canInteract == true &&
                      button.gameObject.activeInHierarchy && player?.killTimer <= 0,
            BasicImpostorSupported = true,
            DynamicPursuitSupported = true,
            LocalPursuitApproachSupported = true,
            RoomSweepEscapeSupported = true,
            MidEdgeNavigationSupported = true,
            LightsKillWaitSupported = true,
            KillSafetySupported = true,
            KillSafety = KillSafetyObservation.Current.ToList()
        };
        // Teammate identities are information the local impostor legally has.
        if (info.IsImpostor)
            foreach (var other in PlayerControl.AllPlayerControls)
                if (other?.Data?.Role?.IsImpostor == true && other != player)
                    info.TeammateIds.Add(other.PlayerId);
        if (LastAction.State == "submitted" && (DateTimeOffset.UtcNow - _submittedAt).TotalSeconds > 4)
            LastAction = new ImpostorActionInfo { CommandId = LastAction.CommandId,
                TargetPlayerId = LastAction.TargetPlayerId, State = "failed", Reason = "未收到本地击杀确认" };
        return info;
    }

    internal static string? Kill(InternalNavigationCommand command, DateTimeOffset? observedAt = null)
    {
        var player = PlayerControl.LocalPlayer;
        if (!RuntimeControl.AutoNavigationEnabled) return "自动控制未启用";
        if (command.ExpectedSessionId != TelemetryCapture.CurrentSessionId ||
            command.ExpectedMatchId != TaskStartSignalPatch.SignalVersion ||
            !command.IssuedAtUtc.HasValue ||
            ((observedAt ?? DateTimeOffset.UtcNow) - command.IssuedAtUtc.Value).TotalSeconds is < -1 or > 2)
            return "击杀指令已过期或属于其他对局";
        if (player?.Data?.RoleType.ToString() != "Impostor" || player.Data.IsDead || !player.CanMove ||
            player.inVent || player.walkingToVent || player.isKilling || MeetingHud.Instance != null ||
            ExileController.Instance != null || Minigame.Instance != null || !ShipStatus.Instance ||
            GameManager.Instance?.GameHasStarted != true)
            return "当前角色或游戏阶段不允许普通内鬼击杀";
        var button = HudManager.Instance?.KillButton;
        var target = button?.currentTarget;
        if (target == null || target.PlayerId != command.TargetNumber || target.Data == null || target.Data.IsDead ||
            target.Data.Disconnected || target.Data.Role?.IsImpostor == true || target.inVent ||
            !button!.canInteract || !button.gameObject.activeInHierarchy || player.killTimer > 0)
            return "目标不是当前游戏允许的击杀目标";
        var origin = player.GetTruePosition();
        var camera = PlayerView.Select(Camera.allCameras, player.transform.position, player.gameObject.layer, out _);
        if (camera == null || !PlayerView.Contains(camera, target.transform.position))
            return "当前无法确认目标在画面中";
        if (PhysicsHelpers.AnythingBetween(origin, target.GetTruePosition(), Constants.ShipAndObjectsMask, false))
            return "目标被墙体遮挡";
        var awareness = PlayerAwareness.Scan(force: true, at: observedAt);
        if (awareness.Diagnostics.Status != "ok") return "玩家观测不可用，取消击杀";
        var safety = KillSafetyObservation.Current.FirstOrDefault(s => s.TargetPlayerId == target.PlayerId);
        if (PlayerAwareness.HasWitness(awareness, target.PlayerId, target.GetTruePosition(), safety?.WitnessMemorySeconds ?? safety?.RequiredSeconds ?? 5))
            return "当前或近期目击者可能仍在附近，取消击杀";
        if (safety?.Ready != true) return safety?.Reason ?? "出手前需连续观察周围安全3至5秒";
        _submittedAt = DateTimeOffset.UtcNow;
        LastAction = new ImpostorActionInfo { CommandId = command.CommandId, TargetPlayerId = target.PlayerId,
            State = "submitted", Reason = "已点击原生击杀按钮，等待游戏确认" };
        button.DoClick();
        return null;
    }

    internal static void Confirm(PlayerControl killer, PlayerControl target)
    {
        CrimeSceneMemory.Remember(killer, target);
        if (killer != PlayerControl.LocalPlayer || LastAction.State != "submitted" ||
            target == null || target.PlayerId != LastAction.TargetPlayerId || target.Data?.IsDead != true) return;
        var position = target.GetTruePosition();
        LastAction = new ImpostorActionInfo { CommandId = LastAction.CommandId, TargetPlayerId = target.PlayerId,
            State = "completed", Reason = "游戏确认本地角色击杀", AtUtc = DateTimeOffset.UtcNow,
            Position = new PositionInfo { Space = "unity_world", X = position.x, Y = position.y } };
    }

    internal static void Reject(InternalNavigationCommand command, string reason) =>
        LastAction = new ImpostorActionInfo { CommandId = command.CommandId, TargetPlayerId = (byte?)command.TargetNumber,
            State = "failed", Reason = reason, AtUtc = DateTimeOffset.UtcNow };
}

[HarmonyPatch(typeof(PlayerControl), nameof(PlayerControl.MurderPlayer))]
internal static class LocalImpostorMurderConfirmationPatch
{
    private static void Postfix(PlayerControl __instance, PlayerControl target) => ImpostorControl.Confirm(__instance, target);
}

internal sealed class OwnRoleInfo
{
    public string RoleType { get; init; } = "unknown";
    public bool IsImpostor { get; init; }
    public bool IsDead { get; init; }
    public bool CanKill { get; init; }
    public float KillCooldown { get; init; }
    public byte? KillTargetId { get; init; }
    public bool BasicImpostorSupported { get; init; }
    public bool DynamicPursuitSupported { get; init; }
    public bool LocalPursuitApproachSupported { get; init; }
    public bool RoomSweepEscapeSupported { get; init; }
    public bool MidEdgeNavigationSupported { get; init; }
    public bool LightsKillWaitSupported { get; init; }
    public bool KillSafetySupported { get; init; }
    public List<KillSafetyInfo> KillSafety { get; init; } = new();
    public List<byte> TeammateIds { get; init; } = new();
}

internal sealed class ImpostorActionInfo
{
    public string? CommandId { get; init; }
    public byte? TargetPlayerId { get; init; }
    public string State { get; init; } = "idle";
    public string? Reason { get; init; }
    public DateTimeOffset? AtUtc { get; init; }
    public PositionInfo? Position { get; init; }
}
