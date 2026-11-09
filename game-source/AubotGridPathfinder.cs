using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEngine;

namespace AUTelemetryProbe;

internal sealed class AubotGridPathfinder
{
    // Optional compatibility data. Resolve relative to this game's plugin root.
    private static readonly string DefaultGridPath =
        Path.Combine(BepInEx.Paths.PluginPath, "AUBot", "aubot_grid.bin");
    private static readonly Lazy<AubotGridPathfinder?> Cached =
        new(LoadDefault);

    private readonly float _cell;
    private readonly float _minX;
    private readonly float _minY;
    private readonly int _width;
    private readonly int _height;
    private readonly byte[] _cells;

    private AubotGridPathfinder(
        float cell,
        float minX,
        float minY,
        int width,
        int height,
        byte[] cells)
    {
        _cell = cell;
        _minX = minX;
        _minY = minY;
        _width = width;
        _height = height;
        _cells = cells;
    }

    public static bool TryFindPath(
        Vector2 start,
        Vector2 goal,
        out Vector2[] path)
    {
        path = Array.Empty<Vector2>();
        var grid = Cached.Value;
        return grid != null && grid.TryFindPathInternal(start, goal, out path);
    }

    private static AubotGridPathfinder? LoadDefault()
    {
        try
        {
            if (!File.Exists(DefaultGridPath))
            {
                return null;
            }

            var raw = File.ReadAllBytes(DefaultGridPath);
            if (raw.Length < 24 ||
                Encoding.ASCII.GetString(raw, 0, 4) != "AUG1")
            {
                return null;
            }

            var cell = BitConverter.ToSingle(raw, 4);
            var minX = BitConverter.ToSingle(raw, 8);
            var minY = BitConverter.ToSingle(raw, 12);
            var width = BitConverter.ToInt32(raw, 16);
            var height = BitConverter.ToInt32(raw, 20);
            if (cell <= 0 || width <= 0 || height <= 0 ||
                raw.Length != 24 + width * height)
            {
                return null;
            }

            return new AubotGridPathfinder(
                cell,
                minX,
                minY,
                width,
                height,
                raw[24..]);
        }
        catch (Exception exception)
        {
            TelemetryProbePlugin.LogSource?.LogWarning(
                $"Could not load AUBot grid: {exception.Message}");
            return null;
        }
    }

    private bool TryFindPathInternal(
        Vector2 start,
        Vector2 goal,
        out Vector2[] path)
    {
        path = Array.Empty<Vector2>();
        var startCell = ToGrid(start);
        var goalCell = ToGrid(goal);
        if (!TryNearestFree(startCell, 12, out startCell) ||
            !TryNearestFree(goalCell, 12, out goalCell))
        {
            return false;
        }

        var frontier = new PriorityQueue<(int X, int Y), double>();
        var cameFrom = new Dictionary<(int X, int Y), (int X, int Y)>();
        var costs = new Dictionary<(int X, int Y), double>
        {
            [startCell] = 0
        };
        frontier.Enqueue(startCell, Heuristic(startCell, goalCell));

        var directions = new (int X, int Y, double Cost)[]
        {
            (1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1),
            (1, 1, Math.Sqrt(2)), (1, -1, Math.Sqrt(2)),
            (-1, 1, Math.Sqrt(2)), (-1, -1, Math.Sqrt(2))
        };

        var found = false;
        while (frontier.TryDequeue(out var current, out _))
        {
            if (current == goalCell)
            {
                found = true;
                break;
            }

            var currentCost = costs[current];
            foreach (var direction in directions)
            {
                var next = (
                    X: current.X + direction.X,
                    Y: current.Y + direction.Y);
                if (!IsFree(next.X, next.Y))
                {
                    continue;
                }
                if (direction.X != 0 && direction.Y != 0 &&
                    (!IsFree(current.X + direction.X, current.Y) ||
                     !IsFree(current.X, current.Y + direction.Y)))
                {
                    continue;
                }

                var nextCost = currentCost + direction.Cost;
                if (nextCost >= costs.GetValueOrDefault(
                        next,
                        double.PositiveInfinity))
                {
                    continue;
                }
                costs[next] = nextCost;
                cameFrom[next] = current;
                frontier.Enqueue(
                    next,
                    nextCost + Heuristic(next, goalCell));
            }
        }

        if (!found)
        {
            return false;
        }

        var cells = new List<(int X, int Y)>();
        var cursor = goalCell;
        while (cursor != startCell)
        {
            cells.Add(cursor);
            if (!cameFrom.TryGetValue(cursor, out cursor))
            {
                return false;
            }
        }
        cells.Reverse();

        var points = new List<Vector2> { start };
        points.AddRange(cells
            .Take(Math.Max(0, cells.Count - 1))
            .Select(cellPoint => ToWorld(cellPoint.X, cellPoint.Y)));
        points.Add(goal);
        path = points
            .Where((point, index) =>
                index == 0 || Vector2.Distance(point, points[index - 1]) > 0.01f)
            .ToArray();
        return path.Length >= 2;
    }

    private bool TryNearestFree(
        (int X, int Y) origin,
        int maxRadius,
        out (int X, int Y) result)
    {
        if (IsFree(origin.X, origin.Y))
        {
            result = origin;
            return true;
        }

        for (var radius = 1; radius <= maxRadius; radius++)
        {
            var bestDistance = double.PositiveInfinity;
            var best = origin;
            var found = false;
            for (var dx = -radius; dx <= radius; dx++)
            {
                for (var dy = -radius; dy <= radius; dy++)
                {
                    if (Math.Max(Math.Abs(dx), Math.Abs(dy)) != radius)
                    {
                        continue;
                    }
                    var x = origin.X + dx;
                    var y = origin.Y + dy;
                    if (!IsFree(x, y))
                    {
                        continue;
                    }
                    var distance = dx * dx + dy * dy;
                    if (distance >= bestDistance)
                    {
                        continue;
                    }
                    bestDistance = distance;
                    best = (x, y);
                    found = true;
                }
            }
            if (found)
            {
                result = best;
                return true;
            }
        }

        result = origin;
        return false;
    }

    private bool IsFree(int x, int y) =>
        x >= 0 && y >= 0 && x < _width && y < _height &&
        _cells[y * _width + x] == 1;

    private (int X, int Y) ToGrid(Vector2 point) =>
        (
            (int)Math.Floor((point.x - _minX) / _cell),
            (int)Math.Floor((point.y - _minY) / _cell));

    private Vector2 ToWorld(int x, int y) =>
        new(
            _minX + (x + 0.5f) * _cell,
            _minY + (y + 0.5f) * _cell);

    private static double Heuristic(
        (int X, int Y) first,
        (int X, int Y) second) =>
        Math.Sqrt(
            (first.X - second.X) * (first.X - second.X) +
            (first.Y - second.Y) * (first.Y - second.Y));
}
