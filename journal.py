"""Persistent observations. Records evidence, never infers task completion or roles."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .files import atomic
from .spatial import route_summary
from .task_receipts import valid_receipts


def seconds(stamp):
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()


def distance(a, b):
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


class Journal:
    def __init__(self, root, enabled=True):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.doc = {}
        self.path = None
        self.dirty = False
        self.last_flush = 0.0
        self.active = {}
        self.current_visible = set()
        self.interrupted = True
        self.enabled = enabled
        self.cleared_at = None
        try:
            self.cleared_at = json.loads((self.root / "control.json").read_text(encoding="utf-8")).get("clearedAt")
        except (OSError, ValueError):
            pass
        summaries = list(self.root.glob("*/summary.json"))
        if summaries:
            latest = max(summaries, key=lambda p: p.stat().st_mtime)
            try:
                self.doc = json.loads(latest.read_text(encoding="utf-8"))
                self.path = latest.parent
            except (OSError, ValueError):
                pass

    def observe(self, state):
        if (
            not self.enabled
            or not state.get("connected")
            or state.get("phase") not in ("TASKS", "MEETING")
            or not state.get("sessionId")
        ):
            with self.lock:
                self.current_visible.clear()
                self.interrupted = True
            return False
        with self.lock:
            identity = [state["sessionId"], state.get("matchId", 0), state.get("mapId")]
            key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
            if self.path != self.root / key:
                self.flush()
                self.path = self.root / key
                self.path.mkdir(parents=True, exist_ok=True)
                try:
                    self.doc = json.loads((self.path / "summary.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self.doc = {}
                if self.doc.get("identity") != identity:
                    self.doc = {
                        "identity": identity,
                        "startedAt": state["telemetryAt"],
                        "route": [],
                        "taskEvents": [],
                        "tasks": {},
                        "encounters": [],
                        "players": {},
                        "routePoints": 0,
                        "distance": 0.0,
                        "eventCount": 0,
                    }
                self.active = {}
                self.interrupted = True
            # Upgrade existing summaries in place; never erase old evidence.
            self.doc.setdefault("timeline", [])
            self.doc.setdefault("taskBatch", 1)
            self.doc.setdefault("recordId", uuid.uuid4().hex)
            self.doc.setdefault("previousVentTransit", False)
            self.doc.setdefault("lastVentCommand", None)
            if self.cleared_at:
                self.doc.setdefault("clearedAt", self.cleared_at)
            stamp = state["telemetryAt"]
            now = seconds(stamp)
            if now <= self.doc.get("lastTimestamp", 0):
                return False
            observation_gap = self.interrupted or now - self.doc.get("lastTimestamp", now) > 5
            self.interrupted = False
            if observation_gap:
                self.active.clear()
            self.doc["lastTimestamp"] = now
            self.doc["updatedAt"] = stamp
            self.dirty = True
            if "crimeScenes" in state:
                self.doc["crimeScenes"] = state["crimeScenes"]
            position = state.get("self", {}).get("position", {})
            route = self.doc["route"]
            new_encounter = False

            def event(kind, **fields):
                item = {
                    "event": kind,
                    "at": stamp,
                    "sessionId": state["sessionId"],
                    "matchId": state.get("matchId", 0),
                    "meetingId": state.get("meetingId"),
                    "eventId": key + ":" + self.doc["recordId"] + ":" + str(self.doc["eventCount"] + 1),
                    "taskBatch": self.doc["taskBatch"],
                    "source": state.get("source", "live_telemetry"),
                    **fields,
                }
                self._append(item)
                if kind not in ("route_point", "player_seen"):
                    self.doc["timeline"].append(item)
                    del self.doc["timeline"][:-500]
                return item

            instances = self.doc.setdefault("taskInstances", {})

            def apply_completions():
                for receipt in valid_receipts(state, after=self.doc.get("clearedAt")):
                    task = receipt["task"]
                    known = instances.get(task["taskInstanceId"])
                    if (
                        not known
                        or known.get("complete")
                        or known.get("taskId") != task.get("taskId")
                        or known.get("taskType") != task.get("taskType")
                    ):
                        continue
                    item = event(
                        "task_completed",
                        task=task,
                        previousStep=known.get("step"),
                        taskBatch=known["taskBatch"],
                        at=receipt["completedAtUtc"],
                        source=receipt["source"],
                        receiptId=receipt["receiptId"],
                        position=receipt.get("position"),
                        location=None,
                    )
                    known.update(complete=True, step=task.get("step"))
                    tid = str(task.get("taskId")) + ":" + str(task.get("taskType"))
                    before = self.doc["tasks"].get(tid, {})
                    if before.get("taskInstanceId") == task["taskInstanceId"]:
                        self.doc["tasks"][tid] = dict(task)
                    self.doc["taskEvents"].append(item)
                    del self.doc["taskEvents"][:-300]

            # Attribute old receipts before replacing the old task catalog.
            # This also runs in meetings, where an immediately preceding
            # completion could otherwise disappear from the action record.
            apply_completions()
            vent = state.get("ventTravel") or {}
            vent_role = state.get("ventRole") or {}
            vent_transit = bool(vent.get("busy") or vent_role.get("inVent") or vent_role.get("walkingToVent"))
            # A terminal native receipt is evidence of the pipe skill only. It
            # must never be interpreted as a walked route point or a task.
            vent_command = vent.get("commandId")
            new_vent_receipt = (
                vent_command
                and vent.get("state") in ("completed", "failed")
                and vent.get("stage") != "unconfirmed"
                and vent_command != self.doc.get("lastVentCommand")
            )
            if new_vent_receipt:
                kind = "vent_completed" if vent.get("state") == "completed" else "vent_failed"
                event(
                    kind,
                    visibility="impostor_private",
                    commandId=vent_command,
                    entryVentId=vent.get("entryVentId", -1),
                    targetVentId=vent.get("targetVentId", -1),
                    route=vent.get("route", []),
                    reason=vent.get("reason"),
                    safeExitConfirmed=bool(vent.get("safeExitConfirmed")),
                    heldInside=bool(vent.get("heldInside")),
                    action=vent.get("action"),
                    position=position,
                )
                self.doc["lastVentCommand"] = vent_command
            if state["phase"] == "TASKS" and "x" in position and "y" in position:
                # A meeting relocation, telemetry gap, or vent transit is a
                # boundary. Pipe displacement is not player walking distance.
                vent_boundary = self.doc.get("previousVentTransit") or new_vent_receipt
                new_segment = self.doc.get("previousPhase") != "TASKS" or observation_gap or bool(vent_boundary)
                if vent_transit and not self.doc.get("previousVentTransit"):
                    if route:
                        event("observation_boundary", reason="vent", position=position)
                    new_segment = True
                if not vent_transit:
                    if route and new_segment:
                        event(
                            "observation_boundary",
                            reason="vent" if vent_boundary else "gap" if observation_gap else "meeting",
                            position=position,
                        )
                    moved = distance(position, route[-1]["position"]) if route else 0
                    if not route or new_segment or moved >= 0.25 or now - seconds(route[-1]["at"]) >= 5:
                        point = event(
                            "route_point",
                            position=position,
                            segmentStart=new_segment,
                            mapId=state.get("mapId"),
                            nearestAnchor=state.get("nearestAnchor"),
                        )
                        if route and not new_segment:
                            self.doc["distance"] += moved
                        route.append(point)
                        del route[:-2048]
                        self.doc["routePoints"] += 1
                    area = (state.get("nearestAnchor") or {}).get("room")
                    if area and area != self.doc.get("lastArea"):
                        event("area_changed", location=state.get("nearestAnchor"), position=position)
                        self.doc["lastArea"] = area
                tasks = [
                    t
                    for t in state.get("tasks", [])
                    if t.get("taskType") not in ("FixLights", "RestoreOxy", "FixComms", "ResetReactor")
                ]
                catalog = sorted(str(t.get("taskId")) + ":" + str(t.get("taskType")) for t in tasks)
                previous_catalog = self.doc.get("taskCatalog")
                # Missing tasks alone aren't refresh or completion evidence.
                added = set(catalog) - set(previous_catalog or [])
                removed = set(previous_catalog or []) - set(catalog)
                old_ids = {key.split(":", 1)[0] for key in removed}
                reused_ids = any(key.split(":", 1)[0] in old_ids for key in added)
                # An additional task or a partial sample doesn't replace the
                # batch. Reused IDs with a different type or a fully replaced
                # nonempty catalog are explicit refresh evidence.
                changed_catalog = bool(
                    catalog
                    and previous_catalog
                    and added
                    and (reused_ids or (removed and not set(catalog) & set(previous_catalog)))
                )
                full_reset = (
                    catalog == previous_catalog
                    and bool(tasks)
                    and not any(t.get("taskInstanceId") for t in tasks)
                    and all(not t.get("complete") and t.get("step", 0) == 0 for t in tasks)
                    and any(t.get("complete") for t in self.doc["tasks"].values())
                )
                reinitialized = any(
                    t.get("taskInstanceId")
                    and self.doc["tasks"]
                    .get(str(t.get("taskId")) + ":" + str(t.get("taskType")), {})
                    .get("taskInstanceId")
                    not in (None, "", t["taskInstanceId"])
                    for t in tasks
                )
                if changed_catalog or full_reset or reinitialized:
                    self.doc["taskBatch"] += 1
                    self.doc["tasks"] = {}
                    event(
                        "task_batch_started",
                        reason="catalog_changed"
                        if changed_catalog
                        else "task_reinitialized"
                        if reinitialized
                        else "progress_reset",
                        position=position,
                        taskCatalog=catalog,
                    )
                if catalog and (not previous_catalog or changed_catalog or full_reset or reinitialized):
                    self.doc["taskCatalog"] = catalog
                    # Legacy summaries could have accumulated removed tasks.
                    self.doc["tasks"] = {k: v for k, v in self.doc["tasks"].items() if k in catalog}
                elif added:
                    self.doc["taskCatalog"] = sorted(set(previous_catalog or []) | set(catalog))
                for task in tasks:
                    instance = task.get("taskInstanceId")
                    if instance:
                        known = instances.setdefault(
                            instance,
                            {
                                "taskBatch": self.doc["taskBatch"],
                                "taskId": task.get("taskId"),
                                "taskType": task.get("taskType"),
                                "step": task.get("step"),
                                "complete": False,
                            },
                        )
                        if known["complete"] and not task.get("complete"):
                            # The task list can lag a retained native receipt.
                            task = {**task, "complete": True, "step": known["step"]}
                    tid = str(task.get("taskId")) + ":" + str(task.get("taskType"))
                    before = self.doc["tasks"].get(tid)
                    if before is None:
                        change = "task_first_observed"
                    elif task.get("complete") and not before.get("complete"):
                        change = "task_completed"
                    elif task.get("step") != before.get("step"):
                        change = "task_reset" if task.get("step", 0) < before.get("step", 0) else "task_progress"
                    else:
                        continue
                    item = event(
                        change,
                        task=task,
                        previousStep=before.get("step") if before else None,
                        position=position,
                        location=state.get("nearestAnchor"),
                    )
                    self.doc["tasks"][tid] = task
                    if instance:
                        instances[instance].update(complete=bool(task.get("complete")), step=task.get("step"))
                    self.doc["taskEvents"].append(item)
                    del self.doc["taskEvents"][:-300]
                apply_completions()
                for expired in list(instances)[:-512]:
                    del instances[expired]
                self.current_visible = {str(p.get("playerId")) for p in state.get("nearbyPlayers", [])}
                for player in state.get("nearbyPlayers", []):
                    pid = str(player.get("playerId"))
                    previous = self.active.get(pid)
                    entering = previous is None or now - previous["timestamp"] > 3
                    if entering:
                        item = event(
                            "player_encounter",
                            player=player,
                            selfPosition=position,
                            position=position,
                            location=state.get("nearestAnchor"),
                        )
                        self.doc["encounters"].append(item)
                        del self.doc["encounters"][:-300]
                        new_encounter = True
                    old = self.doc["players"].get(pid, {})
                    logged = (previous or {}).get("loggedPlayer", player)
                    moved = (
                        "x" in player
                        and "y" in player
                        and "x" in logged
                        and "y" in logged
                        and distance(player, logged) >= 0.5
                    )
                    if not entering and moved:
                        event(
                            "player_moved",
                            player=player,
                            previousPosition={"x": logged["x"], "y": logged["y"]},
                            position=position,
                            location=state.get("nearestAnchor"),
                        )
                    self.doc["players"][pid] = {
                        "player": player,
                        "firstSeenAt": old.get("firstSeenAt", stamp),
                        "lastSeenAt": stamp,
                        "selfPosition": position,
                        "timestamp": now,
                        "lastLocation": state.get("nearestAnchor"),
                        "encounterStartedAt": stamp if entering else old.get("encounterStartedAt", stamp),
                        "encounterCount": old.get("encounterCount", 0) + int(entering),
                    }
                    self.active[pid] = {
                        "timestamp": now,
                        "player": player,
                        "loggedPlayer": player if entering or moved else logged,
                    }
                # Brief edge flicker doesn't create a new encounter; missing
                # players are nevertheless excluded from *current* visibility.
                for pid, previous in list(self.active.items()):
                    if pid not in self.current_visible and now - previous["timestamp"] >= 0.6:
                        player = previous["player"]
                        item = event(
                            "player_left_view",
                            player=player,
                            lastSeenAt=self.doc["players"][pid]["lastSeenAt"],
                            position=position,
                            location=state.get("nearestAnchor"),
                        )
                        self.doc["encounters"].append(item)
                        del self.doc["encounters"][:-300]
                        del self.active[pid]
                nav = state.get("navigation") or {}
                navkey = [nav.get("commandId"), nav.get("state")]
                if navkey != self.doc.get("lastNavigation"):
                    self.doc["lastNavigation"] = navkey
                    if (
                        navkey[0]
                        and nav.get("action") == "navigate"
                        and navkey[1] in ("running", "completed", "failed", "stuck")
                    ):
                        kind = (
                            "started"
                            if navkey[1] == "running"
                            else "failed"
                            if navkey[1] in ("failed", "stuck")
                            else "completed"
                        )
                        event(
                            "navigation_" + kind,
                            targetAnchorId=nav.get("targetAnchorId"),
                            error=nav.get("error"),
                            position=position,
                        )
            self.doc["previousPhase"] = state["phase"]
            self.doc["previousVentTransit"] = vent_transit
            if state["phase"] != "TASKS":
                self.active.clear()
                self.current_visible.clear()
            if now - self.last_flush >= 2 or new_encounter:
                self.flush()
            return new_encounter

    def _append(self, event):
        log = self.path / "events.jsonl"
        if log.exists() and log.stat().st_size > 8 * 1024 * 1024:
            for index in (2, 1):
                old = self.path / f"events.{index}.jsonl"
                if old.exists():
                    old.replace(self.path / f"events.{index + 1}.jsonl")
            log.replace(self.path / "events.1.jsonl")
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.doc["eventCount"] += 1

    def flush(self):
        with self.lock:
            if self.path is None or not self.dirty:
                return
            path = self.path / "summary.json"
            atomic(path, self.doc)
            self.last_flush = self.doc.get("lastTimestamp", 0)
            self.dirty = False

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("记录开关必须为布尔值。")
        with self.lock:
            self.flush()
            self.enabled = enabled
            self.active.clear()
            self.current_visible.clear()
            self.interrupted = True

    def record_action(self, state, kind, **fields):
        with self.lock:
            identity = [state.get("sessionId"), state.get("matchId", 0), state.get("mapId")]
            if not self.enabled or not self.path or self.doc.get("identity") != identity:
                return
            item = {
                "event": kind,
                "at": state.get("telemetryAt"),
                "sessionId": identity[0],
                "matchId": identity[1],
                "eventId": self.path.name + ":" + self.doc["recordId"] + ":" + str(self.doc["eventCount"] + 1),
                "taskBatch": self.doc.get("taskBatch", 1),
                "source": "local_controller",
                "position": state.get("self", {}).get("position"),
                **fields,
            }
            self._append(item)
            self.doc["timeline"].append(item)
            del self.doc["timeline"][:-500]
            self.dirty = True
            self.flush()

    def clear(self, all_matches=False):
        """Delete only this journal's known files, never application memory."""
        with self.lock:
            resolved_root = self.root.resolve()
            candidates = (
                list(self.root.iterdir()) if all_matches and self.root.exists() else [self.path] if self.path else []
            )
            targets = []
            for path in candidates:
                if not path.is_dir() or len(path.name) != 24 or any(c not in "0123456789abcdef" for c in path.name):
                    continue
                if path.is_symlink() or path.resolve().parent != resolved_root:
                    raise ValueError("记录目录不在本插件的历史目录内，已停止清理。")
                targets.append(path)
            # All targets are validated before changing any files.
            for path in targets:
                for name in (
                    "summary.json",
                    "summary.tmp",
                    "events.jsonl",
                    "events.1.jsonl",
                    "events.2.jsonl",
                    "events.3.jsonl",
                ):
                    (path / name).unlink(missing_ok=True)
            stamp = datetime.now(timezone.utc).isoformat()
            if all_matches:
                self.root.mkdir(parents=True, exist_ok=True)
                atomic(self.root / "control.json", {"clearedAt": stamp})
                self.cleared_at = stamp
            identity = self.doc.get("identity")
            self.doc = {
                "identity": identity,
                "startedAt": stamp,
                "updatedAt": stamp,
                "clearedAt": stamp,
                "lastTimestamp": seconds(stamp),
                "recordId": uuid.uuid4().hex,
                "taskBatch": 1,
                "route": [],
                "taskEvents": [],
                "tasks": {},
                "encounters": [],
                "players": {},
                "timeline": [],
                "routePoints": 0,
                "distance": 0.0,
                "eventCount": 0,
            }
            self.active.clear()
            self.current_visible.clear()
            self.interrupted = True
            self.last_flush = 0
            self.dirty = True
            self.flush()
            return {
                "clearedAt": stamp,
                "clearedMatches": len(targets),
                "scope": "all_matches" if all_matches else "displayed_match",
                "summary": "游戏观测记录已清理；清理前的旧记忆不能作为当前事实。N.E.K.O. 聊天记忆不属于此记录目录。",
            }

    def query(self, kind="all", limit=30):
        if kind not in ("all", "route", "tasks", "encounters"):
            raise ValueError("kind 需为 all、route、tasks 或 encounters。")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit 需为1至200的整数。")
        with self.lock:
            d = self.doc if self.enabled else {}
            result = {
                "identity": self.doc.get("identity"),
                "recordId": self.doc.get("recordId"),
                "recordingEnabled": self.enabled,
                "clearedAt": self.doc.get("clearedAt") or self.cleared_at,
                "storedEventCount": self.doc.get("eventCount", 0),
                "startedAt": d.get("startedAt"),
                "updatedAt": d.get("updatedAt"),
                "taskBatch": d.get("taskBatch", 1),
                "routePointCount": d.get("routePoints", 0),
                "distance": round(d.get("distance", 0), 2),
                "crimeScenes": d.get("crimeScenes", []),
                "eventCount": d.get("eventCount", 0),
                "logDirectory": str(self.path) if self.path else None,
                "notice": "这是历史观测；最后目击不代表玩家现在仍在原处，完成任务必须有task_completed证据。",
            }
            if not self.enabled:
                result["notice"] = (
                    "行动记录和历史读取已由用户暂停；不要根据旧推送复述行动，当前实时位置与可见玩家仍可读取。"
                )
            elif result["clearedAt"]:
                result["notice"] += "用户已清理旧记录；clearedAt之前的旧记忆不可用作本轮行动证据。"
            if kind in ("all", "route"):
                result["route"] = d.get("route", [])[-limit:]
                result["routeSummary"] = route_summary(d.get("route", []), limit if kind == "route" else 12)
            if kind in ("all", "tasks"):
                result["tasks"] = list(d.get("tasks", {}).values())
                result["taskEvents"] = d.get("taskEvents", [])[-limit:]
            if kind in ("all", "encounters"):
                result["encounters"] = d.get("encounters", [])[-limit:]
                reference = datetime.now(timezone.utc).timestamp()
                result["lastSeenPlayers"] = [
                    {
                        **p,
                        "isCurrent": str(p.get("player", {}).get("playerId")) in self.current_visible,
                        "presence": "visible_now"
                        if str(p.get("player", {}).get("playerId")) in self.current_visible
                        else "last_seen_only",
                        "ageSeconds": round(max(0, reference - p.get("timestamp", 0)), 1),
                    }
                    for p in sorted(d.get("players", {}).values(), key=lambda p: p.get("timestamp", 0), reverse=True)[
                        :20
                    ]
                ]
            relevant = d.get("timeline", [])
            if kind == "tasks":
                relevant = [e for e in relevant if e.get("event", "").startswith("task_")]
            elif kind == "encounters":
                relevant = [e for e in relevant if e.get("event", "").startswith("player_")]
            elif kind == "route":
                relevant = [
                    e
                    for e in relevant
                    if e.get("event", "").startswith(("navigation_", "area_", "observation_", "vent_"))
                ]
            result["timeline"] = relevant[-limit:]
            # Snapshot players in historical events must never advertise their
            # original visible_now flag as a claim about the present.
            result = json.loads(json.dumps(result, ensure_ascii=False))
            for name in ("timeline", "encounters"):
                for item in result.get(name, []):
                    if item.get("player"):
                        item["player"]["presence"] = "at_event_time"
            for item in result.get("lastSeenPlayers", []):
                item["player"]["presence"] = item["presence"]
                if item["player"].get("relativeToSelf"):
                    item["player"]["relativeToSelf"]["reference"] = "self_position_at_last_sighting"
            return result
