using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using BepInEx;

namespace AUTelemetryProbe;

// Native vent travel with observed entry, each hop and exit. No position writes.
internal static class VentTransport
{
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = true };
    private static VentTravelCommand? _active;
    private static VentTravelInfo _last = new();
    private static string? _lastCommand, _session;
    private static long _match = -1;
    private static DateTimeOffset _nextPoll, _started, _stageAt, _nextExit;
    private static DateTimeOffset _entryPendingUntil;
    private static bool _observedInside;
    private static DateTimeOffset? _clearSince;
    private static DateTimeOffset _nextObservation;
    private static DateTimeOffset? _lastObservationAt;
    private static List<int> _route = new();
    private static int _index;
    private static string? _failure;
    internal static bool Busy => _active != null;

    internal static VentTravelInfo Capture()
    {
        EnsureIdentity();
        _last.Busy = Busy;
        _last.InVent = PlayerControl.LocalPlayer?.inVent == true;
        _last.CurrentVentId = _last.InVent ? Vent.currentVent?.Id ?? -1 : -1;
        return _last;
    }

    private static void EnsureIdentity()
    {
        if (_session == TelemetryCapture.CurrentSessionId && _match == TaskStartSignalPatch.SignalVersion) return;
        _session = TelemetryCapture.CurrentSessionId;
        _match = TaskStartSignalPatch.SignalVersion;
        _active = null;
        _entryPendingUntil = default;
        _observedInside = false;
        _clearSince = null;
        _lastObservationAt = null;
        _last = new();
        _route.Clear();
    }

    internal static void Tick()
    {
        var now = DateTimeOffset.UtcNow;
        EnsureIdentity();
        if (now >= _nextPoll)
        {
            _nextPoll = now.AddMilliseconds(50);
            try
            {
                var path = Path.Combine(Paths.PluginPath, "AUTelemetryProbe", "vent-command.json");
                if (File.Exists(path))
                {
                    using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                    var command = JsonSerializer.Deserialize<VentTravelCommand>(stream, JsonOptions);
                    if (command != null && !string.IsNullOrWhiteSpace(command.CommandId) && command.CommandId != _lastCommand)
                    {
                        _lastCommand = command.CommandId;
                        var error = Submit(command, now);
                        if (error != null && !Busy)
                            _last = new VentTravelInfo { CommandId = command.CommandId, Action = command.Action,
                                State = "failed", Stage = "rejected", Reason = error, AtUtc = now };
                    }
                }
            }
            catch (IOException) { }
            catch (JsonException) { }
            catch (Exception ex) { if (Busy) BeginCleanup(ex.GetBaseException().Message, now); }
        }
        try { Advance(now); }
        catch (Exception ex) { if (Busy) BeginCleanup(ex.GetBaseException().Message, now); }
    }

    internal static string? Submit(VentTravelCommand command, DateTimeOffset now)
    {
        EnsureIdentity();
        if (command.ExpectedSessionId != TelemetryCapture.CurrentSessionId || command.ExpectedMatchId != TaskStartSignalPatch.SignalVersion ||
            !command.IssuedAtUtc.HasValue || (now - command.IssuedAtUtc.Value).TotalSeconds is < -1 or > 2)
            return "管道指令已过期或属于其他对局";
        if (command.Action is not ("travel" or "enter" or "move" or "exit")) return "管道动作仅支持enter、move、travel或exit";
        if (command.GuardedExit && (command.ObserveSeconds < 2 || command.ObserveSeconds > 15 || double.IsNaN(command.ObserveSeconds)))
            return "出口连续观察时间必须为2至15秒";
        if (Busy && command.Action != "exit") return "已有管道技能执行中，请等待结束或请求退出";
        var player = PlayerControl.LocalPlayer;
        if (player?.Data == null || player.MyPhysics == null || player.Data.IsDead || player.Data.Disconnected)
            return "本地角色不可用或已死亡";
        if (MeetingHud.Instance != null || ExileController.Instance != null || GameManager.Instance?.GameHasStarted != true)
            return "会议、放逐或非对局阶段不能操作管道";
        if (ShipStatus.Instance?.Type.ToString() != "Ship") return "本版管道技能仅支持The Skeld";
        if (command.Action != "exit" && (player.Data.RoleType.ToString() != "Impostor" || player.Data.Role?.CanVent != true))
            return "本版管道传送仅适配普通内鬼";
        if (command.Action != "exit" && (!RuntimeControl.AutoNavigationEnabled || !RuntimeControl.UseVentsEnabled))
            return "请开启导航执行器和允许管道技能";
        if (command.Action != "exit" && (Minigame.Instance != null || player.walkingToVent || player.isKilling || (!player.inVent && !player.CanMove)))
            return "当前动画、任务面板或移动状态不允许进入管道";
        var route = new List<int>();
        var startedInside = player.inVent;
        int entry = -1;
        if (command.Action != "exit")
        {
            if (command.Action == "enter" && player.inVent) return "角色已在管道内，请选择移动或出管";
            if (command.Action == "move" && !player.inVent) return "需先钻入管道，才能在管道内移动";
            var vents = UnityEngine.Object.FindObjectsOfType<Vent>().Where(v => v != null).ToDictionary(v => v.Id);
            var source = player.inVent ? Vent.currentVent : command.EntryVentId >= 0 ? vents.GetValueOrDefault(command.EntryVentId) :
                vents.Values.Where(v => v.CanUse(player.Data, out var can, out _) <= v.UsableDistance && can)
                    .OrderBy(v => UnityEngine.Vector2.Distance(player.GetTruePosition(), v.transform.position)).FirstOrDefault();
            if (source == null) return "没有可用入口，请先走到管道旁";
            route = command.Action == "enter" ? new List<int> { source.Id } : FindPath(vents, source.Id, command.TargetVentId);
            if (route.Count == 0) return "目标不在入口连通的管道网络中";
            entry = source.Id;
            // Validate before committing ownership or cancelling a walking route.
            if (!player.inVent)
            {
                if (command.GuardedExit)
                {
                    var observation = VentExitObservation.Scan(false);
                    if (observation.Status != "ok" || observation.NearbyCount != 0)
                        return "入口附近有人或检测不可用，取消钻管并使用地面撤离";
                }
                var error = VentControl.Enter(entry);
                if (error != null) return error;
                // Use() can start its coroutine on the next frame. An
                // immediate cancel must wait for that possible entry first.
                _entryPendingUntil = now.AddSeconds(4);
                _observedInside = false;
            }
            else { _entryPendingUntil = default; _observedInside = true; }
        }
        _active = command;
        _route = route;
        _index = 0;
        _started = _stageAt = now;
        _failure = null;
        _clearSince = null;
        _nextObservation = now;
        _lastObservationAt = null;
        _last = new VentTravelInfo { CommandId = command.CommandId, Action = command.Action, State = "running",
            Stage = command.Action == "exit" ? "exit_requested" : startedInside ? "moving" : "entering",
            EntryVentId = entry, TargetVentId = command.Action == "enter" ? entry : command.TargetVentId, Route = route.ToArray(),
            GuardedExit = command.GuardedExit, ObserveSeconds = command.ObserveSeconds,
            Reason = "管道请求已接受，等待游戏状态确认", AtUtc = now };
        if (command.Action == "exit") _nextExit = now;
        return null;
    }

    internal static List<int> FindPath(Dictionary<int, Vent> vents, int start, int target)
    {
        if (!vents.ContainsKey(start) || !vents.ContainsKey(target)) return new();
        var previous = new Dictionary<int, int> { [start] = -1 };
        var queue = new Queue<int>(); queue.Enqueue(start);
        while (queue.Count > 0 && previous.Count <= 64)
        {
            var id = queue.Dequeue();
            if (id == target)
            {
                var path = new List<int>();
                for (int at = target; at >= 0; at = previous[at]) path.Add(at);
                path.Reverse(); return path;
            }
            var v = vents[id];
            foreach (var neighbor in new[] { v.Left, v.Right, v.Center })
                if (neighbor != null && vents.ContainsKey(neighbor.Id) && !previous.ContainsKey(neighbor.Id))
                { previous[neighbor.Id] = id; queue.Enqueue(neighbor.Id); }
        }
        return new();
    }

    internal static void Advance(DateTimeOffset now)
    {
        if (!Busy) return;
        var player = PlayerControl.LocalPlayer;
        if (player?.Data == null || player.Data.IsDead || player.Data.Disconnected || MeetingHud.Instance != null ||
            ExileController.Instance != null || GameManager.Instance?.GameHasStarted != true)
        { Finish(false, "角色或阶段变化，取消管道技能"); return; }
        if (player.inVent) _observedInside = true;
        if (_last.Stage is "observing_exit" or "cleanup_observing")
        {
            ObserveExit(now);
            return;
        }
        if (_last.Stage is "cleanup_exit" or "exit_requested" or "exiting")
        {
            if (!player.inVent && !player.walkingToVent && player.CanMove && (_observedInside || now >= _entryPendingUntil))
            {
                bool success = _failure == null && (_last.Action == "exit" || _last.ReachedTarget);
                _last.SafeExitConfirmed = true;
                Finish(success, _failure ?? "游戏确认已离开管道，可继续移动");
                return;
            }
            if ((now - _stageAt).TotalSeconds > 5)
            { Finish(false, "出管道未获确认，请使用退出管道或手动接管"); return; }
            if (player.inVent && !player.walkingToVent && now >= _nextExit)
            {
                if (_active!.GuardedExit && _last.Action != "exit")
                {
                    var observation = VentExitObservation.Scan();
                    _last.ExitObservation = observation;
                    if (observation.Status != "ok" || observation.NearbyCount != 0)
                    {
                        _last.Stage = _failure == null ? "observing_exit" : "cleanup_observing";
                        _stageAt = now; _clearSince = null;
                        return;
                    }
                }
                _nextExit = now.AddSeconds(1);
                var error = VentControl.Exit();
                if (error != null) _last.Reason = error;
            }
            return;
        }
        if (!RuntimeControl.AutoNavigationEnabled || !RuntimeControl.UseVentsEnabled)
        { BeginCleanup("管道开关已关闭，取消行程并尝试退出", now); return; }
        if (player.Data.RoleType.ToString() != "Impostor" || player.Data.Role?.CanVent != true)
        { BeginCleanup("角色能力变化，取消行程并尝试退出", now); return; }
        if ((now - _started).TotalSeconds > 12 || (now - _stageAt).TotalSeconds > 4)
        { BeginCleanup("管道动作超时，取消行程并尝试退出", now); return; }
        if (_last.Stage == "entering")
        {
            if (!player.inVent || player.walkingToVent) return;
            if (Vent.currentVent?.Id != _route[0]) { BeginCleanup("入口与实际管道不一致", now); return; }
            _last.Stage = "moving"; _stageAt = now;
        }
        if (!player.inVent) { BeginCleanup("尚未抵达目标就离开管道", now); return; }
        if (player.walkingToVent || Vent.currentVent == null) return;
        var current = Vent.currentVent.Id;
        if (_index < _route.Count - 1 && current == _route[_index + 1])
        { _index++; _stageAt = now; _last.Stage = "moving"; }
        if (current != _route[_index]) return;
        if (_last.Stage == "awaiting_hop") return;
        if (_index == _route.Count - 1)
        {
            _last.ReachedTarget = true;
            if (_last.Action is "enter" or "move")
            {
                _last.HeldInside = true;
                Finish(true, _last.Action == "enter" ? "游戏确认已钻入管道，保持隐藏，等待手动移动或出管" : "游戏确认管道内移动到站，保持隐藏，尚未出管");
                return;
            }
            _last.Stage = _active!.GuardedExit ? "observing_exit" : "exiting"; _stageAt = _nextExit = now;
            _clearSince = null;
            _last.Reason = _active.GuardedExit ? "已到出口，留在管道内持续观察附近玩家" : "已确认抵达目标管道，正在退出";
            return;
        }
        // One native adjacent hop at a time, allowing each animation to settle.
        if ((now - _stageAt).TotalSeconds < .65) return;
        var moveError = VentControl.Move(_route[_index + 1]);
        if (moveError != null) { BeginCleanup(moveError, now); return; }
        _last.Stage = "awaiting_hop"; _stageAt = now;
        _last.Reason = "已请求相邻管道移动，等待当前管道编号变化";
    }

    private static void BeginCleanup(string reason, DateTimeOffset now)
    {
        if (_last.Stage is "cleanup_exit" or "cleanup_observing") return;
        _failure = reason;
        _last.Stage = _active?.GuardedExit == true ? "cleanup_observing" : "cleanup_exit"; _last.Reason = reason;
        _clearSince = null;
        _stageAt = _nextExit = now;
    }

    private static void ObserveExit(DateTimeOffset now)
    {
        var player = PlayerControl.LocalPlayer!;
        if (!player.inVent)
        {
            if (player.walkingToVent || (!_observedInside && now < _entryPendingUntil))
            { _last.Reason = "等待已提交的进管动画结束，再确认安全取消"; return; }
            _last.SafeExitConfirmed = !player.walkingToVent && player.CanMove;
            Finish(false, "观察期间角色已离开管道，未认定自动安全出管成功"); return;
        }
        if (!RuntimeControl.AutoNavigationEnabled || !RuntimeControl.UseVentsEnabled)
            _failure ??= "自动控制已关闭，等待安全后结束管道行程";
        if (player.Data?.RoleType.ToString() != "Impostor" || player.Data?.Role?.CanVent != true)
            _failure ??= "角色能力变化，等待安全后结束管道行程";
        if ((now - _stageAt).TotalSeconds > 45)
        { Finish(false, "出口持续有人或检测不可用，保持管道内隐藏；请手动选择其他出口或退出"); return; }
        if (now < _nextObservation) return;
        _nextObservation = now.AddMilliseconds(80);
        if (_lastObservationAt.HasValue && (now - _lastObservationAt.Value).TotalSeconds > .5) _clearSince = null;
        _lastObservationAt = now;
        var observation = VentExitObservation.Scan();
        _last.ExitObservation = observation;
        if (observation.Status != "ok" || observation.NearbyCount != 0)
        {
            _clearSince = null; _last.ClearSeconds = 0;
            _last.Reason = observation.Status == "ok" ? "出口附近有人，继续藏在管道内；无人观察计时已重置" : "出口观察不可用，保持隐藏，不能认定附近无人";
            return;
        }
        _clearSince ??= now;
        _last.ClearSeconds = Math.Max(0, (now - _clearSince.Value).TotalSeconds);
        _last.Reason = $"出口可见范围内无人，已连续观察{_last.ClearSeconds:F1}/{_last.ObserveSeconds:F1}秒";
        if (_last.ClearSeconds < _last.ObserveSeconds) return;
        _last.Stage = _failure == null ? "exiting" : "cleanup_exit";
        _stageAt = _nextExit = now;
    }

    private static void Finish(bool success, string reason)
    {
        _last.State = success ? "completed" : "failed";
        _last.Stage = success ? "finished" : "failed";
        _last.Reason = reason; _last.AtUtc = DateTimeOffset.UtcNow;
        _active = null;
    }
}

internal sealed class VentTravelCommand
{
    public string CommandId { get; init; } = "";
    public string Action { get; init; } = "travel";
    public int TargetVentId { get; init; } = -1;
    public int EntryVentId { get; init; } = -1;
    public string? ExpectedSessionId { get; init; }
    public long ExpectedMatchId { get; init; } = -1;
    public DateTimeOffset? IssuedAtUtc { get; init; }
    public bool GuardedExit { get; init; }
    public double ObserveSeconds { get; init; } = 2;
}
internal sealed class VentTravelInfo
{
    public bool Supported { get; init; } = true;
    public bool ManualControlSupported { get; init; } = true;
    public bool EscapeSupported { get; init; } = true;
    public bool ApproachSupported { get; init; } = true;
    public bool Busy { get; set; }
    public string? CommandId { get; init; }
    public string Action { get; init; } = "travel";
    public string State { get; set; } = "idle";
    public string Stage { get; set; } = "idle";
    public string? Reason { get; set; }
    public int EntryVentId { get; init; } = -1;
    public int TargetVentId { get; init; } = -1;
    public int CurrentVentId { get; set; } = -1;
    public bool InVent { get; set; }
    public bool ReachedTarget { get; set; }
    public bool HeldInside { get; set; }
    public bool SafeExitConfirmed { get; set; }
    public bool GuardedExit { get; init; }
    public double ObserveSeconds { get; init; }
    public double ClearSeconds { get; set; }
    public VentExitInfo? ExitObservation { get; set; }
    public int[] Route { get; init; } = Array.Empty<int>();
    public DateTimeOffset? AtUtc { get; set; }
}
