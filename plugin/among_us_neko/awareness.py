"""Conservative witness checks. Last sightings never become live positions."""
import math


def potential_witnesses(state, target):
    role = state.get("ownRole") or {}
    excluded = {state.get("self", {}).get("playerId"), target.get("playerId"), *role.get("teammateIds", [])}
    origin = state.get("self", {}).get("position") or {}
    window = next((s.get("witnessMemorySeconds") or s.get("requiredSeconds", 5) for s in role.get("killSafety", []) if s.get("targetPlayerId") == target.get("playerId")), 5)
    window = min(5, max(3, window))
    result = []
    for historical, people in ((False, state.get("nearbyPlayers", [])), (True, state.get("recentPlayers", []))):
        for player in people:
            if player.get("playerId") in excluded or player.get("isDead") or player.get("inVent"):
                continue
            radius = 4
            if historical:
                if not 0 <= player.get("ageSeconds", float("inf")) <= window:
                    continue
                radius += max(0, min(6, player.get("uncertaintyRadius", 0)))
            if any(all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) for p in (point, player) for k in ("x", "y"))
                   and math.hypot(point["x"] - player["x"], point["y"] - player["y"]) <= radius
                   for point in (origin, target)):
                result.append(player)
    return result


def kill_ready(state, target_id):
    role = state.get("ownRole") or {}
    return not role.get("killSafetySupported") or any(s.get("targetPlayerId") == target_id and s.get("ready")
                                                       for s in role.get("killSafety", []))


def safe_destination(state, position):
    return all(math.hypot(position["x"] - s["x"], position["y"] - s["y"]) >= s.get("avoidRadius", 3)
               for s in state.get("crimeScenes", []) if "x" in s and "y" in s)
