using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace AUTelemetryProbe;

public sealed class InternalNavigationBehaviour : MonoBehaviour
{
    private const int CommandPollMs = 50;
    private const int StatusWriteMs = 100;
    private const int ProgressTimeoutMs = 900;
    private const double NormalNavigationMaxSnapRadius = 1.25;
    private const double TaskInteractionStartSnapRadius = 1.75;
    private const int RecoveryStallMs = 450;
    private const int RecoveryAxisTimeoutMs = 3000;
    private const int RecoveryBackoffMs = 620;
    private const float RecoveryProgressDistance = 0.045f;
    private const float RecoveryReachTolerance = 0.38f;
    private const float BodyWaypointTolerance = 0.62f;
    private const int BodyScanMs = 80;
    private const int BodyCandidateLostMs = 5000;
    private const int ReportClickLockMs = 1500;
    private const float BodyDetectionMaxDistance = 4.5f;
    private const float BodyDirectAttachRadius = 2.2f;
    private const float BodyAnchorFinalTolerance = 0.24f;
    internal static InternalNavigationBehaviour? ActiveInstance { get; private set; }

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };

    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private readonly Dictionary<string, CachedNavigationGraph> _graphCache =
        new(StringComparer.OrdinalIgnoreCase);

    private string? _commandPath;
    private string? _statusPath;
    private string? _lastCommandId;
    private ActiveNavigation? _active;
    private PlayerPursuit? _pursuit;
    private PursuitInfo _pursuitInfo = new();
    private InternalNavigationCommand? _openingTask;
    private long _taskOpenStartedMs;
    private long _nextTaskClickMs;
    private int _taskClickAttempts;
    private Vector2 _desiredVelocity;
    private long _nextPollMs;
    private long _nextStatusMs;
    private string _state = "idle";
    private string? _lastError;
    private string? _lastTargetAnchorId;
    private int _lastWaypointIndex;
    private int _lastRoutePointCount;
    private NavigationPoint[] _lastPlannedRoute = Array.Empty<NavigationPoint>();
    private int _lastRecoveries;
    private double? _lastFinalError;
    private string _lastCompletionMode = "hold";
    private string? _lastRecoveryAnchorId;
    private string? _lastRecoveryMode;
    private long _nextBodyScanMs;
    private BodyTarget? _bodyTarget;
    private NavigationPlan? _bodyPlan;
    private int _bodyWaypointIndex;
    private long _bodyTargetSeenMs;
    private long _reportClickLockedUntilMs;
    private BodyReportStatus _bodyReportStatus = new();

    internal string CurrentState => _state;
    internal string? CurrentTargetAnchorId =>
        _active?.Command.TargetAnchorId ?? _lastTargetAnchorId;
    internal string? CurrentRecoveryAnchorId =>
        _active?.Recovery?.CurrentCandidate.AnchorId ?? _lastRecoveryAnchorId;

    private void Start()
    {
        ActiveInstance = this;
        var root = Path.Combine(Paths.PluginPath, "AUTelemetryProbe");
        _commandPath = TelemetryProbePlugin.InternalNavigationCommandPath?.Value
            ?? Path.Combine(root, "internal-navigation-command.json");
        _statusPath = TelemetryProbePlugin.InternalNavigationStatusPath?.Value
            ?? Path.Combine(root, "internal-navigation-status.json");
        WriteStatus();
        TelemetryProbePlugin.LogSource?.LogInfo(
            $"Internal navigation ready: {_commandPath}");
    }

    private void OnDestroy()
    {
        MinigameAutomation.ReleaseMouse();
        if (ReferenceEquals(ActiveInstance, this))
        {
            ActiveInstance = null;
        }
    }

    private void Update()
    {
        PlayerAwareness.Scan();
        VentTransport.Tick();
        SabotageControl.Tick();
        var now = _clock.ElapsedMilliseconds;
        var ownedCommand = _openingTask ?? _active?.Command;
        if (ownedCommand != null && !CommandScope.SameMatch(ownedCommand))
        {
            StopActive("stopped");
            _lastError = "对局已变化，停止旧导航或任务交互";
            WriteStatusIfDue(now);
            return;
        }
        if (VentTransport.Busy || PlayerControl.LocalPlayer?.inVent == true || PlayerControl.LocalPlayer?.walkingToVent == true)
        {
            if (_active != null || _openingTask != null) StopActive("stopped");
            _desiredVelocity = Vector2.zero;
            WriteStatusIfDue(now);
            return;
        }

        // Control commands such as minigame clicks must remain available while
        // a navigation is paused with a minigame open. Only new navigation
        // commands are blocked until the active navigation has finished.
        if (now >= _nextPollMs)
        {
            _nextPollMs = now + CommandPollMs;
            var previousCommandId = _lastCommandId;
            TryReadCommand(_active == null && _openingTask == null);
            if (!string.Equals(
                    previousCommandId,
                    _lastCommandId,
                    StringComparison.Ordinal))
            {
                return;
            }
        }

        if (!RuntimeControl.AutoNavigationEnabled)
        {
            ClearTemporaryBodyAnchor();
            if (_active != null || _openingTask != null)
            {
                StopActive("stopped");
                _lastError = null;
            }
            if (CanNavigate(out _))
            {
                TryImmediateBodyReport(now);
            }
            _desiredVelocity = Vector2.zero;
            if (!string.Equals(_state, "reported", StringComparison.Ordinal))
            {
                _state = "navigation_disabled";
            }
            WriteStatusIfDue(now);
            return;
        }

        if (_pursuit != null && !StepPursuit(now))
        {
            WriteStatusIfDue(now);
            return;
        }

        if (PracticeDialog.Tick())
            TelemetryProbePlugin.LogSource?.LogInfo("Closed practice task-refresh dialogue during autoplay.");

        if (_openingTask != null)
        {
            StepTaskOpening(now);
            WriteStatusIfDue(now);
            return;
        }

        // Terminal navigation results are acknowledgements, not a transient
        // per-frame state. Preserve them until another command is accepted.
        if (_active == null &&
            _state is "completed" or "failed" or "stopped")
        {
            _desiredVelocity = Vector2.zero;
            if (RuntimeControl.AutoReportBodiesEnabled && CanNavigate(out _))
                TryImmediateBodyReport(now);
            WriteStatusIfDue(now);
            return;
        }

        if (_active == null &&
            !RuntimeControl.AutoReportBodiesEnabled)
        {
            _desiredVelocity = Vector2.zero;
            if (CanNavigate(out var idleReason))
            {
                _state = "idle";
                _lastError = null;
            }
            else
            {
                _state = "paused";
                _lastError = idleReason;
            }
            WriteStatusIfDue(now);
            return;
        }

        if (!CanNavigate(out var blockedReason))
        {
            _desiredVelocity = Vector2.zero;
            if (string.Equals(
                    blockedReason,
                    "minigame_open",
                    StringComparison.Ordinal) &&
                string.Equals(
                    _state,
                    "completed",
                    StringComparison.Ordinal) &&
                string.Equals(
                    _lastCompletionMode,
                    "interaction_opened",
                    StringComparison.Ordinal))
            {
                // Opening the task minigame is the terminal signal for an
                // interaction navigation. Preserve that completion signal
                // until the task executor consumes it.
                WriteStatusIfDue(now);
                return;
            }
            if (string.Equals(
                    blockedReason,
                    "meeting_open",
                    StringComparison.Ordinal))
            {
                ClearTemporaryBodyAnchor();
                if (_active != null)
                {
                    StopActive("stopped");
                    _lastError = null;
                }
            }
            if (_active != null)
            {
                _active.HasInterrupted = true;
            }
            SetState("paused", blockedReason);
            WriteStatusIfDue(now);
            return;
        }

        if (now < _reportClickLockedUntilMs)
        {
            _desiredVelocity = Vector2.zero;
            SetState("reporting", null);
            WriteStatusIfDue(now);
            return;
        }

        if (_pursuit != null && _pursuitInfo.State == "native_kill_ready")
        {
            _desiredVelocity = Vector2.zero;
            PlayerControl.LocalPlayer?.MyPhysics?.SetNormalizedVelocity(Vector2.zero);
            WriteStatusIfDue(now);
            return;
        }

        StepBodyReport(now);
        if (_bodyTarget != null)
        {
            StepTemporaryBodyAnchor(now);
            WriteStatusIfDue(now);
            return;
        }

        if (_active == null)
        {
            _desiredVelocity = Vector2.zero;
            return;
        }

        if (_active.HasInterrupted)
        {
            _active.HasInterrupted = false;
            _active.LastProgressAt = now;
            _active.Progress.Reset();
        }

        StepNavigation(now);
        WriteStatusIfDue(now);
    }

    private void FixedUpdate()
    {
        ApplyVelocityIfActive(PlayerControl.LocalPlayer?.MyPhysics);
    }

    internal void ApplyVelocityIfActive(PlayerPhysics? physics)
    {
        if (_active == null ||
            _desiredVelocity == Vector2.zero ||
            physics == null ||
            PlayerControl.LocalPlayer?.MyPhysics == null ||
            physics.Pointer != PlayerControl.LocalPlayer.MyPhysics.Pointer)
        {
            return;
        }

        var position = PlayerControl.LocalPlayer.GetTruePosition();
        if (!CrimeSceneMemory.Allows(position, position + _desiredVelocity.normalized * 0.12f))
        { physics.SetNormalizedVelocity(Vector2.zero); return; }
        physics.SetNormalizedVelocity(_desiredVelocity);
    }

    private void TryReadCommand(bool allowNavigation)
    {
        if (_commandPath == null || !File.Exists(_commandPath))
        {
            return;
        }

        InternalNavigationCommand? command = null;
        try
        {
            using var stream = new FileStream(
                _commandPath,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
            command = JsonSerializer.Deserialize<InternalNavigationCommand>(stream, JsonOptions);
        }
        catch (IOException)
        {
            return;
        }
        catch (JsonException)
        {
            return;
        }
        catch (Exception exception)
        {
            Fail("command_read_failed", exception.Message);
            return;
        }

        if (command == null ||
            string.IsNullOrWhiteSpace(command.CommandId) ||
            string.Equals(command.CommandId, _lastCommandId, StringComparison.Ordinal))
        {
            return;
        }

        if (string.Equals(command.Action, "stop", StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            ClearTemporaryBodyAnchor();
            StopActive("stopped");
            _state = "completed";
            WriteStatus();
            return;
        }

        if (!CommandScope.Fresh(command))
        {
            _lastCommandId = command.CommandId;
            Fail("invalid_command", "指令已过期或属于其他对局，未执行");
            return;
        }

        if (string.Equals(command.Action, "impostor_kill", StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            StopActive("stopped");
            var error = ImpostorControl.Kill(command);
            if (error != null) ImpostorControl.Reject(command, error);
            _state = error == null ? "submitted" : "failed";
            _lastError = error;
            WriteStatus();
            return;
        }

        if (string.Equals(
                command.Action,
                "close_minigame",
                StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            StopActive("stopped");
            var closeError = TryCloseActiveMinigame();
            _state = "completed";
            _lastError = closeError;
            WriteStatus();
            return;
        }

        if (command.Action is "start_inspect_sample" or "select_inspect_sample")
        {
            _lastCommandId = command.CommandId;
            if (!SampleTaskControl.Operate(command))
            {
                Fail("sample_control_failed", "检查样本的任务窗口或阶段已变化，未操作");
                return;
            }
            _state = "completed";
            _lastError = null;
            WriteStatus();
            return;
        }

        if (string.Equals(
                command.Action,
                "click_minigame_object",
                StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            if (TryClickMinigameObject(command.TargetObjectName))
            {
                _state = "completed";
                _lastError = null;
            }
            else
            {
                Fail(
                    "minigame_click_failed",
                    $"Minigame object is unavailable: " +
                    $"{command.TargetObjectName}");
                return;
            }
            WriteStatus();
            return;
        }

        if (string.Equals(
                command.Action,
                "click_unlock_number",
                StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            if (!TryClickUnlockNumber(command.TargetNumber))
            {
                Fail(
                    "minigame_click_failed",
                    $"Unlock number is unavailable: " +
                    $"{command.TargetNumber}");
                return;
            }
            _state = "completed";
            _lastError = null;
            WriteStatus();
            return;
        }

        if (string.Equals(
                command.Action,
                "click_reactor_button",
                StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            if (!TryClickReactorButton(command.TargetNumber))
            {
                Fail(
                    "minigame_click_failed",
                    $"Reactor button is unavailable: " +
                    $"{command.TargetNumber}");
                return;
            }
            _state = "completed";
            _lastError = null;
            WriteStatus();
            return;
        }

        if (string.Equals(
                command.Action,
                "open_vent_cleaning",
                StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            var vent = Minigame.Instance?
                .TryCast<VentCleaningMinigame>();
            if (vent == null)
            {
                Fail(
                    "minigame_click_failed",
                    "VentCleaning minigame is unavailable");
                return;
            }
            if (!vent.ventOpen)
            {
                vent.OpenVent();
            }
            _state = "completed";
            _lastError = null;
            WriteStatus();
            return;
        }

        if (command.Action is "enter_vent" or "move_vent" or "exit_vent")
        {
            _lastCommandId = command.CommandId;
            Fail("vent_command_replaced", "请使用独立vent技能入口，以获得管道进出和移动确认");
            return;
        }

        if (string.Equals(
                command.Action,
                "send_chat",
                StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            if (!MeetingControl.IsCurrentCommand(command))
            {
                Fail("chat_send_failed", "会议发言指令已过期或属于其他会议");
                return;
            }
            if (!MeetingControl.TrySendChat(
                    command.ChatText,
                    out var chatError))
            {
                Fail("chat_send_failed", chatError);
                return;
            }
            _state = "completed";
            _lastError = null;
            WriteStatus();
            return;
        }

        if (string.Equals(
                command.Action,
                "cast_vote",
                StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            if (!MeetingControl.IsCurrentCommand(command))
            {
                Fail("vote_cast_failed", "投票指令已过期或属于其他会议");
                return;
            }
            if (!MeetingControl.TryCastVote(
                    command.TargetNumber,
                    out var alreadyVoted,
                    out var voteError))
            {
                Fail("vote_cast_failed", voteError);
                return;
            }
            _state = "completed";
            _lastError = null;
            // completed here means the command handler returned; voting.hasVoted
            // is the separate game observation used to confirm a recorded vote.
            _lastCompletionMode = alreadyVoted ? "already_voted" : "vote_submitted";
            WriteStatus();
            return;
        }

        if (string.Equals(
                command.Action,
                "clean_vent_dirt",
                StringComparison.OrdinalIgnoreCase))
        {
            _lastCommandId = command.CommandId;
            if (!TryCleanVentDirt(command.TargetNumber))
            {
                Fail(
                    "minigame_click_failed",
                    $"Vent dirt is unavailable: " +
                    $"{command.TargetNumber}");
                return;
            }
            _state = "completed";
            _lastError = null;
            WriteStatus();
            return;
        }

        if (command.Action == "pursue_player")
        {
            if (!allowNavigation) return;
            _lastCommandId = command.CommandId;
            try
            {
                var error = PlayerPursuit.Validate(command, true, out var observed);
                if (error != null) throw new InvalidOperationException(error);
                if (command.InteractAfterArrival || command.StopOnTaskButton || command.MaxRecoveries != 0)
                    throw new InvalidOperationException("追踪不允许任务交互或锚点恢复");
                _pursuit = new PlayerPursuit(command, _clock.ElapsedMilliseconds);
                _pursuitInfo = _pursuit.Info;
                ReplanPursuit(observed!, _clock.ElapsedMilliseconds);
                WriteStatus();
            }
            catch (Exception e) { Fail("pursuit_start_failed", e.Message); }
            return;
        }

        if (command.Action is not ("navigate" or "navigate_vent"))
        {
            _lastCommandId = command.CommandId;
            Fail("unsupported_action", $"Unsupported action: {command.Action}");
            return;
        }

        if (!allowNavigation)
        {
            return;
        }

        try
        {
            var player = PlayerControl.LocalPlayer
                ?? throw new InvalidOperationException("Local player is unavailable.");
            if (!CanNavigate(out var reason))
            {
                throw new InvalidOperationException($"Movement gated: {reason}");
            }
            var ventTarget = command.Action == "navigate_vent"
                ? UnityEngine.Object.FindObjectsOfType<Vent>().FirstOrDefault(v => v != null && v.Id == command.TargetNumber) : null;
            if (command.Action == "navigate_vent" && (ventTarget == null || player.Data?.RoleType.ToString() != "Impostor" ||
                player.Data.Role?.CanVent != true || !RuntimeControl.UseVentsEnabled ||
                command.ExpectedSessionId != TelemetryCapture.CurrentSessionId || command.ExpectedMatchId != TaskStartSignalPatch.SignalVersion ||
                !command.IssuedAtUtc.HasValue || (DateTimeOffset.UtcNow - command.IssuedAtUtc.Value).TotalSeconds is < -1 or > 2))
                throw new InvalidOperationException("管道接近指令的角色、许可、管道编号或对局时效不满足条件");

            var graph = LoadGraph(command.RoutePath, command.ClusterTolerance);
            var start = player.GetTruePosition();
            var targetPosition = ventTarget != null ? new NavigationPoint(ventTarget.transform.position.x, ventTarget.transform.position.y)
                : graph.ResolveTargetPosition(command.TargetAnchorId);
            var startTargetDistance = Vector2.Distance(
                start,
                new Vector2(
                    targetPosition.X,
                    targetPosition.Y));

            // Arrival planning normally creates an ActiveNavigation instance
            // before dispatching the interaction. If the player is already
            // standing on the target route node, FindRoute has no movement
            // points to return, and the interaction branch would never run.
            if (command.InteractAfterArrival &&
                startTargetDistance <= command.TaskStopRadius &&
                IsTaskButtonReady())
            {
                _lastCommandId = command.CommandId;
                _lastTargetAnchorId = command.TargetAnchorId;
                _lastWaypointIndex = 0;
                _lastRoutePointCount = 0;
                _lastRecoveries = 0;
                _lastFinalError = startTargetDistance;
                _lastCompletionMode = "interaction_pending";
                _lastRecoveryAnchorId = null;
                _lastRecoveryMode = null;
                BeginTaskOpening(command, _clock.ElapsedMilliseconds);
                _lastError = null;
                WriteStatus();
                TelemetryProbePlugin.LogSource?.LogInfo(
                    $"Internal navigation {command.CommandId}: " +
                    $"already at {command.TargetAnchorId}; " +
                    $"dispatched task interaction at " +
                    $"distance={startTargetDistance:F3}");
                return;
            }

            var normalSnapRadius = Math.Clamp(
                command.SnapRadius,
                1.0,
                command.InteractAfterArrival
                    ? TaskInteractionStartSnapRadius
                    : NormalNavigationMaxSnapRadius);
            var plan = ventTarget != null
                ? VentApproachPlanner.Plan(graph, start, ventTarget.transform.position, ventTarget.UsableDistance, command.TargetAnchorId, IsRouteConnectionClear)
                : command.EscapeRoute && PlayerControl.LocalPlayer?.Data?.RoleType.ToString() == "Impostor" && CrimeSceneMemory.Capture().Count > 0
                ? EscapePlanner.Plan(graph, start, command.TargetAnchorId, IsRouteConnectionClear)
                : command.InteractAfterArrival && startTargetDistance <= 0.20
                ? new NavigationPlan(new[] { start, new Vector2(targetPosition.X, targetPosition.Y) },
                    0, 0, 0, 0, startTargetDistance, "current_task_point", command.TargetAnchorId)
                : graph.FindRoute(start, command.TargetAnchorId, normalSnapRadius, IsRouteConnectionClear);
            _lastCommandId = command.CommandId;
            _active = new ActiveNavigation(command, plan, _clock.ElapsedMilliseconds);
            _lastPlannedRoute = plan.Points.Select(p => new NavigationPoint(p.x, p.y)).ToArray();
            _lastTargetAnchorId = command.TargetAnchorId;
            _lastWaypointIndex = 0;
            _lastRoutePointCount = plan.Points.Count;
            _lastRecoveries = 0;
            _lastFinalError = null;
            _lastCompletionMode = command.StopOnTaskButton
                ? "task_button_ready"
                : "hold";
            _lastRecoveryAnchorId = null;
            _lastRecoveryMode = null;
            _state = "running";
            _lastError = null;
            _desiredVelocity = Vector2.zero;
            TelemetryProbePlugin.LogSource?.LogInfo(
                $"Internal navigation {command.CommandId}: " +
                $"{plan.StartNode} -> {plan.GoalNode}, " +
                $"{plan.Points.Count} points");
            WriteStatus();
        }
        catch (Exception exception)
        {
            _lastCommandId = command.CommandId;
            StopActive("failed");
            // Rejected requests must not inherit the previous destination or
            // route in the panel and action history.
            _lastTargetAnchorId = command.TargetAnchorId;
            _lastPlannedRoute = Array.Empty<NavigationPoint>();
            _lastWaypointIndex = _lastRoutePointCount = _lastRecoveries = 0;
            _lastFinalError = null;
            _lastRecoveryAnchorId = _lastRecoveryMode = null;
            _lastCompletionMode = "planning_failed";
            Fail("navigation_start_failed", exception.Message);
        }
    }

    private bool StepPursuit(long now)
    {
        var pursuit = _pursuit!;
        var error = PlayerPursuit.Validate(pursuit.Command, false, out var observed);
        if (error != null || pursuit.Expired(now))
        {
            Fail("pursuit_cancelled", error ?? "追踪超过45秒，放弃目标");
            return false;
        }
        try
        {
            if (pursuit.NeedsPlan(observed!, now)) ReplanPursuit(observed!, now);
            var role = ImpostorControl.Capture();
            _pursuitInfo.State = role.CanKill && role.KillTargetId == pursuit.Command.TargetNumber
                ? "native_kill_ready" : "tracking";
            return true;
        }
        catch (Exception e) { Fail("pursuit_route_failed", e.Message); return false; }
    }

    private void ReplanPursuit(ObservedPlayer target, long now)
    {
        var pursuit = _pursuit!;
        var command = pursuit.Command;
        var graph = LoadGraph(command.RoutePath, command.ClusterTolerance);
        var plan = PursuitPlanner.Plan(graph, PlayerControl.LocalPlayer!.GetTruePosition(), target.Position,
            command.TargetAnchorId, IsRouteConnectionClear);
        // Keep the original overall deadline when refreshing the moving endpoint.
        var previous = _active;
        _active = new ActiveNavigation(command, plan, pursuit.StartedAtMs);
        if (previous != null)
        {
            _active.LastProgressAt = previous.LastProgressAt;
            _active.LastTrackedPosition = previous.LastTrackedPosition;
            _active.StuckSinceMs = previous.StuckSinceMs;
        }
        _lastPlannedRoute = plan.Points.Select(p => new NavigationPoint(p.x, p.y)).ToArray();
        _lastTargetAnchorId = command.TargetAnchorId;
        _lastCompletionMode = "pursuit";
        _lastError = null;
        _state = "running";
        _desiredVelocity = Vector2.zero;
        pursuit.Planned(target, now);
    }

    private void StepBodyReport(long now)
    {
        if (now < _nextBodyScanMs)
        {
            return;
        }
        _nextBodyScanMs = now + BodyScanMs;

        if (!RuntimeControl.AutoReportBodiesEnabled)
        {
            ClearTemporaryBodyAnchor();
            return;
        }

        var player = PlayerControl.LocalPlayer;
        var hud = HudManager.Instance;
        var reportButton = hud?.ReportButton;
        if (player?.Data == null || player.Data.IsDead)
        {
            ClearTemporaryBodyAnchor();
            return;
        }

        var buttonVisible = reportButton != null &&
            reportButton.gameObject.activeInHierarchy;
        var reportReady = buttonVisible && reportButton!.CanInteract();
        if (reportReady)
        {
            TriggerReport(now, player);
            return;
        }

        var body = FindBestBodyCandidate(
            player.GetTruePosition(),
            reportReady,
            out var bodyPosition,
            out var bodyDistance);
        if (body == null)
        {
            if (_bodyTarget != null &&
                now - _bodyTargetSeenMs >= BodyCandidateLostMs)
            {
                ClearTemporaryBodyAnchor();
                _bodyReportStatus.State = "idle";
            }
            return;
        }

        if (_bodyTarget == null ||
            _bodyTarget.Body.Pointer != body.Pointer)
        {
            StartTemporaryBodyAnchor(body, player.GetTruePosition());
        }

        _bodyTargetSeenMs = now;
        _bodyReportStatus.State = "approaching";
        _bodyReportStatus.TemporaryAnchorId = _bodyTarget?.AnchorId;
        _bodyReportStatus.TargetDistance = bodyDistance;
    }

    private void TryImmediateBodyReport(long now)
    {
        if (!RuntimeControl.AutoReportBodiesEnabled)
        {
            return;
        }

        var player = PlayerControl.LocalPlayer;
        var reportButton = HudManager.Instance?.ReportButton;
        if (player?.Data == null ||
            player.Data.IsDead ||
            reportButton == null ||
            !reportButton.gameObject.activeInHierarchy ||
            !reportButton.CanInteract())
        {
            return;
        }

        TriggerReport(now, player);
    }

    private DeadBody? FindBestBodyCandidate(
        Vector2 playerPosition,
        bool reportReady,
        out Vector2 bodyPosition,
        out float bodyDistance)
    {
        DeadBody? best = null;
        bodyPosition = Vector2.zero;
        bodyDistance = float.PositiveInfinity;
        var camera = Camera.main;
        var maxDistance = Math.Max(
            1.0f,
            Math.Min(
                BodyDetectionMaxDistance,
                TelemetryProbePlugin.BodyDetectionRadius?.Value ?? 4.5f));

        // Prioritize bodies within line-of-sight by applying a distance penalty
        // to those blocked by walls. This prevents wall-hugging behaviors when
        // a closer-but-blocked body exists alongside a slightly-farther unblocked one.
        const float WallPenaltyMultiplier = 2.5f;

        foreach (var candidate in UnityEngine.Object.FindObjectsOfType<DeadBody>())
        {
            if (candidate == null ||
                candidate.Reported ||
                candidate.gameObject == null ||
                !candidate.gameObject.activeInHierarchy)
            {
                continue;
            }

            var position = candidate.TruePosition;
            var distance = Vector2.Distance(playerPosition, position);
            if (distance > maxDistance)
            {
                continue;
            }

            var isVisible = IsWorldPositionInView(
                camera,
                candidate.transform.position);
            var hasClearPath = HasClearBodyLineOfSight(
                playerPosition,
                position);

            // When report button is not ready, require both visibility and clear path.
            if (!reportReady && (!isVisible || !hasClearPath))
            {
                continue;
            }

            // Apply wall penalty: bodies behind walls get artificially increased distance.
            var effectiveDistance = (!isVisible || !hasClearPath)
                ? distance * WallPenaltyMultiplier
                : distance;

            if (effectiveDistance >= bodyDistance)
            {
                continue;
            }

            best = candidate;
            bodyPosition = position;
            bodyDistance = distance; // Store real distance, not penalized
        }

        return best;
    }

    private void StartTemporaryBodyAnchor(DeadBody body, Vector2 playerPosition)
    {
        var position = body.TruePosition;
        var anchorId = $"temp.body.{body.ParentId}";
        _bodyTarget = new BodyTarget(body, anchorId, position);
        _bodyTargetSeenMs = _clock.ElapsedMilliseconds;
        _bodyPlan = null;
        _bodyWaypointIndex = 0;
        _bodyReportStatus = new BodyReportStatus
        {
            Enabled = true,
            State = "approaching",
            TemporaryAnchorId = anchorId,
            ParentId = body.ParentId,
            Position = new NavigationPoint(position.x, position.y),
            TargetDistance = Vector2.Distance(playerPosition, position)
        };

        if (_active == null)
        {
            return;
        }

        try
        {
            var graph = LoadGraph(
                _active.Command.RoutePath,
                _active.Command.ClusterTolerance);
            var approachPosition = graph.FindNearestRoutePoint(
                new NavigationPoint(position.x, position.y));
            _bodyPlan = graph.FindRouteToPosition(
                playerPosition,
                approachPosition,
                $"{anchorId}.approach",
                Math.Max(4.0, _active.Command.SnapRadius),
                IsRouteConnectionClear);
        }
        catch (Exception exception)
        {
            _bodyReportStatus.PlanningFallback = exception.Message;
        }

        TelemetryProbePlugin.LogSource?.LogInfo(
            $"Temporary body anchor {anchorId} created at " +
            $"({position.x:F3}, {position.y:F3})");
    }

    private void StepTemporaryBodyAnchor(long now)
    {
        var target = _bodyTarget;
        var player = PlayerControl.LocalPlayer;
        if (target == null)
        {
            return;
        }
        if (player?.MyPhysics == null ||
            target.Body == null ||
            target.Body.Reported ||
            target.Body.gameObject == null ||
            !target.Body.gameObject.activeInHierarchy)
        {
            ClearTemporaryBodyAnchor();
            return;
        }

        var pose = player.GetTruePosition();
        var targetPosition = target.Body.TruePosition;
        var distance = Vector2.Distance(pose, targetPosition);
        _bodyReportStatus.TargetDistance = distance;
        if (distance <= BodyAnchorFinalTolerance)
        {
            _desiredVelocity = Vector2.zero;
            player.MyPhysics.SetNormalizedVelocity(Vector2.zero);
            _bodyReportStatus.State = "at_temporary_anchor";
            SetState("report_approach", null);
            return;
        }

        if (_bodyPlan != null && _bodyPlan.Points.Count > 0)
        {
            var points = _bodyPlan.Points;
            while (_bodyWaypointIndex < points.Count - 1 &&
                   Vector2.Distance(
                       pose,
                       points[_bodyWaypointIndex]) <=
                   BodyWaypointTolerance)
            {
                _bodyWaypointIndex++;
            }

            var atRouteEnd =
                _bodyWaypointIndex >= points.Count - 1 &&
                Vector2.Distance(pose, points[^1]) <=
                    BodyWaypointTolerance;
            var bodyVisible =
                IsWorldPositionInView(
                    Camera.main,
                    target.Body.transform.position) &&
                HasClearBodyLineOfSight(pose, targetPosition);
            if (atRouteEnd &&
                (distance > BodyDirectAttachRadius || !bodyVisible))
            {
                _desiredVelocity = Vector2.zero;
                _bodyReportStatus.State = bodyVisible
                    ? "route_end_waiting_distance"
                    : "route_end_waiting_visibility";
                SetState("report_approach", null);
                return;
            }

            var waypoint = atRouteEnd ? targetPosition : points[_bodyWaypointIndex];
            var delta = waypoint - pose;
            _desiredVelocity = delta.sqrMagnitude < 0.0001f
                ? Vector2.zero
                : delta.normalized;
        }
        else
        {
            var delta = targetPosition - pose;
            _desiredVelocity = delta.sqrMagnitude < 0.0001f
                ? Vector2.zero
                : delta.normalized;
        }

        _bodyReportStatus.State = "approaching";
        _bodyReportStatus.TargetDistance = Vector2.Distance(
            pose,
            targetPosition);
        SetState("report_approach", null);
    }

    private void TriggerReport(long now, PlayerControl player)
    {
        var reportButton = HudManager.Instance?.ReportButton;
        if (reportButton == null ||
            player.Data == null ||
            player.Data.IsDead ||
            !reportButton.gameObject.activeInHierarchy ||
            !reportButton.CanInteract())
        {
            return;
        }

        var target = _bodyTarget;
        var parentId = target?.Body?.ParentId;
        var position = target?.Position;
        _desiredVelocity = Vector2.zero;
        player.MyPhysics?.SetNormalizedVelocity(Vector2.zero);
        reportButton.DoClick();
        _reportClickLockedUntilMs = now + ReportClickLockMs;
        ClearTemporaryBodyAnchor();
        _bodyReportStatus = new BodyReportStatus
        {
            Enabled = true,
            State = "reported",
            ParentId = parentId,
            Position = position,
            ReportClickDispatched = true,
            ReportClickedAtUtc = DateTimeOffset.UtcNow
        };
        _state = "reported";
        _lastError = null;
        WriteStatus();
        TelemetryProbePlugin.LogSource?.LogInfo(
            $"Report dispatched from temporary body anchor, parent={parentId?.ToString() ?? "unknown"}");
    }

    private void ClearTemporaryBodyAnchor()
    {
        if (_bodyTarget != null)
        {
            TelemetryProbePlugin.LogSource?.LogInfo(
                $"Temporary body anchor {_bodyTarget.AnchorId} removed");
        }
        _bodyTarget = null;
        _bodyPlan = null;
        _bodyWaypointIndex = 0;
        _bodyReportStatus.TemporaryAnchorId = null;
    }

    private static bool IsWorldPositionInView(Camera? camera, Vector3 position)
    {
        if (camera == null)
        {
            return false;
        }

        var viewport = camera.WorldToViewportPoint(position);
        return viewport.z > 0f &&
            viewport.x >= -0.05f &&
            viewport.x <= 1.05f &&
            viewport.y >= -0.05f &&
            viewport.y <= 1.05f;
    }

    private static bool HasClearBodyLineOfSight(
        Vector2 playerPosition,
        Vector2 bodyPosition)
    {
        try
        {
            return !PhysicsHelpers.AnythingBetween(
                playerPosition,
                bodyPosition,
                Constants.ShipOnlyMask,
                false);
        }
        catch
        {
            return false;
        }
    }

    private void StepNavigation(long now)
    {
        if (_active == null)
        {
            return;
        }

        if (now - _active.StartedAtMs > _active.Command.MaxDurationMs)
        {
            StopActive("failed");
            _lastError = "navigation_timeout";
            _state = "failed";
            WriteStatus();
            return;
        }

        var player = PlayerControl.LocalPlayer;
        if (player?.MyPhysics == null)
        {
            StopActive("failed");
            _lastError = "player_physics_unavailable";
            _state = "failed";
            WriteStatus();
            return;
        }

        var pose = player.GetTruePosition();
        if (_active.Recovery != null)
        {
            StepRouteRecovery(now, player.MyPhysics, pose);
            return;
        }

        var points = _active.Plan.Points;
        var finalPoint = points[^1];
        var finalDistance = Vector2.Distance(pose, finalPoint);

        while (_active.WaypointIndex < points.Count - 1 &&
               Vector2.Distance(pose, points[_active.WaypointIndex]) <=
               _active.Command.WaypointTolerance &&
               IsRouteConnectionClear(pose, points[_active.WaypointIndex + 1]))
        {
            _active.WaypointIndex++;
            _active.Progress.Reset();
            _active.StuckSinceMs = 0;
            _active.RecoveryDirection = Vector2.zero;
            _active.RecoveryRepeats = 0;
        }

        var reachedFinalPoint =
            _active.WaypointIndex == points.Count - 1 &&
            finalDistance <= _active.Command.FinalTolerance;
        var reachedTaskButton =
            !reachedFinalPoint &&
            _active.Command.StopOnTaskButton &&
            finalDistance <= _active.Command.TaskStopRadius &&
            IsTaskButtonReady();
        if (reachedFinalPoint && _pursuit != null)
        {
            _desiredVelocity = Vector2.zero;
            player.MyPhysics.SetNormalizedVelocity(Vector2.zero);
            _active.LastProgressAt = now;
            _active.LastTrackedPosition = pose;
            _state = "running";
            _pursuitInfo.State = "awaiting_native_range";
            return;
        }
        if (reachedFinalPoint || reachedTaskButton)
        {
            if (_active.Command.InteractAfterArrival &&
                !_active.InteractionDispatched)
            {
                _active.InteractionDispatched = true;
                var interactionCommand = _active.Command;
                _active.ArrivalReason = "interaction_pending";
                _lastCompletionMode = _active.ArrivalReason;
                StopActive("stopped");
                BeginTaskOpening(interactionCommand, now);
                _lastError = null;
                WriteStatus();
                return;
            }

            if (!_active.ArrivalHoldStarted)
            {
                _active.ArrivalHoldStarted = true;
                _active.ArrivalReason = reachedTaskButton
                    ? "task_button_ready"
                    : "hold";
                _lastCompletionMode = _active.ArrivalReason;
                _active.ArrivalHoldUntilMs =
                    now + Math.Max(0, _active.Command.HoldAfterArrivalMs);
                _desiredVelocity = Vector2.zero;
                player.MyPhysics.SetNormalizedVelocity(Vector2.zero);
                _state = _active.Command.HoldAfterArrivalMs > 0
                    ? "holding"
                    : "completed";
                WriteStatus();
            }

            if (_active.Command.HoldAfterArrivalMs <= 0 ||
                now >= _active.ArrivalHoldUntilMs)
            {
                StopActive("completed");
                _state = "completed";
                WriteStatus();
            }
            else
            {
                _desiredVelocity = Vector2.zero;
                player.MyPhysics.SetNormalizedVelocity(Vector2.zero);
                WriteStatusIfDue(now);
            }
            return;
        }

        if (now < _active.RecoveryUntilMs)
        {
            return;
        }
        if (_active.Progress.IsStalled(now, pose, _active.WaypointIndex, points[_active.WaypointIndex]))
        {
            if (_active.Recoveries >= _active.Command.MaxRecoveries)
            {
                StopActive("failed");
                _lastError = "route_stalled_no_progress";
                _state = "failed";
                WriteStatus();
                return;
            }
            else if (_active.Command.InteractAfterArrival &&
                     _active.Command.InteractTargetX.HasValue &&
                     _active.Command.InteractTargetY.HasValue &&
                     finalDistance <= _active.Command.TaskStopRadius &&
                     !IsTaskButtonReady())
            {
                var interactionTarget = new Vector2(
                    (float)_active.Command.InteractTargetX.Value,
                    (float)_active.Command.InteractTargetY.Value);
                var interactionDelta = interactionTarget - pose;
                _desiredVelocity = interactionDelta.sqrMagnitude < 0.0025f
                    ? Vector2.zero
                    : ComputeObstacleAwareDirection(
                        player.PlayerId,
                        pose,
                        interactionDelta.normalized,
                        now,
                        0.40f);
                _active.Recoveries++;
                _active.RecoveryUntilMs = now + 550;
                _active.StuckSinceMs = 0;
                _active.Progress.Reset();
                _state = "seeking_task_button";
                WriteStatus();
                return;
            }
            else
            {
                StartRouteRecovery(now, pose);
                return;
            }
        }

        if (now < _active.RecoveryUntilMs)
        {
            return;
        }

        if (_active.Command.InteractAfterArrival &&
            _active.Command.InteractTargetX.HasValue &&
            _active.Command.InteractTargetY.HasValue &&
            _active.WaypointIndex >= points.Count - 1 &&
            finalDistance <= _active.Command.TaskStopRadius &&
            !IsTaskButtonReady())
        {
            if (_active.Recoveries >= _active.Command.MaxRecoveries)
            {
                StopActive("failed");
                _lastError = "task_button_unreachable";
                _state = "failed";
                WriteStatus();
                return;
            }
            var interactionTarget = new Vector2(
                (float)_active.Command.InteractTargetX.Value,
                (float)_active.Command.InteractTargetY.Value);
            var interactionDelta = interactionTarget - pose;
            _desiredVelocity = interactionDelta.sqrMagnitude < 0.0025f
                ? Vector2.zero
                : ComputeObstacleAwareDirection(
                    player.PlayerId,
                    pose,
                    interactionDelta.normalized,
                    now,
                    0.45f);
            _state = "seeking_task_button";
            return;
        }

        var waypoint = points[_active.WaypointIndex];
        if (!CrimeSceneMemory.Allows(pose, waypoint))
        { Fail("crime_scene_avoidance", "路线进入本局击杀现场避让区，停止移动"); return; }
        var delta = waypoint - pose;
        _desiredVelocity = delta.sqrMagnitude < 0.0001f
            ? Vector2.zero
            : ComputeObstacleAwareDirection(
                player.PlayerId,
                pose,
                delta.normalized,
                now,
                Math.Min(0.55f, delta.magnitude));
    }

    private Vector2 ComputeObstacleAwareDirection(
        byte playerId,
        Vector2 pose,
        Vector2 desired,
        long now,
        float lookAhead)
    {
        if (desired.sqrMagnitude < 0.0001f)
        {
            return Vector2.zero;
        }
        if (_active != null &&
            now < _active.AvoidanceUntilMs &&
            _active.AvoidanceDirection.sqrMagnitude > 0.001f &&
            Vector2.Dot(_active.AvoidanceDirection, desired) >= -0.05f &&
            IsNavigationSegmentClear(
                pose,
                _active.AvoidanceDirection,
                lookAhead))
        {
            return _active.AvoidanceDirection;
        }
        if (IsNavigationSegmentClear(pose, desired, lookAhead))
        {
            if (_active != null)
            {
                _active.AvoidanceDirection = Vector2.zero;
                _active.AvoidanceUntilMs = 0;
            }
            return desired;
        }

        var side = playerId % 2 == 0 ? 1f : -1f;
        var angles = new[]
        {
            35f * side, -35f * side,
            65f * side, -65f * side,
            90f * side, -90f * side,
            135f * side, -135f * side,
            180f
        };
        foreach (var angle in angles)
        {
            var candidate = Rotate(desired, angle);
            if (!IsNavigationSegmentClear(pose, candidate, lookAhead))
            {
                continue;
            }
            if (_active != null)
            {
                _active.AvoidanceDirection = candidate;
                _active.AvoidanceUntilMs = now + 360;
            }
            return candidate;
        }
        return Vector2.zero;
    }

    private static bool IsNavigationSegmentClear(
        Vector2 from,
        Vector2 direction,
        float distance)
    {
        return IsRouteConnectionClear(from, from + direction.normalized * distance);
    }

    private static bool IsRouteConnectionClear(Vector2 from, Vector2 to)
    {
        if (!CrimeSceneMemory.Allows(from, to)) return false;
        var delta = to - from;
        if (delta.sqrMagnitude < 0.0001f) return true;
        // A point ray can pass a corner that the player's body cannot pass.
        var hits = Physics2D.CircleCastAll(from, 0.16f, delta.normalized,
            delta.magnitude, Constants.ShipOnlyMask);
        foreach (var hit in hits)
        {
            if (hit.collider != null && !hit.collider.isTrigger)
            {
                return false;
            }
        }
        return true;
    }

    private static Vector2 Rotate(Vector2 value, float degrees)
    {
        var radians = degrees * Mathf.Deg2Rad;
        var cosine = Mathf.Cos(radians);
        var sine = Mathf.Sin(radians);
        return new Vector2(
            value.x * cosine - value.y * sine,
            value.x * sine + value.y * cosine).normalized;
    }

    private static double LocalPathLength(IReadOnlyList<Vector2> points)
    {
        double length = 0;
        for (var index = 1; index < points.Count; index++)
        {
            length += Vector2.Distance(points[index - 1], points[index]);
        }
        return length;
    }

    private static bool IsTaskButtonReady()
    {
        var button = HudManager.Instance?.UseButton;
        return button != null &&
            button.gameObject.activeInHierarchy &&
            button.CanInteract();
    }

    private void BeginTaskOpening(InternalNavigationCommand command, long now)
    {
        _openingTask = command;
        _taskOpenStartedMs = now;
        _nextTaskClickMs = now;
        _taskClickAttempts = 0;
        _desiredVelocity = Vector2.zero;
        PlayerControl.LocalPlayer?.MyPhysics?.SetNormalizedVelocity(Vector2.zero);
        _state = "opening_task";
        _lastCompletionMode = "interaction_pending";
    }

    private void StepTaskOpening(long now)
    {
        var command = _openingTask!;
        if (MeetingHud.Instance != null)
        {
            _openingTask = null;
            Fail("failed", "meeting_interrupted_task_open");
            return;
        }
        var minigame = Minigame.Instance;
        if (minigame != null && !minigame.amOpening && minigame.MyTask != null)
        {
            if (command.InteractTaskId.HasValue && minigame.MyTask?.Id != command.InteractTaskId.Value)
            {
                minigame.Close();
                _openingTask = null;
                Fail("failed", "unexpected_task_panel");
                return;
            }
            _openingTask = null;
            _state = "completed";
            _lastCompletionMode = "interaction_opened";
            _lastError = null;
            return;
        }
        if (now - _taskOpenStartedMs > 3500)
        {
            _openingTask = null;
            Fail("failed", "task_panel_not_opened_after_use");
            return;
        }
        if (now >= _nextTaskClickMs && _taskClickAttempts < 3 && IsTaskButtonReady())
        {
            _taskClickAttempts++;
            _nextTaskClickMs = now + 800;
            HudManager.Instance!.UseButton.DoClick();
        }
    }

    private static string? TryCloseActiveMinigame()
    {
        try
        {
            var minigame = Minigame.Instance;
            if (minigame == null)
            {
                return null;
            }

            string? lastError = null;
            try
            {
                minigame.Close();
            }
            catch (Exception exception)
            {
                lastError = exception.Message;
                TelemetryProbePlugin.LogSource?.LogWarning(
                    $"Minigame.Close failed; trying close controls: " +
                    exception.Message);
            }
            if (Minigame.Instance == null)
            {
                return null;
            }

            foreach (var name in new[]
                     {
                         "CloseButton",
                         "OutsideCloseButton",
                         "BackgroundCloseButton"
                     })
            {
                if (TryClickMinigameObject(name))
                {
                    return null;
                }
            }

            try
            {
                UnityEngine.Object.Destroy(minigame.gameObject);
                return null;
            }
            catch (Exception exception)
            {
                lastError = exception.Message;
            }
            return string.IsNullOrWhiteSpace(lastError)
                ? "minigame_close_unavailable"
                : lastError;
        }
        catch (Exception exception)
        {
            return exception.Message;
        }
    }

    private static bool TryClickMinigameObject(string objectName)
    {
        try
        {
            var minigame = Minigame.Instance;
            if (minigame == null || string.IsNullOrWhiteSpace(objectName))
            {
                return false;
            }

            var transforms = minigame.gameObject
                .GetComponentsInChildren<Transform>(true);
            var target = transforms.FirstOrDefault(
                child => string.Equals(
                    child.name,
                    objectName,
                    StringComparison.Ordinal));
            return target != null && TryInvokeButton(target.gameObject);
        }
        catch
        {
            return false;
        }
    }

    private static bool TryClickUnlockNumber(int number)
    {
        try
        {
            var unlock = Minigame.Instance?
                .TryCast<UnlockManifoldsMinigame>();
            var buttons = unlock?.Buttons;
            if (buttons == null || number < 1 || number > buttons.Length)
            {
                return false;
            }
            unlock!.HitButton(number - 1);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static bool TryClickReactorButton(int index)
    {
        try
        {
            var reactor = Minigame.Instance?.TryCast<SimonSaysGame>();
            var buttons = reactor?.Buttons;
            if (reactor == null ||
                buttons == null ||
                buttons.Length == 0 ||
                index < 0)
            {
                return false;
            }

            if (index >= buttons.Length)
            {
                return false;
            }

            reactor.HitButton(index);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static bool TryCleanVentDirt(int index)
    {
        try
        {
            var vent = Minigame.Instance?
                .TryCast<VentCleaningMinigame>();
            var active = vent?.dirtPool?.activeChildren;
            if (vent == null ||
                active == null ||
                index < 0 ||
                index >= active.Count)
            {
                return false;
            }
            var dirt = active[index]?.TryCast<VentDirt>();
            if (dirt == null)
            {
                return false;
            }
            vent.CleanUp(dirt);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static bool TryInvokeButton(GameObject gameObject)
    {
        var passive = gameObject.GetComponent<PassiveButton>();
        if (passive != null)
        {
            passive.OnClick.Invoke();
            return true;
        }

        var behavior = gameObject.GetComponent<ButtonBehavior>();
        if (behavior != null)
        {
            behavior.OnClick.Invoke();
            return true;
        }

        var action = gameObject.GetComponent<ActionButton>();
        if (action != null)
        {
            action.DoClick();
            return true;
        }
        return false;
    }

    private void StartRouteRecovery(long now, Vector2 pose)
    {
        if (_active == null)
        {
            return;
        }
        if (_active.Recoveries >= _active.Command.MaxRecoveries)
        {
            StopActive("failed");
            _lastError = "local_recovery_exhausted";
            _state = "failed";
            WriteStatus();
            return;
        }

        if (_active.Command.AllowGridRecovery &&
            TryStartLocalGridRecovery(now, pose))
        {
            return;
        }

        if (!_active.Command.AllowGridRecovery)
        {
            // Recorded-route mode replans on the known graph from the actual
            // pose. It must never wander along axes toward an anchor across a wall.
            _active.Recoveries++;
            if (!TryResumeAfterRecovery(now, pose))
            {
                StopActive("failed");
                _lastError = "no_collision_free_recorded_route";
                _state = "failed";
                WriteStatus();
            }
            return;
        }

        var graph = LoadGraph(
            _active.Command.RoutePath,
            _active.Command.ClusterTolerance);
        var expandedRadius = Math.Clamp(
            _active.Command.SnapRadius * 1.75,
            4.5,
            7.0);
        var candidates = graph.FindRecoveryAnchors(
            pose,
            expandedRadius,
            3);
        if (candidates.Count == 0)
        {
            StopActive("failed");
            _lastError = "no_recovery_anchor";
            _state = "failed";
            WriteStatus();
            return;
        }

        _active.Recoveries++;
        _active.StuckSinceMs = 0;
        _active.RecoveryUntilMs = 0;
        _active.Recovery = new ActiveRecovery(candidates, expandedRadius, now, pose);
        _desiredVelocity = Vector2.zero;
        _state = "recovering";
        _lastError = null;
        _lastRecoveryAnchorId = candidates[0].AnchorId;
        _lastRecoveryMode = _active.Recovery.Mode;
        TelemetryProbePlugin.LogSource?.LogWarning(
            $"Navigation stuck; recovery candidates=" +
            string.Join(
                ", ",
                candidates.Select(candidate =>
                    $"{candidate.AnchorId}@{candidate.Distance:F2}")) +
            $", expanded snap={expandedRadius:F2}");
        WriteStatus();
    }

    private void StepRouteRecovery(
        long now,
        PlayerPhysics physics,
        Vector2 pose)
    {
        if (_active?.Recovery == null)
        {
            return;
        }

        var recovery = _active.Recovery;
        var candidate = recovery.CurrentCandidate;
        var candidatePosition = new Vector2(
            candidate.Position.X,
            candidate.Position.Y);
        var delta = candidatePosition - pose;
        var distance = delta.magnitude;
        var moved = Vector2.Distance(
            pose,
            recovery.PhaseLastPosition);
        if (moved >= RecoveryProgressDistance)
        {
            recovery.PhaseLastPosition = pose;
            recovery.PhaseLastProgressAtMs = now;
        }

        _lastRecoveryAnchorId = candidate.AnchorId;
        _lastRecoveryMode = recovery.Mode;
        if (distance <= RecoveryReachTolerance)
        {
            if (TryResumeAfterRecovery(now, pose))
            {
                return;
            }

            AdvanceRecovery(now, pose, "anchor_replan_failed");
            return;
        }

        if (recovery.StrategyIndex == 2)
        {
            if (now - recovery.PhaseStartedAtMs >= RecoveryBackoffMs ||
                Vector2.Distance(pose, recovery.BackoffStartPose) >= 0.30f)
            {
                AdvanceRecovery(now, pose, "backoff_complete");
                return;
            }

            _desiredVelocity = ComputeObstacleAwareDirection(
                PlayerControl.LocalPlayer?.PlayerId ?? 0,
                pose,
                recovery.BackoffDirection,
                now,
                0.38f);
            _state = "recovering";
            physics.SetNormalizedVelocity(_desiredVelocity);
            return;
        }

        if (now - recovery.PhaseLastProgressAtMs >= RecoveryStallMs ||
            now - recovery.PhaseStartedAtMs >= RecoveryAxisTimeoutMs)
        {
            AdvanceRecovery(now, pose, "axis_blocked");
            return;
        }

        var absX = Math.Abs(delta.x);
        var absY = Math.Abs(delta.y);
        var horizontalFirst = recovery.StrategyIndex == 0;
        if (recovery.PhaseIndex == 0)
        {
            var primary = horizontalFirst ? absX : absY;
            if (primary > 0.12f)
            {
                _desiredVelocity = horizontalFirst
                    ? new Vector2(Math.Sign(delta.x), 0f)
                    : new Vector2(0f, Math.Sign(delta.y));
                _desiredVelocity = ComputeObstacleAwareDirection(
                    PlayerControl.LocalPlayer?.PlayerId ?? 0,
                    pose,
                    _desiredVelocity,
                    now,
                    0.38f);
            }
            else
            {
                recovery.PhaseIndex = 1;
                recovery.PhaseStartedAtMs = now;
                recovery.PhaseLastProgressAtMs = now;
                recovery.PhaseLastPosition = pose;
            }
        }

        if (recovery.PhaseIndex == 1)
        {
            var secondary = horizontalFirst ? absY : absX;
            if (secondary > 0.12f)
            {
                _desiredVelocity = horizontalFirst
                    ? new Vector2(0f, Math.Sign(delta.y))
                    : new Vector2(Math.Sign(delta.x), 0f);
                _desiredVelocity = ComputeObstacleAwareDirection(
                    PlayerControl.LocalPlayer?.PlayerId ?? 0,
                    pose,
                    _desiredVelocity,
                    now,
                    0.38f);
            }
            else
            {
                _desiredVelocity = delta.sqrMagnitude < 0.0001f
                    ? Vector2.zero
                    : ComputeObstacleAwareDirection(
                        PlayerControl.LocalPlayer?.PlayerId ?? 0,
                        pose,
                        delta.normalized,
                        now,
                        0.38f);
            }
        }

        _state = "recovering";
        physics.SetNormalizedVelocity(_desiredVelocity);
    }

    private bool TryStartLocalGridRecovery(long now, Vector2 pose)
    {
        if (_active == null || _active.GridRecoveryAttempts >= 2)
        {
            return false;
        }

        try
        {
            var graph = LoadGraph(
                _active.Command.RoutePath,
                _active.Command.ClusterTolerance);
            var target = graph.ResolveTargetPosition(
                _active.Command.TargetAnchorId);
            if (!AubotGridPathfinder.TryFindPath(
                    pose,
                    new Vector2(target.X, target.Y),
                    out var points))
            {
                return false;
            }

            var previous = _active;
            var plan = new NavigationPlan(
                points,
                -1,
                -1,
                0,
                Vector2.Distance(
                    points[^1],
                    new Vector2(target.X, target.Y)),
                LocalPathLength(points),
                "local_grid",
                _active.Command.TargetAnchorId);
            _active = new ActiveNavigation(
                previous.Command,
                plan,
                previous.StartedAtMs)
            {
                Recoveries = previous.Recoveries + 1,
                GridRecoveryAttempts = previous.GridRecoveryAttempts + 1
            };
            _lastRecoveryAnchorId = "aubot_grid";
            _lastRecoveryMode = "local_astar";
            _state = "running";
            _lastError = null;
            _desiredVelocity = Vector2.zero;
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Local collision-grid recovery: {points.Length} points " +
                $"to {_active.Command.TargetAnchorId}");
            WriteStatus();
            return true;
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Local collision-grid recovery failed: {exception.Message}");
            return false;
        }
    }

    private bool TryResumeAfterRecovery(long now, Vector2 pose)
    {
        if (_active == null)
        {
            return false;
        }

        var previous = _active;
        var anchorId = previous.Recovery?.CurrentCandidate.AnchorId ?? "unknown";
        var mode = previous.Recovery?.Mode ?? "unknown";
        try
        {
            var graph = LoadGraph(
                previous.Command.RoutePath,
                previous.Command.ClusterTolerance);
            var snapRadius = Math.Max(
                previous.Command.SnapRadius,
                previous.Recovery?.ExpandedSnapRadius ?? 4.5);
            var plan = graph.FindRoute(
                pose,
                previous.Command.TargetAnchorId,
                snapRadius,
                IsRouteConnectionClear);
            _active = new ActiveNavigation(
                previous.Command,
                plan,
                previous.StartedAtMs)
            {
                Recoveries = previous.Recoveries,
                GridRecoveryAttempts = previous.GridRecoveryAttempts
            };
            _lastPlannedRoute = plan.Points.Select(p => new NavigationPoint(p.x, p.y)).ToArray();
            _desiredVelocity = Vector2.zero;
            _lastRecoveryAnchorId = anchorId;
            _lastRecoveryMode = mode;
            _state = "running";
            _lastError = null;
            TelemetryProbePlugin.LogSource?.LogInfo(
                $"Recovery succeeded via {anchorId} ({mode}); route replanned");
            WriteStatus();
            return true;
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Recovery reached {anchorId}, but replanning failed: {exception.Message}");
            return false;
        }
    }

    private void AdvanceRecovery(long now, Vector2 pose, string reason)
    {
        if (_active?.Recovery == null)
        {
            return;
        }

        var recovery = _active.Recovery;
        if (recovery.StrategyIndex == 0)
        {
            SetRecoveryStrategy(recovery, 1, now, pose, reason);
            return;
        }
        if (recovery.StrategyIndex == 1)
        {
            SetRecoveryStrategy(recovery, 2, now, pose, reason);
            return;
        }

        recovery.CandidateIndex++;
        if (recovery.CandidateIndex < recovery.Candidates.Count)
        {
            SetRecoveryStrategy(recovery, 0, now, pose, "next_anchor");
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Recovery trying next anchor {recovery.CurrentCandidate.AnchorId}");
            return;
        }

        if (!recovery.CandidatesRefreshed)
        {
            var graph = LoadGraph(
                _active.Command.RoutePath,
                _active.Command.ClusterTolerance);
            var refreshed = graph.FindRecoveryAnchors(
                pose,
                recovery.ExpandedSnapRadius,
                3);
            if (refreshed.Count > 0)
            {
                recovery.Candidates = refreshed;
                recovery.CandidateIndex = 0;
                recovery.CandidatesRefreshed = true;
                SetRecoveryStrategy(
                    recovery,
                    0,
                    now,
                    pose,
                    "anchors_refreshed");
                TelemetryProbePlugin.LogSource?.LogWarning(
                    $"Recovery refreshed anchors: " +
                    string.Join(
                        ", ",
                        refreshed.Select(candidate => candidate.AnchorId)));
                return;
            }
        }

        StopActive("failed");
        _lastError = "local_recovery_exhausted";
        _state = "failed";
        WriteStatus();
    }

    private static void SetRecoveryStrategy(
        ActiveRecovery recovery,
        int strategy,
        long now,
        Vector2 pose,
        string reason)
    {
        recovery.StrategyIndex = strategy;
        recovery.PhaseIndex = 0;
        recovery.PhaseStartedAtMs = now;
        recovery.PhaseLastProgressAtMs = now;
        recovery.PhaseLastPosition = pose;
        if (strategy == 2)
        {
            var away = pose - new Vector2(
                recovery.CurrentCandidate.Position.X,
                recovery.CurrentCandidate.Position.Y);
            recovery.BackoffDirection = away.sqrMagnitude < 0.0001f
                ? Vector2.down
                : away.normalized;
            recovery.BackoffStartPose = pose;
        }

        TelemetryProbePlugin.LogSource?.LogWarning(
            $"Recovery strategy -> {recovery.Mode} ({reason})");
    }

    private bool CanNavigate(out string reason)
    {
        var player = PlayerControl.LocalPlayer;
        var gameManager = GameManager.Instance;
        var hud = HudManager.Instance;
        if (player == null || player.MyPhysics == null || gameManager == null)
        {
            reason = "game_not_ready";
            return false;
        }
        if (!gameManager.GameHasStarted)
        {
            reason = "game_not_started";
            return false;
        }
        if (MeetingHud.Instance != null)
        {
            reason = "meeting_open";
            return false;
        }
        if (Minigame.Instance != null)
        {
            reason = "minigame_open";
            return false;
        }
        if (!player.CanMove)
        {
            reason = "player_cannot_move";
            return false;
        }
        if (hud?.FullScreen != null && hud.FullScreen.gameObject.activeInHierarchy)
        {
            reason = "fullscreen_overlay";
            return false;
        }

        reason = string.Empty;
        return true;
    }

    private NavigationGraph LoadGraph(string routePath, double clusterTolerance)
    {
        var fullPath = Path.GetFullPath(routePath);
        var lastWriteUtc = File.GetLastWriteTimeUtc(fullPath);
        var key = $"{fullPath}|{clusterTolerance:F3}";
        if (_graphCache.TryGetValue(key, out var cached) &&
            cached.LastWriteUtc == lastWriteUtc)
        {
            return cached.Graph;
        }

        var graph = NavigationGraph.Load(fullPath, clusterTolerance);
        _graphCache[key] = new CachedNavigationGraph(graph, lastWriteUtc);
        foreach (var stale in _graphCache.Keys.Where(candidate =>
                     !string.Equals(candidate, key, StringComparison.OrdinalIgnoreCase)).ToArray())
        {
            _graphCache.Remove(stale);
        }
        return graph;
    }

    private sealed class CachedNavigationGraph
    {
        public CachedNavigationGraph(
            NavigationGraph graph,
            DateTime lastWriteUtc)
        {
            Graph = graph;
            LastWriteUtc = lastWriteUtc;
        }

        public NavigationGraph Graph { get; }
        public DateTime LastWriteUtc { get; }
    }

    private void StopActive(string state)
    {
        if (_pursuit != null)
        {
            _pursuitInfo.Active = false;
            _pursuitInfo.State = state;
            _pursuit = null;
        }
        _openingTask = null;
        _desiredVelocity = Vector2.zero;
        if (PlayerControl.LocalPlayer?.MyPhysics != null)
        {
            PlayerControl.LocalPlayer.MyPhysics.SetNormalizedVelocity(Vector2.zero);
        }
        _state = state;
        if (state is "completed" or "failed" or "stopped")
        {
            if (_active != null)
            {
                var player = PlayerControl.LocalPlayer;
                if (player != null)
                {
                    _lastFinalError = Math.Round(
                        Vector2.Distance(
                            player.GetTruePosition(),
                            _active.Plan.Points[^1]),
                        5);
                }
                _lastTargetAnchorId = _active.Command.TargetAnchorId;
                _lastWaypointIndex = _active.WaypointIndex;
                _lastRoutePointCount = _active.Plan.Points.Count;
                _lastRecoveries = _active.Recoveries;
                _lastCompletionMode =
                    _active.ArrivalReason ??
                    (_active.Command.StopOnTaskButton
                        ? "task_button_ready"
                        : "hold");
                _lastRecoveryAnchorId =
                    _active.Recovery?.CurrentCandidate.AnchorId ??
                    _lastRecoveryAnchorId;
                _lastRecoveryMode =
                    _active.Recovery?.Mode ??
                    _lastRecoveryMode;
            }
            _active = null;
        }
    }

    private void Fail(string state, string error)
    {
        StopActive("failed");
        _state = "failed";
        _lastError = state == "failed" ? error : $"{state}: {error}";
        TelemetryProbePlugin.LogSource?.LogWarning(
            $"Internal navigation failed [{state}]: {error}");
        WriteStatus();
    }

    private void SetState(string state, string? reason)
    {
        _state = state;
        _lastError = reason;
    }

    private void WriteStatusIfDue(long now)
    {
        if (now < _nextStatusMs)
        {
            return;
        }
        _nextStatusMs = now + StatusWriteMs;
        WriteStatus();
    }

    private void WriteStatus()
    {
        if (_statusPath == null)
        {
            return;
        }

        try
        {
            var player = PlayerControl.LocalPlayer;
            var pose = player?.GetTruePosition() ?? Vector2.zero;
            var status = new InternalNavigationStatus
            {
                State = _state,
                CommandId = _lastCommandId,
                UpdatedAtUtc = DateTimeOffset.UtcNow,
                Error = _lastError,
                Position = new NavigationPoint(pose.x, pose.y),
                TargetAnchorId = _active?.Command.TargetAnchorId ?? _lastTargetAnchorId,
                WaypointIndex = _active?.WaypointIndex ?? _lastWaypointIndex,
                RoutePointCount = _active?.Plan.Points.Count ?? _lastRoutePointCount,
                PlannedRoute = _lastPlannedRoute,
                Recoveries = _active?.Recoveries ?? _lastRecoveries,
                HoldRemainingMs = _active?.ArrivalHoldStarted == true
                    ? Math.Max(
                        0,
                        _active.ArrivalHoldUntilMs - _clock.ElapsedMilliseconds)
                    : 0,
                CompletionMode = _lastCompletionMode,
                FinalError = _active == null
                    ? _lastFinalError
                    : Math.Round(
                        Vector2.Distance(pose, _active.Plan.Points[^1]),
                        5),
                RecoveryAnchorId = _active?.Recovery?.CurrentCandidate.AnchorId ??
                    _lastRecoveryAnchorId,
                RecoveryAnchorPosition =
                    _active?.Recovery?.CurrentCandidate.Position,
                RecoveryMode = _active?.Recovery?.Mode ?? _lastRecoveryMode,
                RecoveryExpandedSnapRadius =
                    _active?.Recovery?.ExpandedSnapRadius,
                Pursuit = _pursuitInfo,
                BodyReport = _bodyReportStatus
            };
            var json = JsonSerializer.Serialize(status, JsonOptions);
            WriteStatusFileWithRetry(json);
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Could not write internal navigation status: {exception.Message}");
        }
    }

    private void WriteStatusFileWithRetry(string json)
    {
        AtomicFile.WriteAllText(_statusPath!, json);
    }

    private sealed class ActiveNavigation
    {
        public ActiveNavigation(
            InternalNavigationCommand command,
            NavigationPlan plan,
            long startedAtMs)
        {
            Command = command;
            Plan = plan;
            StartedAtMs = startedAtMs;
            LastProgressAt = startedAtMs;
            LastTrackedPosition = plan.Points[0];
            WaypointIndex = Vector2.Distance(
                plan.Points[0],
                plan.Points[0]) <= command.WaypointTolerance
                ? Math.Min(1, plan.Points.Count - 1)
                : 0;
        }

        public InternalNavigationCommand Command { get; }
        public NavigationPlan Plan { get; }
        public long StartedAtMs { get; }
        public NavigationProgress Progress { get; } = new(ProgressTimeoutMs);
        public int WaypointIndex { get; set; }
        public int Recoveries { get; set; }
        public int GridRecoveryAttempts { get; set; }
        public long LastProgressAt { get; set; }
        public long RecoveryUntilMs { get; set; }
        public Vector2 LastTrackedPosition { get; set; }
        public Vector2 RecoveryDirection { get; set; }
        public long StuckSinceMs { get; set; }
        public int RecoveryRepeats { get; set; }
        public bool ArrivalHoldStarted { get; set; }
        public long ArrivalHoldUntilMs { get; set; }
        public bool HasInterrupted { get; set; }
        public ActiveRecovery? Recovery { get; set; }
        public string? ArrivalReason { get; set; }
        public bool InteractionDispatched { get; set; }
        public Vector2 AvoidanceDirection { get; set; }
        public long AvoidanceUntilMs { get; set; }
    }

    private sealed class ActiveRecovery
    {
        public ActiveRecovery(
            IReadOnlyList<RecoveryAnchorCandidate> candidates,
            double expandedSnapRadius,
            long now,
            Vector2 pose)
        {
            Candidates = candidates;
            ExpandedSnapRadius = expandedSnapRadius;
            PhaseStartedAtMs = now;
            PhaseLastProgressAtMs = now;
            PhaseLastPosition = pose;
        }

        public IReadOnlyList<RecoveryAnchorCandidate> Candidates { get; set; }
        public int CandidateIndex { get; set; }
        public int StrategyIndex { get; set; }
        public int PhaseIndex { get; set; }
        public long PhaseStartedAtMs { get; set; }
        public long PhaseLastProgressAtMs { get; set; }
        public Vector2 PhaseLastPosition { get; set; }
        public Vector2 BackoffDirection { get; set; }
        public Vector2 BackoffStartPose { get; set; }
        public double ExpandedSnapRadius { get; }
        public bool CandidatesRefreshed { get; set; }
        public RecoveryAnchorCandidate CurrentCandidate =>
            Candidates[Math.Clamp(CandidateIndex, 0, Candidates.Count - 1)];
        public string Mode => StrategyIndex switch
        {
            0 => "axis_x_then_y",
            1 => "axis_y_then_x",
            _ => "backoff"
        };
    }

    private sealed class BodyTarget
    {
        public BodyTarget(DeadBody body, string anchorId, Vector2 position)
        {
            Body = body;
            AnchorId = anchorId;
            Position = new NavigationPoint(position.x, position.y);
        }

        public DeadBody Body { get; }
        public string AnchorId { get; }
        public NavigationPoint Position { get; }
    }
}

[HarmonyPatch(typeof(PlayerPhysics), "FixedUpdate")]
internal static class InternalMovementPatch
{
    private static void Postfix(PlayerPhysics __instance)
    {
        InternalNavigationBehaviour.ActiveInstance?.ApplyVelocityIfActive(__instance);
    }
}

internal sealed class InternalNavigationCommand
{
    public int ExpectedMeetingId { get; init; } = -1;
    public string ExpectedSessionId { get; init; } = string.Empty;
    public long ExpectedMatchId { get; init; } = -1;
    public DateTimeOffset? IssuedAtUtc { get; init; }
    public string CommandId { get; init; } = string.Empty;
    public int? ExpectedMinigameInstanceId { get; init; }
    public string Action { get; init; } = "navigate";
    public string ChatText { get; init; } = string.Empty;
    public string TargetObjectName { get; init; } = string.Empty;
    public int TargetNumber { get; init; } = -1;
    public string TargetAnchorId { get; init; } = string.Empty;
    public string RoutePath { get; init; } = string.Empty;
    public double WaypointTolerance { get; init; } = 0.38;
    public double FinalTolerance { get; init; } = 0.20;
    public double SnapRadius { get; init; } = 3.0;
    public double ClusterTolerance { get; init; } = 0.35;
    public int MaxDurationMs { get; init; } = 60_000;
    public bool EscapeRoute { get; init; }
    public int MaxRecoveries { get; init; } = 12;
    public int HoldAfterArrivalMs { get; init; }
    public bool StopOnTaskButton { get; init; }
    public double TaskStopRadius { get; init; } = 1.6;
    public bool InteractAfterArrival { get; init; }
    public double? InteractTargetX { get; init; }
    public double? InteractTargetY { get; init; }
    public uint? InteractTaskId { get; init; }
    public bool AllowGridRecovery { get; init; }
}

internal sealed class InternalNavigationStatus
{
    public PursuitInfo Pursuit { get; init; } = new();
    public string State { get; init; } = string.Empty;
    public string? CommandId { get; init; }
    public DateTimeOffset UpdatedAtUtc { get; init; }
    public string? Error { get; init; }
    public NavigationPoint Position { get; init; }
    public string? TargetAnchorId { get; init; }
    public int WaypointIndex { get; init; }
    public int RoutePointCount { get; init; }
    public IReadOnlyList<NavigationPoint> PlannedRoute { get; init; } = Array.Empty<NavigationPoint>();
    public int Recoveries { get; init; }
    public long HoldRemainingMs { get; init; }
    public string CompletionMode { get; init; } = string.Empty;
    public double? FinalError { get; init; }
    public string? RecoveryAnchorId { get; init; }
    public NavigationPoint? RecoveryAnchorPosition { get; init; }
    public string? RecoveryMode { get; init; }
    public double? RecoveryExpandedSnapRadius { get; init; }
    public BodyReportStatus BodyReport { get; init; } = new();
}

internal sealed class BodyReportStatus
{
    public bool Enabled { get; init; }
    public string State { get; set; } = "idle";
    public string? TemporaryAnchorId { get; set; }
    public byte? ParentId { get; init; }
    public NavigationPoint? Position { get; init; }
    public float? TargetDistance { get; set; }
    public string? PlanningFallback { get; set; }
    public bool ReportClickDispatched { get; init; }
    public DateTimeOffset? ReportClickedAtUtc { get; init; }
}

