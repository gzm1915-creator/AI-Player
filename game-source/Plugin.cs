using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;
using UnityEngine;

namespace AUTelemetryProbe;

[BepInPlugin(PluginGuid, PluginName, PluginVersion)]
public sealed class TelemetryProbePlugin : BasePlugin
{
    public const string PluginGuid = "com.dsh.autelprobe";
    public const string PluginName = "AU Telemetry Probe";
    public const string PluginVersion = "0.6.91";
    internal static ManualLogSource? LogSource { get; private set; }
    internal static ConfigEntry<bool>? VisualEnabled { get; private set; }
    internal static ConfigEntry<int>? VisualSampleIntervalMs { get; private set; }
    internal static ConfigEntry<int>? VisualMovingSampleIntervalMs { get; private set; }
    internal static ConfigEntry<int>? VisualCaptureCooldownMs { get; private set; }
    internal static ConfigEntry<int>? VisualMovingCaptureCooldownMs { get; private set; }
    internal static ConfigEntry<int>? VisualHeartbeatMs { get; private set; }
    internal static ConfigEntry<int>? VisualMovingHeartbeatMs { get; private set; }
    internal static ConfigEntry<int>? VisualMaxWidth { get; private set; }
    internal static ConfigEntry<int>? VisualMaxCapturesPerRound { get; private set; }
    internal static ConfigEntry<float>? VisualChangedRatioThreshold { get; private set; }
    internal static ConfigEntry<float>? VisualMeanDeltaThreshold { get; private set; }
    internal static ConfigEntry<bool>? VisualDeletePreviousRound { get; private set; }
    internal static ConfigEntry<bool>? VisualDeleteOnSessionEnd { get; private set; }
    internal static ConfigEntry<bool>? ReactorCaptureEnabled { get; private set; }
    internal static ConfigEntry<int>? ReactorCaptureIntervalMs { get; private set; }
    internal static ConfigEntry<int>? ReactorCaptureMaxWidth { get; private set; }
    internal static ConfigEntry<int>? ReactorCaptureMaxFrames { get; private set; }
    internal static ConfigEntry<bool>? MemoryEnabled { get; private set; }
    internal static ConfigEntry<int>? RouteMemorySampleIntervalMs { get; private set; }
    internal static ConfigEntry<float>? RouteMemoryMinDistance { get; private set; }
    internal static ConfigEntry<bool>? CaptureMeetingChat { get; private set; }
    internal static ConfigEntry<bool>? DeleteRoundMemoryAfterMeeting { get; private set; }
    internal static ConfigEntry<bool>? DeleteGameMemoryAtEnd { get; private set; }
    internal static ConfigEntry<string>? InternalNavigationCommandPath { get; private set; }
    internal static ConfigEntry<string>? InternalNavigationStatusPath { get; private set; }
    internal static ConfigEntry<string>? RuntimeControlCommandPath { get; private set; }
    internal static ConfigEntry<string>? RuntimeControlStatusPath { get; private set; }
    internal static ConfigEntry<string>? MeetingControlStatusPath { get; private set; }
    internal static ConfigEntry<bool>? BodyReportEnabled { get; private set; }
    internal static ConfigEntry<float>? BodyDetectionRadius { get; private set; }

    public override void Load()
    {
        LogSource = Log;
        VisualEnabled = Config.Bind(
            "VisualCapture",
            "Enabled",
            true,
            "Capture low-frequency gameplay frames when visible changes are detected.");
        VisualSampleIntervalMs = Config.Bind(
            "VisualCapture",
            "SampleIntervalMs",
            4000,
            "Minimum time between visual change checks while standing still.");
        VisualMovingSampleIntervalMs = Config.Bind(
            "VisualCapture",
            "MovingSampleIntervalMs",
            2000,
            "Minimum time between visual change checks while moving.");
        VisualCaptureCooldownMs = Config.Bind(
            "VisualCapture",
            "CaptureCooldownMs",
            4000,
            "Minimum time between saved screenshots while standing still.");
        VisualMovingCaptureCooldownMs = Config.Bind(
            "VisualCapture",
            "MovingCaptureCooldownMs",
            2000,
            "Minimum time between saved screenshots while moving.");
        VisualHeartbeatMs = Config.Bind(
            "VisualCapture",
            "HeartbeatMs",
            4000,
            "Save one standing frame after this interval even when the image is stable.");
        VisualMovingHeartbeatMs = Config.Bind(
            "VisualCapture",
            "MovingHeartbeatMs",
            2000,
            "Save one moving frame after this interval even when the image is stable.");
        VisualMaxWidth = Config.Bind(
            "VisualCapture",
            "MaxWidth",
            640,
            "Maximum saved screenshot width while preserving aspect ratio.");
        VisualMaxCapturesPerRound = Config.Bind(
            "VisualCapture",
            "MaxCapturesPerRound",
            120,
            "Hard limit for screenshots retained in one round.");
        VisualChangedRatioThreshold = Config.Bind(
            "VisualCapture",
            "ChangedRatioThreshold",
            0.12f,
            "Fraction of sampled color cells that must change before saving.");
        VisualMeanDeltaThreshold = Config.Bind(
            "VisualCapture",
            "MeanDeltaThreshold",
            8f,
            "Mean per-channel color delta needed for saving a changed frame.");
        VisualDeletePreviousRound = Config.Bind(
            "VisualCapture",
            "DeletePreviousRoundAfterMeeting",
            true,
            "Delete the previous round screenshot directory after a meeting closes.");
        VisualDeleteOnSessionEnd = Config.Bind(
            "VisualCapture",
            "DeleteScreenshotsOnSessionEnd",
            true,
            "Delete retained screenshots from this session when the plugin or game closes.");
        ReactorCaptureEnabled = Config.Bind(
            "ReactorCapture",
            "Enabled",
            true,
            "Capture a high-frequency temporary frame burst while the StartReactor minigame is open.");
        ReactorCaptureIntervalMs = Config.Bind(
            "ReactorCapture",
            "IntervalMs",
            100,
            "Minimum interval between StartReactor burst frames.");
        ReactorCaptureMaxWidth = Config.Bind(
            "ReactorCapture",
            "MaxWidth",
            960,
            "Maximum saved width for StartReactor burst frames.");
        ReactorCaptureMaxFrames = Config.Bind(
            "ReactorCapture",
            "MaxFrames",
            360,
            "Hard frame limit for one StartReactor burst.");
        EnforceVisualCadence();
        MemoryEnabled = Config.Bind(
            "Memory",
            "Enabled",
            true,
            "Store per-round route memory and persistent meeting memory.");
        RouteMemorySampleIntervalMs = Config.Bind(
            "Memory",
            "RouteSampleIntervalMs",
            200,
            "Minimum interval for route-memory samples.");
        RouteMemoryMinDistance = Config.Bind(
            "Memory",
            "RouteMinDistance",
            0.04f,
            "Minimum movement needed to store another route-memory sample.");
        CaptureMeetingChat = Config.Bind(
            "Memory",
            "CaptureMeetingChat",
            true,
            "Capture locally visible meeting chat.");
        DeleteRoundMemoryAfterMeeting = Config.Bind(
            "Memory",
            "DeleteRoundMemoryAfterMeeting",
            false,
            "Delete route and visual memory for the round after a meeting closes.");
        DeleteGameMemoryAtEnd = Config.Bind(
            "Memory",
            "DeleteGameMemoryAtEnd",
            false,
            "Delete all session memory after the game-end screen appears.");
        var navigationRoot = Path.Combine(Paths.PluginPath, "AUTelemetryProbe");
        InternalNavigationCommandPath = Config.Bind(
            "InternalNavigation",
            "CommandPath",
            Path.Combine(navigationRoot, "internal-navigation-command.json"),
            "Atomic JSON command file consumed by the in-game navigation controller.");
        InternalNavigationStatusPath = Config.Bind(
            "InternalNavigation",
            "StatusPath",
            Path.Combine(navigationRoot, "internal-navigation-status.json"),
            "Atomic JSON status file written by the in-game navigation controller.");
        RuntimeControlCommandPath = Config.Bind(
            "RuntimeControl",
            "CommandPath",
            Path.Combine(navigationRoot, "runtime-control.json"),
            "Atomic JSON toggles written by the external control panel.");
        RuntimeControlStatusPath = Config.Bind(
            "RuntimeControl",
            "StatusPath",
            Path.Combine(navigationRoot, "runtime-control-status.json"),
            "Atomic JSON status consumed by the external control panel.");
        MeetingControlStatusPath = Config.Bind(
            "MeetingControl",
            "StatusPath",
            Path.Combine(navigationRoot, "meeting-control-status.json"),
            "Atomic JSON meeting state and remaining-task snapshot.");
        BodyReportEnabled = Config.Bind(
            "BodyReport",
            "Enabled",
            false,
            "Create a temporary report anchor when an unreported body is nearby, then report when the button can be used.");
        BodyDetectionRadius = Config.Bind(
            "BodyReport",
            "DetectionRadius",
            6f,
            "Maximum local distance for temporary body-anchor and telemetry candidates.");
        RuntimeControl.Configure(
            RuntimeControlCommandPath.Value,
            RuntimeControlStatusPath.Value,
            VisualEnabled.Value);
        MeetingControl.Configure(
            MeetingControlStatusPath.Value,
            Path.Combine(navigationRoot, "meeting-memory"));
        try
        {
            var harmony = new Harmony(PluginGuid);
            GameHooks.Install(harmony, (hook, error) => {
                TaskCompletionMemory.RecordHook(hook.Name, error == null);
                if (error == null) Log.LogInfo($"Game hook installed: {hook.Name}");
                else Log.LogWarning($"Could not install game hook {hook.Name}: {error.Message}");
            });
        }
        catch (Exception exception)
        {
            Log.LogWarning($"Could not install meeting chat memory hook: {exception.Message}");
        }
        Log.LogInfo($"{PluginName} {PluginVersion} loading (read-only)");
        AddComponent<ProbeBehaviour>();
        AddComponent<InternalNavigationBehaviour>();
        Log.LogInfo($"{PluginName} component registered");
    }

    private void EnforceVisualCadence()
    {
        if (VisualSampleIntervalMs != null &&
            VisualSampleIntervalMs.Value != 4000)
        {
            VisualSampleIntervalMs.Value = 4000;
        }
        if (VisualMovingSampleIntervalMs != null &&
            VisualMovingSampleIntervalMs.Value != 2000)
        {
            VisualMovingSampleIntervalMs.Value = 2000;
        }
        if (VisualCaptureCooldownMs != null &&
            VisualCaptureCooldownMs.Value != 4000)
        {
            VisualCaptureCooldownMs.Value = 4000;
        }
        if (VisualMovingCaptureCooldownMs != null &&
            VisualMovingCaptureCooldownMs.Value != 2000)
        {
            VisualMovingCaptureCooldownMs.Value = 2000;
        }
        if (VisualHeartbeatMs != null &&
            VisualHeartbeatMs.Value != 4000)
        {
            VisualHeartbeatMs.Value = 4000;
        }
        if (VisualMovingHeartbeatMs != null &&
            VisualMovingHeartbeatMs.Value != 2000)
        {
            VisualMovingHeartbeatMs.Value = 2000;
        }
    }
}

public sealed class ProbeBehaviour : MonoBehaviour
{
    private const int SampleIntervalMs = 25;
    private const int LatestIntervalMs = 50;
    private const int JsonlIntervalMs = 100;
    private const int FlushIntervalMs = 1000;
    private const int ReactorCloseGraceMs = 2000;
    private const long MaxTelemetryFileBytes = 16L * 1024 * 1024;
    private const int MaxTelemetryArchives = 3;

    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    private string? _sessionId;
    private string? _outputDirectory;
    private string? _jsonlPath;
    private StreamWriter? _jsonlWriter;
    private long _jsonlBytes;
    private long _seq;
    private long _nextSampleMs;
    private long _nextLatestMs;
    private long _nextJsonlMs;
    private long _nextFlushMs;
    private int _errorCount;
    private string? _lastError;
    private ManualLogSource? _log;
    private List<AnchorOverlayEntry> _overlayAnchors = new();
    private bool _overlayVisible;
    private string? _visualSessionRoot;
    private string? _visualRoundDirectory;
    private string? _visualLogPath;
    private long _nextVisualCheckMs;
    private long _lastVisualCaptureMs = long.MinValue / 2;
    private int _visualRoundId = 1;
    private int _visualCaptureSequence;
    private bool _meetingWasOpen;
    private bool _loggedGameReady;
    private bool _runtimePrewarmed;
    private long _nextPrewarmMs;
    private long _lastGameStartSignal;
    private Color32[]? _visualPreviousSignature;
    private bool _hasVisualPlayerPose;
    private Vector3 _lastVisualPlayerPosition;
    private string? _reactorCaptureDirectory;
    private string? _reactorCaptureMetadataPath;
    private int _reactorCaptureSequence;
    private long _nextReactorCaptureMs;
    private long _reactorMinigameClosedMs;
    private uint? _reactorTaskId;
    private int _reactorTaskInitialStep;
    private int _reactorTaskTargetStep;
    private string? _skillStatusPath;
    private SkillStatusDocument? _skillStatus;
    private long _nextSkillStatusReadMs;
    private bool _shutdownCompleted;

    private void Start()
    {
        _log = TelemetryProbePlugin.LogSource;
        _sessionId = Guid.NewGuid().ToString();
        _outputDirectory = Path.Combine(Paths.PluginPath, "AUTelemetryProbe");
        Directory.CreateDirectory(_outputDirectory);
        _skillStatusPath = Path.Combine(
            _outputDirectory,
            "skill-status.json");
        MemoryRepositories.Configure(_outputDirectory, _sessionId, _log);
        MemoryRepositories.StartRound(1);

        _jsonlPath = Path.Combine(_outputDirectory, "telemetry.jsonl");
        TrimOversizedTelemetryLog(_jsonlPath);
        OpenJsonlWriter(FileMode.Append);
        LoadAnchorOverlay();
        InitializeVisualCapture();

        if (_log != null)
        {
            _log.LogInfo($"Probe session {_sessionId}");
            _log.LogInfo($"Writing telemetry to {_outputDirectory}");
        }
    }

    private void Update()
    {
        if (UnityEngine.Input.GetKeyDown(KeyCode.F8))
        {
            _overlayVisible = !_overlayVisible;
            _log?.LogInfo($"Anchor overlay {(_overlayVisible ? "shown" : "hidden")}");
        }
        if (UnityEngine.Input.GetKeyDown(KeyCode.F9))
        {
            LoadAnchorOverlay();
        }

        var now = _clock.ElapsedMilliseconds;
        RuntimeControl.Tick(now);
        MinigameAutomation.Tick(now);
        if (_lastGameStartSignal != TaskStartSignalPatch.SignalVersion)
        {
            _lastGameStartSignal = TaskStartSignalPatch.SignalVersion;
            _nextSampleMs = 0;
            _nextLatestMs = 0;
            TryPrewarmRuntime(now);
            _log?.LogInfo("Game-start signal triggered immediate task sampling");
        }

        TryPrewarmRuntime(now);
        PollSkillStatus(now);
        ProcessMeetingLifecycle(now);
        ProcessReactorCapture(now);
        ProcessVisualCapture(now);
        if (now < _nextSampleMs)
        {
            return;
        }

        _nextSampleMs = now + SampleIntervalMs;
        try
        {
            var sampleSeq = _seq++;
            var sample = TelemetryCapture.Build(
                now,
                _sessionId!,
                sampleSeq,
                _errorCount,
                _lastError);
            var json = JsonSerializer.Serialize(sample, _jsonOptions);
            if (now >= _nextJsonlMs)
            {
                WriteTelemetryLine(json);
                _nextJsonlMs = now + JsonlIntervalMs;
            }
            MemoryRepositories.RecordRoute(
                sampleSeq,
                now,
                sample.Game.MapId,
                sample.LocalPlayer.Position.X,
                sample.LocalPlayer.Position.Y,
                sample.LocalPlayer.Velocity.X,
                sample.LocalPlayer.Velocity.Y,
                sample.LocalPlayer.CanMove);

            if (!_loggedGameReady && sample.Game.GameHasStarted)
            {
                _loggedGameReady = true;
                _log?.LogInfo($"First in-game task state available after {now} ms");
            }

            if (now >= _nextLatestMs)
            {
                WriteLatestAtomic(json);
                _nextLatestMs = now + LatestIntervalMs;
            }

            if (now >= _nextFlushMs)
            {
                _jsonlWriter?.Flush();
                _nextFlushMs = now + FlushIntervalMs;
            }
        }
        catch (Exception exception)
        {
            _errorCount++;
            _lastError = $"{exception.GetType().Name}: {exception.Message}";
            _log?.LogWarning($"Probe sample failed: {_lastError}");
        }
    }

    private void OnGUI()
    {
        DrawSkillStatusOverlay();
        if (!_overlayVisible)
        {
            return;
        }

        var localPlayer = PlayerControl.LocalPlayer;
        var camera = Camera.main;
        if (localPlayer == null || camera == null)
        {
            return;
        }

        var playerPosition = localPlayer.transform.position;
        var previousColor = GUI.color;
        var shown = 0;
        AnchorOverlayEntry? nearest = null;
        var nearestDistance = double.MaxValue;

        foreach (var anchor in _overlayAnchors)
        {
            if (anchor.Position == null)
            {
                continue;
            }

            var dx = anchor.Position.X - playerPosition.x;
            var dy = anchor.Position.Y - playerPosition.y;
            var distance = Math.Sqrt(dx * dx + dy * dy);
            if (distance < nearestDistance)
            {
                nearestDistance = distance;
                nearest = anchor;
            }
            if (distance > 12.0)
            {
                continue;
            }

            var screen = camera.WorldToScreenPoint(
                new Vector3(anchor.Position.X, anchor.Position.Y, playerPosition.z));
            if (screen.z <= 0)
            {
                continue;
            }

            var screenY = Screen.height - screen.y;
            GUI.color = anchor.Correction?.Status == "auto_corrected"
                ? new Color(0.2f, 0.9f, 0.35f, 0.95f)
                : new Color(1f, 0.75f, 0.15f, 0.95f);
            GUI.Box(new Rect(screen.x - 5, screenY - 5, 10, 10), string.Empty);
            GUI.color = Color.white;
            GUI.Label(
                new Rect(screen.x + 9, screenY - 10, 260, 20),
                $"{anchor.AnchorId}  {distance:F2}");
            shown++;
        }

        GUI.color = Color.white;
        GUI.Box(new Rect(14, 62, 510, 46), string.Empty);
        GUI.Label(
            new Rect(22, 67, 495, 20),
            $"Anchor overlay: F8 hide / F9 reload  |  loaded={_overlayAnchors.Count}  shown={shown}");
        if (nearest?.Position != null)
        {
            GUI.Label(
                new Rect(22, 87, 495, 20),
                $"Nearest: {nearest.AnchorId}  ({nearestDistance:F2})");
        }
        GUI.color = previousColor;
    }

    private void PollSkillStatus(long now)
    {
        if (_skillStatusPath == null || now < _nextSkillStatusReadMs)
        {
            return;
        }
        _nextSkillStatusReadMs = now + 100;
        try
        {
            if (!File.Exists(_skillStatusPath))
            {
                return;
            }
            var json = File.ReadAllText(_skillStatusPath);
            var document = JsonSerializer.Deserialize<SkillStatusDocument>(
                json,
                _jsonOptions);
            if (document != null &&
                !string.IsNullOrWhiteSpace(document.State))
            {
                _skillStatus = document;
            }
        }
        catch
        {
            // The skill writes atomically; keep the previous HUD state on a
            // transient read/parse failure.
        }
    }

    private void DrawSkillStatusOverlay()
    {
        var status = _skillStatus;
        if (status == null)
        {
            return;
        }

        var age = DateTimeOffset.UtcNow - status.UpdatedAtUtc;
        if (age < TimeSpan.Zero || age > TimeSpan.FromSeconds(15))
        {
            return;
        }

        var sequence = status.Sequence == null
            ? string.Empty
            : string.Join(",", status.Sequence);
        var lit = status.LitButtons == null
            ? string.Empty
            : string.Join(",", status.LitButtons);
        var previousColor = GUI.color;
        GUI.color = status.State switch
        {
            "completed" => new Color(0.2f, 0.95f, 0.4f, 0.96f),
            "failed" or "error" => new Color(1f, 0.35f, 0.25f, 0.96f),
            "round_complete" => new Color(0.25f, 0.85f, 1f, 0.96f),
            _ => new Color(1f, 0.82f, 0.25f, 0.96f)
        };
        GUI.Box(new Rect(14, 116, 520, 70), string.Empty);
        GUI.color = Color.white;
        GUI.Label(
            new Rect(22, 120, 505, 20),
            $"StartReactor skill: {status.State}  " +
            $"round={status.Round}/{status.TotalRounds}");
        GUI.Label(
            new Rect(22, 140, 505, 20),
            $"sequence=[{sequence}]  lit=[{lit}]  backend={status.Backend}");
        if (!string.IsNullOrWhiteSpace(status.Error))
        {
            GUI.Label(
                new Rect(22, 160, 505, 20),
                $"error={status.Error}");
        }
        else
        {
            GUI.Label(
                new Rect(22, 160, 505, 20),
                string.IsNullOrWhiteSpace(status.Message)
                    ? "recording / clicking"
                    : status.Message);
        }
        GUI.color = previousColor;
    }

    private void TryPrewarmRuntime(long now)
    {
        if (_runtimePrewarmed || now < _nextPrewarmMs)
        {
            return;
        }

        _nextPrewarmMs = now + 100;
        try
        {
            var player = PlayerControl.LocalPlayer;
            var ship = ShipStatus.Instance;
            var panel = HudManager.Instance?.TaskPanel;
            if (player == null || ship == null)
            {
                return;
            }

            _ = player.GetTruePosition();
            _ = player.CanMove;
            _ = panel?.taskText?.text;
            var tasks = player.myTasks;
            if (tasks != null)
            {
                var count = Math.Min(tasks.Count, 20);
                for (var index = 0; index < count; index++)
                {
                    var task = tasks[index];
                    if (task == null)
                    {
                        continue;
                    }

                    _ = task.TaskType.ToString();
                    _ = task.TaskStep;
                    _ = task.StartAt.ToString();
                    var locations = task.Locations;
                    if (locations != null)
                    {
                        _ = locations.Count;
                    }
                }
            }

            _runtimePrewarmed = true;
            _log?.LogInfo($"Runtime task APIs prewarmed after {now} ms");
        }
        catch
        {
            // The ship may still be constructing; retry after the short interval.
        }
    }

    private void LoadAnchorOverlay()
    {
        if (_outputDirectory == null)
        {
            return;
        }

        var path = Path.Combine(_outputDirectory, "anchors-debug.json");
        try
        {
            if (!File.Exists(path))
            {
                _overlayAnchors = new List<AnchorOverlayEntry>();
                _log?.LogWarning($"Anchor overlay file not found: {path}");
                return;
            }

            var document = JsonSerializer.Deserialize<AnchorOverlayDocument>(
                File.ReadAllText(path),
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            _overlayAnchors = document?.Anchors ?? new List<AnchorOverlayEntry>();
            _log?.LogInfo($"Loaded {_overlayAnchors.Count} anchors for overlay");
        }
        catch (Exception exception)
        {
            _overlayAnchors = new List<AnchorOverlayEntry>();
            _log?.LogWarning($"Could not load anchor overlay: {exception.Message}");
        }
    }

    private void InitializeVisualCapture()
    {
        if (_outputDirectory == null ||
            _sessionId == null ||
            TelemetryProbePlugin.VisualEnabled?.Value != true)
        {
            return;
        }

        var shortSession = _sessionId.Substring(0, Math.Min(8, _sessionId.Length));
        _visualSessionRoot = Path.Combine(_outputDirectory, "visual", shortSession);
        _visualLogPath = Path.Combine(_visualSessionRoot, "rounds.jsonl");
        Directory.CreateDirectory(_visualSessionRoot);
        StartVisualRound();
    }

    private void StartVisualRound()
    {
        if (_visualSessionRoot == null)
        {
            return;
        }

        _visualRoundDirectory = Path.Combine(
            _visualSessionRoot,
            $"round-{_visualRoundId:D4}");
        Directory.CreateDirectory(_visualRoundDirectory);
        _visualCaptureSequence = 0;
        _visualPreviousSignature = null;
        _lastVisualCaptureMs = long.MinValue / 2;
        _nextVisualCheckMs = 0;
        AppendVisualEvent(JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["event"] = "round_started",
            ["sessionId"] = _sessionId,
            ["roundId"] = _visualRoundId,
            ["directory"] = _visualRoundDirectory
        }, _jsonOptions));
        _log?.LogInfo($"Visual capture round {_visualRoundId} started");
    }

    private void ProcessVisualCapture(long now)
    {
        if (_visualRoundDirectory == null ||
            TelemetryProbePlugin.VisualEnabled?.Value != true ||
            !RuntimeControl.VisualCaptureEnabled)
        {
            _nextVisualCheckMs = 0;
            return;
        }

        var meetingOpen = MeetingHud.Instance != null;
        if (meetingOpen)
        {
            return;
        }

        if (now < _nextVisualCheckMs)
        {
            return;
        }

        var gameManager = GameManager.Instance;
        if (gameManager == null || !gameManager.GameHasStarted)
        {
            _nextVisualCheckMs = now + 500;
            return;
        }

        var player = PlayerControl.LocalPlayer;
        var ship = ShipStatus.Instance;
        if (player == null || ship == null)
        {
            return;
        }

        var currentPosition = player.transform.position;
        var movedDistance = _hasVisualPlayerPose
            ? Vector3.Distance(currentPosition, _lastVisualPlayerPosition)
            : 0f;
        var velocity = player.MyPhysics != null
            ? player.MyPhysics.GetVelocity()
            : Vector2.zero;
        var isMoving = velocity.sqrMagnitude >= 0.04f || movedDistance >= 0.025f;
        _lastVisualPlayerPosition = currentPosition;
        _hasVisualPlayerPose = true;

        var interval = isMoving
            ? Math.Max(100, TelemetryProbePlugin.VisualMovingSampleIntervalMs?.Value ?? 2000)
            : Math.Max(500, TelemetryProbePlugin.VisualSampleIntervalMs?.Value ?? 4000);
        _nextVisualCheckMs = now + interval;

        Texture2D? source = null;
        Texture2D? thumbnail = null;
        RenderTexture? renderTexture = null;
        var previousActive = RenderTexture.active;
        try
        {
            source = ScreenCapture.CaptureScreenshotAsTexture();
            if (source == null)
            {
                return;
            }

            var maxWidth = Math.Clamp(
                TelemetryProbePlugin.VisualMaxWidth?.Value ?? 640,
                160,
                1920);
            var scale = Math.Min(1.0, maxWidth / (double)source.width);
            var width = Math.Max(1, (int)Math.Round(source.width * scale));
            var height = Math.Max(1, (int)Math.Round(source.height * scale));

            renderTexture = RenderTexture.GetTemporary(
                width,
                height,
                0,
                RenderTextureFormat.ARGB32);
            Graphics.Blit(source, renderTexture);
            RenderTexture.active = renderTexture;
            thumbnail = new Texture2D(width, height, TextureFormat.RGBA32, false);
            thumbnail.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            thumbnail.Apply(false, false);

            var pixels = thumbnail.GetPixels32();
            var signature = BuildVisualSignature(pixels, width, height);
            var comparison = CompareVisualSignatures(_visualPreviousSignature, signature);
            var sinceLastCapture = now - _lastVisualCaptureMs;
            var cooldown = isMoving
                ? Math.Max(200, TelemetryProbePlugin.VisualMovingCaptureCooldownMs?.Value ?? 2000)
                : Math.Max(1000, TelemetryProbePlugin.VisualCaptureCooldownMs?.Value ?? 4000);
            var heartbeat = Math.Max(
                cooldown,
                isMoving
                    ? TelemetryProbePlugin.VisualMovingHeartbeatMs?.Value ?? 2000
                    : TelemetryProbePlugin.VisualHeartbeatMs?.Value ?? 4000);
            var maxCaptures = Math.Max(
                1,
                TelemetryProbePlugin.VisualMaxCapturesPerRound?.Value ?? 120);

            var changed = comparison.ChangedRatio >=
                (TelemetryProbePlugin.VisualChangedRatioThreshold?.Value ?? 0.12f) ||
                comparison.MeanDelta >=
                (TelemetryProbePlugin.VisualMeanDeltaThreshold?.Value ?? 8f);
            var shouldSave = _visualCaptureSequence < maxCaptures &&
                sinceLastCapture >= cooldown &&
                (_visualPreviousSignature == null || changed || sinceLastCapture >= heartbeat);
            if (!shouldSave)
            {
                return;
            }

            var trigger = _visualPreviousSignature == null
                ? "baseline"
                : changed
                    ? "visual_change"
                    : "heartbeat";
            SaveVisualFrame(
                thumbnail,
                now,
                player,
                ship,
                comparison.ChangedRatio,
                comparison.MeanDelta,
                trigger,
                isMoving,
                movedDistance);
            _visualPreviousSignature = signature;
            _lastVisualCaptureMs = now;
        }
        catch (Exception exception)
        {
            _log?.LogWarning($"Visual capture failed: {exception.Message}");
        }
        finally
        {
            RenderTexture.active = previousActive;
            if (renderTexture != null)
            {
                RenderTexture.ReleaseTemporary(renderTexture);
            }
            if (thumbnail != null)
            {
                UnityEngine.Object.Destroy(thumbnail);
            }
            if (source != null)
            {
                UnityEngine.Object.Destroy(source);
            }
        }
    }

    private void ProcessReactorCapture(long now)
    {
        var enabled =
            _visualSessionRoot != null &&
            TelemetryProbePlugin.VisualEnabled?.Value == true &&
            RuntimeControl.VisualCaptureEnabled &&
            TelemetryProbePlugin.ReactorCaptureEnabled?.Value == true;
        if (!enabled)
        {
            if (_reactorCaptureDirectory != null)
            {
                FinishReactorCapture(now, "capture_disabled");
            }
            return;
        }

        if (MeetingHud.Instance != null)
        {
            if (_reactorCaptureDirectory != null)
            {
                FinishReactorCapture(now, "meeting_started");
            }
            return;
        }

        var player = PlayerControl.LocalPlayer;
        var tasks = TelemetryCapture.ReadOwnTasks(player);
        var task = FindReactorTask(tasks);
        if (_reactorTaskId == null && task != null)
        {
            _reactorTaskId = task.TaskId;
            _reactorTaskInitialStep = task.Step;
            _reactorTaskTargetStep = task.MaxStep > task.Step
                ? task.MaxStep
                : task.Step + 1;
        }

        if (_reactorCaptureDirectory != null && ReactorTaskProgressed(task))
        {
            FinishReactorCapture(now, "task_complete");
            return;
        }

        var minigame = Minigame.Instance;
        var reactor = minigame?.TryCast<SimonSaysGame>();
        // The visual solver must still receive frames when this game build
        // cannot expose the SimonSaysGame reflection fields.
        if (minigame == null || (reactor == null && task == null))
        {
            _nextReactorCaptureMs = 0;
            if (_reactorCaptureDirectory == null)
            {
                _reactorMinigameClosedMs = 0;
                return;
            }

            if (_reactorMinigameClosedMs == 0)
            {
                _reactorMinigameClosedMs = now;
                return;
            }
            if (now - _reactorMinigameClosedMs >= ReactorCloseGraceMs)
            {
                FinishReactorCapture(
                    now,
                    task == null
                        ? "minigame_closed_task_unknown"
                        : "minigame_closed_without_progress");
            }
            return;
        }

        _reactorMinigameClosedMs = 0;
        if (_reactorCaptureDirectory == null)
        {
            StartReactorCapture(now);
        }

        var maxFrames = Math.Clamp(
            TelemetryProbePlugin.ReactorCaptureMaxFrames?.Value ?? 360,
            30,
            1800);
        if (_reactorCaptureSequence >= maxFrames)
        {
            FinishReactorCapture(now, "frame_limit");
            return;
        }
        if (now < _nextReactorCaptureMs)
        {
            return;
        }

        var interval = Math.Clamp(
            TelemetryProbePlugin.ReactorCaptureIntervalMs?.Value ?? 100,
            50,
            500);
        _nextReactorCaptureMs = now + interval;
        CaptureReactorFrame(now, minigame!, task);
    }

    private OwnTaskInfo? FindReactorTask(List<OwnTaskInfo> tasks)
    {
        if (_reactorTaskId != null)
        {
            return tasks.FirstOrDefault(
                task => task.TaskId == _reactorTaskId.Value);
        }
        return tasks.FirstOrDefault(
            task =>
                !task.Complete &&
                string.Equals(
                    task.TaskType,
                    "StartReactor",
                    StringComparison.Ordinal));
    }

    private bool ReactorTaskProgressed(OwnTaskInfo? task)
    {
        if (_reactorTaskId == null || task == null)
        {
            return false;
        }
        var targetStep = Math.Max(
            _reactorTaskTargetStep,
            _reactorTaskInitialStep + 1);
        return task.Complete || task.Step >= targetStep;
    }

    private void StartReactorCapture(long now)
    {
        if (_visualSessionRoot == null)
        {
            return;
        }

        try
        {
            var burstRoot = Path.Combine(
                _visualSessionRoot,
                "task-bursts");
            Directory.CreateDirectory(burstRoot);
            var stamp = DateTimeOffset.UtcNow.ToString("yyyyMMdd-HHmmssfff");
            var burstId = Guid.NewGuid().ToString("N").Substring(0, 8);
            _reactorCaptureDirectory = Path.Combine(
                burstRoot,
                $"start-reactor-{stamp}-{burstId}");
            Directory.CreateDirectory(_reactorCaptureDirectory);
            _reactorCaptureMetadataPath = Path.Combine(
                _reactorCaptureDirectory,
                "reactor-capture.jsonl");
            _reactorCaptureSequence = 0;
            _nextReactorCaptureMs = 0;
            _reactorMinigameClosedMs = 0;
            AppendReactorCaptureEvent(new Dictionary<string, object?>
            {
                ["event"] = "burst_started",
                ["sessionId"] = _sessionId,
                ["roundId"] = _visualRoundId,
                ["monotonicMs"] = now,
                ["capturedAtUtc"] = DateTimeOffset.UtcNow,
                ["taskId"] = _reactorTaskId,
                ["taskInitialStep"] = _reactorTaskInitialStep,
                ["taskTargetStep"] = _reactorTaskTargetStep
            });
            _log?.LogInfo(
                $"StartReactor high-frequency capture started: " +
                _reactorCaptureDirectory);
        }
        catch (Exception exception)
        {
            _log?.LogWarning(
                $"Could not start StartReactor capture: {exception.Message}");
            ResetReactorCaptureState();
        }
    }

    private void CaptureReactorFrame(
        long now,
        Minigame minigame,
        OwnTaskInfo? task)
    {
        if (_reactorCaptureDirectory == null ||
            _reactorCaptureMetadataPath == null)
        {
            return;
        }

        Texture2D? source = null;
        Texture2D? thumbnail = null;
        RenderTexture? renderTexture = null;
        var previousActive = RenderTexture.active;
        try
        {
            source = ScreenCapture.CaptureScreenshotAsTexture();
            if (source == null)
            {
                return;
            }

            var maxWidth = Math.Clamp(
                TelemetryProbePlugin.ReactorCaptureMaxWidth?.Value ?? 960,
                320,
                1920);
            var scale = Math.Min(1.0, maxWidth / (double)source.width);
            var width = Math.Max(1, (int)Math.Round(source.width * scale));
            var height = Math.Max(1, (int)Math.Round(source.height * scale));
            renderTexture = RenderTexture.GetTemporary(
                width,
                height,
                0,
                RenderTextureFormat.ARGB32);
            Graphics.Blit(source, renderTexture);
            RenderTexture.active = renderTexture;
            thumbnail = new Texture2D(
                width,
                height,
                TextureFormat.RGBA32,
                false);
            thumbnail.ReadPixels(
                new Rect(0, 0, width, height),
                0,
                0);
            thumbnail.Apply(false, false);

            _reactorCaptureSequence++;
            var timestamp = DateTimeOffset.UtcNow.ToString(
                "yyyyMMdd-HHmmssfff");
            var fileName =
                $"{_reactorCaptureSequence:D4}-{timestamp}.png";
            var imagePath = Path.Combine(
                _reactorCaptureDirectory,
                fileName);
            File.WriteAllBytes(
                imagePath,
                ImageConversion.EncodeToPNG(thumbnail));

            var sequence = TelemetryCapture.ReadMinigameSafely(
                () => TelemetryCapture.ReadStartReactorSequence(minigame),
                Array.Empty<int>(),
                "reactor capture sequence");
            var lit = TelemetryCapture.ReadMinigameSafely(
                () => TelemetryCapture.ReadStartReactorLitIndices(minigame),
                Array.Empty<int>(),
                "reactor capture lit indices");
            var ordered = TelemetryCapture.ReadMinigameSafely(
                () => TelemetryCapture.ReadStartReactorOrderedButtons(minigame),
                Array.Empty<int>(),
                "reactor capture ordered buttons");
            AppendReactorCaptureEvent(new Dictionary<string, object?>
            {
                ["event"] = "capture",
                ["sessionId"] = _sessionId,
                ["roundId"] = _visualRoundId,
                ["captureIndex"] = _reactorCaptureSequence,
                ["seq"] = _seq,
                ["monotonicMs"] = now,
                ["capturedAtUtc"] = DateTimeOffset.UtcNow,
                ["taskId"] = _reactorTaskId,
                ["taskStep"] = task?.Step,
                ["taskComplete"] = task?.Complete,
                ["password"] = sequence,
                ["litIndices"] = lit,
                ["orderedButtons"] = ordered,
                ["animating"] = lit.Length > 0,
                ["width"] = width,
                ["height"] = height,
                ["image"] = imagePath
            });
        }
        catch (Exception exception)
        {
            _log?.LogWarning(
                $"StartReactor capture frame failed: {exception.Message}");
        }
        finally
        {
            RenderTexture.active = previousActive;
            if (renderTexture != null)
            {
                RenderTexture.ReleaseTemporary(renderTexture);
            }
            if (thumbnail != null)
            {
                UnityEngine.Object.Destroy(thumbnail);
            }
            if (source != null)
            {
                UnityEngine.Object.Destroy(source);
            }
        }
    }

    private void AppendReactorCaptureEvent(
        Dictionary<string, object?> eventData)
    {
        if (_reactorCaptureMetadataPath == null)
        {
            return;
        }
        try
        {
            File.AppendAllText(
                _reactorCaptureMetadataPath,
                JsonSerializer.Serialize(eventData, _jsonOptions) +
                Environment.NewLine,
                new System.Text.UTF8Encoding(false));
        }
        catch (Exception exception)
        {
            _log?.LogWarning(
                $"Could not append StartReactor capture event: " +
                exception.Message);
        }
    }

    private void FinishReactorCapture(long now, string reason)
    {
        var directory = _reactorCaptureDirectory;
        if (directory == null)
        {
            ResetReactorCaptureState();
            return;
        }

        var screenshotCount = Directory.Exists(directory)
            ? Directory.GetFiles(directory, "*.png").Length
            : 0;
        AppendReactorCaptureEvent(new Dictionary<string, object?>
        {
            ["event"] = "burst_finished",
            ["sessionId"] = _sessionId,
            ["roundId"] = _visualRoundId,
            ["monotonicMs"] = now,
            ["capturedAtUtc"] = DateTimeOffset.UtcNow,
            ["taskId"] = _reactorTaskId,
            ["reason"] = reason,
            ["screenshotCount"] = screenshotCount
        });

        var deleted = false;
        try
        {
            if (_visualSessionRoot != null)
            {
                var safeRoot = Path.GetFullPath(
                    Path.Combine(_visualSessionRoot, "task-bursts"))
                    .TrimEnd(Path.DirectorySeparatorChar) +
                    Path.DirectorySeparatorChar;
                var safeTarget = Path.GetFullPath(directory);
                if (!safeTarget.StartsWith(
                        safeRoot,
                        StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException(
                        "Refusing to delete a Reactor burst outside " +
                        "the visual task-bursts directory.");
                }
                if (Directory.Exists(safeTarget))
                {
                    Directory.Delete(safeTarget, true);
                }
                deleted = true;
            }
        }
        catch (Exception exception)
        {
            _log?.LogWarning(
                $"Could not remove StartReactor capture burst: " +
                exception.Message);
        }

        AppendVisualEvent(JsonSerializer.Serialize(
            new Dictionary<string, object?>
            {
                ["event"] = "task_capture_finished",
                ["sessionId"] = _sessionId,
                ["roundId"] = _visualRoundId,
                ["monotonicMs"] = now,
                ["taskId"] = _reactorTaskId,
                ["reason"] = reason,
                ["screenshotCount"] = screenshotCount,
                ["deleted"] = deleted,
                ["directory"] = directory
            },
            _jsonOptions));
        _log?.LogInfo(
            $"StartReactor capture burst finished: reason={reason}, " +
            $"frames={screenshotCount}, deleted={deleted}");
        ResetReactorCaptureState();
    }

    private void ResetReactorCaptureState()
    {
        _reactorCaptureDirectory = null;
        _reactorCaptureMetadataPath = null;
        _reactorCaptureSequence = 0;
        _nextReactorCaptureMs = 0;
        _reactorMinigameClosedMs = 0;
        _reactorTaskId = null;
        _reactorTaskInitialStep = 0;
        _reactorTaskTargetStep = 0;
    }

    private void ProcessMeetingLifecycle(long now)
    {
        var meetingOpen = MeetingHud.Instance != null;
        MeetingControl.Observe(
            meetingOpen,
            now,
            PlayerControl.LocalPlayer);
        if (meetingOpen)
        {
            if (!_meetingWasOpen)
            {
                _meetingWasOpen = true;
                _visualPreviousSignature = null;
                MemoryRepositories.BeginMeeting(_visualRoundId, now);
                AppendVisualEvent(JsonSerializer.Serialize(new Dictionary<string, object?>
                {
                    ["event"] = "meeting_started",
                    ["sessionId"] = _sessionId,
                    ["roundId"] = _visualRoundId,
                    ["monotonicMs"] = now
                }, _jsonOptions));
            }
            return;
        }

        if (_meetingWasOpen)
        {
            _meetingWasOpen = false;
            MemoryRepositories.EndMeeting(now);
            CompleteVisualRound(now);
        }
    }

    private void SaveVisualFrame(
        Texture2D texture,
        long now,
        PlayerControl player,
        ShipStatus ship,
        double changedRatio,
        double meanDelta,
        string trigger,
        bool isMoving,
        float movedDistance)
    {
        if (_visualRoundDirectory == null)
        {
            return;
        }

        Directory.CreateDirectory(_visualRoundDirectory);
        _visualCaptureSequence++;
        var timestamp = DateTimeOffset.UtcNow.ToString("yyyyMMdd-HHmmssfff");
        var fileName = $"{_visualCaptureSequence:D4}-{timestamp}-{trigger}.png";
        var imagePath = Path.Combine(_visualRoundDirectory, fileName);
        var png = ImageConversion.EncodeToPNG(texture);
        File.WriteAllBytes(imagePath, png);

        var position = player.transform.position;
        AppendVisualEvent(JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["event"] = "capture",
            ["sessionId"] = _sessionId,
            ["roundId"] = _visualRoundId,
            ["captureIndex"] = _visualCaptureSequence,
            ["seq"] = _seq,
            ["monotonicMs"] = now,
            ["capturedAtUtc"] = DateTimeOffset.UtcNow,
            ["mapId"] = ship.Type.ToString(),
            ["meetingOpen"] = MeetingHud.Instance != null,
            ["minigameOpen"] = Minigame.Instance != null,
            ["position"] = new { x = position.x, y = position.y },
            ["changedRatio"] = Math.Round(changedRatio, 4),
            ["meanDelta"] = Math.Round(meanDelta, 2),
            ["trigger"] = trigger,
            ["moving"] = isMoving,
            ["movedDistance"] = Math.Round(movedDistance, 4),
            ["width"] = texture.width,
            ["height"] = texture.height,
            ["image"] = imagePath
        }, _jsonOptions));
        _log?.LogInfo($"Visual frame saved: {fileName}");
    }

    private void CompleteVisualRound(long now)
    {
        if (_visualRoundDirectory == null)
        {
            return;
        }

        var completedRound = _visualRoundId;
        var completedDirectory = _visualRoundDirectory;
        var screenshotCount = Directory.Exists(completedDirectory)
            ? Directory.GetFiles(completedDirectory, "*.png").Length
            : 0;
        var deleted = false;
        var deletePrevious =
            TelemetryProbePlugin.VisualDeletePreviousRound?.Value ?? true;
        if (deletePrevious && _visualSessionRoot != null)
        {
            try
            {
                var safeRoot = Path.GetFullPath(_visualSessionRoot)
                    .TrimEnd(Path.DirectorySeparatorChar) +
                    Path.DirectorySeparatorChar;
                var safeTarget = Path.GetFullPath(completedDirectory);
                if (!safeTarget.StartsWith(safeRoot, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException(
                        "Refusing to delete a directory outside the visual session root.");
                }

                if (Directory.Exists(safeTarget))
                {
                    Directory.Delete(safeTarget, true);
                }
                deleted = true;
            }
            catch (Exception exception)
            {
                _log?.LogWarning($"Could not delete previous visual round: {exception.Message}");
            }
        }

        AppendVisualEvent(JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["event"] = "meeting_closed",
            ["sessionId"] = _sessionId,
            ["roundId"] = completedRound,
            ["monotonicMs"] = now,
            ["screenshotCount"] = screenshotCount,
            ["deleted"] = deleted,
            ["directory"] = completedDirectory
        }, _jsonOptions));
        MemoryRepositories.DeleteRound(completedRound);
        _visualRoundId++;
        StartVisualRound();
        MemoryRepositories.StartRound(_visualRoundId);
    }

    private static Color32[] BuildVisualSignature(Color32[] pixels, int width, int height)
    {
        const int columns = 64;
        const int rows = 36;
        var signature = new Color32[columns * rows];
        for (var row = 0; row < rows; row++)
        {
            var sourceY = Math.Min(
                height - 1,
                Math.Max(0, (int)((row + 0.5) * height / rows)));
            for (var column = 0; column < columns; column++)
            {
                var sourceX = Math.Min(
                    width - 1,
                    Math.Max(0, (int)((column + 0.5) * width / columns)));
                signature[row * columns + column] = pixels[sourceY * width + sourceX];
            }
        }
        return signature;
    }

    private static (double ChangedRatio, double MeanDelta) CompareVisualSignatures(
        Color32[]? previous,
        Color32[] current)
    {
        if (previous == null || previous.Length != current.Length)
        {
            return (1.0, double.MaxValue);
        }

        long absoluteDelta = 0;
        var changedCells = 0;
        for (var index = 0; index < current.Length; index++)
        {
            var delta =
                Math.Abs(current[index].r - previous[index].r) +
                Math.Abs(current[index].g - previous[index].g) +
                Math.Abs(current[index].b - previous[index].b);
            absoluteDelta += delta;
            if (delta >= 45)
            {
                changedCells++;
            }
        }

        return (
            changedCells / (double)current.Length,
            absoluteDelta / (double)(current.Length * 3));
    }

    private void AppendVisualEvent(string json)
    {
        if (_visualLogPath == null)
        {
            return;
        }

        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_visualLogPath)!);
            File.AppendAllText(
                _visualLogPath,
                json + Environment.NewLine,
                new System.Text.UTF8Encoding(false));
        }
        catch (Exception exception)
        {
            _log?.LogWarning($"Could not append visual event: {exception.Message}");
        }
    }

    private void OnApplicationQuit()
    {
        ShutdownProbe("application_quit");
    }

    private void OnDestroy()
    {
        ShutdownProbe("component_destroyed");
    }

    private void ShutdownProbe(string trigger)
    {
        if (_shutdownCompleted)
        {
            return;
        }
        _shutdownCompleted = true;

        try
        {
            AppendVisualEvent(JsonSerializer.Serialize(new Dictionary<string, object?>
            {
                ["event"] = "session_closed",
                ["sessionId"] = _sessionId,
                ["roundId"] = _visualRoundId,
                ["trigger"] = trigger
            }, _jsonOptions));
            FinishReactorCapture(
                _clock.ElapsedMilliseconds,
                "session_shutdown");
            DeleteSessionScreenshots();
            MemoryRepositories.Shutdown(
                TelemetryProbePlugin.DeleteGameMemoryAtEnd?.Value ?? true);
            _jsonlWriter?.Flush();
            _jsonlWriter?.Dispose();
        }
        catch (Exception exception)
        {
            _log?.LogWarning($"Probe shutdown failed: {exception}");
        }
    }

    private void DeleteSessionScreenshots()
    {
        if (_visualSessionRoot == null ||
            _outputDirectory == null ||
            TelemetryProbePlugin.VisualDeleteOnSessionEnd?.Value != true)
        {
            return;
        }

        try
        {
            var visualRoot = Path.GetFullPath(
                Path.Combine(_outputDirectory, "visual"))
                .TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            var target = Path.GetFullPath(_visualSessionRoot);
            if (!target.StartsWith(visualRoot, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Refusing to delete screenshots outside the visual root.");
            }
            if (!Directory.Exists(target))
            {
                return;
            }

            var deleted = 0;
            foreach (var path in Directory.EnumerateFiles(
                         target,
                         "*.png",
                         SearchOption.AllDirectories))
            {
                File.Delete(path);
                deleted++;
            }
            _log?.LogInfo(
                $"Deleted {deleted} session screenshots on shutdown");
        }
        catch (Exception exception)
        {
            _log?.LogWarning(
                $"Could not delete session screenshots: {exception.Message}");
        }
    }

    private void WriteLatestAtomic(string json)
    {
        if (_outputDirectory == null)
        {
            return;
        }

        var latestPath = Path.Combine(_outputDirectory, "latest.json");
        AtomicFile.WriteAllText(latestPath, json);
    }

    private void WriteTelemetryLine(string json)
    {
        var encoding = new System.Text.UTF8Encoding(false);
        var lineBytes = encoding.GetByteCount(json) + encoding.GetByteCount(Environment.NewLine);
        if (_jsonlBytes > 0 && _jsonlBytes + lineBytes > MaxTelemetryFileBytes)
        {
            RotateTelemetryLog();
        }

        _jsonlWriter?.WriteLine(json);
        _jsonlBytes += lineBytes;
    }

    private void OpenJsonlWriter(FileMode mode)
    {
        if (_jsonlPath == null)
        {
            throw new InvalidOperationException("Telemetry log path is not initialized.");
        }

        _jsonlWriter = new StreamWriter(
            new FileStream(_jsonlPath, mode, FileAccess.Write, FileShare.Read),
            new System.Text.UTF8Encoding(false))
        {
            AutoFlush = false
        };
        _jsonlBytes = _jsonlWriter.BaseStream.Length;
    }

    private void RotateTelemetryLog()
    {
        if (_jsonlPath == null)
        {
            return;
        }

        _jsonlWriter?.Flush();
        _jsonlWriter?.Dispose();
        _jsonlWriter = null;

        var oldestArchive = ArchivePath(MaxTelemetryArchives);
        if (File.Exists(oldestArchive))
        {
            File.Delete(oldestArchive);
        }

        for (var index = MaxTelemetryArchives - 1; index >= 1; index--)
        {
            var source = ArchivePath(index);
            if (File.Exists(source))
            {
                File.Move(source, ArchivePath(index + 1), true);
            }
        }

        File.Move(_jsonlPath, ArchivePath(1), true);
        OpenJsonlWriter(FileMode.Create);
        _log?.LogInfo($"Rotated telemetry log after reaching {MaxTelemetryFileBytes} bytes");
    }

    private string ArchivePath(int index)
    {
        var directory = Path.GetDirectoryName(_jsonlPath)
            ?? throw new InvalidOperationException("Telemetry log directory is unavailable.");
        return Path.Combine(directory, $"telemetry.{index}.jsonl");
    }

    private void TrimOversizedTelemetryLog(string path)
    {
        if (!File.Exists(path) || new FileInfo(path).Length <= MaxTelemetryFileBytes)
        {
            return;
        }

        var originalBytes = new FileInfo(path).Length;
        var temporaryPath = path + ".trim";
        using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var output = new FileStream(temporaryPath, FileMode.Create, FileAccess.Write, FileShare.None))
        {
            input.Seek(-MaxTelemetryFileBytes, SeekOrigin.End);
            while (input.ReadByte() is var nextByte && nextByte >= 0 && nextByte != '\n')
            {
            }

            input.CopyTo(output);
            output.Flush(true);
        }

        File.Move(temporaryPath, path, true);
        _log?.LogWarning(
            $"Trimmed oversized telemetry log from {originalBytes} to at most {MaxTelemetryFileBytes} bytes");
    }
}

internal static class TelemetryCapture
{
    internal static string CurrentSessionId { get; private set; } = string.Empty;
    private const int NearbyBodyScanMs = 250;
    private static long _nextNearbyBodyScanMs;
    private static List<NearbyBodyInfo> _nearbyBodyCache = new();

    public static TelemetrySample Build(
        long monotonicMs,
        string sessionId,
        long seq,
        int errorCount,
        string? lastError)
    {
        CurrentSessionId = sessionId;
        var localPlayer = PlayerControl.LocalPlayer;
        var ship = ShipStatus.Instance;
        var gameManager = GameManager.Instance;
        var hud = HudManager.Instance;
        var meeting = MeetingHud.Instance;
        var minigame = Minigame.Instance;

        var inGame = localPlayer != null && ship != null;
        var nearbyBodies = ReadNearbyBodies(localPlayer, monotonicMs);
        var nearbyPlayers = ReadNearbyPlayers(localPlayer, monotonicMs);
        var localData = localPlayer?.Data;
        var localOutfit = localData?.DefaultOutfit;
        var localColorId = localOutfit?.ColorId ?? -1;
        var localColorName = string.Empty;
        if (localColorId >= 0)
        {
            try
            {
                localColorName = Palette.GetColorName(localColorId) ?? string.Empty;
            }
            catch
            {
                localColorName = string.Empty;
            }
        }
        return new TelemetrySample
        {
            SchemaVersion = "1.0.0",
            Kind = "telemetry",
            SessionId = sessionId,
            Seq = seq,
            MonotonicMs = monotonicMs,
            SentAtUtc = DateTimeOffset.UtcNow,
            Probe = new ProbeInfo
            {
                Name = TelemetryProbePlugin.PluginName,
                Version = TelemetryProbePlugin.PluginVersion,
                ReadOnly = true,
                SampleIntervalMs = 25,
                ErrorCount = errorCount,
                LastError = lastError
            },
            Game = new GameInfo
            {
                Build = "steam-25134893",
                MapId = ship == null ? "UNKNOWN" : CanonicalMapId(ship.Type.ToString()),
                MapRevision = "raw-steam-25134893",
                Phase = meeting != null
                    ? "MEETING"
                    : inGame
                        ? "TASKS"
                        : "UNKNOWN",
                MatchId = TaskStartSignalPatch.SignalVersion,
                GameHasStarted = gameManager != null && gameManager.GameHasStarted
            },
            LocalPlayer = new LocalPlayerInfo
            {
                PlayerId = localData?.PlayerId ?? byte.MaxValue,
                PlayerName = localData?.PlayerName ?? string.Empty,
                ColorId = localColorId,
                ColorName = localColorName,
                ColorKey = CanonicalColorKey(localColorId),
                Position = ReadPosition(localPlayer),
                RenderPosition = ReadRenderPosition(localPlayer),
                Velocity = ReadVelocity(localPlayer),
                Facing = localPlayer == null
                    ? "unknown"
                    : localPlayer.MyPhysics != null && localPlayer.MyPhysics.FlipX
                        ? "left"
                        : "right",
                PoseKind = inGame ? "local_net" : "uncalibrated",
                SampleAgeMs = 0,
                Confidence = inGame ? 1.0 : 0.0,
                CanMove = localPlayer != null && localPlayer.CanMove,
                InVent = localPlayer != null && localPlayer.inVent
            },
            VentRole = VentControl.ReadRoleInfo(),
            VentTravel = VentTransport.Capture(),
            OwnRole = ImpostorControl.Capture(),
            ImpostorAction = ImpostorControl.LastAction,
            ActiveEmergencies = EmergencyObservation.Active(),
            Sabotage = SabotageControl.Capture(),
            Vents = VentControl.Capture(),
            Ui = new UiInfo
            {
                ReportButton = ReadActionButton(hud?.ReportButton),
                TaskButton = ReadActionButton(hud?.UseButton),
                TaskPanelOpen = IsActive(hud?.TaskPanel),
                TaskPanelText = ReadTaskPanelText(hud?.TaskPanel?.taskText),
                MinigameOpen = minigame != null,
                OpenMinigameTaskId = minigame?.MyTask?.Id,
                OpenMinigameTaskType = EmergencyObservation.WindowTaskType(minigame),
                OpenMinigameInstanceId = minigame?.GetInstanceID(),
                UnlockManifoldsOrder = ReadMinigameSafely(
                    () => ReadUnlockManifoldsOrder(minigame),
                    Array.Empty<int>(),
                    "unlock order"),
                UnlockManifoldsButtons = ReadMinigameSafely(
                    () => ReadUnlockManifoldsButtons(minigame),
                    new List<UnlockManifoldButtonInfo>(),
                    "unlock buttons"),
                InspectSampleButtons = ReadMinigameSafely(
                    () => ReadNamedMinigamePoints(
                        minigame,
                        "medBay_sampleButton",
                        5),
                    new List<IndexedScreenPointInfo>(),
                    "inspect sample buttons"),
                InspectSampleTubes = ReadMinigameSafely(
                    () => ReadNamedMinigamePoints(
                        minigame,
                        "medBay_liquid",
                        5),
                    new List<IndexedScreenPointInfo>(),
                    "inspect sample tubes"),
                UploadButton = ReadMinigameSafely<ScreenPointInfo?>(
                    () => ReadNamedMinigamePoint(
                        minigame,
                        "dataTransfer_uploadButton"),
                    null,
                    "upload button"),
                UploadProgressText = ReadMinigameSafely(
                    () => ReadNamedMinigameText(
                        minigame,
                        "PercentTextt_TMP"),
                    string.Empty,
                    "upload progress"),
                UploadStatusText = ReadMinigameSafely(
                    () => ReadNamedMinigameText(
                        minigame,
                        "Status"),
                    string.Empty,
                    "upload status"),
                VentCleaningVentOpen = ReadMinigameSafely(
                    () => ReadVentCleaningState(minigame).Open,
                    false,
                    "vent cleaning open"),
                VentCleaningDirtCount = ReadMinigameSafely(
                    () => ReadVentCleaningState(minigame).DirtCount,
                    0,
                    "vent cleaning dirt count"),
                VentCleaningCleanedCount = ReadMinigameSafely(
                    () => ReadVentCleaningState(minigame).CleanedCount,
                    0,
                    "vent cleaning cleaned count"),
                VentCleaningDirtPositions = ReadMinigameSafely(
                    () => ReadVentCleaningDirtPositions(minigame),
                    new List<ScreenPointInfo>(),
                    "vent cleaning dirt positions"),
                VentCleaningBounds = ReadMinigameSafely(
                    () => ReadVentCleaningBounds(minigame),
                    new List<ScreenPointInfo>(),
                    "vent cleaning bounds"),
                StartReactorSequence = ReadMinigameSafely(
                    () => ReadStartReactorSequence(minigame),
                    Array.Empty<int>(),
                    "reactor sequence"),
                StartReactorLitIndices = ReadMinigameSafely(
                    () => ReadStartReactorLitIndices(minigame),
                    Array.Empty<int>(),
                    "reactor lit sequence"),
                StartReactorSideColors = ReadMinigameSafely(
                    () => ReadStartReactorColors(
                        minigame,
                        useLeftLights: false),
                    new List<IndexedColorInfo>(),
                    "reactor side colors"),
                StartReactorIndicatorColors = ReadMinigameSafely(
                    () => ReadStartReactorColors(
                        minigame,
                        useLeftLights: true),
                    new List<IndexedColorInfo>(),
                    "reactor indicator colors"),
                StartReactorOrderedButtons = ReadMinigameSafely(
                    () => ReadStartReactorOrderedButtons(minigame),
                    Array.Empty<int>(),
                    "reactor button order"),
                StartReactorButtons = ReadMinigameSafely(
                    () => ReadStartReactorButtons(minigame),
                    new List<ScreenPointInfo>(),
                    "reactor buttons"),
                StartReactorAnimating = ReadMinigameSafely(
                    () => ReadStartReactorAnimating(minigame),
                    false,
                    "reactor animation"),
                MeetingOpen = meeting != null,
                ChatOpen = hud != null && hud.Chat != null && hud.Chat.IsOpenOrOpening,
                MapButtonVisible = IsActive(hud?.MapButton),
                FullScreenOverlay = hud != null && hud.FullScreen != null && hud.FullScreen.gameObject.activeInHierarchy,
                GameEnded = false,
                CanMove = localPlayer != null && localPlayer.CanMove,
                OwnTasks = ReadOwnTasks(localPlayer),
                TaskCompletions = TaskCompletionMemory.Capture(),
                TaskCompletionDiagnostics = TaskCompletionMemory.Diagnostics()
            },
            NearbyBodies = nearbyBodies,
            NearbyPlayers = nearbyPlayers,
            PlayerDetection = _playerDetection,
            RecentPlayers = PlayerAwareness.Current.RecentPlayers,
            CrimeScenes = CrimeSceneMemory.Capture(),
            Privacy = new PrivacyInfo
            {
                RemotePlayerDataIncluded = nearbyPlayers.Count > 0,
                HiddenGameStateIncluded = false
            }
        };
    }

    internal static T ReadMinigameSafely<T>(
        Func<T> reader,
        T fallback,
        string label)
    {
        try
        {
            return reader();
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Minigame telemetry {label} unavailable: " +
                exception.Message);
            return fallback;
        }
    }

    private static int[] ReadUnlockManifoldsOrder(Minigame? minigame)
    {
        var unlock = minigame?.TryCast<UnlockManifoldsMinigame>();
        var buttons = unlock?.Buttons;
        if (buttons == null || buttons.Length != 10)
        {
            return Array.Empty<int>();
        }

        var order = new int[buttons.Length];
        for (var index = 0; index < buttons.Length; index++)
        {
            var number = ReadUnlockManifoldsNumber(buttons[index]);
            if (number == 0)
            {
                return Array.Empty<int>();
            }
            order[index] = number;
        }
        return order;
    }

    private static List<UnlockManifoldButtonInfo> ReadUnlockManifoldsButtons(
        Minigame? minigame)
    {
        var unlock = minigame?.TryCast<UnlockManifoldsMinigame>();
        var buttons = unlock?.Buttons;
        var camera = Camera.main;
        if (buttons == null ||
            camera == null ||
            buttons.Length != 10)
        {
            return new List<UnlockManifoldButtonInfo>();
        }

        var output = new List<UnlockManifoldButtonInfo>(buttons.Length);
        var seenNumbers = new HashSet<int>();
        foreach (var button in buttons)
        {
            if (button == null)
            {
                return new List<UnlockManifoldButtonInfo>();
            }

            var number = ReadUnlockManifoldsNumber(button);
            if (number == 0 || !seenNumbers.Add(number))
            {
                return new List<UnlockManifoldButtonInfo>();
            }

            var screen = camera.WorldToScreenPoint(button.transform.position);
            output.Add(new UnlockManifoldButtonInfo
            {
                Number = number,
                X = screen.x * 1920f / Math.Max(1, Screen.width),
                Y = (Screen.height - screen.y) * 1080f /
                    Math.Max(1, Screen.height)
            });
        }

        if (seenNumbers.Count != 10 ||
            seenNumbers.Any(number => number < 1 || number > 10))
        {
            return new List<UnlockManifoldButtonInfo>();
        }
        return output;
    }

    private static int ReadUnlockManifoldsNumber(
        SpriteRenderer? button)
    {
        var sprite = button == null ? null : button.sprite;
        var name = sprite == null ? string.Empty : sprite.name;
        var digits = new string(name.Where(char.IsDigit).ToArray());
        if (!int.TryParse(digits, out var number) ||
            number < 1 ||
            number > 10)
        {
            return 0;
        }
        return number;
    }

    private static List<IndexedScreenPointInfo> ReadNamedMinigamePoints(
        Minigame? minigame,
        string objectNamePrefix,
        int expectedCount)
    {
        var camera = Camera.main;
        if (minigame == null || camera == null || expectedCount <= 0)
        {
            return new List<IndexedScreenPointInfo>();
        }

        var points = new SortedDictionary<int, IndexedScreenPointInfo>();
        var transforms = minigame.gameObject.GetComponentsInChildren<Transform>(
            true);
        foreach (var child in transforms)
        {
            if (child == null)
            {
                continue;
            }
            var index = ParseIndexedObjectName(
                child.name,
                objectNamePrefix);
            if (index < 0 || index >= expectedCount)
            {
                continue;
            }

            var screen = camera.WorldToScreenPoint(child.position);
            points[index] = new IndexedScreenPointInfo
            {
                Index = index,
                X = screen.x * 1920f / Math.Max(1, Screen.width),
                Y = (Screen.height - screen.y) * 1080f /
                    Math.Max(1, Screen.height)
            };
        }

        if (points.Count != expectedCount)
        {
            return new List<IndexedScreenPointInfo>();
        }
        return points.Values.ToList();
    }

    private static ScreenPointInfo? ReadNamedMinigamePoint(
        Minigame? minigame,
        string objectName)
    {
        var camera = Camera.main;
        if (minigame == null || camera == null)
        {
            return null;
        }

        var transforms = minigame.gameObject.GetComponentsInChildren<Transform>(
            true);
        var target = transforms.FirstOrDefault(
            child => string.Equals(
                child.name,
                objectName,
                StringComparison.Ordinal));
        if (target == null)
        {
            return null;
        }

        var screen = camera.WorldToScreenPoint(target.position);
        return new ScreenPointInfo
        {
            X = screen.x * 1920f / Math.Max(1, Screen.width),
            Y = (Screen.height - screen.y) * 1080f /
                Math.Max(1, Screen.height)
        };
    }

    private static string ReadNamedMinigameText(
        Minigame? minigame,
        string objectName)
    {
        if (minigame == null)
        {
            return string.Empty;
        }

        var labels = minigame.gameObject
            .GetComponentsInChildren<TMPro.TMP_Text>(true);
        var target = labels.FirstOrDefault(
            label => string.Equals(
                label.name,
                objectName,
                StringComparison.Ordinal));
        return target == null ? string.Empty : target.text;
    }

    private static VentCleaningState ReadVentCleaningState(
        Minigame? minigame)
    {
        var vent = minigame?.TryCast<VentCleaningMinigame>();
        if (vent == null)
        {
            return new VentCleaningState();
        }
        return new VentCleaningState
        {
            Open = vent.ventOpen,
            DirtCount = vent.numberOfDirts,
            CleanedCount = vent.numberOfDirtsCleanedUp
        };
    }

    private static List<ScreenPointInfo> ReadVentCleaningDirtPositions(
        Minigame? minigame)
    {
        var vent = minigame?.TryCast<VentCleaningMinigame>();
        var active = vent?.dirtPool?.activeChildren;
        var camera = Camera.main;
        if (vent == null || active == null || camera == null)
        {
            return new List<ScreenPointInfo>();
        }

        var output = new List<ScreenPointInfo>(active.Count);
        foreach (var child in active)
        {
            var dirt = child?.TryCast<VentDirt>();
            if (dirt == null)
            {
                continue;
            }
            var screen = camera.WorldToScreenPoint(dirt.transform.position);
            output.Add(new ScreenPointInfo
            {
                X = screen.x * 1920f / Math.Max(1, Screen.width),
                Y = (Screen.height - screen.y) * 1080f /
                    Math.Max(1, Screen.height)
            });
        }
        return output;
    }

    private static List<ScreenPointInfo> ReadVentCleaningBounds(
        Minigame? minigame)
    {
        var vent = minigame?.TryCast<VentCleaningMinigame>();
        var camera = Camera.main;
        if (vent == null ||
            vent.XSpan == null ||
            vent.YSpan == null ||
            camera == null)
        {
            return new List<ScreenPointInfo>();
        }

        var output = new List<ScreenPointInfo>(4);
        foreach (var point in new[]
                 {
                     new Vector2(vent.XSpan.min, vent.YSpan.min),
                     new Vector2(vent.XSpan.max, vent.YSpan.min),
                     new Vector2(vent.XSpan.max, vent.YSpan.max),
                     new Vector2(vent.XSpan.min, vent.YSpan.max)
                 })
        {
            var world = vent.transform.TransformPoint(point);
            var screen = camera.WorldToScreenPoint(world);
            output.Add(new ScreenPointInfo
            {
                X = screen.x * 1920f / Math.Max(1, Screen.width),
                Y = (Screen.height - screen.y) * 1080f /
                    Math.Max(1, Screen.height)
            });
        }
        return output;
    }

    private static int ParseIndexedObjectName(
        string name,
        string prefix)
    {
        if (string.Equals(name, prefix, StringComparison.Ordinal))
        {
            return 0;
        }
        var indexedPrefix = prefix + " (";
        if (!name.StartsWith(indexedPrefix, StringComparison.Ordinal) ||
            !name.EndsWith(")", StringComparison.Ordinal))
        {
            return -1;
        }

        var numberText = name.Substring(
            indexedPrefix.Length,
            name.Length - indexedPrefix.Length - 1);
        return int.TryParse(numberText, out var index)
            ? index
            : -1;
    }

    internal static int[] ReadStartReactorSequence(Minigame? minigame)
    {
        var reactor = minigame?.TryCast<SimonSaysGame>();
        var operations = reactor?.operations;
        if (operations == null || operations.Count == 0)
        {
            return Array.Empty<int>();
        }
        return operations.ToArray();
    }

    internal static int[] ReadStartReactorLitIndices(
        Minigame? minigame)
    {
        var reactor = minigame?.TryCast<SimonSaysGame>();
        var sides = reactor?.LeftSide;
        if (reactor == null || sides == null || sides.Length == 0)
        {
            return Array.Empty<int>();
        }

        var active = reactor.blue;
        var output = new List<int>();
        for (var index = 0; index < sides.Length; index++)
        {
            var side = sides[index];
            if (side == null)
            {
                continue;
            }
            var color = side.color;
            if (Math.Abs(color.r - active.r) <= 0.12f &&
                Math.Abs(color.g - active.g) <= 0.12f &&
                Math.Abs(color.b - active.b) <= 0.12f)
            {
                output.Add(index);
            }
        }
        return output.ToArray();
    }

    private static List<IndexedColorInfo> ReadStartReactorColors(
        Minigame? minigame,
        bool useLeftLights)
    {
        var reactor = minigame?.TryCast<SimonSaysGame>();
        var renderers = useLeftLights
            ? reactor?.LeftLights
            : reactor?.LeftSide;
        if (renderers == null)
        {
            return new List<IndexedColorInfo>();
        }

        var output = new List<IndexedColorInfo>(renderers.Length);
        for (var index = 0; index < renderers.Length; index++)
        {
            var renderer = renderers[index];
            if (renderer == null)
            {
                continue;
            }
            var color = renderer.color;
            output.Add(new IndexedColorInfo
            {
                Index = index,
                R = color.r,
                G = color.g,
                B = color.b,
                A = color.a
            });
        }
        return output;
    }

    internal static int[] ReadStartReactorOrderedButtons(
        Minigame? minigame)
    {
        var reactor = minigame?.TryCast<SimonSaysGame>();
        var ordered = reactor?.orderedButtonIndices;
        return ordered == null
            ? Array.Empty<int>()
            : ordered.ToArray();
    }

    private static List<ScreenPointInfo> ReadStartReactorButtons(
        Minigame? minigame)
    {
        var reactor = minigame?.TryCast<SimonSaysGame>();
        var buttons = reactor?.Buttons;
        var camera = Camera.main;
        if (buttons == null || camera == null || buttons.Length != 9)
        {
            return new List<ScreenPointInfo>();
        }

        var output = new List<ScreenPointInfo>(buttons.Length);
        foreach (var button in buttons)
        {
            if (button == null)
            {
                output.Add(new ScreenPointInfo());
                continue;
            }
            var screen = camera.WorldToScreenPoint(button.transform.position);
            output.Add(new ScreenPointInfo
            {
                X = screen.x * 1920f / Math.Max(1, Screen.width),
                Y = (Screen.height - screen.y) * 1080f /
                    Math.Max(1, Screen.height)
            });
        }
        return output;
    }

    private static bool ReadStartReactorAnimating(Minigame? minigame)
    {
        return ReadStartReactorLitIndices(minigame).Length > 0;
    }

    private static List<NearbyBodyInfo> ReadNearbyBodies(
        PlayerControl? localPlayer,
        long monotonicMs)
    {
        if (monotonicMs < _nextNearbyBodyScanMs)
        {
            return _nearbyBodyCache;
        }
        _nextNearbyBodyScanMs = monotonicMs + NearbyBodyScanMs;

        var output = new List<NearbyBodyInfo>();
        try
        {
            if (localPlayer == null ||
                localPlayer.Data == null ||
                localPlayer.Data.IsDead)
            {
                _nearbyBodyCache = output;
                return output;
            }

            var pose = localPlayer.GetTruePosition();
            var maxDistance = Math.Max(
                0.5f,
                TelemetryProbePlugin.BodyDetectionRadius?.Value ?? 6f);
            var camera = Camera.main;
            var reportCanInteract = HudManager.Instance?.ReportButton != null &&
                HudManager.Instance.ReportButton.CanInteract();
            foreach (var body in UnityEngine.Object.FindObjectsOfType<DeadBody>())
            {
                if (body == null || body.Reported)
                {
                    continue;
                }

                var gameObject = body.gameObject;
                if (gameObject == null || !gameObject.activeInHierarchy)
                {
                    continue;
                }

                var position = body.TruePosition;
                var distance = Vector2.Distance(pose, position);
                if (distance > maxDistance)
                {
                    continue;
                }

                output.Add(new NearbyBodyInfo
                {
                    Position = new PositionInfo
                    {
                        Space = "unity_world",
                        X = position.x,
                        Y = position.y
                    },
                    Distance = distance,
                    Reported = body.Reported,
                    InCameraView = IsWorldPositionInView(
                        camera,
                        body.transform.position),
                    ReportButtonCanInteract = reportCanInteract
                });
            }
        }
        catch
        {
            return _nearbyBodyCache;
        }

        _nearbyBodyCache = output
            .OrderBy(body => body.Distance)
            .Take(8)
            .ToList();
        return _nearbyBodyCache;
    }

    private static PlayerDetectionInfo _playerDetection = new();

    private static List<NearbyPlayerInfo> ReadNearbyPlayers(PlayerControl? localPlayer, long monotonicMs)
    {
        var frame = PlayerAwareness.Scan();
        _playerDetection = frame.Diagnostics;
        return frame.Players.OrderBy(p => p.Distance).Take(20).Select(observation =>
        {
            var player = observation.Player;
            var velocity = observation.Velocity;
            var colorId = player.Data.DefaultOutfit?.ColorId ?? -1;
            var colorName = string.Empty;
            try { if (colorId >= 0) colorName = Palette.GetColorName(colorId) ?? string.Empty; } catch { }
            return new NearbyPlayerInfo
            {
                PlayerId = player.PlayerId, PlayerName = player.Data.PlayerName,
                ColorId = colorId, ColorName = colorName, ColorKey = CanonicalColorKey(colorId),
                X = observation.Position.x, Y = observation.Position.y,
                VelocityX = velocity.x, VelocityY = velocity.y, Distance = observation.Distance,
                InCameraView = observation.InCameraView,
                ObservationSource = observation.InCameraView ? "camera" : "camera_margin",
                Direction = Math.Abs(velocity.x) >= Math.Abs(velocity.y)
                    ? velocity.x > 0.15f ? "right" : velocity.x < -0.15f ? "left" : "stationary"
                    : velocity.y > 0.15f ? "up" : velocity.y < -0.15f ? "down" : "stationary",
                Facing = velocity.x < -0.15f ? "left" : velocity.x > 0.15f ? "right" : "unknown"
            };
        }).ToList();
    }

    private static bool IsWorldPositionInView(Camera? camera, Vector3 position)
    {
        if (camera == null)
        {
            return true;
        }

        var viewport = camera.WorldToViewportPoint(position);
        return viewport.z > 0f &&
            viewport.x >= -0.05f &&
            viewport.x <= 1.05f &&
            viewport.y >= -0.05f &&
            viewport.y <= 1.05f;
    }

    private static PositionInfo ReadPosition(PlayerControl? player)
    {
        if (player == null)
        {
            return new PositionInfo { Space = "uncalibrated", X = 0, Y = 0 };
        }

        try
        {
            var value = player.GetTruePosition();
            return new PositionInfo { Space = "unity_world", X = value.x, Y = value.y };
        }
        catch
        {
            var value = player.transform.position;
            return new PositionInfo { Space = "uncalibrated", X = value.x, Y = value.y };
        }
    }

    private static PositionInfo? ReadRenderPosition(PlayerControl? player)
    {
        if (player == null)
        {
            return null;
        }

        var value = player.transform.position;
        return new PositionInfo { Space = "unity_world", X = value.x, Y = value.y };
    }

    private static VelocityInfo ReadVelocity(PlayerControl? player)
    {
        if (player == null || player.MyPhysics == null)
        {
            return new VelocityInfo { X = 0, Y = 0 };
        }

        var value = player.MyPhysics.GetVelocity();
        return new VelocityInfo { X = value.x, Y = value.y };
    }

    internal static List<OwnTaskInfo> ReadOwnTasks(PlayerControl? player)
    {
        var output = new List<OwnTaskInfo>();
        if (player == null || player.myTasks == null)
        {
            return output;
        }

        var count = Math.Min(player.myTasks.Count, 20);
        for (var index = 0; index < count; index++)
        {
            var task = player.myTasks[index];
            if (task == null)
            {
                continue;
            }

            output.Add(DescribeOwnTask(task));
        }

        return output;
    }

    internal static OwnTaskInfo DescribeOwnTask(PlayerTask task) => new()
    {
        TaskId = task.Id,
        TaskType = task.TaskType.ToString(),
        TaskInstanceId = TaskCompletionMemory.InstanceId(task),
        Step = task.TaskStep,
        StartAt = task.StartAt.ToString(),
        MaxStep = task.TryCast<NormalPlayerTask>()?.MaxStep ?? 0,
        Locations = ReadTaskLocations(task),
        Complete = task.IsComplete,
        Sample = SampleTaskControl.Capture(task),
        Confidence = 1.0
    };

    private static string ReadTaskPanelText(TMPro.TextMeshPro? text)
    {
        try
        {
            return text?.text?.Trim() ?? string.Empty;
        }
        catch
        {
            return string.Empty;
        }
    }

    private static List<PositionInfo> ReadTaskLocations(PlayerTask task)
    {
        var output = new List<PositionInfo>();
        try
        {
            var locations = task.Locations;
            if (locations == null)
            {
                return output;
            }

            var count = Math.Min(locations.Count, 8);
            for (var index = 0; index < count; index++)
            {
                var position = locations[index];
                output.Add(new PositionInfo
                {
                    Space = "unity_world",
                    X = position.x,
                    Y = position.y
                });
            }
        }
        catch
        {
        }

        return output;
    }

    private static bool IsActive(Component? component)
    {
        return component != null && component.gameObject.activeInHierarchy;
    }

    private static VisibilityInfo ReadActionButton(ActionButton? button)
    {
        return new VisibilityInfo
        {
            Visible = button != null && button.gameObject.activeInHierarchy,
            CanInteract = button != null && button.canInteract
        };
    }

    private static string CanonicalMapId(string unityMapType)
    {
        return unityMapType switch
        {
            "Ship" => "TheSkeld",
            "HQ" => "MiraHQ",
            "Pb" => "Polus",
            "Airship" => "Airship",
            "Fungle" => "Fungle",
            _ => unityMapType
        };
    }

    private static string CanonicalColorKey(int colorId)
    {
        return colorId switch
        {
            0 => "Red",
            1 => "Blue",
            2 => "Green",
            3 => "Pink",
            4 => "Orange",
            5 => "Yellow",
            6 => "Black",
            7 => "White",
            8 => "Purple",
            9 => "Brown",
            10 => "Cyan",
            11 => "Lime",
            12 => "Maroon",
            13 => "Rose",
            14 => "Banana",
            15 => "Gray",
            16 => "Tan",
            17 => "Coral",
            18 => "Fortegreen",
            _ => "Unknown"
        };
    }
}

internal sealed class SkillStatusDocument
{
    public string SchemaVersion { get; init; } = string.Empty;
    public string SkillId { get; init; } = string.Empty;
    public DateTimeOffset UpdatedAtUtc { get; init; }
    public string State { get; init; } = string.Empty;
    public string Message { get; init; } = string.Empty;
    public int Round { get; init; }
    public int TotalRounds { get; init; }
    public int[] Sequence { get; init; } = Array.Empty<int>();
    public int[] LitButtons { get; init; } = Array.Empty<int>();
    public string Backend { get; init; } = string.Empty;
    public string Error { get; init; } = string.Empty;
}

internal sealed class TelemetrySample
{
    public string SchemaVersion { get; init; } = string.Empty;
    public string Kind { get; init; } = string.Empty;
    public string SessionId { get; init; } = string.Empty;
    public long Seq { get; init; }
    public long MonotonicMs { get; init; }
    public DateTimeOffset SentAtUtc { get; init; }
    public ProbeInfo Probe { get; init; } = null!;
    public GameInfo Game { get; init; } = null!;
    public LocalPlayerInfo LocalPlayer { get; init; } = null!;
    public VentRoleInfo VentRole { get; init; } = new();
    public VentTravelInfo VentTravel { get; init; } = new();
    public OwnRoleInfo OwnRole { get; init; } = new();
    public ImpostorActionInfo ImpostorAction { get; init; } = new();
    public List<string>? ActiveEmergencies { get; init; }
    public SabotageInfo Sabotage { get; init; } = new();
    public List<VentInfo> Vents { get; init; } = new();
    public UiInfo Ui { get; init; } = null!;
    public List<NearbyBodyInfo> NearbyBodies { get; init; } = new();
    public List<NearbyPlayerInfo> NearbyPlayers { get; init; } = new();
    public PlayerDetectionInfo PlayerDetection { get; init; } = new();
    public List<RecentPlayerInfo> RecentPlayers { get; init; } = new();
    public List<CrimeSceneInfo> CrimeScenes { get; init; } = new();
    public PrivacyInfo Privacy { get; init; } = null!;
}

public sealed class NearbyPlayerInfo
{
    public bool InCameraView { get; init; }
    public string ObservationSource { get; init; } = "camera";
    public byte PlayerId { get; init; }
    public string PlayerName { get; init; } = string.Empty;
    public int ColorId { get; init; } = -1;
    public string ColorName { get; init; } = string.Empty;
    public string ColorKey { get; init; } = "Unknown";
    public float X { get; init; }
    public float Y { get; init; }
    public float VelocityX { get; init; }
    public float VelocityY { get; init; }
    public float Distance { get; init; }
    public string Direction { get; init; } = "stationary";
    public string Facing { get; init; } = "right";
}

internal sealed class ProbeInfo
{
    public string Name { get; init; } = string.Empty;
    public string Version { get; init; } = string.Empty;
    public bool ReadOnly { get; init; }
    public int SampleIntervalMs { get; init; }
    public int ErrorCount { get; init; }
    public string? LastError { get; init; }
}

internal sealed class GameInfo
{
    public long MatchId { get; init; }
    public string Build { get; init; } = string.Empty;
    public string MapId { get; init; } = string.Empty;
    public string MapRevision { get; init; } = string.Empty;
    public string Phase { get; init; } = string.Empty;
    public bool GameHasStarted { get; init; }
}

internal sealed class LocalPlayerInfo
{
    public byte PlayerId { get; init; } = byte.MaxValue;
    public string PlayerName { get; init; } = string.Empty;
    public int ColorId { get; init; } = -1;
    public string ColorName { get; init; } = string.Empty;
    public string ColorKey { get; init; } = "Unknown";
    public PositionInfo Position { get; init; } = null!;
    public PositionInfo? RenderPosition { get; init; }
    public VelocityInfo Velocity { get; init; } = null!;
    public string Facing { get; init; } = string.Empty;
    public string PoseKind { get; init; } = string.Empty;
    public double SampleAgeMs { get; init; }
    public double Confidence { get; init; }
    public bool CanMove { get; init; }
    public bool InVent { get; init; }
}

internal sealed class PositionInfo
{
    public string Space { get; init; } = string.Empty;
    public float X { get; init; }
    public float Y { get; init; }
}

internal sealed class VelocityInfo
{
    public float X { get; init; }
    public float Y { get; init; }
}

internal sealed class UiInfo
{
    public VisibilityInfo ReportButton { get; init; } = null!;
    public VisibilityInfo TaskButton { get; init; } = null!;
    public bool TaskPanelOpen { get; init; }
    public string TaskPanelText { get; init; } = string.Empty;
    public bool MinigameOpen { get; init; }
    public uint? OpenMinigameTaskId { get; init; }
    public string? OpenMinigameTaskType { get; init; }
    public int? OpenMinigameInstanceId { get; init; }
    public int[] UnlockManifoldsOrder { get; init; } = Array.Empty<int>();
    public List<UnlockManifoldButtonInfo> UnlockManifoldsButtons { get; init; } = new();
    public List<IndexedScreenPointInfo> InspectSampleButtons { get; init; } = new();
    public List<IndexedScreenPointInfo> InspectSampleTubes { get; init; } = new();
    public ScreenPointInfo? UploadButton { get; init; }
    public string UploadProgressText { get; init; } = string.Empty;
    public string UploadStatusText { get; init; } = string.Empty;
    public bool VentCleaningVentOpen { get; init; }
    public int VentCleaningDirtCount { get; init; }
    public int VentCleaningCleanedCount { get; init; }
    public List<ScreenPointInfo> VentCleaningDirtPositions { get; init; } = new();
    public List<ScreenPointInfo> VentCleaningBounds { get; init; } = new();
    public int[] StartReactorSequence { get; init; } = Array.Empty<int>();
    public int[] StartReactorLitIndices { get; init; } = Array.Empty<int>();
    public List<IndexedColorInfo> StartReactorSideColors { get; init; } = new();
    public List<IndexedColorInfo> StartReactorIndicatorColors { get; init; } = new();
    public int[] StartReactorOrderedButtons { get; init; } = Array.Empty<int>();
    public List<ScreenPointInfo> StartReactorButtons { get; init; } = new();
    public bool StartReactorAnimating { get; init; }
    public bool MeetingOpen { get; init; }
    public bool ChatOpen { get; init; }
    public bool MapButtonVisible { get; init; }
    public bool FullScreenOverlay { get; init; }
    public bool GameEnded { get; init; }
    public bool CanMove { get; init; }
    public List<OwnTaskInfo> OwnTasks { get; init; } = null!;
    public List<TaskCompletionInfo> TaskCompletions { get; init; } = new();
    public TaskCompletionDiagnostics TaskCompletionDiagnostics { get; init; } = new();
}

internal sealed class NearbyBodyInfo
{
    public PositionInfo Position { get; init; } = null!;
    public float Distance { get; init; }
    public bool Reported { get; init; }
    public bool InCameraView { get; init; }
    public bool ReportButtonCanInteract { get; init; }
}

internal sealed class ScreenPointInfo
{
    public float X { get; init; }
    public float Y { get; init; }
}

internal sealed class UnlockManifoldButtonInfo
{
    public int Number { get; init; }
    public float X { get; init; }
    public float Y { get; init; }
}

internal sealed class IndexedScreenPointInfo
{
    public int Index { get; init; }
    public float X { get; init; }
    public float Y { get; init; }
}

internal sealed class IndexedColorInfo
{
    public int Index { get; init; }
    public float R { get; init; }
    public float G { get; init; }
    public float B { get; init; }
    public float A { get; init; }
}

internal sealed class VentCleaningState
{
    public bool Open { get; init; }
    public int DirtCount { get; init; }
    public int CleanedCount { get; init; }
}

internal sealed class VisibilityInfo
{
    public bool Visible { get; init; }
    public bool CanInteract { get; init; }
}

internal sealed class OwnTaskInfo
{
    public uint TaskId { get; init; }
    public string TaskInstanceId { get; init; } = string.Empty;
    public string TaskType { get; init; } = string.Empty;
    public int Step { get; init; }
    public string StartAt { get; init; } = string.Empty;
    public int MaxStep { get; init; }
    public List<PositionInfo> Locations { get; init; } = new();
    public bool Complete { get; init; }
    public SampleTaskStatus? Sample { get; init; }
    public double Confidence { get; init; }
}

internal sealed class PrivacyInfo
{
    public bool RemotePlayerDataIncluded { get; init; }
    public bool HiddenGameStateIncluded { get; init; }
}

internal sealed class AnchorOverlayDocument
{
    public List<AnchorOverlayEntry> Anchors { get; init; } = new();
}

internal sealed class AnchorOverlayEntry
{
    public string AnchorId { get; init; } = string.Empty;
    public string? Label { get; init; }
    public string? Room { get; init; }
    public string? Kind { get; init; }
    public PositionInfo? Position { get; init; }
    public AnchorCorrectionInfo? Correction { get; init; }
}

internal sealed class AnchorCorrectionInfo
{
    public string Status { get; init; } = string.Empty;
}

internal static class MemoryRepositories
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    private static bool _enabled;
    private static string? _sessionId;
    private static string? _sessionRoot;
    private static string? _routeRoot;
    private static string? _meetingRoot;
    private static string? _routeDirectory;
    private static string? _meetingDirectory;
    private static string? _meetingTranscriptPath;
    private static StreamWriter? _routeWriter;
    private static ManualLogSource? _log;
    private static int _roundId;
    private static int _meetingId;
    private static int _meetingChatCount;
    private static bool _meetingActive;
    private static long _lastRouteSampleMs = long.MinValue / 2;
    private static float _lastRouteX = float.NaN;
    private static float _lastRouteY = float.NaN;

    public static void Configure(
        string outputDirectory,
        string sessionId,
        ManualLogSource? log)
    {
        _log = log;
        _sessionId = sessionId;
        _enabled = TelemetryProbePlugin.MemoryEnabled?.Value ?? true;
        if (!_enabled)
        {
            log?.LogInfo("Memory repositories disabled");
            return;
        }

        var shortSession = sessionId.Substring(0, Math.Min(8, sessionId.Length));
        _sessionRoot = Path.Combine(outputDirectory, "memory", shortSession);
        _routeRoot = Path.Combine(_sessionRoot, "routes");
        _meetingRoot = Path.Combine(_sessionRoot, "meetings");
        Directory.CreateDirectory(_routeRoot);
        Directory.CreateDirectory(_meetingRoot);
        log?.LogInfo($"Memory repositories: {_sessionRoot}");
    }

    public static void StartRound(int roundId)
    {
        if (!_enabled || _routeRoot == null)
        {
            return;
        }

        CloseRouteWriter();
        _roundId = roundId;
        _routeDirectory = Path.Combine(_routeRoot, $"round-{roundId:D4}");
        Directory.CreateDirectory(_routeDirectory);
        var routePath = Path.Combine(_routeDirectory, "route.jsonl");
        _routeWriter = new StreamWriter(
            new FileStream(routePath, FileMode.Append, FileAccess.Write, FileShare.Read),
            new System.Text.UTF8Encoding(false))
        {
            AutoFlush = false
        };
        _lastRouteSampleMs = long.MinValue / 2;
        _lastRouteX = float.NaN;
        _lastRouteY = float.NaN;
        _log?.LogInfo($"Route memory round {roundId} started");
    }

    public static void RecordRoute(
        long seq,
        long monotonicMs,
        string mapId,
        float x,
        float y,
        float velocityX,
        float velocityY,
        bool canMove)
    {
        if (!_enabled || _routeWriter == null)
        {
            return;
        }

        var interval = Math.Max(
            50,
            TelemetryProbePlugin.RouteMemorySampleIntervalMs?.Value ?? 200);
        var minimumDistance = Math.Max(
            0.0f,
            TelemetryProbePlugin.RouteMemoryMinDistance?.Value ?? 0.04f);
        var moved = float.IsNaN(_lastRouteX)
            ? float.MaxValue
            : MathF.Sqrt(
                MathF.Pow(x - _lastRouteX, 2) +
                MathF.Pow(y - _lastRouteY, 2));
        if (monotonicMs - _lastRouteSampleMs < interval && moved < minimumDistance)
        {
            return;
        }

        var entry = JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["event"] = "route_point",
            ["sessionId"] = _sessionId,
            ["roundId"] = _roundId,
            ["seq"] = seq,
            ["monotonicMs"] = monotonicMs,
            ["capturedAtUtc"] = DateTimeOffset.UtcNow,
            ["mapId"] = mapId,
            ["position"] = new { x, y },
            ["velocity"] = new { x = velocityX, y = velocityY },
            ["canMove"] = canMove
        }, JsonOptions);
        _routeWriter.WriteLine(entry);
        _routeWriter.Flush();
        _lastRouteSampleMs = monotonicMs;
        _lastRouteX = x;
        _lastRouteY = y;
    }

    public static void BeginMeeting(int roundId, long monotonicMs)
    {
        if (!_enabled || _meetingRoot == null || _meetingActive)
        {
            return;
        }

        _meetingId++;
        _meetingActive = true;
        _meetingChatCount = 0;
        _meetingDirectory = Path.Combine(
            _meetingRoot,
            $"meeting-{_meetingId:D4}");
        Directory.CreateDirectory(_meetingDirectory);
        _meetingTranscriptPath = Path.Combine(_meetingDirectory, "transcript.jsonl");
        var conclusionsPath = Path.Combine(_meetingDirectory, "conclusions.jsonl");
        if (!File.Exists(conclusionsPath))
        {
            File.WriteAllText(conclusionsPath, string.Empty);
        }

        AppendMeetingEvent(new Dictionary<string, object?>
        {
            ["event"] = "meeting_started",
            ["meetingId"] = _meetingId,
            ["roundId"] = roundId,
            ["monotonicMs"] = monotonicMs,
            ["gameMeetingCount"] = GameData.MeetingCount
        });
        _log?.LogInfo($"Meeting memory {_meetingId} started");
    }

    public static void RecordChat(PlayerControl? sourcePlayer, string? chatText, bool censor)
    {
        if (!_enabled ||
            !_meetingActive ||
            TelemetryProbePlugin.CaptureMeetingChat?.Value != true ||
            string.IsNullOrWhiteSpace(chatText))
        {
            return;
        }

        var speaker = "SYSTEM";
        var speakerId = -1;
        var isLocalPlayer = false;
        try
        {
            if (sourcePlayer?.Data != null)
            {
                speaker = sourcePlayer.Data.PlayerName;
                speakerId = sourcePlayer.Data.PlayerId;
                var localPlayer = PlayerControl.LocalPlayer;
                isLocalPlayer = localPlayer != null &&
                    sourcePlayer.Pointer == localPlayer.Pointer;
            }
        }
        catch
        {
            speaker = "UNKNOWN";
        }

        _meetingChatCount++;
        AppendMeetingEvent(new Dictionary<string, object?>
        {
            ["event"] = "chat",
            ["meetingId"] = _meetingId,
            ["index"] = _meetingChatCount,
            ["capturedAtUtc"] = DateTimeOffset.UtcNow,
            ["speaker"] = speaker,
            ["speakerId"] = speakerId >= 0 ? speakerId : null,
            ["isLocalPlayer"] = isLocalPlayer,
            ["text"] = chatText,
            ["censor"] = censor
        });
    }

    public static void EndMeeting(long monotonicMs)
    {
        if (!_enabled || !_meetingActive || _meetingDirectory == null)
        {
            return;
        }

        AppendMeetingEvent(new Dictionary<string, object?>
        {
            ["event"] = "meeting_ended",
            ["meetingId"] = _meetingId,
            ["monotonicMs"] = monotonicMs,
            ["chatMessages"] = _meetingChatCount
        });
        var summary = new
        {
            meetingId = _meetingId,
            endedAtUtc = DateTimeOffset.UtcNow,
            chatMessages = _meetingChatCount,
            transcript = _meetingTranscriptPath,
            conclusions = Path.Combine(_meetingDirectory, "conclusions.jsonl"),
            discussionCompressionStatus = "pending"
        };
        File.WriteAllText(
            Path.Combine(_meetingDirectory, "summary.json"),
            JsonSerializer.Serialize(summary, JsonOptions),
            new System.Text.UTF8Encoding(false));
        _meetingActive = false;
        _meetingDirectory = null;
        _meetingTranscriptPath = null;
        _meetingChatCount = 0;
        _log?.LogInfo($"Meeting memory {_meetingId} completed");
    }

    public static void DeleteRound(int roundId)
    {
        if (!_enabled ||
            _routeRoot == null ||
            TelemetryProbePlugin.DeleteRoundMemoryAfterMeeting?.Value != true)
        {
            return;
        }

        if (_roundId == roundId)
        {
            CloseRouteWriter();
        }
        DeleteDirectoryUnderRoot(
            _routeRoot,
            Path.Combine(_routeRoot, $"round-{roundId:D4}"));
    }

    public static void Shutdown(bool deleteSession)
    {
        CloseRouteWriter();
        if (deleteSession && _sessionRoot != null)
        {
            DeleteDirectoryUnderRoot(
                Path.Combine(_sessionRoot, ".."),
                _sessionRoot);
        }
    }

    private static void AppendMeetingEvent(Dictionary<string, object?> entry)
    {
        if (_meetingTranscriptPath == null || _sessionId == null)
        {
            return;
        }

        entry["sessionId"] = _sessionId;
        try
        {
            File.AppendAllText(
                _meetingTranscriptPath,
                JsonSerializer.Serialize(entry, JsonOptions) + Environment.NewLine,
                new System.Text.UTF8Encoding(false));
        }
        catch (Exception exception)
        {
            _log?.LogWarning($"Could not write meeting memory: {exception.Message}");
        }
    }

    private static void CloseRouteWriter()
    {
        try
        {
            _routeWriter?.Flush();
            _routeWriter?.Dispose();
        }
        catch (Exception exception)
        {
            _log?.LogWarning($"Could not close route memory: {exception.Message}");
        }
        finally
        {
            _routeWriter = null;
        }
    }

    private static void DeleteDirectoryUnderRoot(string root, string target)
    {
        try
        {
            var fullRoot = Path.GetFullPath(root)
                .TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            var fullTarget = Path.GetFullPath(target);
            if (!fullTarget.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Refusing to delete outside memory root.");
            }
            if (Directory.Exists(fullTarget))
            {
                Directory.Delete(fullTarget, true);
            }
        }
        catch (Exception exception)
        {
            _log?.LogWarning($"Could not delete memory directory: {exception.Message}");
        }
    }
}

[HarmonyPatch(typeof(ChatController), nameof(ChatController.AddChat))]
internal static class ChatMemoryPatch
{
    private static void Postfix(PlayerControl? sourcePlayer, string chatText, bool censor)
    {
        MemoryRepositories.RecordChat(sourcePlayer, chatText, censor);
        MeetingControl.RecordChatActivity(sourcePlayer, chatText);
    }
}

[HarmonyPatch(typeof(GameManager), nameof(GameManager.StartGame))]
internal static class TaskStartSignalPatch
{
    internal static long SignalVersion { get; private set; }

    private static void Postfix()
    {
        SignalVersion++;
    }
}
