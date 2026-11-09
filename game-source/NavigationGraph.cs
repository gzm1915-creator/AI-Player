using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using UnityEngine;

namespace AUTelemetryProbe;

internal sealed class RecoveryAnchorCandidate
{
    public string AnchorId { get; init; } = string.Empty;
    public NavigationPoint Position { get; init; }
    public float Distance { get; init; }
}

internal readonly record struct NavigationPoint(float X, float Y);

internal sealed class NavigationGraph
{
    private sealed class Node
    {
        public int Id { get; init; }
        public NavigationPoint Position { get; init; }
        public HashSet<string> Aliases { get; } =
            new(StringComparer.OrdinalIgnoreCase);
    }

    private sealed record Edge(
        int To,
        string SegmentId,
        string Direction,
        double Cost,
        IReadOnlyList<NavigationPoint> Points);

    private sealed record Entry(int NodeId, double Cost, double SnapDistance, IReadOnlyList<NavigationPoint> Points);

    private readonly List<Node> _nodes = new();
    private readonly List<List<Edge>> _adjacency = new();
    private readonly Dictionary<string, NavigationPoint> _anchors =
        new(StringComparer.OrdinalIgnoreCase);
    private readonly double _clusterTolerance;

    private NavigationGraph(double clusterTolerance)
    {
        _clusterTolerance = clusterTolerance;
    }

    public static NavigationGraph Load(string path, double clusterTolerance)
    {
        var graph = new NavigationGraph(clusterTolerance);
        using var stream = File.OpenRead(path);
        using var document = JsonDocument.Parse(stream);
        var root = document.RootElement;

        foreach (var anchor in root.GetProperty("anchors").EnumerateArray())
        {
            var id = anchor.GetProperty("anchorId").GetString()!;
            var position = anchor.GetProperty("position");
            graph._anchors[id] = new NavigationPoint(
                (float)position.GetProperty("x").GetDouble(),
                (float)position.GetProperty("y").GetDouble());
        }

        foreach (var segment in root.GetProperty("routeSegments").EnumerateArray())
        {
            var status = segment.GetProperty("status").GetString();
            if (status is not ("observed_success" or "generated_validated") ||
                (!segment.TryGetProperty("path", out var pathElement) &&
                 !segment.TryGetProperty("routePoints", out pathElement)))
            {
                continue;
            }

            var points = pathElement.EnumerateArray()
                .Select(point => new NavigationPoint(
                    (float)point.GetProperty("x").GetDouble(),
                    (float)point.GetProperty("y").GetDouble()))
                .ToArray();
            if (points.Length < 2)
            {
                continue;
            }

            var segmentId = segment.GetProperty("segmentId").GetString()!;
            var fromId = segment.TryGetProperty("fromAnchorId", out var from)
                ? from.GetString()
                : null;
            var toId = segment.TryGetProperty("toAnchorId", out var to)
                ? to.GetString()
                : null;
            if (!string.IsNullOrWhiteSpace(fromId) &&
                graph._anchors.TryGetValue(fromId, out var fromAnchor))
            {
                points[0] = fromAnchor;
            }
            if (!string.IsNullOrWhiteSpace(toId) &&
                graph._anchors.TryGetValue(toId, out var toAnchor))
            {
                points[^1] = toAnchor;
            }
            for (var index = 1; index < points.Length; index++)
            {
                var leg = new[] { points[index - 1], points[index] };
                var fromNode = graph.FindOrAddNode(
                    leg[0],
                    index == 1 ? fromId : null);
                var toNode = graph.FindOrAddNode(
                    leg[1],
                    index == points.Length - 1 ? toId : null);
                var cost = Distance(leg[0], leg[1]) *
                    (status == "observed_success" ? 1.0 : 20.0);
                graph._adjacency[fromNode].Add(new Edge(
                    toNode,
                    segmentId,
                    "forward",
                    cost,
                    leg));
                graph._adjacency[toNode].Add(new Edge(
                    fromNode,
                    segmentId,
                    "reverse",
                    cost,
                    new[] { leg[1], leg[0] }));
            }
        }

        return graph;
    }

    public NavigationPlan FindRoute(
        Vector2 start,
        string target,
        double snapRadius,
        Func<Vector2, Vector2, bool>? canTraverse = null)
    {
        var targetPosition = ResolveTarget(target);
        return FindRouteToPosition(start, targetPosition, target, snapRadius, canTraverse);
    }

    public NavigationPlan FindRouteToPosition(
        Vector2 start,
        NavigationPoint targetPosition,
        string targetName,
        double snapRadius,
        Func<Vector2, Vector2, bool>? canTraverse = null)
    {
        var goalNode = FindNearestNode(targetPosition);
        var goalSnap = Distance(targetPosition, _nodes[goalNode].Position);
        if (goalSnap > snapRadius)
        {
            throw new InvalidOperationException(
                $"Target {targetName} is {goalSnap:F3} from route graph.");
        }
        Vector2 World(NavigationPoint p) => new(p.X, p.Y);
        if (canTraverse != null && !canTraverse(World(_nodes[goalNode].Position), World(targetPosition)))
            throw new InvalidOperationException($"Target {targetName} is blocked from its route node.");
        if (Distance(start, targetPosition) <= 0.20)
        {
            throw new InvalidOperationException("Already at target route node.");
        }

        var entries = _nodes
            .Where(node =>
                Distance(start, node.Position) <= snapRadius &&
                (canTraverse == null || canTraverse(start, World(node.Position))))
            .ToDictionary(node => node.Id, node => new Entry(node.Id, Distance(start, node.Position),
                Distance(start, node.Position), new[] { new NavigationPoint(start.x, start.y), node.Position }));
        // A stopped player can be in the middle of a long recorded leg, far
        // from either vertex. Attach to that leg within the SAME snap radius.
        // Both the short connector and the executed remainder are checked in
        // their travel direction; no new cross-room shortcut is invented.
        var origin = new NavigationPoint(start.x, start.y);
        foreach (var from in _nodes)
        foreach (var edge in _adjacency[from.Id])
        {
            var to = _nodes[edge.To].Position;
            var dx = to.X - from.Position.X;
            var dy = to.Y - from.Position.Y;
            var lengthSquared = dx * dx + dy * dy;
            if (lengthSquared < .0001) continue;
            var fraction = ((start.x - from.Position.X) * dx + (start.y - from.Position.Y) * dy) / lengthSquared;
            if (fraction <= 0 || fraction >= 1) continue;
            var projection = new NavigationPoint(from.Position.X + fraction * dx, from.Position.Y + fraction * dy);
            var snap = Distance(start, projection);
            if (snap > snapRadius || (canTraverse != null &&
                (!canTraverse(start, World(projection)) || !canTraverse(World(projection), World(to))))) continue;
            var cost = snap + edge.Cost * (1 - fraction);
            if (!entries.TryGetValue(edge.To, out var existing) || cost < existing.Cost - .000001)
                entries[edge.To] = new Entry(edge.To, cost, snap, new[] { origin, projection, to });
        }
        var entryCandidates = entries.Values.OrderBy(entry => entry.Cost).ThenBy(entry => entry.NodeId).ToArray();
        if (entryCandidates.Length == 0)
        {
            throw new InvalidOperationException(
                $"No reachable route entry within {snapRadius:F3}; nearby nodes may be across a wall.");
        }

        double[]? selectedGScore = null;
        int[]? selectedCameFrom = null;
        Edge?[]? selectedCameByEdge = null;
        var selectedStartNode = -1;
        var selectedTotalCost = double.PositiveInfinity;
        Entry? selectedEntry = null;
        var traversable = new Dictionary<(int, int), bool>();
        foreach (var entryNode in entryCandidates)
        {
            var gScore = Enumerable.Repeat(
                double.PositiveInfinity,
                _nodes.Count).ToArray();
            var cameFrom = Enumerable.Repeat(-1, _nodes.Count).ToArray();
            var cameByEdge = new Edge?[_nodes.Count];
            var frontier = new PriorityQueue<int, double>();
            gScore[entryNode.NodeId] = entryNode.Cost;
            frontier.Enqueue(
                entryNode.NodeId,
                gScore[entryNode.NodeId] +
                    Distance(
                        _nodes[entryNode.NodeId].Position,
                        _nodes[goalNode].Position));

            while (frontier.TryDequeue(out var current, out _))
            {
                if (current == goalNode)
                {
                    break;
                }
                foreach (var edge in _adjacency[current])
                {
                    // Traversal rules may allow escape out of a crime scene but forbid returning.
                    var key = (current, edge.To);
                    if (canTraverse != null)
                    {
                        if (!traversable.TryGetValue(key, out var clear))
                        {
                            clear = canTraverse(World(_nodes[current].Position), World(_nodes[edge.To].Position));
                            traversable[key] = clear;
                        }
                        if (!clear) continue;
                    }
                    var candidate = gScore[current] + edge.Cost;
                    if (candidate >= gScore[edge.To])
                    {
                        continue;
                    }
                    gScore[edge.To] = candidate;
                    cameFrom[edge.To] = current;
                    cameByEdge[edge.To] = edge;
                    frontier.Enqueue(
                        edge.To,
                        candidate + Distance(
                            _nodes[edge.To].Position,
                            _nodes[goalNode].Position));
                }
            }

            if (!double.IsPositiveInfinity(gScore[goalNode]))
            {
                if (gScore[goalNode] >= selectedTotalCost)
                {
                    continue;
                }
                selectedGScore = gScore;
                selectedCameFrom = cameFrom;
                selectedCameByEdge = cameByEdge;
                selectedStartNode = entryNode.NodeId;
                selectedEntry = entryNode;
                selectedTotalCost = gScore[goalNode];
            }
        }
        if (selectedGScore == null ||
            selectedCameFrom == null ||
            selectedCameByEdge == null ||
            selectedStartNode < 0)
        {
            var blocked = traversable.Where(pair => !pair.Value)
                .OrderBy(pair => Math.Min(Distance(targetPosition, _nodes[pair.Key.Item1].Position),
                    Distance(targetPosition, _nodes[pair.Key.Item2].Position)))
                .Take(6).Select(pair => $"{_nodes[pair.Key.Item1].Position}->{_nodes[pair.Key.Item2].Position}");
            throw new InvalidOperationException("A* could not find a route. Blocked near target: " + string.Join("; ", blocked));
        }

        var edges = new List<Edge>();
        var cursor = goalNode;
        while (selectedCameFrom[cursor] >= 0)
        {
            var edge = selectedCameByEdge[cursor]
                ?? throw new InvalidOperationException("Route reconstruction failed.");
            edges.Add(edge);
            cursor = selectedCameFrom[cursor];
        }
        edges.Reverse();
        var startNode = cursor;
        if (startNode != selectedStartNode)
        {
            throw new InvalidOperationException(
                "Route reconstruction did not reach the selected start anchor.");
        }

        // Execute the same canonical node coordinates that passed collision
        // checks. Clustering must not replace a checked leg with a different one.
        var points = new List<NavigationPoint>(selectedEntry!.Points);
        foreach (var edge in edges)
        {
            points.Add(_nodes[edge.To].Position);
        }
        if (points.Count == 0 || Distance(start, points[0]) > 0.01f)
        {
            points.Insert(0, new NavigationPoint(start.x, start.y));
        }
        var finalPoint = new NavigationPoint(targetPosition.X, targetPosition.Y);
        if (Distance(points[^1], finalPoint) > 0.01)
        {
            points.Add(finalPoint);
        }
        if (points.Count < 2)
        {
            throw new InvalidOperationException("Already at target route node.");
        }

        return new NavigationPlan(
            points.Select(point => new Vector2(point.X, point.Y)).ToArray(),
            startNode,
            goalNode,
            selectedEntry.SnapDistance,
            goalSnap,
            selectedGScore[goalNode] +
                Distance(targetPosition, _nodes[goalNode].Position),
            ResolveNodeName(startNode),
            ResolveNodeName(goalNode));
    }

    private NavigationPoint ResolveTarget(string target)
    {
        if (_anchors.TryGetValue(target, out var position))
        {
            return position;
        }
        var matches = _anchors
            .Where(pair => pair.Key.Contains(target, StringComparison.OrdinalIgnoreCase))
            .ToArray();
        if (matches.Length == 1)
        {
            return matches[0].Value;
        }
        throw new InvalidOperationException($"Unknown or ambiguous target: {target}");
    }

    public NavigationPoint ResolveTargetPosition(string target) =>
        ResolveTarget(target);

    public NavigationPoint FindNearestRoutePoint(
        NavigationPoint position) =>
        _nodes[FindNearestNode(position)].Position;

    public IReadOnlyList<RecoveryAnchorCandidate> FindRecoveryAnchors(
        Vector2 position,
        double maxRadius,
        int maxCount)
    {
        var seenAnchors = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        return _nodes
            .Select(node =>
            {
                var anchorId = ResolveNodeName(node.Id);
                return new RecoveryAnchorCandidate
                {
                    AnchorId = anchorId,
                    Position = node.Position,
                    Distance = (float)Distance(position, node.Position)
                };
            })
            .Where(candidate =>
                candidate.Distance <= maxRadius &&
                seenAnchors.Add(candidate.AnchorId))
            .OrderBy(candidate => candidate.Distance)
            .ThenBy(candidate => candidate.AnchorId, StringComparer.OrdinalIgnoreCase)
            .Take(Math.Max(1, maxCount))
            .ToArray();
    }

    private int FindNearestNode(NavigationPoint point)
    {
        if (_nodes.Count == 0)
        {
            throw new InvalidOperationException("Route graph is empty.");
        }
        var best = 0;
        var bestDistance = Distance(point, _nodes[0].Position);
        for (var index = 1; index < _nodes.Count; index++)
        {
            var candidate = Distance(point, _nodes[index].Position);
            if (candidate < bestDistance - 1e-6f)
            {
                best = index;
                bestDistance = candidate;
            }
        }
        return best;
    }

    private int FindOrAddNode(NavigationPoint position, string? alias)
    {
        for (var index = 0; index < _nodes.Count; index++)
        {
            if (Distance(_nodes[index].Position, position) > _clusterTolerance)
            {
                continue;
            }
            if (!string.IsNullOrWhiteSpace(alias))
            {
                _nodes[index].Aliases.Add(alias);
            }
            return index;
        }

        var node = new Node
        {
            Id = _nodes.Count,
            Position = position
        };
        if (!string.IsNullOrWhiteSpace(alias))
        {
            node.Aliases.Add(alias);
        }
        _nodes.Add(node);
        _adjacency.Add(new List<Edge>());
        return node.Id;
    }

    private string ResolveNodeName(int nodeId) =>
        _nodes[nodeId].Aliases.OrderBy(value => value, StringComparer.Ordinal)
            .FirstOrDefault()
        ?? $"node:{nodeId}";

    private static double PathLength(IReadOnlyList<NavigationPoint> points)
    {
        var result = 0.0;
        for (var index = 1; index < points.Count; index++)
        {
            result += Distance(points[index - 1], points[index]);
        }
        return result;
    }

    private static double Distance(NavigationPoint first, NavigationPoint second) =>
        Math.Sqrt(
            Math.Pow(first.X - second.X, 2) +
            Math.Pow(first.Y - second.Y, 2));

    private static double Distance(Vector2 first, NavigationPoint second) =>
        Math.Sqrt(
            Math.Pow(first.x - second.X, 2) +
            Math.Pow(first.y - second.Y, 2));
}

internal sealed record NavigationPlan(
    IReadOnlyList<Vector2> Points,
    int StartNodeId,
    int GoalNodeId,
    double StartSnapDistance,
    double GoalSnapDistance,
    double PlannedDistance,
    string StartNode,
    string GoalNode);
