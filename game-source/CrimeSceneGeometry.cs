using System;
using UnityEngine;

namespace AUTelemetryProbe;

internal static class CrimeSceneGeometry
{
    // From inside, allow only outward travel. From outside, reject any crossing.
    internal static bool Allows(Vector2 from, Vector2 to, Vector2 scene, float radius)
    {
        var ax = from.x - scene.x; var ay = from.y - scene.y;
        var dx = to.x - from.x; var dy = to.y - from.y;
        var length2 = dx * dx + dy * dy;
        var distance2 = ax * ax + ay * ay;
        if (length2 < .000001f) return true;
        if (distance2 < radius * radius)
            return ax * dx + ay * dy >= -.001f &&
                (to.x - scene.x) * (to.x - scene.x) + (to.y - scene.y) * (to.y - scene.y) >= distance2 - .001f;
        var t = Math.Clamp(-(ax * dx + ay * dy) / length2, 0, 1);
        var x = ax + t * dx; var y = ay + t * dy;
        return x * x + y * y >= radius * radius - .001f;
    }
}
