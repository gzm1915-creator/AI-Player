"""Replay fake-task circuits, four distinct rooms and the observed escape freeze."""

import importlib
import unittest
from unittest.mock import patch

import test_impostor as fixture

b = fixture.b
sweep_module = importlib.import_module("impostor_fixture.room_sweep")
decisions_module = importlib.import_module("impostor_fixture.decisions")


class RoomLedgerTests(unittest.TestCase):
    def test_four_different_other_rooms_are_required_before_revisiting(self):
        sweep = sweep_module.RoomSweep()
        sweep.arrive("Electrical")
        for room in ("Storage", "Admin", "Cafeteria"):
            sweep.arrive(room)
            self.assertFalse(sweep.allowed("Electrical"))
        sweep.arrive("Weapons")
        self.assertTrue(sweep.allowed("Electrical"))
        self.assertFalse(sweep.allowed("Storage"))

    def test_same_room_and_hallway_do_not_advance_the_buffer(self):
        sweep = sweep_module.RoomSweep()
        sweep.arrive("Electrical")
        for _ in range(8):
            sweep.arrive("Storage")
            sweep.arrive("Hallway_South")
        self.assertEqual(sweep.recent, ["Electrical", "Storage"])
        self.assertFalse(sweep.allowed("Electrical"))


class CircuitReplayTests(unittest.TestCase):
    setUp = fixture.ImpostorTests.setUp
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack

    @staticmethod
    def anchor(room, x, kind="waypoint", suffix="inside"):
        return {"anchorId": room.lower() + "." + suffix, "roomId": room, "kind": kind, "position": {"x": x, "y": 0}}

    def circuit(self):
        self.anchors = [
            self.anchor("Storage", 1, "task", "wiring"),
            self.anchor("Admin", 5, "task", "card"),
            self.anchor("Weapons", 13, "task", "power"),
            self.anchor("Electrical", 7),
            self.anchor("LowerEngine", -6),
            self.anchor("Cafeteria", -14),
            self.anchor("Reactor", -21),
            self.anchor("Security", -19),
            self.anchor("UpperEngine", -17),
            self.anchor("Navigation", 25),
            self.anchor("O2", 21),
        ]
        self.t["nearbyPlayers"] = []
        self.t["ownRole"].update(canKill=False, killCooldown=20)
        self.t["ui"]["ownTasks"] = [
            {"taskId": i, "taskType": "FixWiring", "step": 0, "locations": [a["position"]]}
            for i, a in enumerate(self.anchors[:3])
        ]
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})

    def arrive(self, now):
        command = b.load(self.bridge.command_path)
        anchor = next(a for a in self.anchors if a["anchorId"] == command["targetAnchorId"])
        self.t["localPlayer"]["position"].update(anchor["position"])
        self.ack()
        self.tick(now)
        return anchor

    def test_neighboring_pending_task_does_not_replace_required_patrol(self):
        self.circuit()
        self.tick(100)
        self.arrive(102)
        self.assertEqual(self.pilot.impostor.sweep.current, "Storage")
        command = self.tick(109)
        self.assertEqual(self.pilot.impostor.intent, "patrol")
        self.assertEqual(self.pilot.impostor.sweep.next_task, "admin.card")
        self.assertFalse(command["interactAfterArrival"])
        self.assertNotIn("interactTaskId", command)

    def test_multi_room_replay_does_not_loop_between_task_rooms(self):
        self.circuit()
        self.tick(100)
        rooms = []
        for index in range(8):
            imp = self.pilot.impostor
            target = next(a for a in self.anchors if a["anchorId"] == imp.target)
            self.assertNotIn(target["roomId"], imp.sweep.recent, rooms)
            reached = self.arrive(102 + index * 10)
            rooms.append(reached["roomId"])
            self.tick(110 + index * 10)
        self.assertGreaterEqual(len(set(rooms[:5])), 5)
        for index, room in enumerate(rooms):
            if room in rooms[:index]:
                previous = max(i for i in range(index) if rooms[i] == room)
                self.assertGreaterEqual(len(set(rooms[previous + 1 : index])), 4)

    def test_unvisited_distant_room_can_break_out_of_a_local_cluster(self):
        self.circuit()
        imp = self.pilot.impostor
        for room in ("Storage", "Admin", "Cafeteria", "Weapons"):
            imp.sweep.arrive(room)
        far = self.anchor("Reactor", -30)
        target = imp.patrol_target(self.bridge.read(), [far], 100)
        self.assertEqual(target["roomId"], "Reactor")

    def test_time_expiry_cannot_override_the_four_room_buffer(self):
        self.circuit()
        self.save()
        imp = self.pilot.impostor
        imp.sweep.arrive("Electrical")
        self.assertIsNone(imp.patrol_target(self.bridge.read(), [self.anchor("Electrical", 7)], 100000))

    def test_failed_or_unconfirmed_arrival_never_counts_as_a_room_visit(self):
        self.circuit()
        self.tick(100)
        self.ack("failed")
        self.tick(102)
        self.assertEqual(self.pilot.impostor.sweep.recent, [])
        self.tick(110)
        self.ack()
        self.t["localPlayer"]["position"].update(x=100, y=100)
        self.tick(112)
        self.assertEqual(self.pilot.impostor.sweep.recent, [])

    def test_pause_and_resume_keep_room_buffer_but_new_match_clears_it(self):
        self.circuit()
        self.tick(100)
        self.arrive(102)
        self.pilot.set_enabled(False)
        self.pilot.set_enabled(True)
        self.assertEqual(self.pilot.impostor.sweep.recent, ["Storage"])
        self.t["game"]["matchId"] = 2
        self.tick(110)
        self.assertEqual(self.pilot.impostor.sweep.recent, [])

    def test_task_in_a_buffered_room_is_not_remapped_to_a_neighbor_task_anchor(self):
        self.circuit()
        self.pilot.impostor.sweep.arrive("Storage")
        anchors = [self.anchor("Storage", 1, "task"), self.anchor("Admin", 2, "task")]
        state = {"tasks": [{"taskId": 1, "locations": [{"x": 1, "y": 0}]}]}
        self.assertEqual(self.pilot.impostor.cover_targets(state, anchors, 100), [])

    def test_no_fake_tasks_rotates_through_new_rooms_instead_of_returning_early(self):
        self.circuit()
        self.t["ui"]["ownTasks"] = []
        self.tick(100)
        rooms = []
        for index in range(6):
            reached = self.arrive(102 + index * 10)
            rooms.append(reached["roomId"])
            self.tick(110 + index * 10)
        self.assertEqual(len(set(rooms[:5])), 5)

    def test_observed_post_kill_geometry_rejects_old_scene_and_uses_lower_engine(self):
        self.circuit()
        self.tick(100)
        imp = self.pilot.impostor
        imp.release()
        imp.phase = "escape"
        imp.body = {"x": -21.819996, "y": -8.683599}
        imp.escape_until = 200
        imp.vent_attempted = True
        self.t["localPlayer"]["position"].update(imp.body)
        self.t["crimeScenes"] = [{**imp.body, "avoidRadius": 3}, {"x": -14.0199995, "y": -4.1635995, "avoidRadius": 3}]
        self.anchors = [
            {
                "anchorId": "security.entry",
                "kind": "waypoint",
                "roomId": "Security",
                "position": {"x": -13.669004, "y": -5.620815},
            },
            {
                "anchorId": "engine.lower",
                "kind": "waypoint",
                "roomId": "LowerEngine",
                "position": {"x": -15.252331, "y": -11.954123},
            },
        ]
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        self.ack("failed")
        command = self.tick(104)
        self.assertEqual(command["targetAnchorId"], "engine.lower")
        self.assertTrue(command["escapeRoute"])
        self.assertEqual(imp.phase, "escape")

    def test_escape_submission_failure_cools_the_target_and_tries_another(self):
        self.circuit()
        self.tick(100)
        imp = self.pilot.impostor
        imp.release()
        imp.phase = "escape"
        imp.body = {"x": 0, "y": 0}
        imp.escape_until = 200
        imp.vent_attempted = True
        self.ack("failed")
        with patch.object(self.bridge, "navigate", side_effect=ValueError("cannot attach")):
            self.tick(104)
        blocked = {aid for aid, until in imp.visited.items() if until > 104}
        self.assertTrue(blocked)
        command = self.tick(106)
        self.assertNotIn(command["targetAnchorId"], blocked)
        self.assertTrue(command["escapeRoute"])
        self.assertTrue(self.pilot.enabled)
        self.assertEqual(imp.recovery_count, 1)
        self.assertIn("提交失败", imp.last_recovery["reason"])

    def test_active_lights_can_skip_countdown_but_recent_witnesses_still_block(self):
        self.pilot.role_identity = (self.t["sessionId"], 1, "Impostor")
        imp = self.pilot.impostor
        imp.identity = (self.t["sessionId"], 1, "TheSkeld")
        imp.opening_done = True
        self.t["activeEmergencies"] = ["FixLights"]
        self.t["ownRole"].update(
            killSafetySupported=True,
            lightsKillWaitSupported=True,
            killSafety=[
                {
                    "targetPlayerId": 1,
                    "ready": True,
                    "requiredSeconds": 0,
                    "witnessMemorySeconds": 4,
                    "lightsWaitBypassed": True,
                }
            ],
        )
        self.t["recentPlayers"] = [{"playerId": 3, "x": 2, "y": 0, "ageSeconds": 3, "uncertaintyRadius": 2}]
        self.assertNotEqual(self.tick(100).get("action"), "impostor_kill")
        imp.release()
        self.ack("failed")
        self.t["recentPlayers"] = []
        self.assertEqual(self.tick(102)["action"], "impostor_kill")

    def test_completed_sabotage_receipt_alone_does_not_skip_safety(self):
        self.pilot.role_identity = (self.t["sessionId"], 1, "Impostor")
        imp = self.pilot.impostor
        imp.identity = (self.t["sessionId"], 1, "TheSkeld")
        imp.opening_done = True
        self.t["sabotage"] = {"lastAction": {"target": "lights", "state": "completed"}}
        self.t["ownRole"].update(
            killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "ready": False, "requiredSeconds": 4}]
        )
        self.assertNotEqual(self.tick(100).get("action"), "impostor_kill")

    def test_blackout_witness_block_is_explained_without_claiming_a_countdown(self):
        self.t["ownRole"].update(
            killSafetySupported=True,
            killSafety=[
                {
                    "targetPlayerId": 1,
                    "ready": False,
                    "requiredSeconds": 0,
                    "lightsWaitBypassed": True,
                    "witnessMemorySeconds": 4,
                    "reason": "当前或刚离开的玩家可能目击，重新判断",
                }
            ],
        )
        self.save()
        decisions = decisions_module.kill_decisions(self.bridge.read())
        self.assertTrue(decisions[0]["lightsWaitBypassed"])
        self.assertIn("玩家可能目击", decisions[0]["reason"])
        self.assertNotIn("连续3至5秒", decisions[0]["reason"])
