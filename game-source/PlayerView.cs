using System;
using System.Collections.Generic;
using UnityEngine;

namespace AUTelemetryProbe;

// Validate the observer itself before using a camera to reject other players.
// MainCamera may be a menu/UI camera while the world is rendered elsewhere.
internal static class PlayerView
{
    internal const float DetectionRadius = 11f;
    internal const float DetectionViewportMargin = 0.08f;
    internal const int ScanIntervalMs = 80;

    internal static Camera? Select(Camera[] cameras, Vector3 observer, int layer, out string diagnostics)
    {
        Camera? selected = null;
        var best = double.PositiveInfinity;
        var candidates = new List<string>();
        foreach (var camera in cameras)
        {
            if (camera == null || !camera.enabled) continue;
            var point = camera.WorldToViewportPoint(observer);
            var worldLayer = (camera.cullingMask & (1 << layer)) != 0;
            var usable = camera.targetTexture == null && camera.pixelWidth > 0 && camera.pixelHeight > 0;
            var valid = worldLayer && usable && Contains(camera, observer);
            candidates.Add($"{camera.name}:local=({point.x:F2},{point.y:F2},{point.z:F2}),clip=({camera.nearClipPlane:F2},{camera.farClipPlane:F2}),layer={worldLayer},surface={usable},valid={valid}");
            if (!valid) continue;
            var distance = (point.x - 0.5) * (point.x - 0.5) + (point.y - 0.5) * (point.y - 0.5);
            if (distance >= best) continue;
            best = distance;
            selected = camera;
        }
        diagnostics = string.Join("; ", candidates);
        return selected;
    }

    internal static bool Contains(Camera camera, Vector3 position, float margin = 0f)
    {
        var viewport = camera.WorldToViewportPoint(position);
        return !float.IsNaN(viewport.x) && !float.IsInfinity(viewport.x) &&
               !float.IsNaN(viewport.y) && !float.IsInfinity(viewport.y) &&
               // Among Us uses an orthographic world camera whose near plane
               // can be negative. A negative projected depth can be rendered.
               viewport.z >= camera.nearClipPlane && viewport.z <= camera.farClipPlane &&
               viewport.x >= -margin && viewport.x <= 1f + margin &&
               viewport.y >= -margin && viewport.y <= 1f + margin;
    }
}
