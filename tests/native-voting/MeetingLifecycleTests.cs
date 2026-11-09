using System;
using System.IO;
using System.Text.Json;
using System.Threading;
using AUTelemetryProbe;

internal static class MeetingLifecycleTests
{
    private static void Check(bool ok, string why) { if (!ok) throw new Exception(why); }
    public static void Run()
    {
        var temporaryRoot = Path.GetFullPath(Path.GetTempPath());
        var root = Path.GetFullPath(Path.Combine(temporaryRoot, "au-meeting-selfcheck-" + Guid.NewGuid().ToString("N")));
        Check(Path.GetDirectoryName(root)!.TrimEnd(Path.DirectorySeparatorChar) == temporaryRoot.TrimEnd(Path.DirectorySeparatorChar), "test workspace containment");
        Directory.CreateDirectory(root);
        try
        {
            var scope = new InternalNavigationCommand { CommandId = "neko-scope", ExpectedSessionId = "synthetic",
                ExpectedMatchId = 1, IssuedAtUtc = DateTimeOffset.UtcNow };
            Check(CommandScope.Fresh(scope), "fresh adapter command accepted");
            scope.ExpectedMatchId = 0;
            Check(!CommandScope.SameMatch(scope) && !CommandScope.Fresh(scope), "old match cannot move or close a new task");
            scope.ExpectedMatchId = 1;
            scope.IssuedAtUtc = DateTimeOffset.UtcNow.AddSeconds(-5);
            Check(!CommandScope.Fresh(scope), "expired command not replayed");
            scope.CommandId = "original-studybot";
            Check(CommandScope.Fresh(scope), "preserved original StudyBot protocol");
            var file = Path.Combine(root, "snapshot.json");
            AtomicFile.WriteAllText(file, "old");
            using (var reader = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                AtomicFile.WriteAllText(file, "new");
                using var text = new StreamReader(reader);
                Check(text.ReadToEnd() == "old", "open reader retains original snapshot");
                Check(File.ReadAllText(file) == "new", "writer publishes new snapshot to future readers");
            }
            var statusPath = Path.Combine(root, "meeting.json");
            var meeting = new MeetingHud { CurrentState = MeetingHud.MeetingStates.Discussion,
                playerStates = new[] {new PlayerVoteArea {PlayerId = 0}, new PlayerVoteArea {PlayerId = 1}} };
            PlayerControl.LocalPlayer.Data.IsDead = false;
            MeetingHud.Instance = meeting;
            GameOptionsManager.Instance!.CurrentGameOptions!.Discussion = 0;
            GameOptionsManager.Instance.CurrentGameOptions.Voting = 10;
            MeetingControl.Configure(statusPath, Path.Combine(root,"memory"));
            MeetingControl.Observe(true, 0, PlayerControl.LocalPlayer);
            using (var status = JsonDocument.Parse(File.ReadAllText(statusPath)))
                Check(!status.RootElement.GetProperty("voteGate").GetProperty("ready").GetBoolean(), "short timer does not bypass discussion stage");
            string first = File.ReadAllText(statusPath);
            Thread.Sleep(5);
            MeetingControl.Observe(true, 1, PlayerControl.LocalPlayer);
            Check(File.ReadAllText(statusPath) == first, "meeting status is not rewritten every frame");
            meeting.CurrentState = MeetingHud.MeetingStates.NotVoted;
            MeetingControl.Observe(true, 251, PlayerControl.LocalPlayer);
            using (var status = JsonDocument.Parse(File.ReadAllText(statusPath)))
            {
                var gate = status.RootElement.GetProperty("voteGate");
                Check(gate.GetProperty("ready").GetBoolean(), "ten-second window permits vote before normal 15/30s wait");
                Check(gate.GetProperty("reason").GetString() == "voting_deadline_near", "deadline override is explicit");
            }
            MeetingHud.Instance = null;
            MeetingControl.Observe(false, 500, PlayerControl.LocalPlayer);
            using var closed = JsonDocument.Parse(File.ReadAllText(statusPath));
            Check(!closed.RootElement.GetProperty("meetingOpen").GetBoolean(), "closure written immediately");
            Console.WriteLine("PASS meeting lifecycle: Windows shared-reader replacement, throttled writes, actual phase, short deadline and closure (synthetic)");
        }
        finally { Directory.Delete(root, true); }
    }
}
