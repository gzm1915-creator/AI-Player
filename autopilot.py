"""Deterministic navigation scheduler; model remains responsible for discussion."""

import math
import time

from .impostor import BasicImpostor
from .sample_task import sample_actionable, sample_phase, sample_waiting
from .social import CrewSocial
from .task_batch import TaskBatch
from .task_routes import anchor_accepts_task


def navigation_busy(nav):
    if nav.get("state") == "paused":
        # These commands cannot own a route. Their stale paused status may
        # survive a window/meeting animation after the player can move again.
        return nav.get("action") not in ("close_minigame", "send_chat", "cast_vote")
    return nav.get("state") in ("running", "reporting", "opening_task", "seeking_task_button", "holding")


class Autopilot:
    def __init__(self, bridge):
        self.bridge = bridge
        self.impostor = BasicImpostor(bridge)
        self.social = CrewSocial(bridge)
        self.role_policy = "crewmate"
        self.role_identity = None
        self.enabled = False
        self.state = "disabled"
        self.reason = "自动任务导航未开启"
        self.target = None
        self.pending = None
        self.submitted_at = 0
        self.visited = {}
        self.identity = None
        self.task_batches = TaskBatch()
        self.last_error = ""
        self.failed_stages = set()
        self.target_task = None
        self.handoffs = 0
        self.last_handoff = None
        self.released_commands = set()
        self.recovery_count = 0
        self.last_recovery = None
        self.last_control = {"enabled": False, "source": "initial", "reason": "尚未开启自动游玩"}
        self.last_state = None

    def status(self):
        return {
            "enabled": self.enabled,
            "state": self.state,
            "reason": self.reason,
            "target": self.social.target if self.social.pending else self.target,
            "commandId": self.pending or self.social.pending,
            "lastError": self.last_error,
            "targetTask": self.target_task,
            "handoffs": self.handoffs,
            "lastHandoff": self.last_handoff,
            "rolePolicy": self.role_policy,
            "impostor": self.impostor.status(),
            "social": self.social.status(),
            "recoveryCount": self.recovery_count,
            "lastRecovery": self.last_recovery,
            "lastControl": self.last_control,
        }

    def set_enabled(self, enabled, source="user_control"):
        if enabled and not self.enabled:
            self.impostor.reset(keep_rooms=True)
            self.failed_stages.clear()
            self.visited.clear()
            self.pending, self.target, self.target_task = None, None, None
            self.last_error, self.submitted_at = "", 0
            self.released_commands.clear()
        self.enabled = enabled
        self.last_control = {
            "enabled": enabled,
            "source": source,
            "reason": "已明确开启自动游玩" if enabled else "已明确关闭自动游玩",
        }
        if self.last_state is not None:
            self.record(self.last_state, "autopilot_control", **self.last_control)
        if not enabled:
            self.impostor.resume_control = None
            self.pending = None
            self.social.release()
            self.state, self.reason = "disabled", "自动任务导航已关闭"

    def record(self, state, event, **fields):
        # A full disk or unavailable history must never prevent user controls.
        try:
            journal = getattr(self.bridge, "journal", None)
            if journal is not None:
                journal.record_action(state, event, **fields)
        except (OSError, ValueError):
            pass

    @staticmethod
    def cooldown_key(anchor, task):
        if task and task.get("taskId") is not None:
            if task.get("taskType") == "InspectSample":
                return (task["taskId"], task.get("step", 0), sample_phase(task), anchor)
            return (task["taskId"], task.get("step", 0), anchor)
        return anchor

    def release_target(self, now, cooldown=30):
        if self.pending:
            self.released_commands.add(self.pending)
            if len(self.released_commands) > 64:
                self.released_commands = {self.pending}
            release = getattr(self.bridge, "release_navigation", None)
            if release:
                release(self.pending)
        self.visited[self.cooldown_key(self.target, self.target_task)] = now + cooldown
        self.pending = None
        self.submitted_at = now

    def accept_handoff(self, state, solver, now):
        if not self.pending or not self.target_task or self.target_task.get("taskId") is None:
            return
        task_id = self.target_task["taskId"]
        opened = state.get("minigameOpen") and state.get("openMinigame", {}).get("taskId") == task_id
        solver_owns = solver.get("navigationCommandId") == self.pending and solver.get("taskId") == task_id
        task = next((t for t in state.get("tasks", []) if t.get("taskId") == task_id), {})
        progressed = task.get("complete") or task.get("step", 0) > self.target_task.get("step", 0)
        if opened or solver_owns or progressed:
            self.handoffs += 1
            self.last_handoff = {
                "navigationCommandId": self.pending,
                "taskId": task_id,
                "initialStep": self.target_task.get("step", 0),
                "evidence": "task_window" if opened else "skill_owner" if solver_owns else "task_progress",
            }
            self.release_target(now)
            self.last_error = ""

    def tick(self, state):
        self.last_state = state
        if not self.enabled:
            return
        if not state.get("connected"):
            self.social.forecast.clear()
            self.social.group, self.social.estimates = [], []
            self.impostor.forecast.clear()
            self.impostor.search_plan, self.impostor.search_estimate = None, None
            self.state, self.reason = "waiting", "等待新鲜游戏遥测"
            return
        role = state.get("ownRole") or {}
        if (
            state.get("ventTravel", {}).get("busy")
            or state.get("ventRole", {}).get("inVent")
            or state.get("ventRole", {}).get("walkingToVent")
        ) and (role.get("roleType") != "Impostor" or not self.impostor.vent_command):
            self.state, self.reason = (
                "waiting",
                state.get("ventTravel", {}).get("reason") or "管道技能执行中，暂停步行与击杀",
            )
            return
        role_identity = (state.get("sessionId"), state.get("matchId"), role.get("roleType"))
        if self.role_identity != role_identity:
            if self.role_identity is not None:
                owned = self.pending or self.social.pending or self.impostor.pending
                if owned:
                    self.bridge.stop_navigation(owned)
            self.role_identity = role_identity
            self.pending, self.target, self.target_task = None, None, None
            self.social.release()
            self.identity = None
            self.impostor.reset()
        if role.get("isImpostor") or role.get("roleType") == "Impostor":
            if state.get("runtimeControl", {}).get("autoReportBodies"):
                self.bridge.set_runtime("autoReportBodies", False)
            self.role_policy = "basic_impostor"
            self.impostor.tick(state)
            result = self.impostor.status()
            self.state, self.reason, self.target = result["state"], result["reason"], result["target"]
            return
        self.role_policy = "crewmate"
        now = time.monotonic()
        anchors = self.bridge.route_anchors()
        self.social.observe(state, now, anchors)
        if role.get("isDead"):
            if self.pending or self.social.pending:
                self.bridge.halt()
            self.pending = None
            self.social.release()
            self.state, self.reason = "waiting", "角色已死亡，停止船员移动"
            return
        if not state.get("connected"):
            self.state, self.reason = "waiting", "等待新鲜游戏遥测"
            return
        identity = self.task_batches.observe(state)
        if identity != self.identity:
            self.identity = identity
            self.pending, self.target, self.visited, self.failed_stages = None, None, {}, set()
            self.target_task = None
            self.handoffs, self.last_handoff = 0, None
            self.released_commands.clear()
        solver = state.get("runtimeStatus", {}).get("minigameAutomation", {})
        if solver.get("state") == "unsupported" or (solver.get("state") == "failed" and solver.get("attempts", 0) >= 3):
            self.failed_stages.add((solver.get("taskId"), solver.get("initialStep", 0)))
        if state.get("meetingOpen") or state.get("phase") != "TASKS":
            # The game cancels the route on meetings. Votes/chat subsequently
            # replace the command receipt; resume by planning from the new pose.
            self.pending, self.target_task = None, None
            self.submitted_at = now
            self.state, self.reason = "waiting", "等待会议结束或进入任务阶段"
            return
        if state.get("mapId") not in ("TheSkeld", "Skeld"):
            self.state, self.reason = "blocked", "当前仅支持 The Skeld"
            return
        self.accept_handoff(state, solver, now)
        nav = state.get("navigation", {})
        if state.get("runtimeControl", {}).get("autoNavigation") is False:
            self.state, self.reason = "waiting", "导航许可已关闭，等待明确开启；不会自动覆盖用户设置"
            return
        # Consume a navigation receipt before the minigame starts issuing its
        # own commands through the same file. Arrival is not task completion.
        if (
            self.pending
            and nav.get("commandId") == self.pending
            and nav.get("state") in ("completed", "failed", "stuck", "stopped")
        ):
            self.release_target(now, 30 if nav.get("state") == "completed" else 60)
            self.last_error = nav.get("error") or ""
            self.state, self.reason = "holding", "导航已结束，等待任务执行或选择下一阶段"
            return
        if solver.get("busy") or state.get("minigameOpen") or not state.get("self", {}).get("canMove"):
            solver = state.get("runtimeStatus", {}).get("minigameAutomation", {})
            self.state, self.reason = "waiting", solver.get("reason") or "小游戏开启或角色暂不可移动"
            return
        try:
            if (
                self.pending
                and nav.get("commandId") == self.pending
                and nav.get("state") == "running"
                and self.social.can_preempt_task(state, anchors, now)
            ):
                self.bridge.command("stop")
                self.release_target(now, cooldown=3)
                self.state, self.reason = "holding", "剩余任务较少且任务点较远，停止自己的旧路线后跟随团体"
                return
        except (ValueError, OSError) as exc:
            self.last_error = str(exc)
            return
        if self.pending:
            if nav.get("commandId") != self.pending:
                if now - self.submitted_at < 5:
                    self.state, self.reason = "submitted", "等待游戏接收导航指令"
                    return
                self.last_error = "旧任务路线未获确认，释放请求后重新规划；其他控制器的路线继续保留"
                self.last_recovery = {"reason": self.last_error, "commandId": self.pending}
                self.recovery_count += 1
                self.record(state, "autopilot_recovery", **self.last_recovery)
                self.release_target(now, cooldown=min(30, 3 * self.recovery_count))
                self.state, self.reason = "waiting", self.last_error
                return
            if nav.get("state") == "navigation_disabled" or now - self.submitted_at > 65:
                try:
                    self.bridge.stop_navigation(self.pending)
                except (ValueError, OSError) as exc:
                    self.last_error = str(exc)
                    self.state, self.reason = "waiting", "等待能停止旧路线后再恢复调度"
                    return
                self.last_recovery = {
                    "reason": "旧任务路线执行许可变化或超时，停止后重新规划",
                    "commandId": self.pending,
                }
                self.recovery_count += 1
                self.record(state, "autopilot_recovery", **self.last_recovery)
                self.release_target(now, cooldown=15)
                self.state, self.reason = "waiting", self.last_recovery["reason"]
                return
            if nav.get("state") in ("running", "paused", "reporting", "opening_task", "seeking_task_button", "holding"):
                self.state, self.reason = "moving", "游戏导航执行中"
                return
            self.state, self.reason = "waiting", "等待导航状态结束"
            return
        released_pause = nav.get("state") == "paused" and nav.get("commandId") in self.released_commands
        if navigation_busy(nav) and not released_pause:
            if self.social.pending:
                try:
                    self.social.tick(state, anchors, now)
                    self.state, self.reason = "moving", self.social.reason
                    self.last_error = ""
                except (ValueError, OSError, TypeError) as exc:
                    self.state, self.reason = "error", str(exc)
            else:
                self.state, self.reason = "waiting", "已有其他导航指令，等待其结束"
            return
        try:
            if self.social.tick(state, anchors, now):
                self.state, self.reason = "holding" if self.social.phase == "grouped" else "moving", self.social.reason
                self.last_error = ""
                return
        except (ValueError, OSError, TypeError) as exc:
            self.state, self.reason = "error", str(exc)
            self.last_error = str(exc)
            return
        if now - self.submitted_at < 3:
            return
        pos = state.get("self", {}).get("position", {})
        if "x" not in pos or "y" not in pos:
            self.state, self.reason = "blocked", "缺少本地角色位置"
            return

        def dist(a, b):
            return math.hypot(a["x"] - b["x"], a["y"] - b["y"])

        eligible = [a for a in anchors if a.get("kind") == "task" and "x" in a.get("position", {})]
        targets = []
        for task in state.get("tasks", []):
            if task.get("taskType") in ("FixLights", "RestoreOxy", "FixComms", "ResetReactor"):
                continue  # Emergency console routes are explicitly deferred.
            if task.get("complete") or (task.get("taskId"), task.get("step", 0)) in self.failed_stages:
                continue
            if not sample_actionable(task):
                continue
            compatible = [a for a in eligible if anchor_accepts_task(a, task)]
            for location in task.get("locations", []):
                if "x" not in location or "y" not in location or not compatible:
                    continue
                closest = min(compatible, key=lambda a: dist(a["position"], location))
                if dist(closest["position"], location) <= 2.5:
                    targets.append(
                        (closest, {**location, **({"taskId": task["taskId"]} if "taskId" in task else {})}, task)
                    )
        targets = [
            item for item in targets if self.visited.get(self.cooldown_key(item[0]["anchorId"], item[2]), 0) <= now
        ]
        if targets:
            targets = self.social.nearby_tasks(targets, now)
        if not targets:
            waiting = [t for t in state.get("tasks", []) if sample_waiting(t)]
            if waiting:
                points = [p for t in waiting for p in t.get("locations", []) if "x" in p and "y" in p]
                if points and any(dist(pos, p) < 6 for p in points):
                    exits = [
                        a
                        for a in anchors
                        if a.get("kind") not in ("task", "vent")
                        and a.get("roomId") != "MedBay"
                        and "x" in a.get("position", {})
                        and dist(pos, a["position"]) <= 14
                        and all(dist(a["position"], p) >= 6 for p in points)
                        and self.visited.get(a["anchorId"], 0) <= now
                    ]
                    if exits:
                        target = min(exits, key=lambda a: dist(pos, a["position"]))
                        try:
                            result = self.bridge.navigate(target["anchorId"], interact=False)
                            self.pending, self.target, self.target_task = result["commandId"], target["anchorId"], None
                            self.submitted_at = now
                            self.state, self.reason = "moving", "仅剩样本培养等待，沿已知路线离开医务室并寻找同行玩家"
                        except (OSError, ValueError) as exc:
                            self.visited[target["anchorId"]] = now + 30
                            self.state, self.reason = "waiting", "离开医务室的路线暂不可用：" + str(exc)
                        return
                self.state, self.reason = "waiting", "样本在后台培养；已离开任务点，等待就绪或寻找可跟随团体"
                return
            self.state, self.reason = "waiting", "无可匹配的未完成任务目标，或目标停留冷却中；可手动指定锚点"
            return
        target, task_position, task = min(targets, key=lambda item: self.social.task_cost(pos, item[0]["position"]))
        try:
            result = self.bridge.navigate(target["anchorId"], task_position)
            self.pending = result["commandId"]
            self.target = target["anchorId"]
            self.target_task = {
                "taskId": task.get("taskId"),
                "step": task.get("step", 0),
                "taskType": task.get("taskType"),
                "sample": task.get("sample"),
            }
            self.submitted_at = now
            self.state, self.reason = "submitted", "已选择未完成任务附近锚点并提交导航"
            self.last_error = ""
        except (OSError, ValueError, TypeError) as exc:
            self.state, self.reason = "error", str(exc)
            self.last_error = str(exc)
            self.submitted_at = now
