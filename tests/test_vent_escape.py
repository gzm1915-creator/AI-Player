"""Confirmed kill -> optional vent approach -> guarded transit -> native receipt."""
import unittest
from unittest.mock import patch
import test_impostor as fixture
b = fixture.b


class VentEscapeTests(unittest.TestCase):
    setUp = fixture.ImpostorTests.setUp
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack

    def prepare(self, entry_x=.5):
        self.t["ventRole"] = {"roleType": "Impostor", "canVent": True, "inVent": False, "currentVentId": -1}
        self.t["ventTravel"] = {"supported": True, "escapeSupported": True, "approachSupported": True, "state": "idle", "busy": False}
        self.t["vents"] = [
            {"id": 0, "name": "AdminVent", "x": entry_x, "y": 0, "leftId": 1, "rightId": -1, "centerId": -1, "usableDistance": .75},
            {"id": 1, "name": "CafeVent", "x": 10, "y": 0, "leftId": 0, "rightId": -1, "centerId": -1, "usableDistance": .75}]
        self.bridge.set_runtime("useVents", True)
        self.tick(100)
        self.ack()
        ctrl = self.pilot.impostor
        ctrl.release()
        ctrl.kill_command, ctrl.target_player, ctrl.submitted = "kill-confirmed", 1, 101
        self.t["impostorAction"] = {"commandId": "kill-confirmed", "state": "completed", "position": {"x": 1, "y": 0}}
        self.t["nearbyPlayers"] = []
        self.tick(102)
        self.assertEqual(ctrl.phase, "escape")
        self.assertFalse(self.bridge.vent_path.exists())
        return ctrl

    def test_near_vent_is_automatically_used_only_after_confirmed_kill(self):
        ctrl = self.prepare()
        self.save()
        with patch("impostor_fixture.impostor.random.uniform", return_value=12) as sample, patch("time.monotonic", return_value=104):
            self.pilot.tick(self.bridge.read())
        sample.assert_called_once_with(2, 15)
        cmd = b.load(self.bridge.vent_path)
        self.assertEqual(cmd["action"], "travel")
        self.assertEqual((cmd["entryVentId"], cmd["targetVentId"]), (0, 1))
        self.assertTrue(cmd["guardedExit"])
        self.assertEqual(cmd["observeSeconds"], 12)
        self.assertEqual(ctrl.vent_command, cmd["commandId"])
        self.assertEqual(ctrl.result["state"], "submitted")
        self.assertEqual(ctrl.phase, "escape")
        self.assertFalse(any(e["event"] == "vent_completed" for e in self.bridge.history()["timeline"]))

    def test_walk_to_real_entrance_then_guarded_transit(self):
        ctrl = self.prepare(3)
        walk = self.tick(104)
        self.assertEqual(walk["action"], "navigate_vent")
        self.assertEqual(walk["targetNumber"], 0)
        self.assertFalse(walk["interactAfterArrival"])
        self.assertEqual(walk["maxRecoveries"], 0)
        self.assertFalse(self.bridge.vent_path.exists())
        self.t["localPlayer"]["position"]["x"] = 3
        self.ack()
        self.tick(106)
        self.assertIsNone(ctrl.pending)
        self.assertEqual(b.load(self.bridge.vent_path)["targetVentId"], 1)

    def test_witness_or_bad_detection_uses_ground_instead(self):
        self.prepare()
        self.t["nearbyPlayers"] = [{"playerId": 3, "x": 2, "y": 0}]
        command = self.tick(104)
        self.assertEqual(command["action"], "navigate")
        self.assertFalse(self.bridge.vent_path.exists())
        self.assertIn("周围有人", self.pilot.impostor.escape_reason)

    def test_witness_on_approach_cancels_and_falls_back(self):
        ctrl = self.prepare(3)
        self.tick(104)
        self.ack("running")
        self.t["nearbyPlayers"] = [{"playerId": 3, "x": 2, "y": 0}]
        self.assertEqual(self.tick(106)["action"], "stop")
        self.assertIsNone(ctrl.pending)
        self.assertFalse(self.bridge.vent_path.exists())
        self.ack()
        self.assertEqual(self.tick(108)["action"], "navigate")

    def test_failed_entrance_path_never_tries_to_enter_remotely(self):
        ctrl = self.prepare(3)
        self.tick(104)
        self.ack("failed")
        self.tick(106)
        self.assertIsNone(ctrl.vent_plan)
        self.assertEqual(self.tick(108)["action"], "navigate")
        self.assertFalse(self.bridge.vent_path.exists())

    def test_policy_disabled_old_component_or_no_usable_pipe_use_ground(self):
        for case in ("disabled", "old", "no_exit", "far", "bad_detection", "permission"):
            with self.subTest(case=case):
                self.setUp()
                self.prepare()
                if case == "disabled": self.bridge.set_runtime("impostorVentEscape", False)
                if case == "old": self.t["ventTravel"].pop("escapeSupported")
                if case == "no_exit": self.t["vents"][0]["leftId"] = -1
                if case == "far": self.t["vents"][0]["x"] = -20
                if case == "bad_detection": self.t["playerDetection"]["status"] = "partial"
                if case == "permission": self.bridge.set_runtime("useVents", False)
                self.assertEqual(self.tick(104)["action"], "navigate")
                self.assertFalse(self.bridge.vent_path.exists())

    def test_pipeline_waits_for_safe_native_completion_and_resumes_cover(self):
        ctrl = self.prepare()
        self.tick(104)
        cmd = b.load(self.bridge.vent_path)["commandId"]
        self.t["ventTravel"].update(commandId=cmd, state="running", busy=True, stage="observing_exit", reason="出口附近有人，继续隐藏")
        self.t["ventRole"].update(inVent=True, currentVentId=1)
        self.t["localPlayer"]["canMove"] = False
        self.tick(106)
        self.assertEqual(ctrl.phase, "escape")
        self.assertIn("隐藏", ctrl.result["reason"])
        self.t["ventTravel"].update(state="completed", busy=False, reachedTarget=True, safeExitConfirmed=True)
        self.t["ventRole"].update(inVent=False, currentVentId=-1)
        self.t["localPlayer"].update(canMove=True, position={"space": "unity_world", "x": 10, "y": 0})
        self.tick(108)
        self.assertIsNone(ctrl.vent_command)
        self.assertEqual(ctrl.phase, "cover")

    def test_failed_hidden_exit_does_not_issue_walking_or_kill(self):
        ctrl = self.prepare()
        self.tick(104)
        cmd = b.load(self.bridge.vent_path)["commandId"]
        before = b.load(self.bridge.command_path)
        self.t["ventTravel"].update(commandId=cmd, state="failed", busy=False, safeExitConfirmed=False, reason="出口有人，保持隐藏")
        self.t["ventRole"].update(inVent=True, currentVentId=1)
        self.t["localPlayer"]["canMove"] = False
        self.tick(110)
        self.assertEqual(ctrl.phase, "escape")
        self.assertEqual(b.load(self.bridge.command_path), before)
        self.assertIsNone(ctrl.vent_command)

    def test_failed_kill_never_starts_pipe_escape(self):
        self.prepare()
        ctrl = self.pilot.impostor
        ctrl.phase, ctrl.body, ctrl.kill_command = "cover", None, "kill-denied"
        ctrl.submitted = 103
        self.t["impostorAction"] = {"commandId": "kill-denied", "state": "failed"}
        self.tick(104)
        self.assertFalse(self.bridge.vent_path.exists())


if __name__ == "__main__": unittest.main()
