"""Readable vent-network observations. Native game rules remain authoritative."""

import math
from collections import deque

from .spatial import anchor_label


def path_between(vents, start, target):
    nodes = {v["id"]: v for v in vents if type(v.get("id")) is int}
    if start not in nodes or target not in nodes:
        return []
    previous, queue = {start: None}, deque([start])
    while queue and len(previous) <= 64:
        current = queue.popleft()
        if current == target:
            route = []
            while current is not None:
                route.append(current)
                current = previous[current]
            return route[::-1]
        for key in ("leftId", "rightId", "centerId"):
            neighbor = nodes[current].get(key)
            if type(neighbor) is int and neighbor in nodes and neighbor not in previous:
                previous[neighbor] = current
                queue.append(neighbor)
    return []


def vent_options(state, anchors):
    role = state.get("ventRole") or {}
    if not state.get("connected") or role.get("roleType") != "Impostor" or not role.get("canVent"):
        return []
    position = state.get("self", {}).get("position") or {}
    references = [
        a
        for a in anchors
        if a.get("kind") == "vent"
        and all(
            type(a.get("position", {}).get(k)) in (float, int) and math.isfinite(a["position"][k]) for k in ("x", "y")
        )
    ]
    options = []
    vents = [
        v
        for v in state.get("vents", [])
        if type(v.get("id")) is int and all(type(v.get(k)) in (float, int) and math.isfinite(v[k]) for k in ("x", "y"))
    ]
    for v in vents:
        reference = min(
            references, key=lambda a: math.hypot(a["position"]["x"] - v["x"], a["position"]["y"] - v["y"]), default=None
        )
        labelled = (
            reference and math.hypot(reference["position"]["x"] - v["x"], reference["position"]["y"] - v["y"]) <= 2.5
        )
        label = anchor_label(reference["anchorId"], reference.get("roomId")) if labelled else "位置未标注"
        own_distance = (
            math.hypot(position["x"] - v["x"], position["y"] - v["y"])
            if all(type(position.get(k)) in (float, int) for k in ("x", "y"))
            else None
        )
        options.append(
            {
                **v,
                "label": f"{label} · 管道 #{v['id']}",
                "distance": round(own_distance, 2) if own_distance is not None else None,
                "connectedVentIds": [other["id"] for other in vents if path_between(vents, v["id"], other["id"])],
                "entryInRange": own_distance is not None and own_distance <= max(0, v.get("usableDistance", 0)),
            }
        )
    return options
