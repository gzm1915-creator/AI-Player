using System;
using System.IO;
using System.Text.Json;
using AUTelemetryProbe;
using UnityEngine;

internal static class NavigationProgressTests
{
    internal static void Run()
    {
        void Check(bool value, string reason) { if (!value) throw new Exception(reason); }
        using var fixture = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "weapons-jitter-0.6.87.json")));
        var target = fixture.RootElement.GetProperty("target");
        var goal = new Vector2(target.GetProperty("x").GetSingle(), target.GetProperty("y").GetSingle());
        var progress = new NavigationProgress(900);
        var detected = false;
        var oldVelocityRuleNeverStalled = true;
        foreach (var frame in fixture.RootElement.GetProperty("frames").EnumerateArray())
        {
            var p = frame.GetProperty("position");
            var v = frame.GetProperty("velocity");
            var vx = v.GetProperty("x").GetSingle();
            var vy = v.GetProperty("y").GetSingle();
            oldVelocityRuleNeverStalled &= vx * vx + vy * vy >= .0225f;
            detected |= progress.IsStalled(frame.GetProperty("ms").GetInt64(),
                new Vector2(p.GetProperty("x").GetSingle(), p.GetProperty("y").GetSingle()), 9, goal);
        }
        Check(oldVelocityRuleNeverStalled, "the actual oscillation trace must reproduce the old speed-based false progress");
        Check(detected, "actual weapons oscillation must trigger a bounded stall instead of running until the 60s timeout");

        var moving = new NavigationProgress(900);
        for (int now = 0; now < 4000; now += 100)
            Check(!moving.IsStalled(now, new Vector2(now / 400f, 0), 1, new Vector2(20, 0)),
                "normal walking cannot be classified as a stall");
        var slow = new NavigationProgress(900);
        for (int now = 0; now < 4000; now += 100)
            Check(!slow.IsStalled(now, new Vector2(now / 10000f, 0), 1, new Vector2(20, 0)),
                "slow but real progress accumulates instead of being compared frame-by-frame");
        var blocked = new NavigationProgress(900);
        Check(!blocked.IsStalled(0, new Vector2(0, 0), 1, new Vector2(2, 0)), "start has a grace period");
        Check(blocked.IsStalled(900, new Vector2(0, 0), 1, new Vector2(2, 0)), "no motion must time out");
        blocked.Reset();
        Check(!blocked.IsStalled(5000, new Vector2(0, 0), 1, new Vector2(2, 0)), "pause or recovery resets its own grace period");
        Check(!blocked.IsStalled(5800, new Vector2(0, 0), 2, new Vector2(0, 2)), "a newly reached corner starts a new waypoint");
        Check(blocked.IsStalled(6700, new Vector2(0, 0), 2, new Vector2(0, 2)), "corner change cannot give unlimited time");
        Console.WriteLine("Navigation progress: actual oscillation replay, walking, slow progress, blockage, pause and corner transitions passed.");
    }
}
