"""Replay patrol opportunities and retained native kill guards.

Crew policies are covered in test_predictive_play.py.
"""
import importlib
import unittest
import test_impostor as fixture

b = fixture.b
social = importlib.import_module("impostor_fixture.social")


class ImpostorPatrolTests(unittest.TestCase):
    def setUp(self):
        fixture.ImpostorTests.setUp(self)
        self.anchors.append({"anchorId": "skeld.cafeteria.interior", "kind": "waypoint", "roomId": "Cafeteria", "position": {"x": 10, "y": 0}})
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack
    opening_cover = fixture.ImpostorTests.opening_cover

    def patrol(self):
        self.opening_cover()
        self.t["nearbyPlayers"] = []
        self.t["ownRole"].update(canKill=False, killCooldown=20)
        command = self.tick(109)
        self.assertEqual(self.pilot.impostor.intent, "patrol")
        self.assertFalse(command["interactAfterArrival"])
        self.ack("running")
        return command

    def test_cover_then_two_patrols_before_another_task_bar_cover(self):
        self.patrol()
        self.anchors.append({"anchorId": "task.other", "kind": "task", "roomId": "Navigation", "position": {"x": 12, "y": 0}})
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        self.t["ui"]["ownTasks"].append({"taskId": 8, "taskType": "ChartCourse", "locations": [{"x": 12, "y": 0}]})
        self.t["localPlayer"]["position"]["x"] = 10
        self.ack()
        self.tick(111)
        self.assertEqual(self.pilot.impostor.patrol_count, 1)
        self.assertEqual(self.tick(113)["targetAnchorId"], "task.other")
        self.assertEqual(self.pilot.impostor.intent, "patrol")

    def test_no_fake_task_still_has_patrol_instead_of_freezing(self):
        self.t["ui"]["ownTasks"] = []
        self.t["nearbyPlayers"] = []
        command = self.tick(100)
        self.assertEqual(command["targetAnchorId"], "skeld.cafeteria.interior")
        self.assertEqual(self.pilot.impostor.phase, "patrol")

    def test_safe_native_kill_can_preempt_active_patrol(self):
        self.patrol()
        self.t["nearbyPlayers"] = [{"playerId": 1, "x": 1, "y": 0}]
        self.t["ownRole"].update(canKill=True, killCooldown=0, killTargetId=1,
            killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "requiredSeconds": 4, "ready": True}])
        self.assertEqual(self.tick(111)["action"], "impostor_kill")
        self.assertIsNotNone(self.pilot.impostor.kill_command)
        self.assertIsNone(self.pilot.impostor.pending)

    def test_moving_target_switch_waits_for_stop_receipt_then_pursues(self):
        self.patrol()
        self.t["nearbyPlayers"] = [{"playerId": 1, "x": 5, "y": 0}]
        self.t["ownRole"].update(canKill=False, killCooldown=0, killTargetId=None)
        self.assertEqual(self.tick(111)["action"], "stop")
        self.ack()
        self.assertEqual(self.tick(113)["action"], "pursue_player")

    def test_current_recent_witness_cooldown_or_bad_detection_block_mid_route_kill(self):
        for case in ("current", "recent", "cooldown", "detection", "unsafe"):
            self.setUp()
            patrol = self.patrol()
            self.t["nearbyPlayers"] = [{"playerId": 1, "x": 1, "y": 0}]
            self.t["ownRole"].update(canKill=True, killCooldown=0, killTargetId=1)
            if case == "current": self.t["nearbyPlayers"].append({"playerId": 3, "x": 2, "y": 0})
            if case == "recent": self.t["recentPlayers"] = [{"playerId": 3, "x": 2, "y": 0, "ageSeconds": 1, "uncertaintyRadius": 2}]
            if case == "cooldown": self.t["ownRole"]["killCooldown"] = 10
            if case == "detection": self.t["playerDetection"]["status"] = "camera_unavailable"
            if case == "unsafe": self.t["ownRole"].update(killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "requiredSeconds": 4, "ready": False}])
            command = self.tick(111)
            self.assertNotEqual(command["action"], "impostor_kill")
            if case != "unsafe": self.assertEqual(command["commandId"], patrol["commandId"])

    def test_patrol_avoids_crime_scenes_and_does_not_hunt_historical_only_players(self):
        self.opening_cover()
        self.t["nearbyPlayers"] = []
        self.t["recentPlayers"] = [{"playerId": 1, "x": 10, "y": 0, "ageSeconds": 2}]
        self.t["crimeScenes"] = [{"x": 10, "y": 0, "avoidRadius": 3}]
        self.anchors.append({"anchorId": "safe.interior", "kind": "waypoint", "roomId": "Reactor", "position": {"x": -7, "y": 0}})
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        command = self.tick(109)
        self.assertEqual(command["action"], "navigate")
        self.assertEqual(command["targetAnchorId"], "safe.interior")
        self.assertEqual(self.pilot.impostor.phase, "patrol")


if __name__ == "__main__":
    unittest.main()
