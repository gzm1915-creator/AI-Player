using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;

namespace AUTelemetryProbe;

// Observe the game's own completion before practice mode can replace the list.
// Neither invoking a control nor removing/closing a task produces a receipt.
internal static class TaskCompletionMemory
{
    private static string _identity = "";
    private static long _sequence;
    private static readonly Dictionary<int, (string Id, bool Completed)> Instances = new();
    private static readonly List<TaskCompletionInfo> Receipts = new();
    private static TaskCompletionDiagnostics _diagnostics = new();
    private static readonly Dictionary<string, bool> InstalledHooks = new();
    internal static void RecordHook(string name, bool installed) => InstalledHooks[name] = installed;

    private static void EnsureIdentity()
    {
        var identity = $"{TelemetryCapture.CurrentSessionId}/{TaskStartSignalPatch.SignalVersion}/{PlayerControl.LocalPlayer?.PlayerId}";
        if (identity == _identity) return;
        _identity = identity;
        Instances.Clear();
        Receipts.Clear();
        _diagnostics = new();
    }

    internal static string InstanceId(PlayerTask task)
    {
        EnsureIdentity();
        var key = task.GetInstanceID();
        if (!Instances.TryGetValue(key, out var entry))
        {
            if (Instances.Count >= 256) Instances.Remove(Instances.Keys.First());
            entry = ($"{_identity}/{++_sequence}", false);
            Instances[key] = entry;
        }
        return entry.Id;
    }

    internal static void Initializing(PlayerTask task)
    {
        EnsureIdentity();
        Instances.Remove(task.GetInstanceID());
    }

    internal static void Observe(PlayerTask task)
    {
        try
        {
            EnsureIdentity();
            _diagnostics.ObservationCallbacks++;
            var local = PlayerControl.LocalPlayer;
            if (string.IsNullOrEmpty(TelemetryCapture.CurrentSessionId) || local == null ||
                task == null || !IsLocalTask(task) || !task.IsComplete ||
                task.TryCast<NormalPlayerTask>() == null) return;
            var snapshot = TelemetryCapture.DescribeOwnTask(task);
            if (!snapshot.Complete) return;
            Retain(snapshot, "native_own_task_is_complete");
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning($"Task completion observation failed: {exception.Message}");
        }
    }

    private static bool IsLocalTask(PlayerTask task)
    {
        var tasks = PlayerControl.LocalPlayer?.myTasks;
        if (tasks == null) return false;
        for (var i = 0; i < Math.Min(tasks.Count, 20); ++i)
            if (tasks[i] != null && tasks[i].GetInstanceID() == task.GetInstanceID()) return true;
        return false;
    }

    private static void Retain(OwnTaskInfo snapshot, string source)
    {
        if (!snapshot.Complete || Receipts.Any(r => r.ReceiptId == snapshot.TaskInstanceId) ||
            Instances.Values.Any(v => v.Id == snapshot.TaskInstanceId && v.Completed)) return;
        var known = Instances.FirstOrDefault(k => k.Value.Id == snapshot.TaskInstanceId);
        if (known.Value.Id == snapshot.TaskInstanceId) Instances[known.Key] = (snapshot.TaskInstanceId, true);
        var position = PlayerControl.LocalPlayer!.GetTruePosition();
        Receipts.Add(new TaskCompletionInfo {
            ReceiptId = snapshot.TaskInstanceId, SessionId = TelemetryCapture.CurrentSessionId,
            MatchId = TaskStartSignalPatch.SignalVersion, PlayerId = PlayerControl.LocalPlayer.PlayerId,
            CompletedAtUtc = DateTimeOffset.UtcNow,
            Position = new PositionInfo { Space = "unity_world", X = position.x, Y = position.y },
            Task = snapshot, Source = source
        });
        if (Receipts.Count > 128) Receipts.RemoveAt(0);
    }

    internal static PendingTaskCompletion? BeforeNativeComplete(PlayerControl player, uint id)
    {
        EnsureIdentity();
        if (player != PlayerControl.LocalPlayer || player.myTasks == null) return null;
        _diagnostics.NativeCompleteCallbacks++;
        try
        {
            for (var i = 0; i < Math.Min(player.myTasks.Count, 20); ++i)
            {
                var task = player.myTasks[i];
                if (task == null || task.Id != id || task.TryCast<NormalPlayerTask>() == null) continue;
                var snapshot = TelemetryCapture.DescribeOwnTask(task);
                var native = player.Data?.FindTaskById(id);
                _diagnostics.LocalTaskCaptured++;
                _diagnostics.LastOwnerMatches = task.Owner == player;
                _diagnostics.LastTaskWasComplete = task.IsComplete;
                _diagnostics.LastRecordWasComplete = native?.Complete;
                Observe(task);
                return new PendingTaskCompletion { SessionId = TelemetryCapture.CurrentSessionId,
                    MatchId = TaskStartSignalPatch.SignalVersion, PlayerId = player.PlayerId,
                    Task = snapshot, NativeComplete = native == null ? null : () => native.Complete };
            }
        }
        catch (Exception exception) { TelemetryProbePlugin.LogSource?.LogWarning($"Task completion capture failed: {exception.Message}"); }
        return null;
    }

    internal static void AfterNativeComplete(PendingTaskCompletion? pending)
    {
        EnsureIdentity();
        if (pending == null || pending.SessionId != TelemetryCapture.CurrentSessionId ||
            pending.MatchId != TaskStartSignalPatch.SignalVersion || pending.PlayerId != PlayerControl.LocalPlayer?.PlayerId) return;
        try
        {
            // Hold the original network record across native list replacement.
            // The request itself never counts; its Complete field must be true.
            _diagnostics.LastRecordNowComplete = pending.NativeComplete?.Invoke();
            if (_diagnostics.LastRecordNowComplete != true) return;
            var t = pending.Task;
            Retain(new OwnTaskInfo { TaskId = t.TaskId, TaskInstanceId = t.TaskInstanceId,
                TaskType = t.TaskType, Step = Math.Max(t.Step, t.MaxStep), MaxStep = t.MaxStep,
                StartAt = t.StartAt, Locations = t.Locations, Complete = true, Confidence = 1.0 },
                "native_own_task_record_complete");
        }
        catch (Exception exception) { TelemetryProbePlugin.LogSource?.LogWarning($"Task completion confirmation failed: {exception.Message}"); }
    }

    internal static void BeforeTaskListReset(PlayerControl player)
    {
        EnsureIdentity();
        if (player != PlayerControl.LocalPlayer || player.myTasks == null) return;
        _diagnostics.TaskListResetCallbacks++;
        for (var i = 0; i < Math.Min(player.myTasks.Count, 20); ++i)
            Observe(player.myTasks[i]);
    }

    internal static TaskCompletionDiagnostics Diagnostics()
    {
        EnsureIdentity();
        _diagnostics.Hooks = new Dictionary<string, bool>(InstalledHooks);
        return _diagnostics;
    }

    internal static List<TaskCompletionInfo> Capture()
    {
        EnsureIdentity();
        Receipts.RemoveAll(r => DateTimeOffset.UtcNow - r.CompletedAtUtc > TimeSpan.FromMinutes(5));
        return Receipts.ToList();
    }
}

internal sealed class TaskCompletionInfo
{
    public string ReceiptId { get; init; } = "";
    public string SessionId { get; init; } = "";
    public long MatchId { get; init; }
    public byte PlayerId { get; init; }
    public DateTimeOffset CompletedAtUtc { get; init; }
    public PositionInfo Position { get; init; } = null!;
    public OwnTaskInfo Task { get; init; } = null!;
    public string Source { get; init; } = "native_own_task_is_complete";
}

internal sealed class PendingTaskCompletion
{
    public string SessionId = "";
    public long MatchId;
    public byte PlayerId;
    public OwnTaskInfo Task = null!;
    public Func<bool>? NativeComplete;
}

internal sealed class TaskCompletionDiagnostics
{
    public Dictionary<string, bool> Hooks { get; set; } = new();
    public int ObservationCallbacks { get; set; }
    public int NativeCompleteCallbacks { get; set; }
    public int LocalTaskCaptured { get; set; }
    public int TaskListResetCallbacks { get; set; }
    public bool? LastOwnerMatches { get; set; }
    public bool? LastTaskWasComplete { get; set; }
    public bool? LastRecordWasComplete { get; set; }
    public bool? LastRecordNowComplete { get; set; }
}

[HarmonyPatch(typeof(NormalPlayerTask), nameof(NormalPlayerTask.Complete))]
internal static class TaskCompletedPatch
{
    // NextStep may already have set IsComplete when Complete enters. Observe
    // both sides so practice refresh inside Complete cannot erase that fact.
    private static void Prefix(NormalPlayerTask __instance) => TaskCompletionMemory.Observe(__instance);
    private static void Postfix(NormalPlayerTask __instance) => TaskCompletionMemory.Observe(__instance);
}

[HarmonyPatch(typeof(NormalPlayerTask), nameof(NormalPlayerTask.NextStep))]
internal static class TaskStepCompletedPatch
{
    private static void Postfix(NormalPlayerTask __instance) => TaskCompletionMemory.Observe(__instance);
}

[HarmonyPatch(typeof(PlayerControl), nameof(PlayerControl.CompleteTask))]
internal static class LocalTaskCompletedPatch
{
    private static void Prefix(PlayerControl __instance, uint __0, out PendingTaskCompletion? __state) =>
        __state = TaskCompletionMemory.BeforeNativeComplete(__instance, __0);
    private static void Postfix(PendingTaskCompletion? __state) => TaskCompletionMemory.AfterNativeComplete(__state);
}

[HarmonyPatch(typeof(PlayerControl), nameof(PlayerControl.ClearTasks))]
internal static class LocalTaskListResetPatch
{
    private static void Prefix(PlayerControl __instance) => TaskCompletionMemory.BeforeTaskListReset(__instance);
}

[HarmonyPatch(typeof(NormalPlayerTask), nameof(NormalPlayerTask.Initialize))]
internal static class TaskInitializedPatch
{
    private static void Prefix(NormalPlayerTask __instance) => TaskCompletionMemory.Initializing(__instance);
}
