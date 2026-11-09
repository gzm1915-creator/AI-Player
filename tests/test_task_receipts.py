"""Completion can precede a practice refresh by less than one polling interval."""
import copy
import importlib
import itertools
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import test_action_memory as memory
import test_studybot as skills
import test_sample_task as sample

receipts = importlib.import_module("memory_fixture.task_receipts")
batch_module = importlib.import_module("memory_fixture.task_batch")


def fact(state, task, stamp=None):
    return {"receiptId": task["taskInstanceId"], "sessionId": state["sessionId"],
            "matchId": state["matchId"], "playerId": state["self"]["playerId"],
            "completedAtUtc": stamp or state["telemetryAt"], "source": receipts.SOURCE,
            "task": {**copy.deepcopy(task), "complete": True, "step": task.get("maxStep", 1)},
            "position": {"x": 1, "y": 2}}


class ReceiptMemoryTests(unittest.TestCase):
    setUp = memory.ActionMemoryTests.setUp
    observe = memory.ActionMemoryTests.observe

    def prepare(self):
        self.base["self"]["playerId"] = 0
        self.base["tasks"][0]["taskInstanceId"] = "batch1-task0"
        self.observe(0)
        completed = (self.epoch + timedelta(seconds=1)).isoformat()
        return fact({**self.base, "telemetryAt": completed}, self.base["tasks"][0])

    def test_refresh_between_polls_retains_completion_in_previous_batch(self):
        receipt = self.prepare()
        task = {"taskId": 0, "taskType": "SubmitScan", "taskInstanceId": "batch2-task0", "step": 0, "complete": False}
        history = self.observe(2, tasks=[task], taskCompletions=[receipt])
        completed = [e for e in history["taskEvents"] if e["event"] == "task_completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["taskBatch"], 1)
        self.assertEqual(completed[0]["at"], receipt["completedAtUtc"])
        self.assertEqual(completed[0]["source"], receipts.SOURCE)
        self.assertEqual(history["taskBatch"], 2)
        self.assertFalse(history["tasks"][0]["complete"])
        history = self.observe(3, tasks=[task], taskCompletions=[receipt])
        self.assertEqual(sum(e["event"] == "task_completed" for e in history["taskEvents"]), 1)

    def test_delayed_receipt_does_not_complete_reused_id(self):
        receipt = self.prepare()
        task = {**self.base["tasks"][0], "taskInstanceId": "batch2-task0"}
        self.observe(2, tasks=[task])
        history = self.observe(3, tasks=[task], taskCompletions=[receipt])
        self.assertEqual(history["taskBatch"], 2)
        self.assertFalse(history["tasks"][0]["complete"])
        self.assertEqual([e["taskBatch"] for e in history["taskEvents"] if e["event"] == "task_completed"], [1])

    def test_receipt_wins_over_cached_unfinished_list_without_false_new_batch(self):
        receipt = self.prepare()
        history = self.observe(2, taskCompletions=[receipt])
        self.assertTrue(history["tasks"][0]["complete"])
        history = self.observe(3, taskCompletions=[receipt])
        self.assertTrue(history["tasks"][0]["complete"])
        self.assertEqual(history["taskBatch"], 1)

    def test_polled_completion_then_native_receipt_never_duplicates_event(self):
        receipt = self.prepare()
        self.observe(1, tasks=[receipt["task"]])
        history = self.observe(2, tasks=[receipt["task"]], taskCompletions=[receipt])
        self.assertEqual(sum(e["event"] == "task_completed" for e in history["taskEvents"]), 1)

    def test_completion_before_meeting_retained_and_survives_plugin_reload(self):
        receipt = self.prepare()
        self.journal.flush()
        self.journal = memory.j.Journal(self.journal.root)
        history = self.observe(2, phase="MEETING", taskCompletions=[receipt])
        self.assertTrue(history["tasks"][0]["complete"])
        self.assertEqual(sum(e["event"] == "task_completed" for e in history["taskEvents"]), 1)

    def test_task_instance_change_detects_same_catalog_refresh_without_complete_frame(self):
        self.prepare()
        task = {**self.base["tasks"][0], "taskInstanceId": "batch2-task0"}
        history = self.observe(2, tasks=[task])
        self.assertEqual(history["taskBatch"], 2)
        tracker = batch_module.TaskBatch()
        first = tracker.observe(self.base)
        self.assertNotEqual(first, tracker.observe({**self.base, "tasks": [task]}))


class ReceiptValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.task = {"taskId": 0, "taskType": "InspectSample", "taskInstanceId": "instance1"}
        self.state = {"sessionId": "session", "matchId": 1, "self": {"playerId": 0}, "telemetryAt": self.now.isoformat()}
        self.receipt = fact(self.state, self.task)
        self.state["taskCompletions"] = [self.receipt]

    def test_wrong_identity_incomplete_malformed_and_expired_facts_rejected(self):
        self.assertIsNotNone(receipts.receipt_for(self.state, self.task))
        changes = ({"sessionId": "old"}, {"matchId": 2}, {"playerId": 7}, {"source": "clicked"}, {"source": []},
                   {"matchId": True}, {"playerId": False},
                   {"receiptId": "other"}, {"task": {}}, {"task": {**self.task, "complete": False}},
                   {"completedAtUtc": "invalid"}, {"completedAtUtc": (self.now - timedelta(seconds=301)).isoformat()},
                   {"completedAtUtc": (self.now + timedelta(seconds=2)).isoformat()})
        for change in changes:
            with self.subTest(change=change):
                state = {**self.state, "taskCompletions": [{**self.receipt, **change}]}
                self.assertIsNone(receipts.receipt_for(state, self.task))
        self.assertIsNone(receipts.receipt_for(self.state, {**self.task, "taskInstanceId": "next"}))
        self.assertIsNone(receipts.receipt_for(self.state, {**self.task, "taskType": "SubmitScan"}))

    def test_pre_clear_receipt_cannot_recreate_cleared_evidence(self):
        self.assertEqual(list(receipts.valid_receipts(self.state, after=self.now.isoformat())), [])

    def test_native_record_complete_is_accepted_with_the_same_identity_guards(self):
        self.receipt["source"] = "native_own_task_record_complete"
        self.assertIsNotNone(receipts.receipt_for(self.state, self.task))
        self.receipt["matchId"] = 2
        self.assertIsNone(receipts.receipt_for(self.state, self.task))


class ReceiptSkillTests(unittest.TestCase):
    setUp = skills.StudyBotTests.setUp

    def test_stop_does_not_leave_a_running_label_without_a_child(self):
        self.runner.tick(self.state)
        self.runner.set_enabled(False)
        self.assertFalse(self.runner.status()["busy"])
        self.assertEqual(self.runner.status()["state"], "disabled")

    def test_batch_refresh_confirms_old_skill_without_closing_or_completing_new_task(self):
        self.state.update(self={"playerId": 0}, telemetryAt=datetime.now(timezone.utc).isoformat())
        task = self.state["tasks"][1]
        task.update(taskType="InspectSample", taskInstanceId="sample1", maxStep=1,
                    sample={"supported": True, "phase": "ready"})
        self.runner.tick(self.state)
        receipt = fact(self.state, task)
        self.state.update(taskCompletions=[receipt], minigameOpen=False)
        self.state["tasks"] = [{"taskId": 7, "taskType": "SwipeCard", "taskInstanceId": "card2", "step": 0}]
        self.runner.tick(self.state)
        self.assertEqual(self.runner.status()["state"], "completed")
        self.assertEqual(self.runner.status()["taskType"], "InspectSample")
        self.runner.close_window.assert_not_called()
        self.assertFalse(self.runner.status()["busy"])
        self.assertNotIn("complete", self.state["tasks"][0])


class ReceiptRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sample.SampleRunnerTests.setUpClass()
        cls.runner = sample.SampleRunnerTests.runner
    setUp = sample.SampleRunnerTests.setUp

    def test_window_close_or_task_disappearance_requires_native_fact(self):
        from PIL import Image, ImageDraw
        for native in (False, True):
            with self.subTest(native=native):
                self.setUp()
                self.t.update(sentAtUtc=datetime.now(timezone.utc).isoformat(), localPlayer={"playerId": 0})
                task = self.t["ui"]["ownTasks"][0]
                task["taskInstanceId"] = "sample1"
                task["sample"]["phase"] = "ready"
                original = copy.deepcopy(task)
                image = Image.new("RGB", (1920, 1080), (80, 120, 245))
                ImageDraw.Draw(image).rectangle((952, 550, 974, 630), fill=(246, 134, 134))
                def select(action, **fields):
                    self.t["ui"].update(minigameOpen=False, ownTasks=[])
                    if native:
                        state = {"sessionId": self.t["sessionId"], "matchId": 1,
                                 "self": self.t["localPlayer"], "telemetryAt": self.t["sentAtUtc"]}
                        self.t["ui"]["taskCompletions"] = [fact(state, original)]
                    return True
                ticks = itertools.count(0, .1)
                with patch.object(self.runner, "read_latest_telemetry", side_effect=lambda: copy.deepcopy(self.t)), \
                     patch.object(self.runner, "telemetry_indexed_points", return_value=None), \
                     patch("PIL.ImageGrab.grab", return_value=image), patch.object(self.runner.time, "sleep"), \
                     patch.object(self.runner.time, "monotonic", side_effect=lambda: next(ticks)), \
                     patch.object(self.runner, "send_plugin_action", side_effect=select):
                    if native:
                        self.runner.solve_inspect_sample(self.pointer, 7, 0)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "no native completion"):
                            self.runner.solve_inspect_sample(self.pointer, 7, 0)
