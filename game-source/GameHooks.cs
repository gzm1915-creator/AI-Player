using System;
using System.Linq;
using HarmonyLib;

namespace AUTelemetryProbe;

internal static class GameHooks
{
    internal static void Install(Harmony harmony, Action<Type, Exception?> report)
    {
        // Discover this component's declared hooks together so adding a task
        // observer cannot silently omit its registration in Plugin.Load.
        var hooks = typeof(GameHooks).Assembly.GetTypes()
            .Where(t => t.IsDefined(typeof(HarmonyPatch), inherit: false))
            .OrderBy(t => t.Name);
        foreach (var hook in hooks)
        {
            try { harmony.PatchAll(hook); report(hook, null); }
            catch (Exception exception) { report(hook, exception); }
        }
    }
}
