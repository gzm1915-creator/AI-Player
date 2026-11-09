using System;
using System.IO;
using System.Text.Json;
using BepInEx;

namespace AUTelemetryProbe;

// Independent command/receipt channel: sabotage must not steal an active route.
internal static class SabotageControl
{
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = true };
    private static string? _lastCommand;
    private static long _match = -1;
    private static string? _session;
    private static DateTimeOffset _nextPoll, _submitted;
    internal static SabotageActionInfo LastAction { get; private set; } = new();

    internal static void Tick()
    {
        if (DateTimeOffset.UtcNow < _nextPoll) return;
        _nextPoll = DateTimeOffset.UtcNow.AddMilliseconds(50);
        try
        {
            var path = Path.Combine(Paths.PluginPath, "AUTelemetryProbe", "sabotage-command.json");
            if (!File.Exists(path)) return;
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            var command = JsonSerializer.Deserialize<SabotageCommand>(stream, JsonOptions);
            if (command == null || string.IsNullOrWhiteSpace(command.CommandId) || command.CommandId == _lastCommand) return;
            _lastCommand = command.CommandId;
            var error = Execute(command);
            if (error != null) LastAction = new SabotageActionInfo { CommandId = command.CommandId,
                Target = command.Target, State = "failed", Reason = error, AtUtc = DateTimeOffset.UtcNow };
        }
        catch (IOException) { }
        catch (JsonException) { }
        catch (Exception ex)
        {
            LastAction = new SabotageActionInfo { CommandId = _lastCommand, State = "failed",
                Reason = ex.GetBaseException().Message, AtUtc = DateTimeOffset.UtcNow };
        }
    }

    internal static SabotageInfo Capture()
    {
        if (_session != TelemetryCapture.CurrentSessionId || _match != TaskStartSignalPatch.SignalVersion)
        {
            LastAction = new();
            _session = TelemetryCapture.CurrentSessionId;
            _match = TaskStartSignalPatch.SignalVersion;
        }
        if (LastAction.State == "submitted")
        {
            var active = EmergencyObservation.Active();
            if (active != null && active.Contains(RepairType(LastAction.Target)))
                LastAction = new SabotageActionInfo { CommandId = LastAction.CommandId, Target = LastAction.Target,
                    State = "completed", Reason = "游戏系统确认破坏已发生", AtUtc = DateTimeOffset.UtcNow };
            else if ((DateTimeOffset.UtcNow - _submitted).TotalSeconds > 4)
                LastAction = new SabotageActionInfo { CommandId = LastAction.CommandId, Target = LastAction.Target,
                    State = "failed", Reason = "未观察到对应破坏发生，不能认定成功", AtUtc = DateTimeOffset.UtcNow };
        }
        var reason = BlockReason(out var cooldown);
        return new SabotageInfo { Supported = true, CanSabotage = reason == null, Cooldown = cooldown,
            Reason = reason ?? "可发起破坏", LastAction = LastAction };
    }

    internal static string? Execute(SabotageCommand command)
    {
        Capture(); // Initialize identity before creating a receipt.
        if (command.ExpectedSessionId != TelemetryCapture.CurrentSessionId ||
            command.ExpectedMatchId != TaskStartSignalPatch.SignalVersion || !command.IssuedAtUtc.HasValue ||
            (DateTimeOffset.UtcNow - command.IssuedAtUtc.Value).TotalSeconds is < -1 or > 2)
            return "破坏指令已过期或属于其他对局";
        var system = command.Target switch { "lights" => SystemTypes.Electrical, "comms" => SystemTypes.Comms,
            "reactor" => SystemTypes.Reactor, "oxygen" => SystemTypes.LifeSupp, _ => (SystemTypes?)null };
        if (!system.HasValue) return "未知破坏目标";
        var error = BlockReason(out _);
        if (error != null) return error;
        var ship = ShipStatus.Instance;
        if (ship == null) return "游戏场景已切换";
        _submitted = DateTimeOffset.UtcNow;
        LastAction = new SabotageActionInfo { CommandId = command.CommandId, Target = command.Target,
            State = "submitted", Reason = "已提交原生破坏请求，等待游戏系统确认", AtUtc = _submitted };
        ship.RpcUpdateSystem(SystemTypes.Sabotage, (byte)system.Value);
        return null;
    }

    private static string? BlockReason(out float? cooldown)
    {
        cooldown = null;
        var player = PlayerControl.LocalPlayer;
        var ship = ShipStatus.Instance;
        if (!RuntimeControl.AutoNavigationEnabled) return "自动控制未启用，请先开始自动游玩或开启导航执行器";
        if (player?.Data?.RoleType.ToString() != "Impostor" || player.Data.IsDead || player.Data.Disconnected)
            return "仅支持存活的普通内鬼";
        if (GameManager.Instance?.GameHasStarted != true || ship == null || ship.Type.ToString() != "Ship")
            return "仅支持已开始的 The Skeld 对局";
        if (MeetingHud.Instance != null || ExileController.Instance != null) return "会议或放逐阶段不能制造破坏";
        if (!player.CanMove || player.inVent || player.walkingToVent || player.isKilling || Minigame.Instance != null)
            return "当前行动状态暂不能制造破坏";
        if (!ship.Systems.TryGetValue(SystemTypes.Sabotage, out var raw) || raw.TryCast<SabotageSystemType>() is not { } system)
            return "原生破坏系统不可用";
        cooldown = system.Timer;
        if (float.IsNaN(system.Timer) || float.IsInfinity(system.Timer)) return "破坏冷却数据不可用";
        if (system.AnyActive) return "已有破坏正在发生";
        if (system.Timer > 0) return "破坏冷却中";
        if (LastAction.State == "submitted") return "上一条破坏请求正在等待游戏确认";
        return null;
    }

    private static string RepairType(string? target) => target switch { "lights" => "FixLights", "comms" => "FixComms",
        "reactor" => "ResetReactor", "oxygen" => "RestoreOxy", _ => "unknown" };
}

internal sealed class SabotageCommand
{
    public string CommandId { get; init; } = "";
    public string Target { get; init; } = "";
    public string? ExpectedSessionId { get; init; }
    public long ExpectedMatchId { get; init; } = -1;
    public DateTimeOffset? IssuedAtUtc { get; init; }
}
internal sealed class SabotageInfo
{
    public bool Supported { get; init; }
    public bool CanSabotage { get; init; }
    public float? Cooldown { get; init; }
    public string? Reason { get; init; }
    public SabotageActionInfo LastAction { get; init; } = new();
}
internal sealed class SabotageActionInfo
{
    public string? CommandId { get; init; }
    public string? Target { get; init; }
    public string State { get; init; } = "idle";
    public string? Reason { get; init; }
    public DateTimeOffset? AtUtc { get; init; }
}
