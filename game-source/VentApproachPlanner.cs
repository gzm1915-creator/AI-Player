using System;
using System.Collections.Generic;
using UnityEngine;

namespace AUTelemetryProbe;

// Attach a real vent's usable standing area to the recorded walking graph.
// No invented routes: every attachment and recorded leg passes collision tests.
internal static class VentApproachPlanner
{
    internal static NavigationPlan Plan(NavigationGraph graph, Vector2 start, Vector2 vent,
        float usableDistance, string name, Func<Vector2, Vector2, bool> clear)
    {
        var offset = Math.Min(.45f, usableDistance * .6f);
        NavigationPlan? best = null;
        double bestLength = double.PositiveInfinity;
        foreach (var delta in new[] { new Vector2(0, 0), new Vector2(0, -offset), new Vector2(0, offset),
            new Vector2(-offset, 0), new Vector2(offset, 0), new Vector2(-offset*.7f, -offset*.7f),
            new Vector2(offset*.7f, -offset*.7f), new Vector2(-offset*.7f, offset*.7f), new Vector2(offset*.7f, offset*.7f) })
        {
            var target = new Vector2(vent.x + delta.x, vent.y + delta.y);
            try
            {
                var plan = Vector2.Distance(start, target) <= 4.5f && clear(start, target)
                    ? new NavigationPlan(new[] { start, target }, 0, 0, 0, 0, Vector2.Distance(start, target), "vent_standing_area", name)
                    : graph.FindRouteToPosition(start, new NavigationPoint(target.x, target.y), name, 2.5, clear);
                double length = 0;
                for (int i = 1; i < plan.Points.Count; i++) length += Vector2.Distance(plan.Points[i-1], plan.Points[i]);
                if (length > 14 || length >= bestLength) continue;
                best = plan; bestLength = length;
            }
            catch (InvalidOperationException) { }
        }
        return best ?? throw new InvalidOperationException("附近管道没有可通行的已知路线或安全站位，改用地面撤离");
    }
}
