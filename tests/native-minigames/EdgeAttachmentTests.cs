using System;
using System.IO;
using System.Linq;
using AUTelemetryProbe;
using UnityEngine;

internal static class EdgeAttachmentTests
{
    internal static void Run()
    {
        void Check(bool value, string reason) { if (!value) throw new Exception(reason); }
        var fixture = Path.GetTempFileName();
        try
        {
            // Actual stopped position and the recorded leg from the report.
            File.WriteAllText(fixture,"""
            {"anchors":[{"anchorId":"engine.upper","position":{"x":-15.325397,"y":0.808254}},{"anchorId":"hall","position":{"x":-9.158753,"y":0.808254}}],
            "routeSegments":[{"segmentId":"actual-corridor","fromAnchorId":"engine.upper","toAnchorId":"hall","status":"observed_success","path":[{"x":-15.325397,"y":0.808254},{"x":-9.158753,"y":0.808254}]},
            {"segmentId":"detour","fromAnchorId":"hall","toAnchorId":"engine.upper","status":"observed_success","path":[{"x":-9.158753,"y":0.808254},{"x":-9.158753,"y":3.8},{"x":-15.325397,"y":3.8},{"x":-15.325397,"y":0.808254}]}]}
            """);
            var graph = NavigationGraph.Load(fixture,.08);
            var start = new Vector2(-12.721802f,.80820525f);
            var plan = graph.FindRoute(start,"engine.upper",1.25,(a,b)=>true);
            Check(plan.StartSnapDistance < .001,"mid-leg reattachment retains the 1.25 radius");
            Check(plan.Points.Count >= 3 && Vector2.Distance(start,plan.Points[0]) < .001,
                "actual mid-corridor stop reconnects without teleporting to an endpoint");
            bool Gate(Vector2 a, Vector2 b) {
                if ((a.x < -13 && b.x > -13) || (b.x < -13 && a.x > -13))
                    return a.y+(b.y-a.y)*(-13-a.x)/(b.x-a.x)>2.5;
                return true;
            }
            var around = graph.FindRoute(start,"engine.upper",1.25,Gate);
            Check(around.Points.Any(p=>p.y>3),"blocked half of a corridor must choose the other endpoint and detour");
            for(int i=1;i<around.Points.Count;i++) Check(Gate(around.Points[i-1],around.Points[i]),"every executed connector must pass the gate check");
            bool blocked=false;
            try { graph.FindRoute(start,"engine.upper",1.25,(a,b)=>false); } catch(InvalidOperationException) { blocked=true; }
            Check(blocked,"edge projection cannot bypass body collision");
            bool outside=false;
            try { graph.FindRoute(new Vector2(start.x,2.2f),"engine.upper",1.25,(a,b)=>true); } catch(InvalidOperationException) { outside=true; }
            Check(outside,"a point more than 1.25 from every recorded leg must remain rejected");
            var scene=new Vector2(-14,.8f);
            bool sceneBlocked=false;
            try { graph.FindRoute(start,"engine.upper",1.25,(a,b)=>CrimeSceneGeometry.Allows(a,b,scene,3)); }
            catch(InvalidOperationException) { sceneBlocked=true; }
            Check(sceneBlocked,"reattaching cannot return into a previous crime scene");
        }
        finally { File.Delete(fixture); }
        Console.WriteLine("Mid-edge attachment: reported coordinates, closed-door detour, collision, snap radius and crime-scene protection passed.");
    }
}
