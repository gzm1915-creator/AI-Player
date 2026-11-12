"""Deterministic replay of role decisions and portable command boundaries."""

import copy
import importlib
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

package = types.ModuleType("impostor_fixture")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
b = importlib.import_module("impostor_fixture.bridge")


class ImpostorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.bridge = b.Bridge(Path(temp.name))
        self.bridge.root.mkdir(parents=True)
        self.anchors = [
            {"anchorId": "skeld.admin.swipe-card", "kind": "task", "position": {"x": 1, "y": 0}},
            {"anchorId": "skeld.med-scanner", "kind": "task", "position": {"x": 0, "y": 0}},
            {"anchorId": "skeld.cafeteria.door", "kind": "waypoint", "position": {"x": 10, "y": 0}},
        ]
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        self.t = {
            "sessionId": "12345678-1234-1234-1234-123456789012",
            "game": {"phase": "TASKS", "matchId": 1, "mapId": "TheSkeld"},
            "localPlayer": {
                "playerId": 0,
                "canMove": True,
                "inVent": False,
                "position": {"space": "unity_world", "x": 0, "y": 0},
            },
            "ownRole": {
                "roleType": "Impostor",
                "isImpostor": True,
                "isDead": False,
                "basicImpostorSupported": True,
                "dynamicPursuitSupported": True,
                "killCooldown": 0,
                "canKill": True,
                "killTargetId": 1,
                "teammateIds": [2],
            },
            "playerDetection": {"status": "ok"},
            "nearbyPlayers": [{"playerId": 1, "x": 1, "y": 0}],
            "activeEmergencies": [],
            "ui": {"ownTasks": [{"taskId": 7, "taskType": "SwipeCard", "step": 0, "locations": [{"x": 1, "y": 0}]}]},
        }
        self.pilot = b.Autopilot(self.bridge)
        self.pilot.set_enabled(True)
        self.bridge.set_runtime("autoNavigation", True)
        self.save()

    def save(self):
        self.t["sentAtUtc"] = datetime.now(timezone.utc).isoformat()
        b.atomic(self.bridge.root / "latest.json", self.t)

    def tick(self, at):
        self.save()
        with patch("time.monotonic", return_value=at):
            with patch("impostor_fixture.impostor.random.uniform", return_value=6):
                self.pilot.tick(self.bridge.read())
        return b.load(self.bridge.command_path)

    def ack(self, state="completed"):
        cmd = b.load(self.bridge.command_path)
        b.atomic(self.bridge.nav_path, {"commandId": cmd["commandId"], "state": state})

    def opening_cover(self):
        people = self.t["nearbyPlayers"]
        self.t["nearbyPlayers"] = []
        cmd = self.tick(100)
        self.assertEqual(cmd["action"], "navigate")
        self.assertNotIn("med-scanner", cmd["targetAnchorId"])
        self.assertFalse(cmd["interactAfterArrival"])
        self.assertNotIn("interactTaskId", cmd)
        self.ack()
        self.tick(102)
        self.t["nearbyPlayers"] = people

    def test_cover_first_hold_then_native_request_then_escape(self):
        self.opening_cover()
        original = b.load(self.bridge.command_path)["commandId"]
        people = self.t["nearbyPlayers"]
        self.t["nearbyPlayers"] = []
        self.assertEqual(self.tick(105)["commandId"], original)
        self.t["nearbyPlayers"] = people
        cmd = self.tick(109)
        self.assertEqual(cmd["action"], "impostor_kill")
        timeline = self.bridge.history()["timeline"]
        self.assertTrue(any(e["event"] == "impostor_cover" for e in timeline))
        self.assertFalse(any(e["event"] in ("task_completed", "impostor_kill_confirmed") for e in timeline))
        self.t["impostorAction"] = {"commandId": cmd["commandId"], "state": "completed", "position": {"x": 1, "y": 0}}
        self.tick(110)
        self.assertEqual(self.pilot.impostor.phase, "escape")
        escape = self.tick(112)
        self.assertEqual(escape["action"], "navigate")
        self.assertEqual(escape["targetAnchorId"], "skeld.cafeteria.door")
        self.assertFalse(escape["interactAfterArrival"])
        timeline = self.bridge.history()["timeline"]
        self.assertEqual(sum(e["event"] == "impostor_kill_confirmed" for e in timeline), 1)

    def test_witness_cooldown_detection_and_teammate_prevent_kill(self):
        self.pilot.role_identity = (self.t["sessionId"], 1, "Impostor")
        for case in ("witness", "cooldown", "detection", "teammate"):
            with self.subTest(case=case):
                self.pilot.impostor.identity = (self.t["sessionId"], 1, "TheSkeld")
                self.pilot.impostor.reset()
                self.pilot.impostor.opening_done = True
                old = copy.deepcopy(self.t)
                if case == "witness":
                    self.t["nearbyPlayers"].append({"playerId": 3, "x": 2, "y": 0})
                elif case == "cooldown":
                    self.t["ownRole"]["killCooldown"] = 10
                elif case == "detection":
                    self.t["playerDetection"]["status"] = "camera_unavailable"
                else:
                    self.t["ownRole"]["teammateIds"].append(1)
                b.atomic(self.bridge.nav_path, {"state": "completed"})
                cmd = self.tick(100)
                self.assertNotEqual(cmd.get("action"), "impostor_kill")
                self.t = old
                self.bridge.last_command_at = 0

    def test_target_leaves_view_cancel_even_when_temporarily_immobile(self):
        self.pilot.role_identity = (self.t["sessionId"], 1, "Impostor")
        self.pilot.impostor.identity = (self.t["sessionId"], 1, "TheSkeld")
        self.pilot.impostor.opening_done = True
        self.t["ownRole"].update(canKill=False, killTargetId=None)
        self.tick(100)
        self.assertEqual(self.pilot.impostor.intent, "hunt")
        self.ack("running")
        self.t["nearbyPlayers"] = []
        self.t["localPlayer"]["canMove"] = False
        self.assertEqual(self.tick(102)["action"], "stop")
        self.assertIsNone(self.pilot.impostor.pending)

    def test_meeting_resets_plan_and_does_not_replay_kill(self):
        self.opening_cover()
        cmd = self.tick(109)
        self.t["game"]["phase"] = "MEETING"
        self.tick(110)
        self.assertIsNone(self.pilot.impostor.kill_command)
        self.assertFalse(self.pilot.impostor.opening_done)
        self.assertEqual(b.load(self.bridge.command_path)["commandId"], cmd["commandId"])

    def test_special_role_blocked_and_death_stops_owned_route(self):
        self.t["ownRole"]["roleType"] = "Shapeshifter"
        self.tick(100)
        self.assertFalse(self.bridge.command_path.exists())
        self.assertEqual(self.pilot.state, "blocked")
        self.t["ownRole"]["roleType"] = "Impostor"
        self.t["ownRole"].update(canKill=False, killTargetId=None)
        self.tick(102)
        self.t["ownRole"]["isDead"] = True
        self.assertEqual(self.tick(104)["action"], "stop")

    def test_failed_receipt_and_unconfirmed_request_never_log_kill(self):
        self.opening_cover()
        cmd = self.tick(109)
        self.t["impostorAction"] = {"commandId": "other-command", "state": "completed"}
        self.tick(111)
        self.assertEqual(self.pilot.impostor.kill_command, cmd["commandId"])
        self.tick(115)
        self.assertIsNone(self.pilot.impostor.kill_command)
        self.assertFalse(any(e["event"] == "impostor_kill_confirmed" for e in self.bridge.history()["timeline"]))

    def test_bridge_rejects_stale_cross_match_invisible_or_wrong_native_target(self):
        for case in ("match", "invisible", "target", "stale"):
            with self.subTest(case=case):
                old = copy.deepcopy(self.t)
                if case == "invisible":
                    self.t["nearbyPlayers"] = []
                if case == "target":
                    self.t["ownRole"]["killTargetId"] = 9
                self.save()
                if case == "stale":
                    self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
                    b.atomic(self.bridge.root / "latest.json", self.t)
                with self.assertRaises(ValueError):
                    self.bridge.kill(1, self.t["sessionId"], 2 if case == "match" else 1)
                self.assertFalse(self.bridge.command_path.exists())
                self.t = old

    def test_role_switch_clears_ordinary_pending_and_disables_auto_report(self):
        self.pilot.pending = "old-crewmate-route"
        self.bridge.set_runtime("autoReportBodies", True)
        self.tick(100)
        self.assertIsNone(self.pilot.pending)
        self.assertFalse(b.load(self.bridge.control_path)["autoReportBodies"])
        self.t["ownRole"].update(roleType="Crewmate", isImpostor=False)
        self.tick(102)
        self.assertIsNone(self.pilot.impostor.pending)
        self.assertEqual(self.pilot.role_policy, "crewmate")

    def test_interact_false_and_manual_impostor_navigation_never_open_task(self):
        for interact in (False, True):
            self.bridge.last_command_at = 0
            self.bridge.navigate(self.anchors[0]["anchorId"], {"x": 1, "y": 0, "taskId": 7}, interact=interact)
            cmd = b.load(self.bridge.command_path)
            self.assertFalse(cmd["interactAfterArrival"])
            self.assertFalse(cmd["stopOnTaskButton"])
            self.assertNotIn("interactTaskId", cmd)

    def test_expired_telemetry_clears_role_action_and_emergencies(self):
        self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.t["impostorAction"] = {"state": "completed"}
        b.atomic(self.bridge.root / "latest.json", self.t)
        state = self.bridge.read()
        self.assertEqual(state["ownRole"], {})
        self.assertEqual(state["impostorAction"], {})
        self.assertIsNone(state["activeEmergencies"])

    def test_sabotage_uses_independent_command_during_navigation(self):
        self.t["nearbyPlayers"] = []
        route = self.tick(100)
        self.ack("running")
        self.t["sabotage"] = {"supported": True, "canSabotage": True, "cooldown": 0}
        self.save()
        with patch("time.monotonic", return_value=102):
            result = self.bridge.sabotage("通讯")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(b.load(self.bridge.command_path)["commandId"], route["commandId"])
        command = b.load(self.bridge.sabotage_path)
        self.assertEqual(command["target"], "comms")
        self.assertEqual(command["expectedMatchId"], 1)

    def test_sabotage_missing_component_cooldown_meeting_and_role_refused(self):
        for case in ("legacy", "cooldown", "meeting", "crewmate", "unknown_target"):
            with self.subTest(case=case):
                old = copy.deepcopy(self.t)
                self.t["sabotage"] = {"supported": True, "canSabotage": True}
                if case == "legacy":
                    self.t["sabotage"] = {}
                if case == "cooldown":
                    self.t["sabotage"].update(canSabotage=False, reason="破坏冷却中")
                if case == "meeting":
                    self.t["game"]["phase"] = "MEETING"
                if case == "crewmate":
                    self.t["ownRole"].update(roleType="Crewmate", isImpostor=False)
                self.save()
                with self.assertRaises(ValueError):
                    self.bridge.sabotage("doors" if case == "unknown_target" else "lights")
                self.assertFalse(self.bridge.sabotage_path.exists())
                self.t = old

    def test_autonomous_sabotage_receipt_only_confirms_after_native_system(self):
        self.opening_cover()
        self.t["nearbyPlayers"].append({"playerId": 3, "x": 2, "y": 0})
        self.t["ownRole"].update(canKill=False, killCooldown=15)
        self.t["sabotage"] = {"supported": True, "canSabotage": True}
        self.tick(109)
        command = b.load(self.bridge.sabotage_path)
        self.assertEqual(command["target"], "comms")
        self.assertFalse(any(e["event"] == "impostor_sabotage_confirmed" for e in self.bridge.history()["timeline"]))
        self.t["sabotage"] = {
            "supported": True,
            "canSabotage": False,
            "lastAction": {"commandId": command["commandId"], "state": "completed", "target": "comms"},
        }
        self.tick(111)
        self.assertEqual(sum(e["event"] == "impostor_sabotage_confirmed" for e in self.bridge.history()["timeline"]), 1)

    def test_task_bar_cover_rotates_rooms_and_prefers_unvisited_tasks(self):
        self.anchors = [
            {"anchorId": "electrical.wiring", "roomId": "Electrical", "kind": "task", "position": {"x": 1, "y": 0}},
            {"anchorId": "electrical.power", "roomId": "Electrical", "kind": "task", "position": {"x": 2, "y": 0}},
            {"anchorId": "reactor.manifolds", "roomId": "Reactor", "kind": "task", "position": {"x": 20, "y": 0}},
            {"anchorId": "unassigned.nearest", "roomId": "Cafeteria", "kind": "task", "position": {"x": 0, "y": 0}},
        ]
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        self.t["ui"]["ownTasks"] = [
            {"taskId": 0, "taskType": "FixWiring", "locations": [{"x": 1, "y": 0}]},
            {"taskId": 2, "taskType": "DivertPower", "locations": [{"x": 2, "y": 0}]},
            {"taskId": 3, "taskType": "UnlockManifolds", "locations": [{"x": 20, "y": 0}]},
        ]
        self.t["nearbyPlayers"] = []
        first = self.tick(100)
        self.assertEqual(first["targetAnchorId"], "electrical.wiring")
        self.t["localPlayer"]["position"]["x"] = 1
        self.ack()
        self.tick(102)
        second = self.tick(109)
        self.assertEqual(second["targetAnchorId"], "reactor.manifolds")
        self.t["localPlayer"]["position"]["x"] = 20
        self.ack()
        self.tick(120)
        third = self.tick(128)
        self.assertEqual(third["targetAnchorId"], "unassigned.nearest")
        self.assertEqual(self.pilot.impostor.intent, "patrol")
        self.assertFalse(third["interactAfterArrival"])
        self.assertEqual(self.pilot.impostor.fake_task["source"], "own_task_bar")

    def test_visual_tasks_and_storage_dump_never_become_cover(self):
        banned = [
            ("ClearAsteroids", "skeld.weapons.asteroids", "Weapons"),
            ("SubmitScan", "skeld.medical.med-scanner", "MedBay"),
            ("InspectSample", "skeld.medical.med-bay", "MedBay"),
            ("EmptyGarbage", "skeld.storage.garbage.runtime", "Storage"),
        ]
        anchors = []
        tasks = []
        for i, (kind, aid, room) in enumerate(banned):
            position = {"x": i * 10, "y": 0}
            anchors.append({"anchorId": aid, "kind": "task", "roomId": room, "position": position})
            tasks.append({"taskId": i, "taskType": kind, "locations": [position]})
        self.assertEqual(self.pilot.impostor.cover_targets({"tasks": tasks}, anchors, 100), [])
        self.assertEqual(self.pilot.impostor.cover_targets({"tasks": []}, self.anchors, 100), [])

    def test_cover_dwell_is_sampled_from_five_to_fifteen_seconds(self):
        self.t["nearbyPlayers"] = []
        self.tick(100)
        self.ack()
        with (
            patch("time.monotonic", return_value=102),
            patch("impostor_fixture.impostor.random.uniform", return_value=15) as sample,
        ):
            self.pilot.tick(self.bridge.read())
        sample.assert_called_once_with(5, 15)
        self.assertEqual(self.pilot.impostor.dwell_until, 117)

    def start_pursuit(self):
        self.pilot.role_identity = (self.t["sessionId"], 1, "Impostor")
        self.pilot.impostor.identity = (self.t["sessionId"], 1, "TheSkeld")
        self.pilot.impostor.opening_done = True
        self.t["ownRole"].update(canKill=False, killTargetId=None)
        command = self.tick(100)
        self.assertEqual(command["action"], "pursue_player")
        self.ack("running")
        return command

    def test_running_pursuit_kills_in_native_range_without_waiting_for_arrival(self):
        chase = self.start_pursuit()
        self.t["nearbyPlayers"][0].update(x=3.7, velocityX=2)
        self.assertEqual(self.tick(101)["commandId"], chase["commandId"])
        self.t["ownRole"].update(canKill=True, killTargetId=1)
        command = self.tick(102)
        self.assertEqual(command["action"], "impostor_kill")
        self.assertIsNone(self.pilot.impostor.pending)
        self.assertEqual(self.pilot.impostor.kill_command, command["commandId"])
        self.assertFalse(any(e["event"] == "impostor_kill_confirmed" for e in self.bridge.history()["timeline"]))
        self.t["impostorAction"] = {
            "commandId": command["commandId"],
            "state": "completed",
            "position": {"x": 3.7, "y": 0},
        }
        self.tick(103)
        self.assertEqual(self.pilot.impostor.phase, "escape")

    def test_slow_target_does_not_bypass_native_range(self):
        chase = self.start_pursuit()
        self.t["nearbyPlayers"][0].update(x=4, velocityX=0, velocityY=0)
        b.atomic(
            self.bridge.nav_path,
            {
                "commandId": chase["commandId"],
                "state": "running",
                "pursuit": {"active": True, "targetSlowed": True, "targetSpeed": 0, "replans": 3},
            },
        )
        self.assertEqual(self.tick(102)["commandId"], chase["commandId"])
        self.assertIsNone(self.pilot.impostor.kill_command)
        self.assertTrue(self.pilot.impostor.status()["pursuit"]["targetSlowed"])

    def test_current_or_recent_witness_cancels_running_pursuit(self):
        self.start_pursuit()
        self.t["recentPlayers"] = [
            {
                "playerId": 3,
                "playerName": "刚转过墙角",
                "x": 3,
                "y": 0,
                "ageSeconds": 2,
                "uncertaintyRadius": 3,
                "presence": "last_seen_only",
            }
        ]
        self.assertEqual(self.tick(102)["action"], "stop")
        self.assertIsNone(self.pilot.impostor.pending)
        self.assertIsNone(self.pilot.impostor.kill_command)

    def test_recent_witness_blocks_ai_direct_kill_then_expires(self):
        self.t["recentPlayers"] = [{"playerId": 3, "x": 2, "y": 0, "ageSeconds": 2, "uncertaintyRadius": 3}]
        self.save()
        with self.assertRaises(ValueError):
            self.bridge.kill(1, self.t["sessionId"], 1)
        self.assertFalse(self.bridge.command_path.exists())
        self.t["recentPlayers"][0]["ageSeconds"] = 10.1
        self.save()
        self.assertEqual(self.bridge.kill(1, self.t["sessionId"], 1)["status"], "submitted")

    def test_uncertainty_near_self_matters_even_if_last_sighting_far_from_target(self):
        self.t["nearbyPlayers"][0]["x"] = 5
        self.t["recentPlayers"] = [{"playerId": 3, "x": -5, "y": 0, "ageSeconds": 1, "uncertaintyRadius": 1.5}]
        self.save()
        with self.assertRaises(ValueError):
            self.bridge.kill(1, self.t["sessionId"], 1)

    def test_pursuit_uses_live_player_id_without_nearest_task_anchor(self):
        self.t["nearbyPlayers"][0].update(x=4, y=4)
        command = self.start_pursuit()
        self.assertEqual(command["targetNumber"], 1)
        self.assertEqual(command["expectedMatchId"], 1)
        self.assertEqual(command["maxRecoveries"], 0)
        self.assertFalse(command["interactAfterArrival"])
        self.assertNotIn("interactTaskId", command)

    def test_pursuit_time_limit_cancels_instead_of_restarting_forever(self):
        self.start_pursuit()
        self.assertEqual(self.tick(146)["action"], "stop")
        self.assertIsNone(self.pilot.impostor.pending)

    def test_pursuit_requires_new_component_and_rejects_historical_only_target(self):
        for change in ("old_component", "not_visible", "teammate", "witness"):
            old = copy.deepcopy(self.t)
            if change == "old_component":
                self.t["ownRole"]["dynamicPursuitSupported"] = False
            if change == "not_visible":
                self.t["nearbyPlayers"] = []
            if change == "teammate":
                self.t["ownRole"]["teammateIds"].append(1)
            if change == "witness":
                self.t["nearbyPlayers"].append({"playerId": 3, "x": 2, "y": 0})
            self.save()
            with self.assertRaises(ValueError):
                self.bridge.pursue_player(1)
            self.assertFalse(self.bridge.command_path.exists())
            self.t = old

    def test_recent_memory_exposed_as_history_only_and_cleared_when_telemetry_stale(self):
        self.t["recentPlayers"] = [
            {"playerId": 3, "x": 2, "y": 0, "ageSeconds": 1, "uncertaintyRadius": 1.5, "presence": "last_seen_only"}
        ]
        self.save()
        state = self.bridge.read()
        self.assertEqual(state["recentPlayers"][0]["presence"], "last_seen_only")
        self.assertEqual(len(state["nearbyPlayers"]), 1)
        self.assertEqual(self.bridge.map_context()["recentPlayers"], state["recentPlayers"])
        self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.bridge.last_telemetry = None
        b.atomic(self.bridge.root / "latest.json", self.t)
        self.assertEqual(self.bridge.read()["recentPlayers"], [])
