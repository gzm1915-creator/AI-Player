using System;
using System.IO;
using System.Text.Json;
using BepInEx;
using UnityEngine;

namespace AUTelemetryProbe;

internal static class RuntimeControl
{
    private const int PollIntervalMs = 100;
    private const int StatusIntervalMs = 250;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };

    private static string? _commandPath;
    private static string? _statusPath;
    private static long _nextPollMs;
    private static long _nextStatusMs;
    private static bool _configured;
    private static bool _initialized;

    public static bool AutoNavigationEnabled { get; private set; }
    public static bool AutoReportBodiesEnabled { get; private set; }
    public static bool VisualCaptureEnabled { get; private set; } = true;
    public static bool MeetingControlEnabled { get; private set; } = true;
    public static bool UseVentsEnabled { get; private set; }
    public static bool MinigameControlEnabled { get; private set; }
    public static long Revision { get; private set; }

    public static void Configure(
        string commandPath,
        string statusPath,
        bool visualCaptureEnabled)
    {
        _commandPath = Path.GetFullPath(commandPath);
        _statusPath = Path.GetFullPath(statusPath);
        VisualCaptureEnabled = visualCaptureEnabled;
        _configured = true;

        Directory.CreateDirectory(
            Path.GetDirectoryName(_commandPath)
            ?? throw new InvalidOperationException("Runtime control path is invalid."));
        Directory.CreateDirectory(
            Path.GetDirectoryName(_statusPath)
            ?? throw new InvalidOperationException("Runtime status path is invalid."));

        if (!TryRead(out var command, out _))
        {
            command = CreateDefaultCommand();
            WriteControlFile(command);
        }

        Apply(command);
        _initialized = true;
        WriteStatus();
        TelemetryProbePlugin.LogSource?.LogInfo(
            $"Runtime control ready: navigation={AutoNavigationEnabled}, " +
            $"report={AutoReportBodiesEnabled}, visual={VisualCaptureEnabled}");
    }

    public static void Tick(long now)
    {
        if (!_configured)
        {
            return;
        }

        if (now >= _nextPollMs)
        {
            _nextPollMs = now + PollIntervalMs;
            if (TryRead(out var command, out var error))
            {
                var changed =
                    !_initialized ||
                    command.Revision != Revision ||
                    command.AutoNavigation != AutoNavigationEnabled ||
                    command.AutoReportBodies != AutoReportBodiesEnabled ||
                    command.VisualCapture != VisualCaptureEnabled ||
                    command.MeetingControl != MeetingControlEnabled ||
                    command.UseVents != UseVentsEnabled ||
                    command.MinigameControl != MinigameControlEnabled;
                Apply(command);
                _initialized = true;
                if (changed)
                {
                    TelemetryProbePlugin.LogSource?.LogInfo(
                        $"Runtime control changed: navigation={AutoNavigationEnabled}, " +
                        $"report={AutoReportBodiesEnabled}, visual={VisualCaptureEnabled}, " +
                        $"meeting={MeetingControlEnabled}, vents={UseVentsEnabled}, " +
                        $"minigame={MinigameControlEnabled}");
                    WriteStatus();
                }
            }
            else if (!string.IsNullOrWhiteSpace(error) &&
                     error != "missing" &&
                     now >= _nextStatusMs)
            {
                TelemetryProbePlugin.LogSource?.LogWarning(
                    $"Runtime control read failed: {error}");
            }
        }

        if (now >= _nextStatusMs)
        {
            _nextStatusMs = now + StatusIntervalMs;
            WriteStatus();
        }
    }

    internal static string GetCurrentState()
    {
        return InternalNavigationBehaviour.ActiveInstance?.CurrentState ?? "idle";
    }

    internal static string? GetCurrentTarget()
    {
        return InternalNavigationBehaviour.ActiveInstance?.CurrentTargetAnchorId;
    }

    internal static string? GetCurrentRecoveryAnchor()
    {
        return InternalNavigationBehaviour.ActiveInstance?.CurrentRecoveryAnchorId;
    }

    private static RuntimeControlCommand CreateDefaultCommand()
    {
        return new RuntimeControlCommand
        {
            Revision = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            UpdatedAtUtc = DateTimeOffset.UtcNow,
            AutoNavigation = false,
            AutoReportBodies = false,
            VisualCapture = false,
            MeetingControl = false,
            UseVents = false,
            MinigameControl = false
        };
    }

    private static bool TryRead(
        out RuntimeControlCommand command,
        out string? error)
    {
        command = new RuntimeControlCommand();
        error = null;
        if (_commandPath == null || !File.Exists(_commandPath))
        {
            error = "missing";
            return false;
        }

        try
        {
            using var stream = new FileStream(
                _commandPath,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
            var value = JsonSerializer.Deserialize<RuntimeControlCommand>(
                stream,
                JsonOptions);
            if (value == null)
            {
                error = "empty";
                return false;
            }

            command = value;
            return true;
        }
        catch (JsonException exception)
        {
            error = exception.Message;
            return false;
        }
        catch (IOException exception)
        {
            error = exception.Message;
            return false;
        }
    }

    private static void Apply(RuntimeControlCommand command)
    {
        Revision = command.Revision;
        AutoNavigationEnabled = command.AutoNavigation;
        AutoReportBodiesEnabled = command.AutoReportBodies;
        VisualCaptureEnabled = command.VisualCapture;
        MeetingControlEnabled = command.MeetingControl;
        UseVentsEnabled = command.UseVents;
        MinigameControlEnabled = command.MinigameControl;
    }

    private static void WriteControlFile(RuntimeControlCommand command)
    {
        if (_commandPath == null)
        {
            return;
        }

        AtomicFile.WriteAllText(
            _commandPath,
            JsonSerializer.Serialize(command, JsonOptions));
    }

    private static void WriteStatus()
    {
        if (_statusPath == null)
        {
            return;
        }

        try
        {
            var gameManager = GameManager.Instance;
            var meeting = MeetingHud.Instance;
            var status = new RuntimeControlStatus
            {
                SchemaVersion = "1.0.0",
                PluginVersion = TelemetryProbePlugin.PluginVersion,
                UpdatedAtUtc = DateTimeOffset.UtcNow,
                Revision = Revision,
                AutoNavigation = AutoNavigationEnabled,
                AutoReportBodies = AutoReportBodiesEnabled,
                VisualCapture = VisualCaptureEnabled,
                MeetingControl = MeetingControlEnabled,
                UseVents = UseVentsEnabled,
                MinigameControl = MinigameControlEnabled,
                MinigameAutomation = AUTelemetryProbe.MinigameAutomation.Status,
                GamePhase = meeting != null
                    ? "MEETING"
                    : gameManager?.GameHasStarted == true
                        ? "TASKS"
                        : "MENU",
                NavigationState = GetCurrentState(),
                TargetAnchorId = GetCurrentTarget(),
                RecoveryAnchorId = GetCurrentRecoveryAnchor()
            };

            AtomicFile.WriteAllText(
                _statusPath,
                JsonSerializer.Serialize(status, JsonOptions));
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Could not write runtime control status: {exception.Message}");
        }
    }
}

internal sealed class RuntimeControlCommand
{
    public long Revision { get; init; }
    public DateTimeOffset UpdatedAtUtc { get; init; }
    public bool AutoNavigation { get; init; } = true;
    public bool AutoReportBodies { get; init; }
    public bool VisualCapture { get; init; } = true;
    public bool MeetingControl { get; init; } = true;
    public bool UseVents { get; init; }
    public bool MinigameControl { get; init; }
}

internal sealed class RuntimeControlStatus
{
    public MinigameAutomationStatus MinigameAutomation { get; init; } = new();
    public string SchemaVersion { get; init; } = "1.0.0";
    public string PluginVersion { get; init; } = string.Empty;
    public DateTimeOffset UpdatedAtUtc { get; init; }
    public long Revision { get; init; }
    public bool AutoNavigation { get; init; }
    public bool AutoReportBodies { get; init; }
    public bool VisualCapture { get; init; }
    public bool MeetingControl { get; init; }
    public bool UseVents { get; init; }
    public bool MinigameControl { get; init; }
    public string GamePhase { get; init; } = "UNKNOWN";
    public string NavigationState { get; init; } = "idle";
    public string? TargetAnchorId { get; init; }
    public string? RecoveryAnchorId { get; init; }
}
