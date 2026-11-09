using System;

namespace AUTelemetryProbe;

// Only the local task's public phase and timer are exposed, never AnomalyId.
internal static class SampleTaskControl
{
    internal static SampleTaskStatus? Capture(PlayerTask? task)
    {
        if (task?.TaskType.ToString() != "InspectSample") return null;
        try
        {
            var normal = task.TryCast<NormalPlayerTask>();
            if (normal?.Data == null || normal.Data.Length == 0) return new();
            return Describe(normal.Data[0], normal.TaskTimer, task.IsComplete);
        }
        catch { return new(); }
    }

    internal static SampleTaskStatus Describe(byte state, float timer, bool complete)
    {
        if (complete) return new() { Supported = true, Phase = "complete" };
        if (!float.IsFinite(timer)) return new();
        var phase = (SampleMinigame.States)state;
        return phase switch
        {
            SampleMinigame.States.Processing => new() { Supported = true,
                Phase = timer > 0 ? "processing" : "ready", RemainingSeconds = Math.Max(0, timer) },
            SampleMinigame.States.Selection => new() { Supported = true, Phase = "ready", RemainingSeconds = 0 },
            SampleMinigame.States.PrepareSample or SampleMinigame.States.AwaitingStart =>
                new() { Supported = true, Phase = "not_started" },
            _ => new()
        };
    }

    internal static bool Operate(InternalNavigationCommand command)
    {
        var game = Minigame.Instance?.TryCast<SampleMinigame>();
        if (game?.MyTask == null || game.MyTask.IsComplete ||
            command.InteractTaskId != game.MyTask.Id ||
            command.ExpectedMinigameInstanceId != game.GetInstanceID() ||
            !CommandScope.Protected(command) || !CommandScope.Fresh(command) ||
            PlayerControl.LocalPlayer?.Data?.IsDead != false || MeetingHud.Instance != null)
            return false;
        if (command.Action == "start_inspect_sample" && game.State == SampleMinigame.States.AwaitingStart)
        {
            game.NextStep(); // The native start button's handler; no task fields are written here.
            return true;
        }
        if (command.Action == "select_inspect_sample" && game.State == SampleMinigame.States.Selection &&
            command.TargetNumber is >= 0 and < 5)
        {
            game.SelectTube(command.TargetNumber);
            return true;
        }
        return false;
    }
}

internal sealed class SampleTaskStatus
{
    public bool Supported { get; init; }
    public string Phase { get; init; } = "unavailable";
    public float? RemainingSeconds { get; init; }
    public string Source { get; init; } = "native_own_task_timer";
}
