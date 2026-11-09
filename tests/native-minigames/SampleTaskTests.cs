using System;
using AUTelemetryProbe;

internal static class SampleTaskTests
{
    internal static void Run()
    {
        static void Check(bool value, string message) { if (!value) throw new Exception(message); }
        var task = new NormalPlayerTask { TaskType = "InspectSample", Id = 9 };
        var game = new SampleMinigame { MyTask = task, State = SampleMinigame.States.AwaitingStart };
        Minigame.Instance = game; MeetingHud.Instance = null;
        PlayerControl.LocalPlayer = new PlayerControl();
        var command = new InternalNavigationCommand { InteractTaskId = 9, ExpectedMinigameInstanceId = game.GetInstanceID() };
        Check(SampleTaskControl.Capture(task)!.Phase == "not_started", "unstarted culture must not look ready");
        Check(SampleTaskControl.Operate(command) && game.StartCalls == 1, "start via native button handler");
        Check(!task.IsComplete, "start must not mark task complete");
        Check(!SampleTaskControl.Operate(command) && game.StartCalls == 1, "processing must not restart");
        task.Data[0] = (byte)SampleMinigame.States.Processing; task.TaskTimer = 58;
        Minigame.Instance = null;
        Check(SampleTaskControl.Capture(task) is { Phase: "processing", RemainingSeconds: 58 }, "timer must be readable away from UI");
        task.TaskTimer = 0;
        Check(SampleTaskControl.Capture(task)!.Phase == "ready", "expired native timer must allow return");
        Check(!task.IsComplete, "ready culture is still unfinished");
        task.TaskTimer = float.NaN;
        Check(!SampleTaskControl.Capture(task)!.Supported, "invalid timer cannot guess readiness");
        task.TaskTimer = 0; task.Data[0] = 255;
        Check(!SampleTaskControl.Capture(task)!.Supported, "unknown native phase fails closed");
        Minigame.Instance = game; game.State = SampleMinigame.States.Selection;
        command.Action = "select_inspect_sample"; command.TargetNumber = 3;
        Check(SampleTaskControl.Operate(command) && game.SelectCalls == 1 && game.LastSelected == 3, "select visually chosen tube");
        Check(!task.IsComplete, "submission does not prove native completion");
        command.ExpectedMinigameInstanceId = game.GetInstanceID()+1;
        Check(!SampleTaskControl.Operate(command), "old window cannot receive a selection");
        command.ExpectedMinigameInstanceId = game.GetInstanceID(); command.ExpectedMatchId = 2;
        Check(!SampleTaskControl.Operate(command), "old match cannot receive a selection");
        command.ExpectedMatchId = 1; command.TargetNumber = 5;
        Check(!SampleTaskControl.Operate(command), "invalid tube cannot be selected");
        command.TargetNumber = 0; command.IssuedAtUtc = DateTimeOffset.UtcNow.AddSeconds(-10);
        Check(!SampleTaskControl.Operate(command), "expired action cannot click");
        task.IsComplete = true;
        Check(SampleTaskControl.Capture(task)!.Phase == "complete", "only game completion means complete");
        Console.WriteLine("PASS sample culture: public phase/timer, leave UI, ready vs complete, native controls and window/match/time guards (synthetic)");
    }
}
