using System;
using System.Collections.Generic;

namespace AUTelemetryProbe;

// Reads only the public meeting roster and our own vote, never anyone else's choice.
internal static class MeetingVoting
{
    private static DateTimeOffset? _submittedAt;
    private static int? _submittedTarget;

    internal static void Reset()
    {
        _submittedAt = null;
        _submittedTarget = null;
    }

    internal static MeetingVotingStatus Capture(DateTimeOffset? at = null)
    {
        var result = new MeetingVotingStatus();
        var meeting = MeetingHud.Instance;
        var player = PlayerControl.LocalPlayer;
        if (meeting == null || player?.Data == null) return result;
        try
        {
            result.Phase = meeting.CurrentState.ToString();
            result.LocalAlive = !player.Data.IsDead && !player.Data.Disconnected;
            result.VotingOpen = meeting.CurrentState is MeetingHud.MeetingStates.NotVoted
                or MeetingHud.MeetingStates.Voted;
            try
            {
                var options = GameOptionsManager.Instance?.CurrentGameOptions;
                var votingSeconds = options?.GetInt(AmongUs.GameOptions.Int32OptionNames.VotingTime) ?? 0;
                var discussionSeconds = options?.GetInt(AmongUs.GameOptions.Int32OptionNames.DiscussionTime) ?? 0;
                var elapsed = meeting.discussionTimer;
                if (votingSeconds > 0 && float.IsFinite(elapsed) && elapsed >= 0)
                    result.RemainingSeconds = Math.Max(0, discussionSeconds + votingSeconds - elapsed);
            }
            catch { /* Unknown/infinite timers keep the normal discussion gate. */ }
            var areas = meeting.playerStates;
            if (areas == null) return result;
            bool localFound = false;
            for (int i = 0; i < areas.Length; i++)
            {
                var area = areas[i];
                if (area == null) continue;
                byte id = area.PlayerId;
                var info = GameData.Instance?.GetPlayerById(id);
                result.Candidates.Add(new MeetingVoteCandidate
                {
                    PlayerId = id,
                    PlayerName = info?.PlayerName ?? string.Empty,
                    Eligible = !area.AmDead && info != null && !info.IsDead && !info.Disconnected
                });
                if (id != player.PlayerId) continue;
                localFound = true;
                result.LocalAlive &= !area.AmDead;
                result.HasVoted = area.DidVote;
                // Do not expose any other player's VotedForId, even if the host knows it.
                if (area.DidVote)
                {
                    byte target = area.VotedForId;
                    result.TargetPlayerId = target == PlayerVoteArea.SkippedVote ? -1
                        : target < PlayerVoteArea.SkippedVote ? (int?)target : null;
                }
            }
            result.Supported = localFound;
            result.Pending = _submittedAt.HasValue && !result.HasVoted &&
                ((at ?? DateTimeOffset.UtcNow) - _submittedAt.Value).TotalSeconds < 5;
            result.SubmittedTargetPlayerId = _submittedTarget;
            result.State = result.HasVoted ? "recorded" : result.Pending ? "submitted"
                : _submittedAt.HasValue ? "unconfirmed" : "not_submitted";
            result.CanVote = result.Supported && result.LocalAlive && result.VotingOpen
                && !result.HasVoted && !result.Pending;
        }
        catch
        {
            // A partially rebuilt meeting must not allow a vote or report success.
            result.Supported = false;
            result.CanVote = false;
            result.HasVoted = false;
            result.State = "unavailable";
        }
        return result;
    }

    internal static bool TrySubmit(int target, out bool alreadyVoted, out string error)
    {
        var status = Capture();
        alreadyVoted = status.Supported && status.HasVoted;
        error = string.Empty;
        if (alreadyVoted) return true;
        if (!status.CanVote)
        {
            error = "Voting unavailable: discussion/results, dead player, pending request or missing roster";
            return false;
        }
        var meeting = MeetingHud.Instance;
        var player = PlayerControl.LocalPlayer;
        if (meeting == null || player == null)
        {
            error = "Meeting or local player no longer available";
            return false;
        }
        if (target != -1 && !status.Candidates.Exists(p => p.PlayerId == target && p.Eligible))
        {
            error = "Vote target is not a living connected meeting participant";
            return false;
        }
        try
        {
            // Use the game's actual skip constant, not 255 (HasNotVoted).
            byte suspect = target == -1 ? PlayerVoteArea.SkippedVote : (byte)target;
            meeting.CmdCastVote(player.PlayerId, suspect);
            _submittedAt = DateTimeOffset.UtcNow;
            _submittedTarget = target;
            // Leave the native vote flags alone. Only a subsequent game observation confirms it.
            return true;
        }
        catch (Exception exception)
        {
            error = exception.GetBaseException().Message;
            return false;
        }
    }
}

internal sealed class MeetingVotingStatus
{
    public bool Supported { get; set; }
    public string Phase { get; set; } = "unavailable";
    public bool LocalAlive { get; set; }
    public bool VotingOpen { get; set; }
    public bool CanVote { get; set; }
    public bool HasVoted { get; set; }
    public bool Pending { get; set; }
    public double? RemainingSeconds { get; set; }
    public int? TargetPlayerId { get; set; }
    public int? SubmittedTargetPlayerId { get; set; }
    public string State { get; set; } = "unavailable";
    public List<MeetingVoteCandidate> Candidates { get; set; } = new();
}

internal sealed class MeetingVoteCandidate
{
    public int PlayerId { get; set; }
    public string PlayerName { get; set; } = string.Empty;
    public bool Eligible { get; set; }
}
