// Synthetic interfaces: no game binaries are loaded and no real vote is sent.
using System;
using System.Collections.Generic;
public sealed class PlayerInfo { public bool IsDead, Disconnected; public string PlayerName = "synthetic"; }
public sealed class PlayerControl {
    public static PlayerControl LocalPlayer = new(); public byte PlayerId; public PlayerInfo Data = new();
    public IntPtr Pointer; public object MyPhysics = new(); public bool CanMove = true;
    public List<PlayerTask> myTasks = new(); public UnityEngine.Vector2 GetTruePosition() => new(0,0);
    public bool RpcSendChat(string text) => true;
}
public sealed class PlayerVoteArea {
    public static byte SkippedVote = 253;
    public byte PlayerId, VotedForId = 255; public bool AmDead, DidVote;
}
public sealed class GameData {
    public static GameData Instance = new(); public Dictionary<byte, PlayerInfo> Players = new();
    public PlayerInfo? GetPlayerById(byte id) => Players.GetValueOrDefault(id);
}
public sealed class MeetingHud {
    public enum MeetingStates { Animating, Discussion, NotVoted, Voted, Results, Proceeding }
    public static MeetingHud? Instance;
    public MeetingStates CurrentState;
    public float discussionTimer;
    public PlayerVoteArea[]? playerStates;
    public int RpcCalls; public byte LastTarget;
    public void CmdCastVote(byte voter, byte target) { RpcCalls++; LastTarget = target; }
}
namespace AmongUs.GameOptions { public enum Int32OptionNames { DiscussionTime, VotingTime } }
namespace UnityEngine {
    public readonly record struct Vector2(float x, float y) {
        public static float Distance(Vector2 a, Vector2 b) => MathF.Sqrt((a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y));
    }
}
public sealed class PlayerTask {
    public bool IsComplete; public uint Id; public int TaskStep;
    public string TaskType = "SwipeCard", StartAt = "Admin";
    public List<UnityEngine.Vector2> Locations = new();
}
public sealed class Minigame { public static Minigame? Instance; }
public sealed class GameManager { public static GameManager Instance = new(); public bool GameHasStarted = true; }
public sealed class HudManager { public static HudManager Instance = new(); public object Chat = new(); }
namespace AUTelemetryProbe {
    internal static class TelemetryCapture { internal static string CurrentSessionId = "synthetic"; }
    internal static class TaskStartSignalPatch { internal static long SignalVersion = 1; }
    internal static class RuntimeControl { internal static bool MeetingControlEnabled = true; }
    internal static class TelemetryProbePlugin { internal static SilentLog? LogSource = null; }
    internal sealed class SilentLog { public void LogInfo(string text) {} public void LogWarning(string text) {} }
    internal sealed class InternalNavigationCommand {
        public string CommandId = ""; public long ExpectedMatchId = -1;
        public string? ExpectedSessionId = null; public int ExpectedMeetingId = 0; public DateTimeOffset? IssuedAtUtc = null;
    }
}
public sealed class GameOptionsManager {
    public static GameOptionsManager? Instance = new();
    public Options? CurrentGameOptions = new();
    public sealed class Options {
        public int Discussion = 0, Voting = 10;
        public int GetInt(AmongUs.GameOptions.Int32OptionNames name) => name == AmongUs.GameOptions.Int32OptionNames.VotingTime ? Voting : Discussion;
    }
}
