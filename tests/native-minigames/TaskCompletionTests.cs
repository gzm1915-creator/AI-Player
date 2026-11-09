using System;
using System.Collections.Generic;
using System.Linq;
using AUTelemetryProbe;

internal static class TaskCompletionTests
{
    internal static void Run()
    {
        static void Check(bool value, string message) { if (!value) throw new Exception(message); }
        var harmony = new HarmonyLib.Harmony { FailType = nameof(LocalTaskCompletedPatch) };
        var registration = new Dictionary<string, bool>();
        GameHooks.Install(harmony, (type, error) => registration[type.Name] = error == null);
        var declared = typeof(TaskCompletionTests).Assembly.GetTypes()
            .Where(t => t.IsDefined(typeof(HarmonyLib.HarmonyPatch), false)).ToArray();
        Check(harmony.Patched.Count == declared.Length && declared.All(harmony.Patched.Contains),
            "startup must register every declared native observer, including newly added completion hooks");
        Check(!registration[nameof(LocalTaskCompletedPatch)] && registration[nameof(TaskStepCompletedPatch)],
            "one failed hook must be reported and cannot skip the remaining observers");
        PlayerControl.LocalPlayer = new PlayerControl { PlayerId = 0 };
        TelemetryCapture.CurrentSessionId = "completion-tests";
        var task = new NormalPlayerTask { Owner = PlayerControl.LocalPlayer, Id = 7, TaskType = "InspectSample" };
        PlayerControl.LocalPlayer.myTasks!.Add(task);
        var original = TaskCompletionMemory.InstanceId(task);
        TaskCompletionMemory.Observe(task);
        Check(TaskCompletionMemory.Capture().Count == 0, "invoking Complete on an unfinished task is not evidence");
        task.IsComplete = true;
        TaskCompletionMemory.Observe(task);
        TaskCompletionMemory.Observe(task);
        var receipts = TaskCompletionMemory.Capture();
        Check(receipts.Count == 1 && receipts[0].Task.Complete && receipts[0].ReceiptId == original,
            "reentrant Complete/NextStep produces one retained native fact");
        TaskCompletionMemory.Initializing(task);
        task.IsComplete = false;
        Check(TaskCompletionMemory.InstanceId(task) != original, "same task ID and Unity object can belong to a new batch");
        Check(TaskCompletionMemory.Capture()[0].Task.Complete, "refreshing task cannot mutate previous receipt");
        task.IsComplete = true;
        TaskCompletionMemory.Observe(task);
        Check(TaskCompletionMemory.Capture().Count == 2, "same ID completed again is a separate receipt");
        var remote = new NormalPlayerTask { Owner = new PlayerControl(), IsComplete = true };
        TaskCompletionMemory.Observe(remote);
        Check(TaskCompletionMemory.Capture().Count == 2, "other players cannot populate local task history");
        for (int i = 0; i < 300; ++i)
        {
            var extra = new NormalPlayerTask { Id = (uint)i, Owner = PlayerControl.LocalPlayer, IsComplete = true };
            PlayerControl.LocalPlayer.myTasks.Clear();
            PlayerControl.LocalPlayer.myTasks.Add(extra);
            TaskCompletionMemory.Observe(extra);
        }
        Check(TaskCompletionMemory.Capture().Count == 128, "retention is bounded across repeated practice batches");
        TaskStartSignalPatch.SignalVersion++;
        Check(TaskCompletionMemory.Capture().Count == 0, "another match discards native completion receipts");
        var local = PlayerControl.LocalPlayer;
        task = new NormalPlayerTask { Id = 9, TaskType = "InspectSample" }; // Owner is allowed to be null.
        local.myTasks.Clear(); local.myTasks.Add(task);
        var record = new NativeTaskRecord { Id = 9 };
        local.Data!.Tasks.Add(record);
        var pending = TaskCompletionMemory.BeforeNativeComplete(local, 9);
        TaskCompletionMemory.AfterNativeComplete(pending);
        Check(TaskCompletionMemory.Capture().Count == 0, "native request without Complete flag cannot succeed");
        record.Complete = true;
        local.myTasks.Clear(); local.Data.Tasks.Clear(); // Native practice replacement inside CompleteTask.
        TaskCompletionMemory.AfterNativeComplete(pending);
        Check(TaskCompletionMemory.Capture().Count == 1 && TaskCompletionMemory.Capture()[0].Source == "native_own_task_record_complete",
            "retain actual Complete flag from original record after immediate list replacement");
        TaskCompletionMemory.AfterNativeComplete(pending);
        Check(TaskCompletionMemory.Capture().Count == 1, "repeated native record is deduplicated");
        task = new NormalPlayerTask { Id = 11, IsComplete = true }; local.myTasks.Add(task);
        TaskCompletionMemory.BeforeTaskListReset(local);
        Check(TaskCompletionMemory.Capture().Count == 2, "local membership proves ownership even when Owner is unset");
        pending = TaskCompletionMemory.BeforeNativeComplete(local, 11);
        TaskStartSignalPatch.SignalVersion++;
        TaskCompletionMemory.AfterNativeComplete(pending);
        Check(TaskCompletionMemory.Capture().Count == 0, "pending native record cannot cross matches");
        TaskStartSignalPatch.SignalVersion = 1;
        TelemetryCapture.CurrentSessionId = "test-session";
        Console.WriteLine("PASS native completion retention: no inferred success, duplicate guard, refresh isolation, ownership, bound and match boundary (synthetic)");
    }
}
