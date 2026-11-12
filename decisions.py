"""Explain native opportunities from current observations; never choose hidden targets."""

from .awareness import kill_ready, potential_witnesses, safe_destination
from .prediction import distance, xy


def kill_decisions(state):
    role = state.get("ownRole") or {}
    origin = state.get("self", {}).get("position") or {}
    result = []
    for player in state.get("nearbyPlayers", []):
        pid = player.get("playerId")
        if (
            pid == state.get("self", {}).get("playerId")
            or pid in role.get("teammateIds", [])
            or player.get("isDead")
            or player.get("inVent")
            or not xy(player)
            or not xy(origin)
        ):
            continue
        reasons = []
        witnesses = potential_witnesses(state, player)
        safety = next((s for s in role.get("killSafety", []) if s.get("targetPlayerId") == pid), {})
        if state.get("playerDetection", {}).get("status") != "ok":
            reasons.append("玩家检测不可用")
        if role.get("killCooldown", 1) > 0:
            reasons.append("击杀冷却未结束")
        if witnesses:
            reasons.append("当前或近期玩家可能目击")
        if not kill_ready(state, pid):
            reasons.append(
                safety.get("reason")
                or (
                    "照明破坏生效，等待本次安全判定"
                    if safety.get("lightsWaitBypassed")
                    else "连续3至5秒安全观察尚未满足"
                )
            )
        if not safe_destination(state, player):
            reasons.append("目标在本局击杀现场避让区")
        if pid != role.get("killTargetId") or not role.get("canKill"):
            reasons.append("原生按钮尚未允许该目标，需继续接近并核对距离")
        if not state.get("self", {}).get("canMove"):
            reasons.append("角色暂不可移动")
        result.append(
            {
                "playerId": pid,
                "distance": round(distance(origin, player), 2),
                "nativeSelected": pid == role.get("killTargetId"),
                "nativeCanKill": bool(role.get("canKill")),
                "safeReady": kill_ready(state, pid),
                "safety": safety,
                "lightsWaitBypassed": bool(safety.get("lightsWaitBypassed")),
                "witnessIds": sorted({p.get("playerId") for p in witnesses if type(p.get("playerId")) is int}),
                "reasons": reasons,
                "reason": "；".join(reasons) or "原生按钮与安全条件已满足，等待本次行动调度",
                "evidence": "live_observation_only",
            }
        )
    return sorted(result, key=lambda p: p["distance"])
