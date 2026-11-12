"""Bounded area occupancy and occasional moving-group cover; no hidden players."""

import math
import random

from .awareness import safe_destination
from .prediction import distance, xy
from .room_sweep import room_of
from .social import _group


def sector(point):
    if not xy(point):
        return None
    return "west" if point["x"] < -7 else "east" if point["x"] > 4 else "central"


class PatrolRange:
    MAX_SECONDS = 65
    NAMES = {"west": "西侧片区", "central": "中央片区", "east": "东侧片区"}

    def __init__(self):
        self.area = self.candidate = None
        self.since = self.last = self.crossed = None
        self.elapsed = 0
        self.due = False
        self.visits = {}

    def observe(self, state, now):
        point = state.get("self", {}).get("position")
        area = sector(point) if state.get("connected") and not state.get("meetingOpen") else None
        if area is None:
            self.since = self.last = None
            self.elapsed, self.due = 0, False
            return
        gap = self.last is None or not 0 <= now - self.last <= 5
        self.last = now
        if self.area is None or gap:
            self.area, self.since = area, now
            self.candidate = None
        elif area != self.area:
            if area != self.candidate:
                self.candidate, self.crossed = area, now
            elif now - self.crossed >= 3:
                self.area, self.since = area, self.crossed
                self.visits[area] = now
                self.candidate = None
        else:
            self.candidate = None
        self.elapsed = max(0, now - self.since)
        self.due = self.elapsed >= self.MAX_SECONDS

    def accepts(self, anchor):
        return not self.due or sector(anchor.get("position")) != self.area

    def status(self):
        return {
            "area": self.area,
            "areaName": self.NAMES.get(self.area, "等待位置"),
            "elapsedSeconds": round(self.elapsed, 1),
            "limitSeconds": self.MAX_SECONDS,
            "exitDue": self.due,
            "source": "coarse_local_x_bands",
            "notice": "片区是按自身坐标划分的粗略区域，不是实时房间判定；越界连续3秒后才换区。",
        }


class TrafficFollow:
    REVIEW_SECONDS = 0.25
    REPLAN_SECONDS = 1.2

    def __init__(self):
        self.active = False
        self.members = []
        self.started = self.until = self.next_attempt = 0
        self.duration = 0
        self.last_review = -1e9
        self.goal = None
        self.failures = 0
        self.reason = "等待经过的移动人群"
        self.elapsed = 0

    def observe(self, state, estimates, now):
        self.elapsed = max(0, now - self.started) if self.active else 0
        if state.get("playerDetection", {}).get("status") != "ok" or state.get("activeEmergencies"):
            self.members = []
            return
        eligible = [p for p in estimates if p.get("speed", 0) >= 0.35 and not p.get("suspectedTaskStop")]
        # Keep the current group while it remains coherent; never reset its
        # duration just because another person joins or passes by.
        old_ids = {p["playerId"] for p in self.members}
        same = [p for p in eligible if p["playerId"] in old_ids]
        if len(same) >= 2:
            same = _group(same, state.get("self", {}).get("position"))
        if self.active and len(same) >= 2:
            self.members = same
        elif now - self.last_review >= self.REVIEW_SECONDS or len(same) < 2:
            self.members = _group(eligible, state.get("self", {}).get("position"))
            self.last_review = now
        else:
            self.members = same
        if self.members and math.hypot(*self.velocity()) < 0.35:
            self.members = []  # Two people passing in opposite directions are not a flow.

    def velocity(self):
        return (
            tuple(sum(p.get(k, 0) for p in self.members) / len(self.members) for k in ("velocityX", "velocityY"))
            if self.members
            else (0, 0)
        )

    def center(self):
        return {k: sum(p[k] for p in self.members) / len(self.members) for k in ("x", "y")}

    def can_start(self, now):
        return (
            not self.active and now >= self.next_attempt and sum(p.get("visibleNow", False) for p in self.members) >= 2
        )

    def begin(self, now):
        self.active, self.started = True, now
        self.duration = round(random.uniform(12, 24), 1)
        self.until = now + self.duration
        self.failures = 0
        self.reason = "短时随移动人群同行，之后恢复假任务与跨区巡视"

    def finish(self, now, reason):
        self.active = False
        self.next_attempt = now + random.uniform(30, 50)
        self.goal = None
        self.reason = reason

    def end_reason(self, now, range_policy):
        if now >= self.until:
            return "本轮随人流时间已到，恢复假任务与巡视"
        if len(self.members) < 2:
            return "人群停下、分散或失联，结束同行，避免在别人任务点旁闲站"
        if range_policy.due and sector(self.center()) == range_policy.area:
            return "当前片区停留过久，结束本区同行并转往其他片区"
        if self.failures >= 2:
            return "同行路线连续失败，冷却同行后恢复其他路线"
        return None

    def destination(self, state, anchors, sweep, range_policy, blocked, now):
        if len(self.members) < 2:
            return None
        center = self.center()
        pos = state.get("self", {}).get("position") or {}
        vx, vy = self.velocity()
        speed = max(0.35, math.hypot(vx, vy))
        choices = [
            a
            for a in anchors
            if a.get("kind") not in ("task", "vent")
            and xy(a.get("position"))
            and xy(pos)
            and 0.8 <= distance(pos, a["position"]) <= 14
            and distance(center, a["position"]) <= 3.5
            and sweep.allowed(room_of(a))
            and range_policy.accepts(a)
            and safe_destination(state, a["position"])
            and blocked.get(a["anchorId"], 0) <= now
            and ((a["position"]["x"] - center["x"]) * vx + (a["position"]["y"] - center["y"]) * vy) / speed >= -1.5
        ]
        return (
            min(choices, key=lambda a: distance(a["position"], center) + 0.1 * distance(pos, a["position"]))
            if choices
            else None
        )

    def status(self):
        return {
            "active": self.active,
            "reason": self.reason,
            "members": self.members,
            "requiredOthers": 2,
            "durationSeconds": self.duration,
            "elapsedSeconds": round(self.elapsed, 1),
            "minSeconds": 12,
            "maxSeconds": 24,
            "decisionIntervalSeconds": self.REVIEW_SECONDS,
            "minimumReplanSeconds": self.REPLAN_SECONDS,
            "notice": "沿用船员的移动预测与分组方式；失联最多3秒，预测不能当作真实目击或击杀依据。",
        }
