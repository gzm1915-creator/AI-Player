"""Replay room patrol, command ownership, recovery tickets and kill explanations."""
import importlib
import unittest
from unittest.mock import patch
import test_impostor as fixture

b = fixture.b
decisions = importlib.import_module("impostor_fixture.decisions")


class PatrolRecoveryTests(unittest.TestCase):
    setUp = fixture.ImpostorTests.setUp
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack

    @staticmethod
    def anchor(aid="room.a", room="Reactor", x=6, kind="task"):
        return {"anchorId": aid, "roomId": room, "kind": kind, "position": {"x": x, "y": 0}}

    def ready(self):
        self.pilot.role_identity = (self.t["sessionId"], 1, "Impostor")
        self.pilot.impostor.identity = (self.t["sessionId"], 1, "TheSkeld")
        self.t["nearbyPlayers"] = []
        self.t["ui"]["ownTasks"] = []
        self.t["ownRole"].update(canKill=False, killCooldown=20)

    def route(self):
        self.ready()
        self.anchors.append(self.anchor())
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        return self.tick(100)

    def emergency_ticket(self):
        cmd = self.route()
        with patch.object(self.bridge, "stop_navigation", side_effect=ValueError("sample lost")):
            self.pilot.impostor.stop_owned()
        self.assertIsNotNone(self.pilot.impostor.resume_control)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])
        return cmd

    def test_only_labeled_interiors_are_patrol_destinations(self):
        impostor = self.pilot.impostor
        valid = self.anchor()
        self.assertTrue(impostor.inside_room(valid))
        for aid, room, kind in [("room.entry", "Reactor", "waypoint"), ("room.door", "Reactor", "waypoint"),
                                ("room.a", "Hallway_Cross", "waypoint"), ("room.a", None, "waypoint"),
                                ("room.vent", "Reactor", "vent"), ("skeld.weapons.asteroid", "Weapons", "task")]:
            anchor = self.anchor(aid, room, kind=kind)
            self.assertFalse(impostor.inside_room(anchor), anchor)
        choices = [self.anchor("room.entry"), self.anchor("room.hall", "Hallway_Cross"), valid]
        self.assertEqual(impostor.patrol_target(self.bridge.read(), choices, 100), valid)

    def test_cooldowns_survive_route_start_and_arrival(self):
        command = self.route()
        imp = self.pilot.impostor
        self.assertEqual(command["targetAnchorId"], "room.a")
        self.assertIsNone(imp.patrol_target(self.bridge.read(), [self.anchor("room.b")], 150))
        self.assertIsNotNone(imp.patrol_target(self.bridge.read(), [self.anchor("room.b")], 191))
        self.assertIsNone(imp.patrol_target(self.bridge.read(), [self.anchor()], 250))
        self.t["localPlayer"]["position"]["x"] = 6
        self.ack()
        self.tick(102)
        self.assertEqual(imp.patrol_rooms_until["Reactor"], 192)
        self.assertEqual(imp.patrol_anchors_until["room.a"], 282)
        self.assertGreaterEqual(imp.patrol_stop_seconds, .6)
        self.assertLessEqual(imp.patrol_stop_seconds, 2)

    def test_return_to_same_room_uses_another_available_interior(self):
        self.ready()
        imp = self.pilot.impostor
        imp.patrol_room_last_anchor["Reactor"] = "room.a"
        for _ in range(5):
            chosen = imp.patrol_target(self.bridge.read(), [self.anchor(), self.anchor("room.b", x=7)], 1000)
            self.assertEqual(chosen["anchorId"], "room.b")

    def test_receipt_without_interior_pose_does_not_start_stop(self):
        self.route()
        self.ack()
        self.tick(102)
        self.assertEqual(self.pilot.impostor.patrol_stop_seconds, 0)

    def test_safe_opportunity_interrupts_room_observation(self):
        self.route()
        self.t["localPlayer"]["position"]["x"] = 6
        self.ack()
        self.tick(102)
        self.t["nearbyPlayers"] = [{"playerId": 1, "x": 7, "y": 0}]
        self.t["ownRole"].update(canKill=True, killCooldown=0, killTargetId=1,
            killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "ready": True, "requiredSeconds": 4}])
        self.assertEqual(self.tick(103.1)["action"], "impostor_kill")

    def test_stop_is_not_throttled_by_just_submitted_route(self):
        cmd = self.route()
        with patch("time.monotonic", return_value=100.01):
            self.assertEqual(self.bridge.stop_navigation(cmd["commandId"])["status"], "submitted")
        self.assertEqual(b.load(self.bridge.command_path)["action"], "stop")

    def test_stop_never_replaces_newer_foreign_command(self):
        cmd = self.route()
        b.atomic(self.bridge.command_path, {"commandId": "foreign", "action": "navigate"})
        self.assertEqual(self.bridge.stop_navigation(cmd["commandId"])["status"], "superseded")
        self.assertEqual(b.load(self.bridge.command_path)["commandId"], "foreign")

    def test_emergency_gate_restores_and_replans_without_manual_restart(self):
        self.emergency_ticket()
        self.tick(102)
        self.assertTrue(b.load(self.bridge.control_path)["autoNavigation"])
        self.assertIsNone(self.pilot.impostor.resume_control)
        self.assertTrue(self.pilot.enabled)
        self.assertEqual(self.pilot.impostor.recovery_count, 1)

    def test_manual_stop_discards_gate_ticket(self):
        self.emergency_ticket()
        self.pilot.set_enabled(False, source="panel.stop")
        self.tick(103)
        self.assertIsNone(self.pilot.impostor.resume_control)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])
        self.assertEqual(self.pilot.last_control["source"], "panel.stop")

    def test_later_user_setting_is_not_overridden_even_with_same_clock(self):
        with patch("time.time_ns", return_value=1000000000):
            self.emergency_ticket()
            ticket = self.pilot.impostor.resume_control
            self.bridge.set_runtime("autoNavigation", False)
        self.assertNotEqual(ticket, b.load(self.bridge.control_path))
        self.tick(103)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])
        self.assertIsNone(self.pilot.impostor.resume_control)
        self.tick(110)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])

    def test_restore_waits_for_movable_fresh_task_phase(self):
        self.emergency_ticket()
        self.t["localPlayer"]["canMove"] = False
        self.tick(103)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])
        self.t["localPlayer"]["canMove"] = True
        self.tick(105)
        self.assertTrue(b.load(self.bridge.control_path)["autoNavigation"])

    def test_death_discards_gate_ticket(self):
        self.emergency_ticket()
        self.t["ownRole"]["isDead"] = True
        self.tick(103)
        self.assertIsNone(self.pilot.impostor.resume_control)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])
        self.tick(110)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])

    def test_new_match_discards_gate_ticket(self):
        self.emergency_ticket()
        self.t["game"]["matchId"] = 2
        self.tick(103)
        self.assertIsNone(self.pilot.impostor.resume_control)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])

    def test_missing_patrol_receipt_recovers_without_turning_off_pilot(self):
        self.route()
        self.tick(106)
        self.assertIsNone(self.pilot.impostor.pending)
        self.assertEqual(self.pilot.impostor.recovery_count, 1)
        self.assertTrue(self.pilot.enabled)

    def test_patrol_timeout_is_released_and_cools_failed_destination(self):
        self.route()
        self.ack("running")
        self.tick(146)
        self.assertIsNone(self.pilot.impostor.pending)
        self.assertGreater(self.pilot.impostor.visited["room.a"], 146)
        self.assertTrue(self.pilot.enabled)

    def test_role_change_keeps_solver_and_navigation_permissions(self):
        self.route()
        self.bridge.set_runtime("minigameControl", True)
        self.t["ownRole"].update(roleType="Crewmate", isImpostor=False)
        self.tick(103)
        control = b.load(self.bridge.control_path)
        self.assertTrue(control["minigameControl"])
        self.assertTrue(control["autoNavigation"])

    def test_history_failure_cannot_prevent_manual_stop(self):
        self.tick(100)
        with patch.object(self.bridge.journal, "record_action", side_effect=OSError("disk full")):
            self.pilot.set_enabled(False, "panel.stop")
        self.assertFalse(self.pilot.enabled)

    def test_failure_receipt_records_actual_pursuit_problem(self):
        self.ready()
        imp = self.pilot.impostor
        imp.opening_done = True
        self.t["nearbyPlayers"] = [{"playerId": 1, "x": 3, "y": 0}]
        self.t["ownRole"].update(killCooldown=0)
        self.tick(100)
        command = b.load(self.bridge.command_path)
        b.atomic(self.bridge.nav_path, {"commandId": command["commandId"], "state": "pursuit_start_failed", "error": "Target is 1.845 from route graph."})
        self.tick(102)
        self.assertIn("1.845", imp.status()["lastPursuitFailure"]["error"])
        self.assertTrue(self.pilot.enabled)

    def test_kill_diagnostics_distinguish_witnesses_safety_and_native_range(self):
        self.save()
        state = self.bridge.read()
        state["ownRole"].update(canKill=False, killTargetId=None,
            killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "ready": False, "requiredSeconds": 3.5}])
        state["recentPlayers"] = [{"playerId": 3, "x": 2, "y": 0, "ageSeconds": 2, "uncertaintyRadius": 1}]
        check = decisions.kill_decisions(state)[0]
        self.assertEqual(check["witnessIds"], [3])
        self.assertIn("原生按钮", check["reason"])
        self.assertIn("安全观察", check["reason"])
        state["nearbyPlayers"] = []
        state["motionEstimates"] = [{"playerId": 1, "x": 1, "y": 0}]
        self.assertEqual(decisions.kill_decisions(state), [])


if __name__ == "__main__":
    unittest.main()
