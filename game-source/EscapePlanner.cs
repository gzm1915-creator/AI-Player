using System;
using UnityEngine;

namespace AUTelemetryProbe;

// A murder can leave the local player away from the original route nodes.
// Every attachment and executed leg still obeys body collision and scene rules.
internal static class EscapePlanner
{
    internal static NavigationPlan Plan(NavigationGraph graph, Vector2 start, string target,
        Func<Vector2, Vector2, bool> clear)
    {
        var point = graph.ResolveTargetPosition(target);
        var destination = new Vector2(point.X, point.Y);
        var distance = Vector2.Distance(start, destination);
        if (distance <= 4.5f && clear(start, destination))
            return new NavigationPlan(new[] { start, destination }, 0, 0, 0, 0, distance, "escape_local", target);
        return graph.FindRoute(start, target, 2.5, clear);
    }
}
