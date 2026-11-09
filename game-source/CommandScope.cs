using System;

namespace AUTelemetryProbe;

internal static class CommandScope
{
    // Preserve the original StudyBot/external-controller protocol. Adapter
    // commands carry identities, including ordinary navigation and cleanup.
    internal static bool Protected(InternalNavigationCommand command) =>
        command.CommandId?.StartsWith("neko-", StringComparison.Ordinal) == true;

    internal static bool SameMatch(InternalNavigationCommand command) => !Protected(command) ||
        command.ExpectedSessionId == TelemetryCapture.CurrentSessionId &&
        command.ExpectedMatchId == TaskStartSignalPatch.SignalVersion;

    internal static bool Fresh(InternalNavigationCommand command, DateTimeOffset? at = null) =>
        SameMatch(command) && (!Protected(command) || command.IssuedAtUtc.HasValue &&
        ((at ?? DateTimeOffset.UtcNow) - command.IssuedAtUtc.Value).TotalSeconds is >= -1 and <= 2);
}
