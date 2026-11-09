using UnityEngine;

namespace AUTelemetryProbe;

// Velocity and cumulative movement both look healthy during an avoidance loop.
// Only a new best distance to the current waypoint counts as route progress.
internal sealed class NavigationProgress
{
    private readonly long _timeoutMs;
    private bool _initialized;
    private int _waypoint;
    private Vector2 _target;
    private float _bestDistance;
    private long _lastProgressAt;

    internal NavigationProgress(long timeoutMs) => _timeoutMs = timeoutMs;

    internal void Reset() => _initialized = false;

    internal bool IsStalled(long now, Vector2 pose, int waypoint, Vector2 target)
    {
        var distance = Vector2.Distance(pose, target);
        if (!_initialized || waypoint != _waypoint || Vector2.Distance(target, _target) > .01f)
        {
            _initialized = true;
            _waypoint = waypoint;
            _target = target;
            _bestDistance = distance;
            _lastProgressAt = now;
        }
        else if (_bestDistance - distance >= .04f)
        {
            _bestDistance = distance;
            _lastProgressAt = now;
        }
        return now - _lastProgressAt >= _timeoutMs;
    }
}
