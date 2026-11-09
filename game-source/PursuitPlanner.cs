using System;
using UnityEngine;

namespace AUTelemetryProbe;

internal static class PursuitPlanner
{
    internal static NavigationPlan Plan(NavigationGraph graph, Vector2 start, Vector2 target, string name,
        Func<Vector2, Vector2, bool> clear)
    {
        var distance = Vector2.Distance(start, target);
        // A visible player can stand away from the recorded task/door nodes.
        // This is a walking approach, never an extension of native kill range.
        // The callback sweeps the body and applies crime-scene avoidance.
        if (distance <= 4.5f && clear(start, target))
            return new NavigationPlan(new[] { start, target }, 0, 0, 0, 0, distance, "pursuit_local", name);
        var plan = graph.FindRouteToPosition(start, new NavigationPoint(target.x, target.y), name, 2.5, clear);
        if (plan.PlannedDistance > 18) throw new InvalidOperationException("追踪路线绕行过远，放弃本次目标");
        return plan;
    }
}
