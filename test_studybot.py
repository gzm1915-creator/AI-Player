import copy
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_bridge as fixture
module = importlib.import_module(fixture.b.__package__ + ".studybot")


class StudyBotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        bridge = SimpleNamespace(root=root, command_path=root / "commands.json", nav_path=root / "status.json",
                                 journal=SimpleNamespace(root=root, record_action=Mock()), set_runtime=Mock())
        self.runner = module.StudyBot(bridge)
        self.runner.enabled = True
        self.runner.command = Mock(return_value=["python", "original-adapter.py"])
        self.runner.environment = Mock(return_value={"AU_PLUGIN_ROOT": str(root)})
        self.runner.close_window = Mock()
        mouse = patch.object(module.StudyBot, "release_mouse", create=True)
        mouse.start()
        self.addCleanup(mouse.stop)
        self.child = Mock()
        self.child.poll.return_value = None
        self.popen = patch.object(module.subprocess, "Popen", return_value=self.child)
        self.spawn = self.popen.start()
        self.addCleanup(self.popen.stop)
        self.addCleanup(self.runner.cancel)
        self.state = {"connected": True, "phase": "TASKS", "sessionId": "session", "matchId": 1,
                      "minigameOpen": True, "openMinigame": {"taskId": 7, "instanceId": 99},
                      "tasks": [{"taskId": 6, "taskType": "SwipeCard", "step": 0},
                                {"taskId": 7, "taskType": "FixWiring", "step": 1}]}

    def test_only_open_task_selected_not_nearest_or_first(self):
        self.runner.tick(self.state)
        cmd = self.spawn.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--task-type") + 1], "FixWiring")
        self.assertEqual(cmd[cmd.index("--task-id") + 1], "7")

    def test_missing_open_task_identity_refuses_guess(self):
        self.state["openMinigame"] = {}
        self.runner.tick(self.state)
        self.spawn.assert_not_called()
        self.assertEqual(self.runner.status()["state"], "blocked")

    def test_practice_task_catalog_refresh_clears_previous_attempts(self):
        self.state["minigameOpen"] = False
        self.runner.tick(self.state)
        self.runner.attempts[(7, 1)] = 3
        self.state["tasks"][1].update(taskType="SubmitScan", step=1)
        self.runner.tick(self.state)
        self.assertEqual(self.runner.attempts, {})
        self.assertEqual(self.runner.status()["attempts"], 0)
        self.state["minigameOpen"] = True
        self.runner.tick(self.state)
        self.assertEqual(self.runner.attempts[(7, 1)], 1)

    def test_partial_catalog_does_not_restart_running_skill_or_reset_retries(self):
        self.runner.tick(self.state)
        self.state["tasks"] = self.state["tasks"][1:]
        self.runner.tick(self.state)
        self.child.terminate.assert_not_called()
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.runner.attempts[(7, 1)], 1)

    def test_new_extra_task_does_not_interrupt_existing_skill(self):
        self.runner.tick(self.state)
        self.state["tasks"].append({"taskId": 99, "taskType": "UploadData", "step": 0})
        self.runner.tick(self.state)
        self.child.terminate.assert_not_called()
        self.assertEqual(self.spawn.call_count, 1)

    def test_same_ids_new_task_round_resets_exhausted_attempts(self):
        self.state["minigameOpen"] = False
        for task in self.state["tasks"]:
            task.update(complete=True, step=3)
        self.runner.tick(self.state)
        self.runner.attempts[(7, 0)] = 3
        for task in self.state["tasks"]:
            task.update(complete=False, step=0)
        self.runner.tick(self.state)
        self.assertEqual(self.runner.attempts, {})

    def test_user_switches_window_old_child_stops_without_closing_new_panel(self):
        self.runner.tick(self.state)
        self.state["openMinigame"] = {"taskId": 6, "instanceId": 100}
        self.runner.tick(self.state)
        self.child.terminate.assert_called_once()
        self.runner.close_window.assert_not_called()
        self.assertIsNone(self.runner.active)

    def test_user_closes_window_child_stops_immediately_during_startup(self):
        self.runner.tick(self.state)
        self.state["minigameOpen"] = False
        self.runner.tick(self.state)
        self.child.terminate.assert_called_once()
        self.runner.close_window.assert_not_called()

    def test_game_completion_and_auto_close_still_confirms_success(self):
        self.runner.tick(self.state)
        self.state["tasks"][1].update(complete=True, step=3)
        self.state["minigameOpen"] = False
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "completed")

    def test_cancel_escalates_slow_child_and_preserves_handle_if_unstoppable(self):
        self.runner.tick(self.state)
        self.child.wait.side_effect = [module.subprocess.TimeoutExpired("child", 1), None]
        self.assertTrue(self.runner.cancel())
        self.child.kill.assert_called_once()
        self.assertIsNone(self.runner.proc)
        self.runner.tick({**self.state, "minigameOpen": False})
        self.state["openMinigame"]["instanceId"] = 101
        self.runner.tick(self.state)
        self.child.wait.side_effect = module.subprocess.TimeoutExpired("child", 1)
        self.runner.set_enabled(False)
        self.assertIs(self.runner.proc, self.child)
        self.assertEqual(self.runner.status()["state"], "blocked")
        self.runner.tick(self.state)
        self.assertEqual(self.spawn.call_count, 2)
        self.child.wait.side_effect = None
        self.child.poll.return_value = 0
        self.runner.cancel()

    def test_empty_or_invalid_preflight_output_is_an_actionable_error(self):
        for stdout in ("", "[]", "{}", '{"engine":"StudyBot","skills":[]}'):
            with self.subTest(stdout=stdout), patch.object(module.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr="")):
                with self.assertRaises(ValueError):
                    self.runner.preflight()
                self.assertFalse(self.runner.ready)

    def test_exit_zero_is_failure_without_progress_and_closes(self):
        self.runner.tick(self.state)
        self.runner.active["started"] -= 3
        self.child.poll.return_value = 0
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "failed")
        self.runner.close_window.assert_called_once()
        self.runner.tick(self.state)
        self.assertEqual(self.spawn.call_count, 1)

    def test_step_progress_does_not_interrupt_running_skill(self):
        self.runner.tick(self.state)
        self.state["tasks"][1]["step"] = 2
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "running")
        self.child.terminate.assert_not_called()
        self.runner.close_window.assert_not_called()

    def test_completed_task_stops_child(self):
        self.runner.tick(self.state)
        self.state["tasks"][1].update(step=3, maxStep=3, complete=True)
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "completed")
        self.child.terminate.assert_called_once()

    def test_asteroids_continues_from_first_hit_until_twenty(self):
        task = self.state["tasks"][1]
        task.update(taskType="ClearAsteroids", step=0, maxStep=20)
        self.runner.tick(self.state)
        for step in range(1, 20):
            task["step"] = step
            self.runner.tick(self.state)
            self.assertEqual(self.runner.status()["state"], "running")
        self.child.terminate.assert_not_called()
        self.runner.close_window.assert_not_called()
        task["step"] = 20
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "completed")

    def test_asteroids_partial_progress_is_not_success_on_exit(self):
        task = self.state["tasks"][1]
        task.update(taskType="ClearAsteroids", step=0, maxStep=20)
        self.runner.tick(self.state)
        self.runner.active["started"] -= 3
        task["step"] = 2
        self.child.poll.return_value = 0
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "failed")

    def test_wiring_stage_progress_is_verified_after_child_exits(self):
        self.runner.tick(self.state)
        self.runner.active["started"] -= 3
        self.state["tasks"][1]["step"] = 2
        self.child.poll.return_value = 0
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "completed")

    def test_meeting_cancels_owned_worker(self):
        self.runner.tick(self.state)
        self.state["meetingOpen"] = True
        self.runner.tick(self.state)
        self.child.terminate.assert_called_once()
        self.assertIsNone(self.runner.proc)

    def test_native_controller_is_disabled_before_studybot_launch(self):
        self.state["runtimeControl"] = {"minigameControl": True}
        self.runner.tick(self.state)
        self.runner.bridge.set_runtime.assert_called_once_with("minigameControl", False)
        self.spawn.assert_not_called()

    def test_timeout_terminates_and_closes(self):
        self.runner.tick(self.state)
        self.runner.active["started"] -= 100
        self.runner.tick(self.state)
        self.child.terminate.assert_called_once()
        self.runner.close_window.assert_called_once()
        self.assertEqual(self.runner.status()["state"], "failed")

    def test_three_attempt_limit(self):
        self.runner.tick({**self.state, "minigameOpen": False})
        self.runner.attempts[(7, 1)] = 3
        self.runner.tick(self.state)
        self.spawn.assert_not_called()
        self.runner.close_window.assert_called_once()

    def test_session_change_cancels_old_worker(self):
        self.runner.tick(self.state)
        changed = copy.deepcopy(self.state)
        changed.update(sessionId="new-session", minigameOpen=False)
        self.runner.tick(changed)
        self.child.terminate.assert_called_once()
        self.assertEqual(self.runner.attempts, {})

    def repair(self, name="FixLights"):
        self.state.update(activeEmergencies=[name], openMinigame={"taskId": None, "taskType": name, "instanceId": 100})

    def test_four_repairs_use_exact_window_type_without_normal_task_id(self):
        for name in module.REPAIR_TASKS:
            with self.subTest(name=name):
                self.runner.cancel()
                self.runner.seen = None
                self.repair(name)
                self.runner.tick(self.state)
                cmd = self.spawn.call_args.args[0]
                self.assertEqual(cmd[cmd.index("--task-type") + 1], name)
                self.assertNotIn("--task-id", cmd)

    def test_missing_emergency_state_refuses_start(self):
        self.repair()
        self.state.pop("activeEmergencies")
        self.runner.tick(self.state)
        self.spawn.assert_not_called()

    def test_repair_exit_or_task_disappearance_does_not_prove_repaired(self):
        self.repair()
        self.runner.tick(self.state)
        self.runner.active["started"] -= 3
        self.state["tasks"] = []
        self.child.poll.return_value = 0
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "failed")
        self.runner.bridge.journal.record_action.assert_not_called()

    def test_unavailable_system_does_not_prove_repaired(self):
        self.repair()
        self.runner.tick(self.state)
        self.state["activeEmergencies"] = None
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "running")

    def test_native_system_clear_confirms_repair_and_new_round_resets_attempts(self):
        self.repair()
        self.runner.tick(self.state)
        self.runner.attempts[("repair:FixLights", 0)] = 3
        self.state["activeEmergencies"] = []
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "completed")
        self.runner.bridge.journal.record_action.assert_called_once()
        self.state["openMinigame"]["instanceId"] = 101
        self.state["activeEmergencies"] = ["FixLights"]
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["attempts"], 1)

    def test_repair_appearing_does_not_cancel_ordinary_skill(self):
        self.runner.tick(self.state)
        self.state["tasks"].append({"taskId": 99, "taskType": "ResetReactor", "step": 0})
        self.state["activeEmergencies"] = ["ResetReactor"]
        self.runner.tick(self.state)
        self.child.terminate.assert_not_called()
        self.assertEqual(self.runner.active["taskId"], 7)

    def test_impostor_never_executes_real_task_or_repair(self):
        self.state["ownRole"] = {"isImpostor": True}
        self.runner.tick(self.state)
        self.repair()
        self.runner.tick(self.state)
        self.spawn.assert_not_called()

    def test_repair_retry_limit_applies_even_with_different_task_id(self):
        self.repair()
        self.runner.tick({**self.state, "minigameOpen": False})
        self.runner.attempts[("repair:FixLights", 0)] = 3
        self.state["openMinigame"]["taskId"] = 22
        self.runner.tick(self.state)
        self.spawn.assert_not_called()
        self.runner.close_window.assert_called_once()
