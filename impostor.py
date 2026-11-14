"""Ordinary Impostor: cover, visible opportunity, native kill receipt, escape.

Mechanisms inspired by the user-supplied DeepBot. Uses only this bridge's
local observations and verified route anchors; never creates virtual players.
"""

import logging
import math
import random
import time

from .awareness import kill_ready, potential_witnesses, safe_destination
from .decisions import kill_decisions
from .impostor_roaming import PatrolRange, TrafficFollow, sector
from .prediction import PlayerForecast, xy
from .room_sweep import RoomSweep, room_of
from .spatial import ROOMS
from .task_routes import anchor_accepts_task


def distance(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


class BasicImpostor:
    PATROL_ROOM_COOLDOWN = 90
    PATROL_ANCHOR_COOLDOWN = 180
    MAX_COVER_VISITS = 2
    PATROLS_BETWEEN_COVERS = 2

    def __init__(self, bridge):
        self.bridge = bridge
        self.identity = None
        self.reset()

    def reset(self, keep_rooms=False):
        retained = {
            key: getattr(self, key)
            for key in (
                "sweep",
                "range_policy",
                "traffic",
                "fake_visits",
                "patrol_visits",
                "patrol_room_last_anchor",
                "patrol_rooms_until",
                "patrol_anchors_until",
                "last_cover_room",
                "last_cover_anchor",
                "last_patrol_room",
                "last_patrol_anchor",
                "cover_counts",
                "cover_anchor_counts",
                "extra_cover_plan",
                "patrols_since_cover",
            )
            if keep_rooms and hasattr(self, key)
        }
        self.forecast = PlayerForecast()
        self.search_plan = None
        self.search_estimate = None
        self.phase = "cover"
        self.pursuit_status = {}
        self.pending = None
        self.intent = None
        self.target = None
        self.target_player = None
        self.submitted = 0
        self.dwell_until = 0
        self.opening_done = False
        self.kill_command = None
        self.body = None
        self.escape_until = 0
        self.avoid = {}
        self.visited = {}
        self.released = set()
        self.sabotage_command = None
        self.sabotage_submitted = 0
        self.next_sabotage = 0
        self.fake_task = None
        self.fake_visits = {}
        self.cover_counts = {}
        self.cover_anchor_counts = {}
        self.extra_cover_plan = None
        self.patrols_since_cover = 0
        self.last_cover_room = None
        self.last_cover_anchor = None
        self.cover_seconds = None
        self.next_activity = "cover"
        self.sweep = RoomSweep()
        self.range_policy = PatrolRange()
        self.traffic = TrafficFollow()
        self.patrol_visits = {}
        self.patrol_room_last_anchor = {}
        self.last_patrol_room = None
        self.last_patrol_anchor = None
        self.patrol_count = 0
        self.patrol_rooms_until = {}
        self.patrol_anchors_until = {}
        self.patrol_dwell_until = 0
        self.patrol_stop_seconds = 0
        self.patrol_room = None
        self.recovery_count = 0
        self.last_recovery = None
        self.resume_control = None
        self.last_pursuit_failure = None
        self.kill_checks = []
        self.vent_attempted = False
        self.vent_plan = None
        self.vent_command = None
        self.vent_submitted = 0
        self.escape_reason = ""
        self.result = {"state": "waiting", "reason": "等待普通内鬼行动", "target": None}
        self.__dict__.update(retained)
        if keep_rooms:
            self.traffic.active = False

    def status(self):
        return {
            **self.result,
            "policy": "basic_impostor",
            "phase": self.phase,
            "target": self.target,
            "commandId": self.pending or self.kill_command,
            "targetPlayerId": self.target_player,
            "pursuit": getattr(self, "pursuit_status", {}),
            "motionEstimate": self.search_estimate,
            "predictionTtlSeconds": PlayerForecast.TTL,
            "killSafety": getattr(self, "kill_safety", []),
            "killDecisions": self.kill_checks,
            "lastPursuitFailure": self.last_pursuit_failure,
            "sabotageCommandId": self.sabotage_command,
            "fakeTask": self.fake_task,
            "coverSeconds": self.cover_seconds,
            "coverBudget": {
                "maxPerTask": self.MAX_COVER_VISITS,
                "taskVisits": dict(self.cover_counts),
                "anchorVisits": dict(self.cover_anchor_counts),
                "extraAnchors": list(self.extra_cover_plan or []),
                "maxPerExtraAnchor": 1,
                "patrolsBetweenCovers": self.PATROLS_BETWEEN_COVERS,
                "patrolsSinceCover": self.patrols_since_cover,
            },
            "patrolCount": self.patrol_count,
            "nextActivity": self.next_activity,
            "roomSweep": self.sweep.status(),
            "patrolRange": self.range_policy.status(),
            "trafficFollow": self.traffic.status(),
            "patrolRoom": self.patrol_room,
            "patrolStopSeconds": self.patrol_stop_seconds,
            "patrolRoomCooldownSeconds": self.PATROL_ROOM_COOLDOWN,
            "patrolAnchorCooldownSeconds": self.PATROL_ANCHOR_COOLDOWN,
            "recoveryCount": self.recovery_count,
            "lastRecovery": self.last_recovery,
            "ventCommandId": self.vent_command,
            "ventPlan": self.vent_plan,
            "escapeReason": self.escape_reason,
            "deferred": ["破坏点导航", "复杂管道埋伏战术", "特殊内鬼角色"],
        }

    def note(self, state, event, **fields):
        try:
            self.bridge.journal.record_action(state, event, visibility="impostor_private", **fields)
        except (OSError, ValueError):
            pass

    def stop_owned(self):
        if self.pending:
            try:
                self.bridge.stop_navigation(self.pending)
            except (OSError, ValueError) as exc:
                # A lost sample can prevent a stop command. Retain a revision
                # ticket so only this temporary gate can later be restored.
                self.resume_control = self.bridge.set_runtime("autoNavigation", False)
                self.last_recovery = {"reason": "停止请求失败，暂时关闭执行器并等待恢复", "error": str(exc)}
            finally:
                self.release()

    def release(self):
        if self.pending:
            self.released.add(self.pending)
            if len(self.released) > 64:
                self.released = {self.pending}
            self.bridge.release_navigation(self.pending)
            self.pending = None

    def route(self, state, anchor, intent, now, player=None, task=None):
        result = self.bridge.navigate(
            anchor["anchorId"], interact=False, **({"escape": True} if intent == "escape" else {})
        )
        self.pending, self.target, self.intent = result["commandId"], anchor["anchorId"], intent
        self.target_player = player
        self.phase = intent
        if intent == "patrol":
            self.patrol_room = room_of(anchor)
            self.patrol_rooms_until[self.patrol_room] = now + self.PATROL_ROOM_COOLDOWN
            self.patrol_anchors_until[self.target] = now + self.PATROL_ANCHOR_COOLDOWN
        if intent == "cover":
            self.fake_task = (
                {
                    "taskId": task.get("taskId"),
                    "taskType": task.get("taskType"),
                    "step": task.get("step", 0),
                    "roomId": room_of(anchor),
                    "source": task.get("source", "own_task_bar"),
                    "coverKey": self.cover_key(task),
                }
                if task
                else None
            )
        self.submitted = now
        self.note(
            state,
            "impostor_intent",
            intent=intent,
            targetAnchorId=self.target,
            targetPlayerId=player,
            fakeTask=self.fake_task if intent == "cover" else None,
            positionSource="motion_forecast" if intent == "search" else "live_observation",
        )
        self.result.update(
            state="submitted",
            reason={
                "cover": "前往非可视任务点做掩护，不操作任务",
                "patrol": "当前片区停留已达上限，前往其他片区巡视"
                if self.range_policy.due
                else "前往未处于冷却的房间内巡查点，途中不安排停留",
                "flow": "沿已有通道短时随移动人群同行，不追着任务停留者闲站",
                "hunt": "接近当前可见的落单目标",
                "search": "沿已知通道短时寻找失联目标；预测不用于击杀",
                "escape": "击杀已确认，沿已有路线远离现场",
            }[intent],
        )

    def pursue(self, state, player, now):
        result = self.bridge.pursue_player(player["playerId"])
        self.search_plan, self.search_estimate = None, None
        self.pending, self.target_player = result["commandId"], player["playerId"]
        self.target, self.intent, self.phase = "temp.player." + str(self.target_player), "hunt", "hunt"
        self.submitted = now
        self.note(
            state,
            "impostor_intent",
            intent="hunt",
            targetPlayerId=self.target_player,
            commandId=self.pending,
            mode="dynamic_visible_pursuit",
        )
        self.result.update(
            state="submitted", reason="持续追踪当前可见目标；进入原生击杀范围时立即判断，不等待旧坐标导航结束"
        )

    def submit_kill(self, state, player, now):
        result = self.bridge.kill(player["playerId"], state["sessionId"], state["matchId"])
        self.search_plan, self.search_estimate = None, None
        # The native kill command cancels the owned pursuit before clicking.
        self.release()
        self.kill_command, self.target_player, self.submitted = result["commandId"], player["playerId"], now
        self.result.update(state="submitted", reason="击杀请求已提交，等待原生游戏确认")

    def begin_search(self, state, now):
        estimate = self.forecast.get(self.target_player, now)
        if (
            not estimate
            or estimate["visibleNow"]
            or not safe_destination(state, estimate)
            or potential_witnesses(state, estimate)
        ):
            return False
        track = self.forecast.tracks[self.target_player]
        self.search_plan = {
            "playerId": self.target_player,
            "expires": track["seen"] + PlayerForecast.TTL,
            "searched": False,
        }
        self.search_estimate = estimate
        self.stop_owned()
        self.submitted, self.phase = now, "search"
        self.result.update(state="holding", reason="目标刚离开视野，先停止原生追踪，等待回执后按运动预测短时寻找")
        return True

    def search_step(self, state, players, anchors, now):
        if not self.search_plan:
            return False
        plan = self.search_plan
        pid = plan["playerId"]
        estimate = self.forecast.get(pid, now)
        selected = players.get(pid)
        self.search_estimate = estimate
        role = state.get("ownRole") or {}
        if (
            now >= plan["expires"]
            or not estimate
            or role.get("killCooldown", 1) > 0
            or pid in role.get("teammateIds", [])
            or not safe_destination(state, estimate)
            or potential_witnesses(state, estimate)
        ):
            self.stop_owned()
            self.avoid[pid] = now + 8
            self.search_plan, self.search_estimate = None, None
            self.phase, self.submitted = "cover", now
            self.result.update(
                state="waiting", reason="短时预测到期或风险出现，停止寻找；不把预测位置当作目击或击杀依据"
            )
            return True
        nav = state.get("navigation") or {}
        if self.pending:
            if selected is not None:
                self.stop_owned()
                self.submitted = now
                self.result.update(state="holding", reason="重新看见目标，停止预测路线后核对真实位置")
            elif nav.get("commandId") == self.pending and nav.get("state") in (
                "completed",
                "failed",
                "stuck",
                "stopped",
            ):
                self.release()
                plan["searched"] = True
                self.result.update(state="holding", reason="预测路线已结束，等待真实目击；三秒期限不续期")
            else:
                self.result.update(state="moving", reason="沿已有通道短时寻找，未看见目标时不追击或击杀")
            return True
        busy = nav.get("state") in ("running", "paused", "holding", "reporting", "opening_task", "seeking_task_button")
        released_pause = nav.get("state") == "paused" and nav.get("commandId") in self.released
        if (busy and not released_pause) or not state.get("self", {}).get("canMove") or now - self.submitted < 0.25:
            self.result.update(state="waiting", reason="等待旧路线停止和角色可移动，预测期限继续计时")
            return True
        if selected is not None:
            # Only this newly observed player may enter the native opportunity checks.
            self.search_plan, self.search_estimate = None, None
            if not self.opportunity(state, [selected], now):
                self.phase = "cover"
                self.result.update(state="waiting", reason="目标已重新可见，等待实时安全与距离判定")
            return True
        origin = state.get("self", {}).get("position") or {}
        choices = [
            a
            for a in anchors
            if a.get("kind") not in ("task", "vent")
            and xy(a.get("position"))
            and xy(origin)
            and 0.8 <= distance(origin, a["position"]) <= 12
            and distance(a["position"], estimate) <= 3.5
            and distance(a["position"], estimate) + 0.25 < distance(origin, estimate)
            and safe_destination(state, a["position"])
            and self.visited.get(a["anchorId"], 0) <= now
        ]
        if choices and not plan["searched"]:
            anchor = min(choices, key=lambda a: distance(a["position"], estimate))
            self.route(state, anchor, "search", now, player=pid)
            plan["searched"] = True
        else:
            self.result.update(state="holding", reason="等待目标重新可见；没有可达预测锚点时不直穿墙体")
        return True

    def opportunity(self, state, candidates, now, moving=False):
        role = state.get("ownRole") or {}
        if state.get("playerDetection", {}).get("status") != "ok" or role.get("killCooldown", 1) > 0:
            return False
        legal = [p for p in candidates if not potential_witnesses(state, p)]
        selected = next((p for p in legal if p["playerId"] == role.get("killTargetId")), None)
        if selected and role.get("canKill") and kill_ready(state, selected["playerId"]):
            self.submit_kill(state, selected, now)
            return True
        if legal and role.get("dynamicPursuitSupported"):
            if moving:
                # Stop only our route, then wait for its receipt before a chase.
                self.stop_owned()
                self.submitted = now
                self.result.update(state="holding", reason="途中发现可见落单目标，先停止巡场/掩护路线再重新判断")
            else:
                origin = state.get("self", {}).get("position") or {}
                self.pursue(state, min(legal, key=lambda p: distance(origin, p)), now)
            return True
        return False

    def patrol_target(self, state, anchors, now):
        origin = state.get("self", {}).get("position") or {}
        choices = [
            a
            for a in anchors
            if self.patrol_point(a)
            and self.cover_anchor_counts.get(a["anchorId"], 0) < self.anchor_cover_limit(a["anchorId"])
            and 1.5 <= distance(origin, a["position"]) <= 55
            and safe_destination(state, a["position"])
            and self.range_policy.accepts(a)
            and self.sweep.allowed(room_of(a))
            and self.visited.get(a["anchorId"], 0) <= now
            and self.patrol_rooms_until.get(a.get("roomId"), 0) <= now
            and self.patrol_anchors_until.get(a["anchorId"], 0) <= now
        ]
        if not choices:
            return None
        # On a return to a room, prefer a different interior stop when available.
        alternatives = {
            a.get("roomId") for a in choices if a["anchorId"] != self.patrol_room_last_anchor.get(a.get("roomId"))
        }
        choices = [
            a
            for a in choices
            if a.get("roomId") not in alternatives or a["anchorId"] != self.patrol_room_last_anchor.get(a.get("roomId"))
        ]
        # A previous sighting guides a sweep; it is never an unseen kill target.
        traffic = [p for p in state.get("nearbyPlayers", []) if "x" in p and "y" in p and not p.get("isDead")]
        traffic += [
            p
            for p in state.get("recentPlayers", [])
            if "x" in p and "y" in p and not p.get("isDead") and 0 <= p.get("ageSeconds", 999) <= 6
        ]
        candidates = sorted(
            choices,
            key=lambda a: (
                self.sweep.visits.get(room_of(a), 0),
                self.range_policy.visits.get(sector(a["position"]), -1) if self.range_policy.due else 0,
                self.patrol_visits.get(a.get("roomId") or a["anchorId"], -1),
                -sum(distance(a["position"], p) <= 5 for p in traffic),
                not self.sweep.nearby(room_of(a)) if self.sweep.current else False,
                abs(distance(origin, a["position"]) - 7),
            ),
        )
        # Vary equally plausible destinations, without favoring a revisited room.
        best = candidates[0]
        peers = [
            a
            for a in candidates
            if self.sweep.visits.get(room_of(a), 0) == self.sweep.visits.get(room_of(best), 0)
            and self.patrol_visits.get(a.get("roomId") or a["anchorId"], -1)
            == self.patrol_visits.get(best.get("roomId") or best["anchorId"], -1)
            and (
                not self.range_policy.due
                or self.range_policy.visits.get(sector(a["position"]), -1)
                == self.range_policy.visits.get(sector(best["position"]), -1)
            )
            and self.sweep.nearby(room_of(a)) == self.sweep.nearby(room_of(best))
            and abs(distance(origin, a["position"]) - distance(origin, best["position"])) <= 2
        ]
        return random.choice(peers)

    @staticmethod
    def patrol_point(anchor):
        # This recorded medical-room entry is a walk-through check, never a
        # fake scan or a stop at either medical task console.
        return BasicImpostor.inside_room(anchor) or (
            anchor.get("anchorId") == "medical.entry"
            and room_of(anchor) == "MedBay"
            and anchor.get("kind") == "waypoint"
            and xy(anchor.get("position"))
        )

    @staticmethod
    def inside_room(anchor):
        room = room_of(anchor)
        aid = anchor.get("anchorId", "")
        if (
            room not in ROOMS
            or room.startswith("Hallway")
            or anchor.get("kind") == "vent"
            or not xy(anchor.get("position"))
        ):
            return False
        # Entrance labels describe a boundary, not evidence of standing inside.
        excluded = (
            "entry",
            "door",
            "departure",
            "approach",
            "bypass",
            "returned",
            "return",
            "arrival",
            "vent",
            "asteroid",
            "med-scanner",
            "med-bay",
            "storage.garbage",
            "shields.shield",
        )
        return not any(word in aid for word in excluded)

    def recover_runtime(self, state):
        if self.resume_control is None:
            return False
        if not state.get("self", {}).get("canMove"):
            self.result.update(state="waiting", reason="角色暂不可移动，等待后再恢复临时关闭的导航许可")
            return True
        if self.bridge.restore_navigation(self.resume_control):
            self.resume_control = None
            self.recovery_count += 1
            self.last_recovery = {"reason": "已恢复本脚本为停止失败而暂时关闭的导航许可"}
            self.note(
                state, "autopilot_recovery", reason=self.last_recovery["reason"], recoveryCount=self.recovery_count
            )
            self.result.update(state="waiting", reason="导航许可已恢复，等待新回执后继续自动游玩")
        else:
            self.resume_control = None
            self.result.update(state="blocked", reason="导航设置已由其他操作改变，保持用户设置并等待重新允许")
        return True

    @staticmethod
    def entry_clear(state):
        if state.get("playerDetection", {}).get("status") != "ok":
            return False
        pos = state.get("self", {}).get("position") or {}
        return not any(
            not p.get("isDead")
            and not p.get("inVent")
            and p.get("playerId") != state.get("self", {}).get("playerId")
            and distance(pos, p) <= 6
            for p in state.get("nearbyPlayers", [])
            if "x" in p and "y" in p
        )

    def submit_vent_escape(self, state, now):
        plan = self.vent_plan
        try:
            result = self.bridge.vent(
                "travel", plan["exit"], plan["entry"], guarded_exit=True, observe_seconds=plan["observeSeconds"]
            )
        except (ValueError, OSError) as exc:
            self.escape_reason = "管道未提交，改用地面撤离：" + str(exc)
            self.vent_plan = None
            self.result.update(state="waiting", reason=self.escape_reason)
            return
        self.vent_command, self.vent_submitted = result["commandId"], now
        self.note(state, "impostor_vent_intent", commandId=self.vent_command, **plan)
        self.result.update(state="submitted", reason="已提交击杀后管道撤离；到出口先观察，尚未确认完成")

    def try_vent_escape(self, state, now):
        self.vent_attempted = True
        control, native = state.get("runtimeControl", {}), state.get("ventTravel", {})
        if (
            not control.get("impostorVentEscape", True)
            or not control.get("useVents")
            or not native.get("escapeSupported")
            or not native.get("approachSupported")
        ):
            self.escape_reason = "管道撤离未启用或组件不支持，使用地面撤离"
            return False
        if not self.entry_clear(state):
            self.escape_reason = "周围有人或检测不可用，使用地面撤离"
            return False
        vents = state.get("vents", [])
        pos = state["self"]["position"]
        choices = [
            (entry, dest)
            for entry in vents
            if distance(pos, entry) <= 6
            for dest in vents
            if dest["id"] != entry["id"]
            and dest["id"] in entry.get("connectedVentIds", [])
            and distance(self.body, dest) >= 4.5
            and safe_destination(state, dest)
        ]
        if not choices:
            self.escape_reason = "附近无合适的连通管道，使用地面撤离"
            return False
        entry, dest = min(choices, key=lambda pair: (distance(pos, pair[0]), -distance(self.body, pair[1])))
        self.vent_plan = {"entry": entry["id"], "exit": dest["id"], "observeSeconds": round(random.uniform(2, 15), 1)}
        if entry.get("entryInRange"):
            self.submit_vent_escape(state, now)
        else:
            try:
                result = self.bridge.approach_vent(entry["id"])
            except (ValueError, OSError) as exc:
                self.escape_reason = "入口无法到达，使用地面撤离：" + str(exc)
                self.vent_plan = None
                return False
            self.pending, self.intent = result["commandId"], "vent_escape"
            self.target, self.submitted = "temp.vent." + str(entry["id"]), now
            self.result.update(state="submitted", reason="击杀已确认，优先沿可通行路线走到管道入口")
        return True

    def tick(self, state):
        now = time.monotonic()
        identity = (state.get("sessionId"), state.get("matchId"), state.get("mapId"))
        if identity != self.identity:
            self.reset()
            self.identity = identity
        self.forecast.update(state, now)
        role = state.get("ownRole") or {}
        self.kill_safety = role.get("killSafety", [])
        self.kill_checks = (
            kill_decisions(state)
            if state.get("connected") and state.get("phase") == "TASKS" and not role.get("isDead")
            else []
        )
        if not state.get("connected") or state.get("meetingOpen") or state.get("phase") != "TASKS":
            self.range_policy.observe(state, now)
            self.traffic.active, self.traffic.members = False, []
            self.pending = self.kill_command = None
            self.sabotage_command = None
            self.opening_done = False
            self.next_activity = "cover"
            self.body = None
            self.vent_command = self.vent_plan = None
            self.phase = "cover"
            self.search_plan, self.search_estimate = None, None
            self.result.update(state="waiting", reason="等待会议结束或新鲜遥测")
            return
        if role.get("roleType") != "Impostor" or not role.get("basicImpostorSupported"):
            self.result.update(state="blocked", reason="基础内鬼需要新版游戏组件；特殊内鬼角色仍待适配")
            return
        if role.get("isDead"):
            self.stop_owned()
            self.traffic.active = False
            self.resume_control = None
            self.kill_command = None
            self.vent_command = self.vent_plan = None
            self.search_plan, self.search_estimate = None, None
            self.result.update(state="waiting", reason="角色已死亡，停止内鬼行动")
            return
        pos = state.get("self", {}).get("position", {})
        if "x" not in pos or "y" not in pos or state.get("mapId") not in ("TheSkeld", "Skeld"):
            self.result.update(state="blocked", reason="基础内鬼目前仅支持有已验证路线的 The Skeld")
            return
        if self.phase == "escape" and not (
            isinstance(self.body, dict)
            and all(type(self.body.get(axis)) in (int, float) and math.isfinite(self.body[axis]) for axis in ("x", "y"))
        ):
            # Do not guess a crime scene or resume hunting after damaged state.
            self.stop_owned()
            self.result.update(state="blocked", reason="撤离阶段缺少有效击杀位置，已停止自动行动；请人工接管。")
            return
        if state.get("minigameOpen") or state.get("runtimeStatus", {}).get("minigameAutomation", {}).get("busy"):
            self.result.update(state="waiting", reason="等待小游戏与技能输入结束")
            return
        travel = state.get("ventTravel") or {}
        vent_role = state.get("ventRole") or {}
        if self.vent_command:
            if travel.get("commandId") == self.vent_command and travel.get("state") in ("completed", "failed"):
                success = (
                    travel.get("state") == "completed"
                    and travel.get("reachedTarget")
                    and travel.get("safeExitConfirmed")
                )
                self.vent_command = self.vent_plan = None
                if success:
                    self.phase, self.opening_done, self.body = "cover", False, None
                    self.result.update(state="holding", reason="游戏确认管道撤离并安全出管，恢复假任务掩护")
                    return
                self.escape_reason = "管道撤离失败；仍在管道内时保持隐藏，否则使用地面撤离"
            elif (
                now - self.vent_submitted > 5
                and not travel.get("busy")
                and not vent_role.get("inVent")
                and not vent_role.get("walkingToVent")
            ):
                self.vent_command = self.vent_plan = None
                self.escape_reason = "管道请求未获游戏确认，改用地面撤离"
            else:
                self.result.update(state="waiting", reason=travel.get("reason") or "管道撤离执行中，等待游戏确认")
                return
        if travel.get("busy") or vent_role.get("inVent") or vent_role.get("walkingToVent"):
            self.result.update(state="waiting", reason=travel.get("reason") or "保持管道内隐藏，等待安全出管")
            return
        if self.recover_runtime(state):
            return
        if state.get("runtimeControl", {}).get("autoNavigation") is False:
            self.result.update(state="waiting", reason="导航许可已关闭，等待明确开启；不会自动覆盖用户设置")
            return
        players = {p["playerId"]: p for p in state.get("nearbyPlayers", []) if "x" in p and "y" in p}
        teammates = set(role.get("teammateIds", []))
        candidates = [
            p
            for pid, p in players.items()
            if pid not in teammates
            and pid != state.get("self", {}).get("playerId")
            and not p.get("isDead")
            and not p.get("inVent")
            and safe_destination(state, p)
            and self.avoid.get(pid, 0) <= now
        ]
        nav = state.get("navigation", {})
        anchors = [a for a in self.bridge.route_anchors() if "x" in a.get("position", {}) and "y" in a["position"]]
        self.range_policy.observe(state, now)
        self.traffic.observe(state, self.forecast.players(now, anchors), now)
        try:
            if self.sabotage_command:
                receipt = (state.get("sabotage") or {}).get("lastAction") or {}
                if receipt.get("commandId") == self.sabotage_command and receipt.get("state") in (
                    "completed",
                    "failed",
                ):
                    if receipt["state"] == "completed":
                        self.note(
                            state,
                            "impostor_sabotage_confirmed",
                            target=receipt.get("target"),
                            commandId=self.sabotage_command,
                            evidence="native_system_active",
                        )
                    else:
                        self.next_sabotage = now + 8
                    self.sabotage_command = None
                elif now - self.sabotage_submitted > 5:
                    self.sabotage_command = None
                    self.next_sabotage = now + 8
            if self.kill_command:
                receipt = state.get("impostorAction") or {}
                if receipt.get("commandId") == self.kill_command and receipt.get("state") == "completed":
                    self.note(
                        state,
                        "impostor_kill_confirmed",
                        targetPlayerId=self.target_player,
                        bodyPosition=receipt.get("position"),
                        commandId=self.kill_command,
                    )
                    self.body = receipt.get("position") or pos.copy()
                    self.vent_attempted = False
                    self.vent_plan = self.vent_command = None
                    self.escape_reason = "击杀已确认，准备选择撤离方式"
                    self.escape_until = now + 8
                    self.phase = "escape"
                    self.kill_command = None
                    self.target_player = None
                    self.submitted = now  # Allow the game's murder animation to finish.
                elif (
                    receipt.get("commandId") == self.kill_command and receipt.get("state") == "failed"
                ) or now - self.submitted > 5:
                    self.avoid[self.target_player] = now + 15
                    self.kill_command = None
                    self.result.update(state="waiting", reason="击杀未获游戏确认，不记作成功；暂时放弃该目标")
                return
            if self.search_step(state, players, anchors, now):
                return
            if self.pending:
                if self.intent == "flow":
                    self.follow_traffic(state, anchors, now)
                    return
                if self.intent == "vent_escape":
                    if (
                        not self.entry_clear(state)
                        or not state.get("runtimeControl", {}).get("useVents")
                        or not state.get("runtimeControl", {}).get("impostorVentEscape", True)
                    ):
                        self.stop_owned()
                        self.vent_plan = None
                        self.escape_reason = "接近途中出现玩家、检测异常或许可关闭，取消钻管并地面撤离"
                        self.result.update(state="waiting", reason=self.escape_reason)
                        return
                    if nav.get("commandId") == self.pending and nav.get("state") in (
                        "completed",
                        "failed",
                        "stuck",
                        "stopped",
                    ):
                        self.release()
                        if nav.get("state") == "completed":
                            self.submit_vent_escape(state, now)
                        else:
                            self.vent_plan = None
                            self.escape_reason = "管道入口路径失败，改用地面撤离"
                            self.result.update(state="waiting", reason=self.escape_reason)
                        return
                    if (nav.get("commandId") != self.pending and now - self.submitted > 5) or now - self.submitted > 20:
                        self.stop_owned()
                        self.vent_plan = None
                        self.escape_reason = "入口导航未确认或超时，取消后地面撤离"
                    else:
                        self.result.update(state="moving", reason="正在前往管道入口，持续检查周围玩家")
                    return
                if self.intent == "hunt":
                    selected = players.get(self.target_player)
                    self.pursuit_status = nav.get("pursuit") or {}
                    if selected is None and now - self.submitted <= 45 and self.begin_search(state, now):
                        return
                    if (
                        selected is None
                        or selected.get("isDead")
                        or selected.get("inVent")
                        or self.target_player in teammates
                        or state.get("playerDetection", {}).get("status") != "ok"
                        or potential_witnesses(state, selected)
                        or not safe_destination(state, selected)
                        or now - self.submitted > 45
                    ):
                        self.avoid[self.target_player] = now + 8
                        self.stop_owned()
                        self.result.update(state="waiting", reason="目标失联、出现当前/近期目击者或追踪超时，取消追踪")
                        return
                    if (
                        role.get("canKill")
                        and role.get("killTargetId") == self.target_player
                        and role.get("killCooldown", 1) <= 0
                        and kill_ready(state, self.target_player)
                    ):
                        self.submit_kill(state, selected, now)
                        return
                    if nav.get("commandId") == self.pending and nav.get("state") in (
                        "completed",
                        "failed",
                        "stuck",
                        "stopped",
                    ):
                        if nav.get("state") != "completed":
                            self.last_pursuit_failure = {
                                "playerId": self.target_player,
                                "at": state.get("telemetryAt"),
                                "error": nav.get("error") or nav.get("failureCode") or nav.get("state"),
                            }
                        self.avoid[self.target_player] = now + 8
                        self.release()
                        self.result.update(state="waiting", reason="追踪已结束或路线不可通行，暂时放弃目标；未记作击杀")
                        return
                    if nav.get("commandId") != self.pending and now - self.submitted > 5:
                        self.stop_owned()
                        self.result.update(state="waiting", reason="追踪未获游戏确认，已取消")
                        return
                    self.result.update(
                        state="moving",
                        reason="目标已降速，继续接近并等待原生击杀判定"
                        if self.pursuit_status.get("targetSlowed")
                        else "持续更新目标位置与接近路线，等待原生击杀判定",
                    )
                    return
                if nav.get("commandId") != self.pending:
                    if now - self.submitted > 5:
                        self.last_recovery = {
                            "reason": "内鬼路线未获确认，释放旧请求并重新选择",
                            "commandId": self.pending,
                        }
                        self.recovery_count += 1
                        self.visited[self.target] = now + 30
                        self.release()
                        self.submitted = now
                        self.note(state, "autopilot_recovery", **self.last_recovery)
                        self.result.update(state="waiting", reason=self.last_recovery["reason"])
                    return
                if nav.get("state") == "navigation_disabled" or now - self.submitted > 45:
                    self.stop_owned()
                    self.visited[self.target] = now + 30
                    self.recovery_count += 1
                    self.last_recovery = {"reason": "旧路线失去执行许可或超时，释放后重新规划"}
                    self.note(state, "autopilot_recovery", **self.last_recovery)
                    self.submitted = now
                    self.result.update(state="waiting", reason=self.last_recovery["reason"])
                    return
                if (
                    self.intent in ("cover", "patrol")
                    and nav.get("state") == "running"
                    and state.get("self", {}).get("canMove")
                    and self.opportunity(state, candidates, now, moving=True)
                ):
                    return
                destination = next((a for a in anchors if a["anchorId"] == self.target), {})
                if (
                    self.intent == "patrol"
                    and nav.get("state") == "running"
                    and self.range_policy.due
                    and destination
                    and not self.range_policy.accepts(destination)
                ):
                    self.stop_owned()
                    self.submitted = now
                    self.result.update(state="waiting", reason="片区停留达到上限，停止本区巡视后选择跨区路线")
                    return
                if nav.get("state") in ("completed", "failed", "stuck", "stopped"):
                    self.release()
                    if nav.get("state") != "completed":
                        self.visited[self.target] = now + 60
                        self.result.update(state="waiting", reason="路线未到达，冷却此目标后重新选择")
                        return
                    if self.intent == "cover":
                        self.cover_seconds = round(random.uniform(5, 15), 1)
                        self.dwell_until = now + self.cover_seconds
                        self.opening_done = True
                        self.visited[self.target] = now + 45
                        self.fake_visits[self.target] = now
                        self.last_cover_anchor = self.target
                        self.last_cover_room = (self.fake_task or {}).get("roomId")
                        anchor = next((a for a in anchors if a["anchorId"] == self.target), {})
                        if xy(anchor.get("position")) and distance(pos, anchor["position"]) <= 0.6:
                            self.sweep.arrive(room_of(anchor))
                            if self.fake_task:
                                key = self.cover_key(self.fake_task)
                                self.cover_counts[key] = self.cover_counts.get(key, 0) + 1
                                self.cover_anchor_counts[self.target] = self.cover_anchor_counts.get(self.target, 0) + 1
                            self.patrols_since_cover = 0
                        self.next_activity = "patrol"
                        self.note(
                            state,
                            "impostor_cover",
                            targetAnchorId=self.target,
                            fakeTask=self.fake_task,
                            dwellSeconds=self.cover_seconds,
                        )
                    elif self.intent == "patrol":
                        self.opening_done = True
                        self.patrol_count += 1
                        self.last_patrol_anchor = self.target
                        self.last_patrol_room = next(
                            (room_of(a) for a in anchors if a["anchorId"] == self.target), None
                        )
                        self.patrol_room_last_anchor[self.last_patrol_room] = self.target
                        self.patrol_visits[self.last_patrol_room or self.target] = now
                        anchor = next((a for a in anchors if a["anchorId"] == self.target), {})
                        # Only an acknowledged interior arrival may start a short stop.
                        self.patrol_stop_seconds = (
                            round(random.uniform(0.6, 2), 1)
                            if self.inside_room(anchor) and distance(pos, anchor["position"]) <= 0.6
                            else 0
                        )
                        self.patrol_stop_seconds = min(2, max(0, self.patrol_stop_seconds))
                        if self.patrol_point(anchor) and distance(pos, anchor["position"]) <= 0.6:
                            self.sweep.arrive(room_of(anchor))
                            self.patrols_since_cover += 1
                        self.next_activity = (
                            "cover" if self.patrols_since_cover >= self.PATROLS_BETWEEN_COVERS else "patrol"
                        )
                        self.patrol_dwell_until = now + self.patrol_stop_seconds
                        self.patrol_rooms_until[self.last_patrol_room] = now + self.PATROL_ROOM_COOLDOWN
                        self.patrol_anchors_until[self.target] = now + self.PATROL_ANCHOR_COOLDOWN
                        self.visited[self.target] = now + self.PATROL_ANCHOR_COOLDOWN
                        self.note(
                            state,
                            "impostor_patrol_arrived",
                            targetAnchorId=self.target,
                            evidence="native_navigation_completed",
                            roomId=self.last_patrol_room,
                            dwellSeconds=self.patrol_stop_seconds,
                        )
                        self.submitted = now
                        self.result.update(
                            state="holding",
                            reason="已到房间内巡查点，短暂观察后换房间或做假任务"
                            if self.patrol_stop_seconds
                            else "已到巡查点，重新选择路线，不在入口或通道安排停留",
                        )
                        return
                else:
                    self.result.update(state="moving", reason="沿已验证路线移动")
                    return
            elif nav.get("state") in ("running", "paused", "holding", "opening_task", "seeking_task_button") and not (
                nav.get("state") == "paused"
                and (
                    nav.get("commandId") in self.released
                    or nav.get("action") in ("close_minigame", "send_chat", "cast_vote")
                )
            ):
                self.result.update(state="waiting", reason="等待已有导航结束")
                return
            if not state.get("self", {}).get("canMove") or now - self.submitted < 1:
                return
            if self.phase == "escape":
                if not self.vent_attempted and self.try_vent_escape(state, now):
                    return
                if now >= self.escape_until and distance(pos, self.body) >= 4.5:
                    self.phase, self.opening_done = "cover", False
                else:
                    escape = [
                        a
                        for a in anchors
                        if a.get("kind") == "waypoint"
                        and 0.8 <= distance(pos, a["position"]) <= 22
                        and distance(self.body, a["position"]) >= 3.1
                        and safe_destination(state, a["position"])
                        and self.visited.get(a["anchorId"], 0) <= now
                    ]
                    if escape:
                        anchor = min(escape, key=lambda a: abs(distance(self.body, a["position"]) - 9))
                        try:
                            self.route(state, anchor, "escape", now)
                        except (ValueError, OSError) as exc:
                            self.visited[anchor["anchorId"]] = now + 30
                            self.escape_reason = "地面撤离目标提交失败，冷却后改选其他出口：" + str(exc)
                            self.submitted = now
                            self.recovery_count += 1
                            self.last_recovery = {"reason": self.escape_reason, "targetAnchorId": anchor["anchorId"]}
                            self.result.update(state="waiting", reason=self.escape_reason)
                            self.note(state, "autopilot_recovery", **self.last_recovery)
                    else:
                        self.result.update(state="blocked", reason="没有可用撤离锚点；保持撤离阶段，不再击杀")
                    return
            # A visible opportunity takes priority over opening cover and its
            # dwell. Pursuit approaches first; only the existing native kill
            # range, witness and observation checks can authorize a strike.
            if self.opportunity(state, candidates, now):
                self.dwell_until = self.patrol_dwell_until = 0
                return
            if now < self.dwell_until:
                self.result.update(state="holding", reason="在任务点短暂停留做掩护，未执行真实任务")
                return
            if self.traffic.active:
                self.follow_traffic(state, anchors, now)
                return
            detection_ok = state.get("playerDetection", {}).get("status") == "ok"

            def witnesses(p):
                return len(potential_witnesses(state, p))

            native_sabotage = state.get("sabotage") or {}
            immediate_kill = any(
                p["playerId"] == role.get("killTargetId") and witnesses(p) == 0 for p in candidates
            ) and role.get("canKill")
            if (
                self.opening_done
                and detection_ok
                and not immediate_kill
                and candidates
                and not self.sabotage_command
                and now >= self.next_sabotage
                and native_sabotage.get("canSabotage")
            ):
                target = (
                    "lights"
                    if len(candidates) == 1 and role.get("killCooldown", 30) <= 5
                    else "comms"
                    if len(candidates) >= 2
                    else None
                )
                if target:
                    result = self.bridge.sabotage(target)
                    self.sabotage_command, self.sabotage_submitted = result["commandId"], now
                    self.next_sabotage, self.submitted = now + 35, now
                    self.result.update(state="submitted", reason="根据当前可见玩家发起照明或通讯破坏，等待原生确认")
                    return
            if now < self.patrol_dwell_until:
                self.result.update(state="holding", reason="仅在房间内短暂观察，发现安全机会可立即结束停留")
                return
            if self.opening_done and self.traffic.can_start(now):
                flow_target = self.traffic.destination(state, anchors, self.sweep, self.range_policy, self.visited, now)
                if flow_target:
                    self.traffic.begin(now)
                    self.note(
                        state,
                        "impostor_traffic",
                        stage="begin",
                        playerIds=[p["playerId"] for p in self.traffic.members],
                        durationSeconds=self.traffic.duration,
                        evidence="live_visible_moving_group",
                    )
                    self.follow_traffic(state, anchors, now)
                    return
            cover = [item for item in self.cover_targets(state, anchors, now) if self.range_policy.accepts(item[0])]
            if self.next_activity == "cover" and self.last_patrol_room:
                other_rooms = [item for item in cover if item[0].get("roomId") != self.last_patrol_room]
                if other_rooms:
                    cover = other_rooms
            # Inspect fresh rooms before returning to a familiar cover task.
            # Cover no longer replaces a required patrol just because it is near.
            patrol = self.patrol_target(state, anchors, now)
            if patrol:
                cover = [
                    item
                    for item in cover
                    if self.sweep.visits.get(room_of(item[0]), 0) <= self.sweep.visits.get(room_of(patrol), 0)
                ]
            next_cover = self.choose_cover(state, cover)
            self.sweep.next_task = next_cover[0]["anchorId"] if next_cover else None
            patrol_due = self.next_activity == "patrol" or (
                bool(self.fake_visits) and self.patrols_since_cover < self.PATROLS_BETWEEN_COVERS
            )
            if patrol and (patrol_due or not next_cover):
                self.route(state, patrol, "patrol", now)
                return
            if next_cover:
                # Prefer a different room, then the least recently visited
                # task-bar destination. Distance is only the final tiebreaker.
                self.schedule_cover(state, next_cover, cover, now)
            else:
                self.result.update(state="waiting", reason="暂无可用掩护点或巡场路线；等待新观察或路线冷却")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            if isinstance(exc, (KeyError, TypeError)):
                logging.getLogger(__name__).exception("Among Us impostor decision failed")
            self.submitted = now
            self.result.update(state="error", reason=str(exc))

    def follow_traffic(self, state, anchors, now):
        flow = self.traffic
        nav = state.get("navigation") or {}
        reason = flow.end_reason(now, self.range_policy)
        if reason:
            if self.pending:
                self.stop_owned()
            flow.finish(now, reason)
            self.phase, self.next_activity, self.submitted = "cover", "cover", now
            self.note(state, "impostor_traffic", stage="end", reason=reason)
            self.result.update(state="waiting", reason=reason)
            return
        if self.pending:
            if nav.get("commandId") == self.pending:
                if nav.get("state") in ("completed", "failed", "stuck", "stopped"):
                    reached = nav.get("state") == "completed"
                    anchor = next((a for a in anchors if a["anchorId"] == self.target), {})
                    if (
                        reached
                        and xy(anchor.get("position"))
                        and distance(state["self"]["position"], anchor["position"]) <= 0.6
                    ):
                        self.sweep.arrive(room_of(anchor))
                    self.visited[self.target] = now + (3 if reached else 30)
                    if not reached:
                        flow.failures += 1
                    self.release()
                    self.submitted = now
                    self.result.update(state="waiting", reason="同行路线结束，核对人群的新位置")
                    return
                shifted = flow.goal and distance(flow.goal, flow.center()) >= 2
                if shifted and now - self.submitted >= flow.REPLAN_SECONDS:
                    self.stop_owned()
                    self.submitted = now
                    self.result.update(state="holding", reason="人流方向改变，停止旧路线后按新位置继续同行")
                    return
            elif now - self.submitted > 5:
                self.stop_owned()
                flow.failures += 1
                self.submitted = now
            self.result.update(state="moving", reason="短时随人流行走，使用本地运动预测，随时核对人群是否停下")
            return
        if now - self.submitted < 1:
            return
        anchor = flow.destination(state, anchors, self.sweep, self.range_policy, self.visited, now)
        if anchor:
            try:
                self.route(state, anchor, "flow", now)
                flow.goal = flow.center()
            except (ValueError, OSError) as exc:
                flow.failures += 1
                self.visited[anchor["anchorId"]] = now + 30
                self.submitted = now
                self.result.update(state="waiting", reason="同行路线提交失败，冷却目标后换路：" + str(exc))
        else:
            # A moving group can be right beside us while between route points.
            # The overall deadline still runs; this does not extend the outing.
            self.phase = "flow"
            self.result.update(state="holding", reason="等待人流前进到已有安全通道；不进入近期巡视房间或击杀现场")

    def choose_cover(self, state, cover):
        if not cover:
            return None
        origin = state.get("self", {}).get("position") or {}
        planned = next((item for item in cover if item[0]["anchorId"] == self.sweep.next_task), None)
        if planned:
            return planned
        return min(
            cover,
            key=lambda item: (
                self.fake_visits.get(item[0]["anchorId"], -1),
                room_of(item[0]) == self.last_cover_room if self.last_cover_room else False,
                distance(origin, item[0]["position"]),
            ),
        )

    def schedule_cover(self, state, selected, cover, now):
        anchor, task = selected
        self.route(state, anchor, "cover", now, task=task)
        following = self.choose_cover(
            state,
            [
                item
                for item in cover
                if item[0]["anchorId"] != anchor["anchorId"] and room_of(item[0]) != room_of(anchor)
            ],
        )
        self.sweep.next_task = following[0]["anchorId"] if following else None

    def cover_targets(self, state, anchors, now):
        eligible = [
            a
            for a in anchors
            if a.get("kind") == "task"
            and not any(
                word in a["anchorId"]
                for word in ("med-scanner", "asteroid", "shields.shield", "storage.garbage", "approach")
            )
        ]
        if self.extra_cover_plan is None:
            wiring = sorted({a["anchorId"] for a in eligible if a["anchorId"].split(".")[2:3] == ["fix-wiring"]})
            self.extra_cover_plan = random.sample(wiring, min(2, len(wiring)))
            if any(a["anchorId"] == "skeld.comms.upload-data" for a in eligible):
                self.extra_cover_plan.append("skeld.comms.upload-data")
        result = []
        for task in state.get("tasks", []):
            if task.get("complete") or task.get("taskType") in (
                "ClearAsteroids",
                "SubmitScan",
                "InspectSample",
                "PrimeShields",
                "FixLights",
                "RestoreOxy",
                "FixComms",
                "ResetReactor",
            ):
                continue
            if self.cover_counts.get(self.cover_key(task), 0) >= self.MAX_COVER_VISITS:
                continue
            compatible = [a for a in eligible if anchor_accepts_task(a, task)]
            for position in task.get("locations", []):
                if "x" not in position or "y" not in position or not compatible:
                    continue
                anchor = min(compatible, key=lambda a: distance(a["position"], position))
                if distance(anchor["position"], position) > 2.5:
                    continue
                if (
                    task.get("taskType") == "FixWiring"
                    and anchor["anchorId"].split(".")[2:3] == ["fix-wiring"]
                    and anchor["anchorId"] not in self.extra_cover_plan
                ):
                    continue
                if (
                    not self.sweep.allowed(room_of(anchor))
                    or not safe_destination(state, anchor["position"])
                    or self.visited.get(anchor["anchorId"], 0) > now
                    or self.cover_anchor_counts.get(anchor["anchorId"], 0)
                    >= self.anchor_cover_limit(anchor["anchorId"])
                ):
                    continue
                if task.get("taskType") in ("EmptyGarbage", "EmptyChute") and (
                    anchor.get("roomId") == "Storage" or ".storage." in anchor["anchorId"]
                ):
                    continue
                result.append((anchor, task))
        included = {a["anchorId"] for a, _ in result}
        for anchor in eligible:
            aid = anchor["anchorId"]
            if (
                aid not in self.extra_cover_plan
                or aid in included
                or self.cover_anchor_counts.get(aid, 0) >= 1
                or not self.sweep.allowed(room_of(anchor))
                or not safe_destination(state, anchor["position"])
                or self.visited.get(aid, 0) > now
            ):
                continue
            result.append(
                (
                    anchor,
                    {
                        "taskId": None,
                        "taskType": "FixWiring" if "fix-wiring" in aid else "UploadData",
                        "step": 0,
                        "source": "extra_cover_point",
                        "coverKey": "extra:" + aid,
                    },
                )
            )
        return result

    @staticmethod
    def cover_key(task):
        # Steps and console aliases must not reset the same task's allowance.
        return task.get("coverKey") or str(task.get("taskId")) + ":" + str(task.get("taskType"))

    def anchor_cover_limit(self, anchor_id):
        return 1 if anchor_id in (self.extra_cover_plan or []) else self.MAX_COVER_VISITS
