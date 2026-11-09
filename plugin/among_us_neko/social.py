"""Task-aware grouping with bounded local forecasts and navigation hysteresis."""
from .prediction import PlayerForecast, distance, xy
from .sample_task import sample_actionable

REPAIRS = {"FixLights", "RestoreOxy", "FixComms", "ResetReactor"}


def _group(players, origin):
    if not xy(origin):
        return []
    groups = []
    for first in players:
        group = [first]
        for person in sorted(players, key=lambda p: (distance(first, p), p["playerId"])):
            if person["playerId"] != first["playerId"] and all(distance(person, member) <= 4 for member in group):
                group.append(person)
        if len(group) >= 2:
            groups.append(group)
    return min(groups, key=lambda g: (-len(g), sum(distance(origin, p) for p in g) / len(g),
                                     tuple(sorted(p["playerId"] for p in g)))) if groups else []


def visible_group(state):
    if not state.get("connected") or state.get("playerDetection", {}).get("status") != "ok":
        return []
    players = {p["playerId"]: p for p in state.get("nearbyPlayers", [])
               if type(p.get("playerId")) is int and p["playerId"] != state.get("self", {}).get("playerId")
               and not p.get("isDead") and not p.get("inVent") and xy(p)}
    return _group(list(players.values()), state.get("self", {}).get("position"))


class CrewSocial:
    OPENING_SECONDS = 40
    FOLLOW_SECONDS = 90
    TASK_DISTANCE = 10
    REVIEW_SECONDS = .25
    REPLAN_SECONDS = 1.2

    def __init__(self, bridge):
        self.bridge = bridge
        self.identity = None
        self.reset()

    def reset(self):
        self.forecast = PlayerForecast()
        self.started = None
        self.follow_since = None
        self._elapsed = 0
        self.force_task = False
        self.task_commit = None
        self.remaining = None
        self.stage = "unknown"
        self.tasks = []
        self.pending = None
        self.intent = "join"
        self.target = None
        self.goal = None
        self.submitted = 0
        self.next_attempt = 0
        self.blocked = {}
        self.group = []
        self.estimates = []
        self.last_review = -1e9
        self.task_priority = False
        self.last_recorded_group = None
        self.phase, self.reason = "waiting", "等待任务进度和玩家观察"

    def observe(self, state, now, anchors=()):
        identity = (state.get("sessionId"), state.get("matchId"), state.get("mapId"),
                    (state.get("ownRole") or {}).get("roleType"), (state.get("history") or {}).get("taskBatch"))
        if identity != self.identity:
            self.reset()
            self.identity = identity
        self.forecast.update(state, now)
        self.estimates = self.forecast.players(now, anchors)
        if not state.get("connected") or state.get("meetingOpen") or state.get("phase") != "TASKS":
            self.release()
            self.group = []
            self.phase, self.reason = "waiting", "会议期间暂停跟随并清空位置预测"
            return
        if self.started is None:
            self.started = now
        catalog = [t for t in state.get("tasks", []) if t.get("taskType") not in REPAIRS]
        previous_remaining = self.remaining
        remaining = [t for t in catalog if not t.get("complete")]
        self.tasks = [t for t in remaining if sample_actionable(t)]
        self.remaining = len(remaining) if catalog else None
        if not self.tasks or (self.task_commit and not any(t.get("taskId") == self.task_commit[0] for t in self.tasks)):
            self.task_commit, self.force_task = None, False
        self.stage = "unknown" if self.remaining is None else "done" if self.remaining == 0 else "late" if self.remaining <= 2 else "early"
        committed = next((t for t in catalog if self.task_commit and t.get("taskId") == self.task_commit[0]), None)
        progressed = (self.task_commit and committed and
                      (committed.get("complete") or committed.get("step", 0) > self.task_commit[1]))
        if progressed or (previous_remaining is not None and self.remaining is not None and self.remaining < previous_remaining):
            self.task_commit, self.follow_since, self.force_task = None, None, False
        usable = [p for p in self.estimates if not p["suspectedTaskStop"]]
        old_ids = {p["playerId"] for p in self.group}
        if now - self.last_review >= self.REVIEW_SECONDS or not old_ids.issubset({p["playerId"] for p in usable}):
            self.group = _group(usable, state.get("self", {}).get("position"))
            self.last_review = now
        else:
            self.group = [p for p in usable if p["playerId"] in old_ids]
        nearby = self.near_task(state)
        if self.stage == "late" and self.follow_since is not None and now - self.follow_since >= self.FOLLOW_SECONDS:
            self.force_task = True
        if self.stage == "late" and not self.task_commit and (nearby or self.force_task):
            task = nearby or self.closest_task(state)
            if task:
                self.task_commit = (task.get("taskId"), task.get("step", 0))
        self.task_priority = bool(self.task_commit or self.force_task or self.stage == "unknown")
        if self.stage == "early" and self.near_task(state, radius=6):
            self.task_priority = True
        if self.stage == "late" and self.group and not self.task_priority and self.follow_since is None:
            self.follow_since = now
        self._elapsed = max(0, now - self.follow_since) if self.follow_since is not None else 0

    def task_points(self):
        return [(t, p) for t in self.tasks for p in t.get("locations", []) if xy(p)]

    def closest_task(self, state):
        origin = state.get("self", {}).get("position") or {}
        points = self.task_points()
        return min(points, key=lambda item: distance(origin, item[1]))[0] if xy(origin) and points else None

    def near_task(self, state, radius=TASK_DISTANCE):
        origin = state.get("self", {}).get("position") or {}
        points = [(t, p) for t, p in self.task_points() if xy(origin) and distance(origin, p) <= radius]
        return min(points, key=lambda item: distance(origin, item[1]))[0] if points else None

    def active(self, now):
        return not self.task_priority and (self.stage in ("late", "done") or
            self.stage == "early" and self.started is not None and now - self.started < self.OPENING_SECONDS)

    def status(self):
        return {"phase": self.phase, "reason": self.reason, "commandId": self.pending, "target": self.target,
                "openingSeconds": self.OPENING_SECONDS, "requiredOthers": 2, "groupSize": len(self.group),
                "taskStage": self.stage, "remainingTasks": self.remaining, "taskPriority": self.task_priority,
                "followLimitSeconds": self.FOLLOW_SECONDS, "taskDetachDistance": self.TASK_DISTANCE,
                "followElapsedSeconds": round(self._elapsed, 1) if hasattr(self, "_elapsed") else 0,
                "predictionTtlSeconds": PlayerForecast.TTL, "decisionIntervalSeconds": self.REVIEW_SECONDS,
                "minimumReplanSeconds": self.REPLAN_SECONDS,
                "members": self.group, "motionEstimates": self.estimates,
                "notice": "预测位置不是实时目击，不用于击杀或身份判断；疑似任务停留仅由静止和任务点距离估计。"}

    def near_group(self, state):
        pos = state.get("self", {}).get("position") or {}
        return xy(pos) and sum(distance(pos, p) <= 3.5 for p in self.group) >= 2

    def destination(self, state, anchors, now):
        pos = state.get("self", {}).get("position") or {}
        eligible = [a for a in anchors if a.get("kind") not in ("task", "vent") and xy(a.get("position"))
                    and self.blocked.get(a["anchorId"], 0) <= now and xy(pos)
                    and .8 <= distance(pos, a["position"]) <= 14
                    and sum(distance(a["position"], p) <= 3.5 for p in self.group) >= 2]
        return min(eligible, key=lambda a: (sum(distance(a["position"], p) for p in self.group) / len(self.group)
                                             + .1 * distance(pos, a["position"]))) if eligible and self.group else None

    def wants_join(self, state, anchors, now):
        return (self.active(now) and now >= self.next_attempt and len(self.group) >= 2
                and not self.near_group(state) and not state.get("activeEmergencies")
                and self.destination(state, anchors, now) is not None)

    def can_preempt_task(self, state, anchors, now):
        return self.stage in ("late", "done") and self.wants_join(state, anchors, now)

    def release(self):
        if self.pending:
            self.bridge.release_navigation(self.pending)
            self.pending = None

    def stop_owned(self, state, now):
        if self.pending and state.get("navigation", {}).get("commandId") == self.pending:
            self.bridge.command("stop")
        self.release()
        self.next_attempt = now + .4

    def watch_target(self, state, anchors, now):
        origin = state.get("self", {}).get("position") or {}
        stopped = [p for p in self.estimates if p["suspectedTaskStop"]]
        if not stopped or not xy(origin) or now < self.next_attempt:
            return None
        if all(distance(origin, p) >= 2.5 for p in stopped):
            return None  # Already observing at a distance; do not pace between watch points.
        choices = [a for a in anchors if a.get("kind") not in ("task", "vent") and xy(a.get("position"))
                   and 2 <= distance(origin, a["position"]) <= 8 and self.blocked.get(a["anchorId"], 0) <= now
                   and all(distance(a["position"], p) >= 2.5 for p in stopped)]
        return min(choices, key=lambda a: abs(distance(origin, a["position"]) - 4)) if choices else None

    def tick(self, state, anchors, now):
        nav = state.get("navigation") or {}
        self._elapsed = max(0, now - self.follow_since) if self.follow_since is not None else 0
        unavailable = state.get("playerDetection", {}).get("status") != "ok" or bool(state.get("activeEmergencies"))
        if self.pending:
            if nav.get("commandId") != self.pending:
                if now - self.submitted > 5:
                    self.release()
                    self.next_attempt = now + 5
                    self.phase, self.reason = "waiting", "跟随路线未获确认，不把提交当成到达"
                return True
            if nav.get("state") in ("completed", "failed", "stuck", "stopped"):
                self.blocked[self.target] = now + (8 if nav.get("state") == "completed" else 30)
                self.release()
                self.next_attempt = now + .4
                self.phase, self.reason = "checking", "到达或路线结束，核对新观察和任务距离"
                return True
            changed = (self.goal and self.group and distance(self.goal, self.group_center()) >= 2
                       and now - self.submitted >= self.REPLAN_SECONDS)
            abandon = self.intent == "join" and (not self.active(now) or len(self.group) < 2 or changed or self.near_group(state))
            if self.intent == "watch" and self.wants_join(state, anchors, now):
                abandon = True
            if unavailable or abandon or self.task_priority or now - self.submitted >= 18:
                self.stop_owned(state, now)
                self.phase, self.reason = "checking", "任务优先级或预测发生变化，停止旧路线后重新判断"
                return True
            self.phase, self.reason = (("observing", "离开疑似任务停留者身旁，移至通道观察") if self.intent == "watch" else
                                       ("following", "按短时运动预测跟随；丢失视野最多外推3秒"))
            return True
        if unavailable or self.task_priority or not self.active(now):
            self.phase = "tasks"
            self.reason = ("抱团已达90秒，先完成待办任务" if self.force_task else
                           "进入待办任务点10单位内，先完成任务" if self.task_commit else "继续自身任务，避免在别人任务点旁闲站")
            return False
        if self.group and self.near_group(state):
            ids = tuple(sorted(p["playerId"] for p in self.group if p["visibleNow"]))
            if len(ids) >= 2 and ids != self.last_recorded_group:
                self.bridge.journal.record_action(state, "crew_group_observed", playerIds=list(ids), evidence="live_visible_group")
                self.last_recorded_group = ids
            self.phase, self.reason = "grouped", "跟随团体观察；接近待办任务点10单位时脱团，剩余任务最多等待90秒"
            return self.stage in ("late", "done")
        if self.wants_join(state, anchors, now):
            anchor = self.destination(state, anchors, now)
            result = self.bridge.navigate(anchor["anchorId"], interact=False)
            self.pending, self.target, self.submitted = result["commandId"], anchor["anchorId"], now
            self.goal, self.intent = self.group_center(), "join"
            self.phase, self.reason = "submitted", "沿已知通道靠近团体的短时预测位置"
            return True
        if self.stage == "done":
            anchor = self.watch_target(state, anchors, now)
            if anchor:
                result = self.bridge.navigate(anchor["anchorId"], interact=False)
                self.pending, self.target, self.submitted = result["commandId"], anchor["anchorId"], now
                self.goal, self.intent = None, "watch"
                self.next_attempt = now + 5
                self.phase, self.reason = "observing", "团体疑似停在任务点，移至通道观察并寻找移动团体"
                return True
            self.phase, self.reason = "observing", "自身任务已完成；寻找移动团体，当前没有可靠或可达的跟随目标"
            return True
        self.phase, self.reason = "tasks", "没有可靠移动团体，继续自身任务而非停在别人旁边"
        return False

    def group_center(self):
        return {k: sum(p[k] for p in self.group) / len(self.group) for k in ("x", "y")}

    def nearby_tasks(self, targets, now):
        if self.task_commit:
            committed = [item for item in targets if item[2].get("taskId") == self.task_commit[0]]
            if committed:
                return committed
        return targets

    def task_cost(self, origin, target):
        base = distance(origin, target)
        return base if self.task_priority or not self.group else base + 2 * max(0, sum(distance(target, p) for p in self.group) / len(self.group) - 4)
