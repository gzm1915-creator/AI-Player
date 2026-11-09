using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using UnityEngine;

namespace AUTelemetryProbe;

internal static class MeetingControl
{
    private const double MinimumVoteWaitSeconds = 30.0;
    private const double OtherPlayerSilenceSeconds = 15.0;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = true
    };

    private static readonly Vector2 CafeteriaOrigin =
        new(-0.781414f, 2.170088f);

    private static string? _statusPath;
    private static string? _memoryRoot;
    private static bool _meetingOpen;
    private static bool _configured;
    private static string _state = "idle";
    private static int _meetingSequence;
    internal static int MeetingSequence => _meetingSequence;

    internal static bool IsCurrentCommand(InternalNavigationCommand command) =>
        command.ExpectedSessionId == TelemetryCapture.CurrentSessionId &&
        command.ExpectedMeetingId == _meetingSequence && command.IssuedAtUtc.HasValue &&
        (DateTimeOffset.UtcNow - command.IssuedAtUtc.Value).TotalSeconds is >= -1 and <= 2;
    private static long _resumeEpoch;
    private static long _nextStatusWriteMs;
    private static long _nextTaskSnapshotMs;
    private static DateTimeOffset? _startedAtUtc;
    private static DateTimeOffset? _endedAtUtc;
    private static DateTimeOffset? _lastOtherChatAtUtc;
    private static bool _playerAtCafeteria;
    private static List<MeetingTaskMemory> _lastKnownTasks = new();
    private static List<MeetingTaskMemory> _meetingTasks = new();

    public static void Configure(string statusPath, string memoryRoot)
    {
        _statusPath = Path.GetFullPath(statusPath);
        _memoryRoot = Path.GetFullPath(memoryRoot);
        Directory.CreateDirectory(
            Path.GetDirectoryName(_statusPath)
            ?? throw new InvalidOperationException("Meeting status path is invalid."));
        Directory.CreateDirectory(_memoryRoot);
        _configured = true;
        WriteStatus();
        TelemetryProbePlugin.LogSource?.LogInfo(
            $"Meeting control ready: {_statusPath}");
    }

    public static void Observe(
        bool meetingOpen,
        long monotonicMs,
        PlayerControl? player)
    {
        if (!_configured)
        {
            return;
        }

        var tasks = new List<MeetingTaskMemory>();
        if (meetingOpen != _meetingOpen || monotonicMs >= _nextTaskSnapshotMs)
        {
            tasks = ReadTasks(player);
            _nextTaskSnapshotMs = monotonicMs + 500;
        }
        if (!meetingOpen && tasks.Count > 0)
        {
            _lastKnownTasks = tasks;
        }

        if (meetingOpen)
        {
            bool justOpened = !_meetingOpen;
            if (!_meetingOpen)
            {
                _meetingOpen = true;
                MeetingVoting.Reset();
                _meetingSequence++;
                _startedAtUtc = DateTimeOffset.UtcNow;
                _lastOtherChatAtUtc = _startedAtUtc;
                _endedAtUtc = null;
                _playerAtCafeteria = false;
                _meetingTasks = tasks.Count > 0
                    ? tasks
                    : CloneTasks(_lastKnownTasks);
                _state = "meeting";
                SaveMeetingSnapshot();
                TelemetryProbePlugin.LogSource?.LogInfo(
                    $"Meeting control detected meeting {_meetingSequence} " +
                    $"with {_meetingTasks.Count} remaining tasks");
            }
            WriteStatusIfDue(monotonicMs, force: justOpened);
            return;
        }

        if (_meetingOpen)
        {
            _meetingOpen = false;
            MeetingVoting.Reset();
            _endedAtUtc = DateTimeOffset.UtcNow;
            _state = "awaiting_cafeteria";
            TelemetryProbePlugin.LogSource?.LogInfo(
                "Meeting control detected meeting end; waiting for cafeteria");
            WriteStatus();
        }

        if (_state == "awaiting_cafeteria")
        {
            _playerAtCafeteria =
                player != null &&
                player.MyPhysics != null &&
                player.CanMove &&
                GameManager.Instance?.GameHasStarted == true &&
                MeetingHud.Instance == null &&
                Minigame.Instance == null &&
                Vector2.Distance(
                    player.GetTruePosition(),
                    CafeteriaOrigin) <= 4.0f;
            if (_playerAtCafeteria)
            {
                _state = "ready";
                _resumeEpoch++;
                TelemetryProbePlugin.LogSource?.LogInfo(
                    $"Meeting control ready to resume, epoch={_resumeEpoch}");
                WriteStatus();
                return;
            }
        }

        WriteStatusIfDue(monotonicMs, force: false);
    }

    internal static void RecordChatActivity(
        PlayerControl? sourcePlayer,
        string? chatText)
    {
        if (!_meetingOpen ||
            sourcePlayer?.Data == null ||
            string.IsNullOrWhiteSpace(chatText))
        {
            return;
        }

        try
        {
            var localPlayer = PlayerControl.LocalPlayer;
            if (localPlayer != null &&
                sourcePlayer.Pointer == localPlayer.Pointer)
            {
                return;
            }

            _lastOtherChatAtUtc = DateTimeOffset.UtcNow;
            TelemetryProbePlugin.LogSource?.LogInfo(
                "Meeting vote gate observed another player's chat");
        }
        catch
        {
            // Chat observation must never interrupt the game chat handler.
        }
    }

    internal static bool TrySendChat(string? text, out string error)
    {
        error = string.Empty;
        var player = PlayerControl.LocalPlayer;
        if (MeetingHud.Instance == null ||
            HudManager.Instance?.Chat == null)
        {
            error = "Meeting chat is not open";
            return false;
        }
        if (player?.Data == null || player.Data.IsDead)
        {
            error = "Local player cannot chat while dead";
            return false;
        }

        var message = (text ?? string.Empty)
            .Replace('\r', ' ')
            .Replace('\n', ' ')
            .Trim();
        if (message.Length == 0)
        {
            error = "Chat text is empty";
            return false;
        }
        if (message.Length > 100)
        {
            message = message[..100];
        }

        try
        {
            if (!player.RpcSendChat(message))
            {
                error = "RpcSendChat rejected the message";
                return false;
            }
            TelemetryProbePlugin.LogSource?.LogInfo(
                $"Meeting chat sent: {message}");
            return true;
        }
        catch (Exception exception)
        {
            error = exception.GetBaseException().Message;
            return false;
        }
    }

    internal static bool TryCastVote(
        int targetPlayerId,
        out bool alreadyVoted,
        out string error)
    {
        alreadyVoted = false;
        error = string.Empty;
        var player = PlayerControl.LocalPlayer;
        if (MeetingHud.Instance == null)
        {
            error = "Meeting is not open";
            return false;
        }
        if (player?.Data == null || player.Data.IsDead)
        {
            error = "Local player cannot vote while dead";
            return false;
        }
        if (MeetingVoting.Capture().HasVoted)
        {
            alreadyVoted = true;
            TelemetryProbePlugin.LogSource?.LogInfo(
                "Meeting vote request satisfied: local player already voted");
            return true;
        }
        var voteGate = GetVoteGateStatus();
        if (!voteGate.Ready)
        {
            error =
                "Vote delayed until 30s elapsed or 15s without other-player " +
                $"chat (elapsed={voteGate.ElapsedSeconds:F1}s, " +
                $"silence={voteGate.SilenceSeconds:F1}s)";
            return false;
        }
        return MeetingVoting.TrySubmit(targetPlayerId, out alreadyVoted, out error);
    }

    private static void SaveMeetingSnapshot()
    {
        if (_memoryRoot == null)
        {
            return;
        }

        var snapshot = new MeetingMemoryDocument
        {
            SchemaVersion = "1.0.0",
            MeetingSequence = _meetingSequence,
            CapturedAtUtc = _startedAtUtc,
            DefaultOriginAnchorId = "cafeteria.default_start",
            DefaultOrigin = new MeetingPoint(
                CafeteriaOrigin.x,
                CafeteriaOrigin.y),
            Tasks = CloneTasks(_meetingTasks)
        };
        var json = JsonSerializer.Serialize(snapshot, JsonOptions);
        WriteAtomic(
            Path.Combine(
                _memoryRoot,
                $"meeting-{_meetingSequence:D4}.json"),
            json);
        WriteAtomic(
            Path.Combine(_memoryRoot, "remaining-tasks.json"),
            json);
    }

    private static List<MeetingTaskMemory> ReadTasks(PlayerControl? player)
    {
        var output = new List<MeetingTaskMemory>();
        if (player?.myTasks == null)
        {
            return output;
        }

        try
        {
            var count = Math.Min(player.myTasks.Count, 20);
            for (var index = 0; index < count; index++)
            {
                try
                {
                    var task = player.myTasks[index];
                    if (task == null || task.IsComplete)
                    {
                        continue;
                    }

                    var locations = new List<MeetingPoint>();
                    try
                    {
                        var sourceLocations = task.Locations;
                        if (sourceLocations != null)
                        {
                            var locationCount = Math.Min(sourceLocations.Count, 8);
                            for (var locationIndex = 0;
                                 locationIndex < locationCount;
                                 locationIndex++)
                            {
                                var point = sourceLocations[locationIndex];
                                locations.Add(new MeetingPoint(point.x, point.y));
                            }
                        }
                    }
                    catch
                    {
                        // Some tasks, such as SubmitScan, expose no console locations.
                    }

                    output.Add(new MeetingTaskMemory
                    {
                        TaskId = task.Id,
                        TaskType = task.TaskType.ToString(),
                        Step = task.TaskStep,
                        StartAt = task.StartAt.ToString(),
                        Locations = locations
                    });
                }
                catch
                {
                    // Skip only the task that cannot currently be represented.
                }
            }
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Could not snapshot meeting tasks: {exception.Message}");
        }

        return output;
    }

    private static List<MeetingTaskMemory> CloneTasks(
        IEnumerable<MeetingTaskMemory> tasks)
    {
        return tasks.Select(task => new MeetingTaskMemory
        {
            TaskId = task.TaskId,
            TaskType = task.TaskType,
            Step = task.Step,
            StartAt = task.StartAt,
            Locations = task.Locations
                .Select(point => new MeetingPoint(point.X, point.Y))
                .ToList()
        }).ToList();
    }

    private static void WriteStatusIfDue(long now, bool force)
    {
        if (!force && now < _nextStatusWriteMs)
        {
            return;
        }
        _nextStatusWriteMs = now + 250;
        WriteStatus();
    }

    private static void WriteStatus()
    {
        if (_statusPath == null)
        {
            return;
        }

        try
        {
            var player = PlayerControl.LocalPlayer;
            var status = new MeetingControlStatus
            {
                SchemaVersion = "1.0.0",
                UpdatedAtUtc = DateTimeOffset.UtcNow,
                Enabled = RuntimeControl.MeetingControlEnabled,
                State = _state,
                MeetingOpen = _meetingOpen,
                MeetingSequence = _meetingSequence,
                StartedAtUtc = _startedAtUtc,
                EndedAtUtc = _endedAtUtc,
                DefaultOriginAnchorId = "cafeteria.default_start",
                DefaultOrigin = new MeetingPoint(
                    CafeteriaOrigin.x,
                    CafeteriaOrigin.y),
                PlayerAtCafeteria = _playerAtCafeteria,
                ResumeEpoch = _resumeEpoch,
                RemainingTaskCount = _meetingTasks.Count,
                RemainingTasks = CloneTasks(_meetingTasks),
                VoteGate = GetVoteGateStatus(),
                Voting = _meetingOpen ? MeetingVoting.Capture() : new MeetingVotingStatus(),
                PlayerPosition = player == null
                    ? null
                    : new MeetingPoint(
                        player.GetTruePosition().x,
                        player.GetTruePosition().y)
            };
            WriteAtomic(
                _statusPath,
                JsonSerializer.Serialize(status, JsonOptions));
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Could not write meeting status: {exception.Message}");
        }
    }

    private static void WriteAtomic(string path, string content)
    {
        AtomicFile.WriteAllText(path, content);
    }

    private static MeetingVoteGateStatus GetVoteGateStatus()
    {
        var now = DateTimeOffset.UtcNow;
        var started = _startedAtUtc;
        var elapsed = started.HasValue
            ? Math.Max(0.0, (now - started.Value).TotalSeconds)
            : 0.0;
        var silenceAnchor = _lastOtherChatAtUtc ?? started;
        var silence = silenceAnchor.HasValue
            ? Math.Max(0.0, (now - silenceAnchor.Value).TotalSeconds)
            : 0.0;
        var voting = MeetingVoting.Capture();
        var ready = _meetingOpen && voting.CanVote &&
            started.HasValue &&
            (elapsed >= MinimumVoteWaitSeconds ||
             silence >= OtherPlayerSilenceSeconds ||
             voting.RemainingSeconds is > 0 and <= 10);
        var reason = !_meetingOpen
            ? "meeting_not_open"
            : !voting.Supported ? "voting_observation_unavailable"
            : voting.HasVoted ? "already_voted"
            : !voting.LocalAlive ? "local_player_dead"
            : !voting.VotingOpen ? "game_not_in_voting_phase"
            : voting.Pending ? "vote_pending"
            : ready && voting.RemainingSeconds is > 0 and <= 10 ? "voting_deadline_near"
            : ready && silence >= OtherPlayerSilenceSeconds
                ? "other_players_silent_15s"
                : ready
                    ? "minimum_30s_elapsed"
                    : "waiting_for_discussion";

        return new MeetingVoteGateStatus
        {
            Ready = ready,
            Reason = reason,
            MinimumWaitSeconds = MinimumVoteWaitSeconds,
            SilenceThresholdSeconds = OtherPlayerSilenceSeconds,
            ElapsedSeconds = Math.Round(elapsed, 2),
            SilenceSeconds = Math.Round(silence, 2),
            LastOtherChatAtUtc = _lastOtherChatAtUtc
        };
    }
}

internal sealed class MeetingControlStatus
{
    public string SchemaVersion { get; init; } = string.Empty;
    public DateTimeOffset UpdatedAtUtc { get; init; }
    public bool Enabled { get; init; }
    public string State { get; init; } = string.Empty;
    public bool MeetingOpen { get; init; }
    public int MeetingSequence { get; init; }
    public DateTimeOffset? StartedAtUtc { get; init; }
    public DateTimeOffset? EndedAtUtc { get; init; }
    public string DefaultOriginAnchorId { get; init; } = string.Empty;
    public MeetingPoint DefaultOrigin { get; init; }
    public bool PlayerAtCafeteria { get; init; }
    public long ResumeEpoch { get; init; }
    public int RemainingTaskCount { get; init; }
    public List<MeetingTaskMemory> RemainingTasks { get; init; } = new();
    public MeetingVoteGateStatus VoteGate { get; init; } = new();
    public MeetingVotingStatus Voting { get; init; } = new();
    public MeetingPoint? PlayerPosition { get; init; }
}

internal sealed class MeetingVoteGateStatus
{
    public bool Ready { get; init; }
    public string Reason { get; init; } = string.Empty;
    public double MinimumWaitSeconds { get; init; }
    public double SilenceThresholdSeconds { get; init; }
    public double ElapsedSeconds { get; init; }
    public double SilenceSeconds { get; init; }
    public DateTimeOffset? LastOtherChatAtUtc { get; init; }
}

internal sealed class MeetingMemoryDocument
{
    public string SchemaVersion { get; init; } = string.Empty;
    public int MeetingSequence { get; init; }
    public DateTimeOffset? CapturedAtUtc { get; init; }
    public string DefaultOriginAnchorId { get; init; } = string.Empty;
    public MeetingPoint DefaultOrigin { get; init; }
    public List<MeetingTaskMemory> Tasks { get; init; } = new();
}

internal sealed class MeetingTaskMemory
{
    public uint TaskId { get; init; }
    public string TaskType { get; init; } = string.Empty;
    public int Step { get; init; }
    public string StartAt { get; init; } = string.Empty;
    public List<MeetingPoint> Locations { get; init; } = new();
}

internal readonly record struct MeetingPoint(float X, float Y);
