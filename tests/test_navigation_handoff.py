"""Replay navigation -> real task telemetry -> skill command -> next route."""

import importlib
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1]
package = types.ModuleType("handoff_fixture")
package.__path__ = [str(PLUGIN)]
sys.modules[package.__name__] = package
b = importlib.import_module("handoff_fixture.bridge")


class NavigationHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.bridge = b.Bridge(Path(temp.name))
        self.bridge.root.mkdir(parents=True)
        b.atomic(
            self.bridge.root / "data/skeld-route-observations.json",
            {
                "anchors": [
                    {"anchorId": "shared-console", "kind": "task", "position": {"x": 1, "y": 0}},
                    {"anchorId": "next-room", "kind": "task", "position": {"x": 9, "y": 0}},
                ]
            },
        )
        self.telemetry = {
            "sessionId": "12345678-1234-1234-1234-123456789012",
            "game": {"phase": "TASKS", "mapId": "TheSkeld", "matchId": 1},
            "localPlayer": {"canMove": True, "position": {"space": "unity_world", "x": 0, "y": 0}},
            "ui": {
                "minigameOpen": False,
                "ownTasks": [
                    {
                        "taskId": 3,
                        "taskType": "FixWiring",
                        "step": 0,
                        "maxStep": 3,
                        "locations": [{"x": 1, "y": 0}],
                        "complete": False,
                    },
                    {
                        "taskId": 4,
                        "taskType": "UploadData",
                        "step": 0,
                        "maxStep": 2,
                        "locations": [{"x": 1, "y": 0}],
                        "complete": False,
                    },
                ],
            },
        }
        self.monitor = b.Monitor(self.bridge, lambda **_: None)
        self.monitor.pilot.set_enabled(True)
        self.bridge.set_runtime("autoNavigation", True)
        self.monitor.solver.enabled = True
        self.monitor.solver.command = Mock(return_value=["python", "original-skill.py"])
        self.child = Mock()
        mouse = patch.object(b.StudyBot, "release_mouse")
        mouse.start()
        self.addCleanup(mouse.stop)
        self.child.poll.return_value = None
        popen = patch("handoff_fixture.studybot.subprocess.Popen", return_value=self.child)
        popen.start()
        self.addCleanup(popen.stop)
        self.addCleanup(self.monitor.close)

    def tick(self, now):
        self.telemetry["sentAtUtc"] = datetime.now(timezone.utc).isoformat()
        b.atomic(self.bridge.root / "latest.json", self.telemetry)
        with patch("time.monotonic", return_value=now):
            self.monitor.tick()

    def ack(self, command_id=None, state="completed", action=None):
        cmd = b.load(self.bridge.command_path)
        if command_id is not None:
            cmd = {"commandId": command_id, "action": action or "close_minigame"}
            b.atomic(self.bridge.command_path, cmd)
        b.atomic(
            self.bridge.nav_path,
            {"commandId": cmd["commandId"], "state": state, "completionMode": "interaction_opened", "error": None},
        )

    def open_task(self, task_id):
        self.telemetry["ui"].update(minigameOpen=True, openMinigameTaskId=task_id, openMinigameInstanceId=100 + task_id)
        self.telemetry["localPlayer"]["canMove"] = False

    def close_task(self):
        self.telemetry["ui"]["minigameOpen"] = False
        self.telemetry["localPlayer"]["canMove"] = True

    def test_partial_catalog_preserves_pending_route_and_failure_cooldown(self):
        self.tick(100)
        original = self.monitor.pilot.pending
        self.monitor.pilot.failed_stages.add((4, 0))
        self.telemetry["ui"]["ownTasks"] = self.telemetry["ui"]["ownTasks"][:1]
        self.tick(100.2)
        self.assertEqual(self.monitor.pilot.pending, original)
        self.assertIn((4, 0), self.monitor.pilot.failed_stages)

    def test_one_missing_receipt_cannot_abandon_an_acknowledged_running_route(self):
        self.tick(100)
        original = self.monitor.pilot.pending
        self.ack(state="running")
        receipt = b.load(self.bridge.nav_path)
        receipt["updatedAtUtc"] = datetime.now(timezone.utc).isoformat()
        b.atomic(self.bridge.nav_path, receipt)
        self.tick(101)
        load = b.load

        def intermittent(path):
            return {} if Path(path) == self.bridge.nav_path else load(path)

        with patch.object(b, "load", side_effect=intermittent):
            self.tick(106)
        self.assertEqual(self.monitor.pilot.pending, original)
        self.assertEqual(self.monitor.pilot.recovery_count, 0)
        self.assertEqual(self.bridge.read()["navigation"]["commandId"], original)

    def test_click_and_close_receipts_do_not_disable_navigation(self):
        self.tick(100)
        original = self.monitor.pilot.pending
        self.ack()
        self.open_task(3)
        self.tick(101)
        self.assertIsNone(self.monitor.pilot.pending)
        self.assertEqual(self.monitor.pilot.last_handoff["navigationCommandId"], original)
        self.ack("skill-click", action="click_unlock_number")
        self.tick(102)
        self.ack("skill-close")
        self.close_task()
        self.telemetry["ui"]["ownTasks"][0].update(step=1, locations=[{"x": 9, "y": 0}])
        self.tick(104)
        self.tick(108)
        command = b.load(self.bridge.command_path)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertEqual(command["action"], "navigate")
        # Another task sharing the console must not inherit the old cooldown.
        self.assertEqual(command["interactTaskId"], 4)
        self.assertNotEqual(command["commandId"], original)

    def test_delayed_observer_uses_exact_skill_ownership(self):
        self.tick(100)
        original = self.monitor.pilot.pending
        self.ack()
        self.open_task(3)
        # Simulate the navigation observer missing the window-open sample.
        b.atomic(self.bridge.root / "latest.json", self.telemetry)
        state = self.bridge.read()
        with patch("time.monotonic", return_value=101):
            self.monitor.solver.tick(state)
        self.ack("skill-close")
        self.close_task()
        self.child.poll.return_value = 0
        self.tick(104)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertEqual(self.monitor.pilot.last_handoff["navigationCommandId"], original)
        self.assertEqual(self.monitor.pilot.last_handoff["evidence"], "skill_owner")

    def test_closed_window_cancels_old_child_before_navigation_resumes(self):
        self.tick(100)
        self.ack()
        self.open_task(3)
        self.tick(101)
        self.ack("skill-close")
        self.close_task()
        self.tick(101.1)
        self.assertFalse(self.monitor.solver.status()["busy"])
        self.child.terminate.assert_called_once()
        self.assertEqual(b.load(self.bridge.command_path)["commandId"], "skill-close")
        self.assertTrue(self.monitor.pilot.enabled)

    def test_stage_progress_survives_missed_window_and_replaced_receipt(self):
        self.tick(100)
        self.telemetry["ui"]["ownTasks"][0]["step"] = 1
        self.ack("skill-close")
        self.tick(106)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertIsNone(self.monitor.pilot.pending)
        self.assertEqual(self.monitor.pilot.last_handoff["evidence"], "task_progress")

    def test_unknown_controller_keeps_ownership_while_autoplay_recovers(self):
        self.tick(100)
        self.ack("foreign-command", state="running", action="navigate")
        self.tick(106)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertIsNone(self.monitor.pilot.pending)
        self.assertEqual(self.monitor.pilot.recovery_count, 1)
        self.tick(110)
        self.assertEqual(b.load(self.bridge.command_path)["commandId"], "foreign-command")
        self.ack("foreign-command", state="completed", action="navigate")
        self.tick(114)
        self.assertEqual(b.load(self.bridge.command_path)["action"], "navigate")
        self.assertNotEqual(self.monitor.pilot.pending, "foreign-command")

    def test_meeting_vote_does_not_leave_stale_pending_route(self):
        self.tick(100)
        self.telemetry["game"]["phase"] = "MEETING"
        self.tick(101)
        self.assertIsNone(self.monitor.pilot.pending)
        self.telemetry["game"]["phase"] = "TASKS"
        self.ack("vote-receipt", state="paused", action="cast_vote")
        self.tick(106)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertEqual(b.load(self.bridge.command_path)["action"], "navigate")

    def test_same_console_next_stage_is_not_cooled_down(self):
        self.telemetry["ui"]["ownTasks"] = self.telemetry["ui"]["ownTasks"][:1]
        self.tick(100)
        self.ack()
        self.open_task(3)
        self.tick(101)
        self.ack("skill-close")
        self.close_task()
        self.telemetry["ui"]["ownTasks"][0]["step"] = 1
        self.tick(104)
        self.assertEqual(b.load(self.bridge.command_path)["action"], "navigate")
        self.assertEqual(self.monitor.pilot.target_task["step"], 1)

    def test_manual_restart_clears_old_pending_and_error(self):
        self.tick(100)
        self.ack("foreign-command", action="navigate")
        self.tick(106)
        self.assertTrue(self.monitor.pilot.enabled)
        self.monitor.pilot.set_enabled(False)
        self.monitor.pilot.set_enabled(True)
        self.assertIsNone(self.monitor.pilot.pending)
        self.assertEqual(self.monitor.pilot.last_error, "")

    def test_active_route_paused_by_game_is_not_stolen(self):
        self.ack("foreign-route", state="paused", action="navigate")
        self.tick(100)
        self.assertIsNone(self.monitor.pilot.pending)
        self.assertEqual(b.load(self.bridge.command_path)["commandId"], "foreign-route")

    def test_owned_disabled_receipt_does_not_leave_scheduler_stuck(self):
        self.tick(100)
        self.ack(state="navigation_disabled", action="navigate")
        self.tick(106)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertIsNone(self.monitor.pilot.pending)
        self.assertEqual(self.monitor.pilot.recovery_count, 1)

    def test_owned_task_route_has_bounded_recovery_deadline(self):
        self.tick(100)
        self.ack(state="running", action="navigate")
        self.tick(166)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertIsNone(self.monitor.pilot.pending)
        self.assertEqual(b.load(self.bridge.command_path)["action"], "stop")

    def test_closed_permission_is_not_reenabled_by_autoplay(self):
        self.bridge.set_runtime("autoNavigation", False)
        self.tick(100)
        self.tick(120)
        self.assertFalse(self.bridge.command_path.exists())
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])

    def test_five_consecutive_stages_keep_navigating_after_aux_commands(self):
        self.tick(100)
        for stage in range(5):
            now = 101 + stage * 10
            command = b.load(self.bridge.command_path)
            self.assertEqual(command["action"], "navigate")
            task_id = command["interactTaskId"]
            task = next(t for t in self.telemetry["ui"]["ownTasks"] if t["taskId"] == task_id)
            self.ack()
            self.open_task(task_id)
            self.tick(now)
            self.ack("click-" + str(stage), action="click_unlock_number")
            self.tick(now + 1)
            self.ack("close-" + str(stage))
            self.close_task()
            task["step"] += 1
            task["complete"] = task["step"] == task["maxStep"]
            task["locations"] = [{"x": 9 if task["step"] % 2 else 1, "y": 0}]
            self.tick(now + 3)
            if b.load(self.bridge.command_path).get("action") == "navigate":
                self.ack(state="running")
            self.tick(now + 8)
            self.assertTrue(self.monitor.pilot.enabled)
            self.assertEqual(self.monitor.pilot.last_error, "")
        self.assertEqual(self.monitor.pilot.handoffs, 5)
        self.assertTrue(all(t["complete"] for t in self.telemetry["ui"]["ownTasks"]))

    def test_missing_game_task_id_uses_only_accepted_bound_command(self):
        self.tick(100)
        self.ack()
        self.open_task(3)
        self.telemetry["ui"]["openMinigameTaskId"] = None
        self.telemetry["ui"]["ownTasks"][0]["taskType"] = "FuelEngines"
        self.tick(101)
        state = self.bridge.read()
        self.assertEqual(state["openMinigame"]["taskId"], 3)
        self.assertEqual(state["openMinigame"]["source"], "accepted_task_command")
        self.assertTrue(self.monitor.solver.status()["busy"])
        self.assertIsNone(self.monitor.pilot.pending)

    def test_missing_task_id_never_binds_a_foreign_command(self):
        self.tick(100)
        self.ack("other-controller", action="navigate")
        self.open_task(3)
        self.telemetry["ui"]["openMinigameTaskId"] = None
        self.tick(101)
        self.assertIsNone(self.bridge.read()["openMinigame"]["taskId"])
        self.assertFalse(self.monitor.solver.status()["busy"])

    def test_missing_task_id_does_not_bind_a_window_in_another_room(self):
        self.tick(100)
        self.ack()
        self.open_task(3)
        self.telemetry["ui"]["openMinigameTaskId"] = None
        self.telemetry["localPlayer"]["position"]["x"] = 20
        self.tick(101)
        self.assertIsNone(self.bridge.read()["openMinigame"]["taskId"])
        self.assertFalse(self.monitor.solver.status()["busy"])

    def test_missing_task_id_does_not_reuse_a_binding_across_matches(self):
        self.tick(100)
        self.ack()
        self.open_task(3)
        self.telemetry["ui"]["openMinigameTaskId"] = None
        self.telemetry["game"]["matchId"] = 2
        self.tick(101)
        self.assertIsNone(self.bridge.read()["openMinigame"]["taskId"])
        self.assertFalse(self.monitor.solver.status()["busy"])

    def test_released_route_stale_pause_does_not_block_next_target(self):
        self.tick(100)
        original = self.monitor.pilot.pending
        self.ack(state="paused")
        self.open_task(3)
        self.tick(101)
        self.close_task()
        self.telemetry["ui"]["ownTasks"][0]["step"] = 1
        self.tick(104)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertNotEqual(self.monitor.pilot.pending, original)
        self.assertEqual(b.load(self.bridge.command_path)["action"], "navigate")
        self.assertEqual(self.monitor.pilot.target_task["step"], 1)

    def test_navigation_waits_until_game_applies_enable_revision(self):
        b.atomic(self.bridge.runtime_status_path, {"autoNavigation": False, "revision": 0})

        def apply_runtime(_):
            self.assertFalse(self.bridge.command_path.exists())
            requested = b.load(self.bridge.control_path)
            b.atomic(self.bridge.runtime_status_path, {"autoNavigation": True, "revision": requested["revision"]})

        with patch("handoff_fixture.bridge.time.sleep", side_effect=apply_runtime) as wait:
            self.tick(100)
        self.assertTrue(wait.called)
        self.assertEqual(b.load(self.bridge.command_path)["action"], "navigate")

    def test_no_solver_navigates_without_opening_minigames_or_claiming_progress(self):
        self.monitor.solver.enabled = False
        self.tick(100)
        command = b.load(self.bridge.command_path)
        self.assertEqual(command["action"], "navigate")
        self.assertFalse(command["interactAfterArrival"])
        self.assertNotIn("targetTaskId", command)
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertIn("仅导航", self.monitor.pilot.reason)
        self.assertFalse(any(t.get("complete") for t in self.bridge.read()["tasks"]))

    def test_no_move_is_sent_when_game_never_confirms_enable(self):
        b.atomic(self.bridge.runtime_status_path, {"autoNavigation": False, "revision": 0})
        clock = [100.0]
        self.telemetry["sentAtUtc"] = datetime.now(timezone.utc).isoformat()
        b.atomic(self.bridge.root / "latest.json", self.telemetry)

        def advance(_):
            clock[0] += 1

        with patch("time.monotonic", side_effect=lambda: clock[0]), patch("time.sleep", side_effect=advance):
            self.monitor.tick()
        self.assertFalse(self.bridge.command_path.exists())
        self.assertTrue(self.monitor.pilot.enabled)
        self.assertEqual(self.monitor.pilot.state, "error")

    def test_graph_start_failure_releases_pending_navigation(self):
        self.tick(100)
        command_id = self.monitor.pilot.pending
        b.atomic(
            self.bridge.nav_path,
            {"commandId": command_id, "state": "navigation_start_failed", "error": "A* could not find a route."},
        )
        self.tick(101)
        self.assertIsNone(self.monitor.pilot.pending)
        self.assertEqual(self.bridge.read()["navigation"]["failureCode"], "navigation_start_failed")
        self.assertEqual(self.monitor.pilot.last_error, "A* could not find a route.")
