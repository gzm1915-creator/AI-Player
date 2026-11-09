"""Culture runs away from the UI; only the native result proves completion."""
import copy
import importlib.util
import os
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import test_studybot as skills
import test_navigation_handoff as routes


class SampleSkillTests(unittest.TestCase):
    setUp = skills.StudyBotTests.setUp
    # Reuse the real worker fixture, without duplicating its unrelated test cases.
    def sample(self, phase="not_started", seconds=None):
        task = self.state["tasks"][1]
        task.update(taskType="InspectSample", step=0, maxStep=1, complete=False,
                    sample={"supported": True, "phase": phase, "remainingSeconds": seconds})
        return task

    def test_culture_confirmation_closes_immediately_without_failure_or_completion(self):
        task = self.sample()
        self.runner.tick(self.state)
        task["sample"].update(phase="processing", remainingSeconds=59)
        self.runner.tick(self.state)
        self.child.terminate.assert_called_once()
        self.runner.close_window.assert_called_once()
        self.assertEqual(self.runner.status()["state"], "deferred")
        self.assertFalse(self.runner.status()["busy"])
        self.assertFalse(task["complete"])
        self.runner.tick(self.state)
        self.assertEqual(self.spawn.call_count, 1)

    def test_reopening_a_processing_sample_never_launches_long_skill(self):
        self.sample("processing", 40)
        self.runner.tick(self.state)
        self.spawn.assert_not_called()
        self.runner.close_window.assert_called_once()
        self.assertEqual(self.runner.status()["deferredTasks"][0]["remainingSeconds"], 40)

    def test_waiting_does_not_consume_selection_retries(self):
        task = self.sample()
        self.runner.tick(self.state)
        self.runner.attempts[(7, 0, "not_started")] = 3
        task["sample"].update(phase="processing", remainingSeconds=59)
        self.runner.tick(self.state)
        self.state["minigameOpen"] = False
        self.runner.tick(self.state)
        self.state["minigameOpen"] = True
        self.state["openMinigame"]["instanceId"] = 100
        task["sample"].update(phase="ready", remainingSeconds=0)
        self.runner.tick(self.state)
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.runner.status()["attempts"], 1)
        task["complete"] = True
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "completed")

    def test_sample_timer_survives_meeting_without_worker_or_guessed_completion(self):
        task = self.sample()
        self.runner.tick(self.state)
        task["sample"].update(phase="processing", remainingSeconds=55)
        self.state["meetingOpen"] = True
        self.runner.tick(self.state)
        self.state.update(meetingOpen=False, minigameOpen=False)
        self.runner.tick(self.state)
        self.assertFalse(self.runner.status()["busy"])
        self.assertEqual(self.runner.status()["deferredTasks"][0]["remainingSeconds"], 55)
        self.assertFalse(task["complete"])

    def test_sample_without_native_timer_never_falls_back_to_sixty_second_wait(self):
        task = self.sample()
        task.pop("sample")
        self.runner.tick(self.state)
        self.spawn.assert_not_called()
        self.runner.close_window.assert_called_once()
        self.assertEqual(self.runner.status()["state"], "unsupported")

    def test_culture_confirmation_does_not_close_user_new_window(self):
        task = self.sample()
        self.runner.tick(self.state)
        task["sample"].update(phase="processing", remainingSeconds=59)
        self.state["openMinigame"] = {"taskId":6,"instanceId":100}
        self.runner.tick(self.state)
        self.child.terminate.assert_called_once()
        self.runner.close_window.assert_not_called()


class SampleNavigationTests(unittest.TestCase):
    setUp = routes.NavigationHandoffTests.setUp
    tick = routes.NavigationHandoffTests.tick
    ack = routes.NavigationHandoffTests.ack
    def tasks(self, other=True):
        sample = {"taskId":3,"taskType":"InspectSample","step":0,"maxStep":1,"complete":False,
                  "locations":[{"x":1,"y":0}],"sample":{"supported":True,"phase":"processing","remainingSeconds":40}}
        others = [{"taskId":4,"taskType":"UploadData","step":0,"maxStep":2,"locations":[{"x":9,"y":0}]}] if other else []
        self.telemetry["ui"]["ownTasks"] = [sample, *others]
        return sample

    def test_processing_sample_is_skipped_for_other_task_and_ready_can_return(self):
        task = self.tasks()
        self.tick(100)
        self.assertEqual(self.monitor.pilot.target_task["taskId"], 4)
        self.telemetry["ui"]["ownTasks"][1].update(complete=True,step=2)
        self.ack()
        self.tick(101)
        task["sample"].update(phase="ready",remainingSeconds=0)
        self.tick(105)
        self.assertEqual(self.monitor.pilot.target_task["taskId"], 3)

    def test_only_processing_sample_leaves_room_without_opening_other_task(self):
        self.tasks(other=False)
        routes.b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors":[
            {"anchorId":"shared-console","kind":"task","roomId":"MedBay","position":{"x":1,"y":0}},
            {"anchorId":"cafeteria-hall","kind":"waypoint","roomId":"Cafeteria","position":{"x":8,"y":0}}]})
        self.tick(100)
        command = routes.b.load(self.bridge.command_path)
        self.assertEqual(command["targetAnchorId"], "cafeteria-hall")
        self.assertFalse(command["interactAfterArrival"])
        self.assertIsNone(self.monitor.pilot.target_task)

    def test_ready_stage_has_no_cooldown_inherited_from_start(self):
        task = self.tasks(other=False)
        pilot = self.monitor.pilot
        key = pilot.cooldown_key("shared-console", {**task,"sample":{"supported":True,"phase":"not_started"}})
        self.tick(100)
        pilot.visited[key] = time.monotonic() + 999
        task["sample"].update(phase="ready",remainingSeconds=0)
        self.tick(105)
        self.assertEqual(self.monitor.pilot.target_task["taskId"], 3)


class SampleRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "plugin/among_us_neko/studybot_runner"
        env = {key:str(root) for key in ("AU_PLUGIN_ROOT","AU_STUDYBOT_STATE_DIR","AU_NAV_COMMAND","AU_NAV_STATUS")}
        with patch.dict(os.environ,env), patch.object(sys,"path",[str(root),str(root/"studybot-adapter-deps"),*sys.path]):
            spec = importlib.util.spec_from_file_location("sample_runner_fixture",root/"studybot_minigame_adapter.py")
            cls.runner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.runner)

    def setUp(self):
        self.t = {"sessionId":"12345678-1234-1234-1234-123456789012","game":{"matchId":1},
                  "ui":{"minigameOpen":True,"openMinigameInstanceId":99,"openMinigameTaskId":7,
                        "ownTasks":[{"taskId":7,"taskType":"InspectSample","complete":False,
                                     "sample":{"supported":True,"phase":"not_started"}}]}}
        self.pointer = Mock()

    def test_start_uses_control_and_returns_on_confirmation_without_sleeping_for_culture(self):
        sleeps=[]
        def start(action,**fields):
            self.assertEqual(action,"start_inspect_sample")
            self.assertEqual(fields["expectedMinigameInstanceId"],99)
            self.t["ui"]["ownTasks"][0]["sample"].update(phase="processing",remainingSeconds=59)
            return True
        with patch.object(self.runner,"read_latest_telemetry",side_effect=lambda:copy.deepcopy(self.t)), \
             patch.object(self.runner,"send_plugin_action",side_effect=start), patch.object(self.runner.time,"sleep",side_effect=sleeps.append):
            self.runner.solve_inspect_sample(self.pointer,7,0)
        self.assertLessEqual(sum(sleeps),.5)
        self.pointer.click.assert_not_called()

    def test_processing_never_clicks_or_waits(self):
        self.t["ui"]["ownTasks"][0]["sample"]["phase"]="processing"
        with patch.object(self.runner,"read_latest_telemetry",return_value=self.t), \
             patch.object(self.runner,"send_plugin_action") as send,patch.object(self.runner.time,"sleep") as sleep:
            self.runner.solve_inspect_sample(self.pointer,7,0)
        send.assert_not_called();sleep.assert_not_called()

    def test_sample_command_binds_current_match_and_exact_window_using_atomic_file_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            command = Path(folder)/"command.json"
            def accepted(command_id, timeout):
                value=json.loads(command.read_text(encoding="utf-8"))
                self.assertEqual(value["commandId"],command_id)
                self.assertTrue(command_id.startswith("neko-"))
                self.assertEqual(value["expectedSessionId"],self.t["sessionId"])
                self.assertEqual(value["expectedMatchId"],1)
                self.assertEqual(value["expectedMinigameInstanceId"],99)
                self.assertIn("issuedAtUtc",value)
                return True
            with patch.object(self.runner,"read_latest_telemetry",return_value=self.t), \
                 patch.object(self.runner,"NAVIGATION_COMMAND_PATH",command), \
                 patch.object(self.runner,"wait_plugin_action",side_effect=accepted):
                self.assertTrue(self.runner.send_plugin_action("start_inspect_sample",interactTaskId=7,expectedMinigameInstanceId=99))

    def test_ready_uses_fresh_visual_outlier_and_native_selection_without_restart(self):
        from PIL import Image,ImageDraw
        self.t["ui"]["ownTasks"][0]["sample"]["phase"]="ready"
        img=Image.new("RGB",(1920,1080),(80,120,245));draw=ImageDraw.Draw(img)
        draw.rectangle((952,550,974,630),fill=(246,134,134))
        actions=[]
        def select(action,**fields):
            actions.append((action,fields["targetNumber"]))
            self.t["ui"]["ownTasks"][0]["complete"]=True
            return True
        with patch.object(self.runner,"read_latest_telemetry",side_effect=lambda:copy.deepcopy(self.t)), \
             patch.object(self.runner,"telemetry_indexed_points",return_value=None), \
             patch("PIL.ImageGrab.grab",return_value=img),patch.object(self.runner.time,"sleep"), \
             patch.object(self.runner,"send_plugin_action",side_effect=select):
            self.runner.solve_inspect_sample(self.pointer,7,0)
        self.assertEqual(actions,[("select_inspect_sample",2)])

    def test_entry_dispatches_split_sample_handler_instead_of_original_blocking_method(self):
        from types import SimpleNamespace
        args=SimpleNamespace(root=str(Path(__file__).resolve().parents[1]/"plugin/among_us_neko"),
                             preflight=False,task_type="InspectSample",step=0,task_id=7,start_at="MedBay")
        manager=Mock();fake=SimpleNamespace(TaskManager=Mock(return_value=manager))
        with patch.object(self.runner,"parse_args",return_value=args),patch.object(Path,"is_file",return_value=True), \
             patch.object(self.runner,"configure_game_surface"),patch.object(self.runner,"patch_image_grab"), \
             patch.object(self.runner.os,"chdir"),patch.object(self.runner.time,"sleep"), \
             patch.object(self.runner.importlib,"import_module",side_effect=lambda name:fake if name=="src.tasks" else self.pointer), \
             patch.object(self.runner,"solve_inspect_sample") as solve,patch.object(sys,"path",list(sys.path)):
            self.assertEqual(self.runner.main(),0)
        solve.assert_called_once_with(self.pointer,7,0)
        manager.inspect_sample.assert_not_called()
