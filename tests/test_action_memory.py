"""Regression scenarios for observation order, reused tasks and fleeting players."""
import copy
import importlib
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

package = types.ModuleType("memory_fixture")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "plugin/among_us_neko")]
sys.modules[package.__name__] = package
j = importlib.import_module("memory_fixture.journal")
s = importlib.import_module("memory_fixture.spatial")
b = importlib.import_module("memory_fixture.bridge")


class ActionMemoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journal = j.Journal(Path(tmp.name) / "history")
        self.addCleanup(self.journal.flush)
        self.epoch = datetime.now(timezone.utc) - timedelta(seconds=100)
        self.base = {"connected": True, "sessionId": "12345678-1234-1234-1234-123456789012", "matchId": 1,
                     "mapId": "TheSkeld", "meetingId": 0, "phase": "TASKS", "nearbyPlayers": [],
                     "self": {"position": {"x": 1, "y": 2}},
                     "nearestAnchor": {"anchorId": "skeld.admin.fix-wiring", "room": "Admin", "label": "管理室·修复线路", "distance": .1},
                     "tasks": [{"taskId": 0, "taskType": "FixWiring", "step": 0, "maxStep": 3, "complete": False}]}

    def observe(self, sec, **fields):
        state = {**copy.deepcopy(self.base), **fields, "telemetryAt": (self.epoch + timedelta(seconds=sec)).isoformat()}
        self.journal.observe(state)
        return self.journal.query()

    def task(self, step, typ="FixWiring", maximum=3):
        return [{"taskId": 0, "taskType": typ, "step": step, "maxStep": maximum, "complete": step == maximum}]

    def test_task_progress_is_not_completion_and_id_reuse_is_separate_batch(self):
        self.observe(0)
        history = self.observe(1, tasks=self.task(1))
        text = s.action_record(history)["text"]
        self.assertIn("0→1/3", text)
        self.assertIn("尚未确认整个任务完成", text)
        self.observe(2, tasks=self.task(3))
        history = self.observe(3, tasks=self.task(0, "SubmitScan", 1))
        self.assertEqual(history["taskBatch"], 2)
        self.assertEqual([t["taskType"] for t in history["tasks"]], ["SubmitScan"])
        completed = [e for e in history["taskEvents"] if e["event"] == "task_completed"]
        self.assertEqual(completed[0]["taskBatch"], 1)
        self.assertIn("第 2 批", s.action_record(history)["text"])

    def test_same_catalog_complete_reset_is_new_batch(self):
        self.observe(0)
        self.observe(1, tasks=self.task(3))
        history = self.observe(2)
        self.assertEqual(history["taskBatch"], 2)
        self.assertFalse(history["tasks"][0]["complete"])

    def test_missing_task_does_not_create_completion_or_batch(self):
        self.observe(0)
        history = self.observe(1, tasks=[])
        self.assertEqual(history["taskBatch"], 1)
        self.assertEqual(history["tasks"][0]["step"], 0)
        self.assertFalse(any(e["event"] == "task_completed" for e in history["taskEvents"]))

    def test_subsecond_player_is_persisted_and_departure_is_not_current_position(self):
        self.observe(0)
        player = {"playerId": 7, "playerName": "小蓝", "colorName": "蓝色", "x": 2, "y": 2,
                  "presence": "visible_now",
                  "relativeToSelf": {"distance": 1, "direction": "+X", "directionName": "右侧"}}
        self.observe(.1, nearbyPlayers=[player])
        history = self.observe(.2)
        self.assertFalse(history["lastSeenPlayers"][0]["isCurrent"])
        history = self.observe(.8)
        self.assertEqual(history["lastSeenPlayers"][0]["presence"], "last_seen_only")
        self.assertEqual(history["lastSeenPlayers"][0]["player"]["presence"], "last_seen_only")
        self.assertEqual(history["lastSeenPlayers"][0]["player"]["relativeToSelf"]["reference"], "self_position_at_last_sighting")
        self.assertTrue(all(e["player"]["presence"] == "at_event_time" for e in history["encounters"]))
        record = s.action_record(history)
        self.assertIn("小蓝", record["text"])
        self.assertIn("右侧约 1.00", record["text"])
        self.assertIn("当前去向未知", record["text"])

    def test_last_sighting_timestamp_updates_each_sample_and_recent_sort_is_correct(self):
        p = {"playerId": 1, "x": 1, "y": 0}
        q = {"playerId": 2, "x": 2, "y": 0}
        self.observe(0, nearbyPlayers=[p])
        self.observe(.1, nearbyPlayers=[q])
        history = self.observe(.2, nearbyPlayers=[p])
        self.assertEqual(history["lastSeenPlayers"][0]["player"]["playerId"], 1)
        self.assertEqual(history["lastSeenPlayers"][0]["lastSeenAt"], (self.epoch + timedelta(seconds=.2)).isoformat())

    def test_player_movement_carries_from_and_to_positions(self):
        self.observe(0, nearbyPlayers=[{"playerId": 1, "x": 0, "y": 0}])
        history = self.observe(.3, nearbyPlayers=[{"playerId": 1, "x": 1, "y": 0}])
        event = next(e for e in history["timeline"] if e["event"] == "player_moved")
        self.assertEqual(event["previousPosition"], {"x": 0, "y": 0})
        self.assertIn("移动到(1.00, 0.00)", s.describe_event(event))

    def test_disconnect_and_meeting_boundaries_do_not_imply_walked_teleport(self):
        self.observe(0)
        self.observe(1, connected=False)
        self.base["self"] = {"position": {"x": 100, "y": 200}}
        history = self.observe(2)
        self.assertEqual(history["distance"], 0)
        self.assertTrue(history["route"][-1]["segmentStart"])
        self.assertIn("遥测中断", s.action_record(history)["text"])

    def test_incremental_task_discovery_does_not_erase_existing_progress(self):
        self.observe(0)
        self.observe(1, tasks=self.task(1))
        extra = {"taskId": 1, "taskType": "SwipeCard", "step": 0, "maxStep": 1, "complete": False}
        history = self.observe(2, tasks=self.task(1) + [extra])
        self.assertEqual(history["taskBatch"], 1)
        self.assertEqual(len(history["tasks"]), 2)
        self.observe(3, tasks=[extra])  # A partial sample isn't a replacement.
        history = self.observe(4, tasks=self.task(2) + [extra])
        self.assertEqual(history["taskBatch"], 1)
        progress = [e for e in history["taskEvents"] if e["event"] == "task_progress"][-1]
        self.assertEqual(progress["previousStep"], 1)

    def test_meeting_relocation_is_a_separate_route_segment(self):
        self.observe(0)
        self.observe(1, phase="MEETING")
        self.base["self"] = {"position": {"x": 30, "y": 40}}
        history = self.observe(2)
        self.assertEqual(history["distance"], 0)
        self.assertTrue(history["route"][-1]["segmentStart"])
        self.assertIn("会议切换", s.action_record(history)["text"])

    def test_old_match_history_never_claims_current_visible_players(self):
        self.observe(0, nearbyPlayers=[{"playerId": 1, "x": 1, "y": 0}])
        history = self.journal.query()
        b.Bridge._history_scope(history, {**self.base, "matchId": 2})
        self.assertEqual(history["scope"], "previous_match")
        self.assertFalse(history["lastSeenPlayers"][0]["isCurrent"])
        self.assertEqual(s.model_history(history)["actionRecord"]["scope"], "previous_match")

    def test_visiting_task_anchor_does_not_claim_task_was_done(self):
        history = self.observe(0)
        self.assertNotIn("task_completed", [e["event"] for e in history["timeline"]])
        self.assertIn("路过任务锚点不等于做过任务", history["routeSummary"]["notice"])

    def test_model_keeps_task_previous_step_and_match_scope(self):
        self.observe(0)
        history = self.observe(1, tasks=self.task(1))
        history["scope"] = "current_match"
        data = s.model_state({"connected": True, "history": history})["history"]
        self.assertEqual(data["identity"], history["identity"])
        self.assertEqual(data["taskEvents"][-1]["previousStep"], 0)
        self.assertEqual(data["taskBatch"], 1)
        self.assertIn("0→1/3", data["actionRecord"]["text"])

    def test_pause_hides_history_from_ai_but_keeps_files_until_user_clears(self):
        self.observe(0, nearbyPlayers=[{"playerId": 1, "playerName": "保留的目击", "x": 2, "y": 2}])
        self.journal.set_enabled(False)
        history = self.observe(1)
        self.assertFalse(history["recordingEnabled"])
        for key in ("route", "tasks", "timeline", "lastSeenPlayers", "encounters"):
            self.assertEqual(history[key], [])
        self.assertGreater(history["storedEventCount"], 0)
        self.assertIn("保留的目击", (self.journal.path / "summary.json").read_text(encoding="utf-8"))
        self.assertNotIn("保留的目击", str(s.model_history(history)))
        self.journal.set_enabled(True)
        history = self.observe(2)
        self.assertEqual(history["lastSeenPlayers"][0]["player"]["playerName"], "保留的目击")
        self.assertFalse(history["lastSeenPlayers"][0]["isCurrent"])
        self.assertTrue(history["route"][-1]["segmentStart"])

    def test_clear_current_is_persistent_and_preserves_another_match(self):
        self.observe(0, nearbyPlayers=[{"playerId": 1, "playerName": "第一局", "x": 2, "y": 2}])
        older = self.journal.path
        self.observe(1, matchId=2, nearbyPlayers=[{"playerId": 2, "playerName": "第二局", "x": 2, "y": 2}])
        old_ids = {e["eventId"] for e in self.journal.query()["timeline"]}
        result = self.journal.clear()
        self.assertEqual(result["clearedMatches"], 1)
        self.assertTrue((older / "summary.json").exists())
        restored = j.Journal(self.journal.root)
        self.assertEqual(restored.query()["eventCount"], 0)
        self.assertEqual(restored.query()["lastSeenPlayers"], [])
        self.assertTrue(restored.query()["clearedAt"])
        self.assertNotIn("第二局", (self.journal.path / "summary.json").read_text(encoding="utf-8"))
        # Even an already cached pre-clear sample can't recreate deleted data.
        self.assertEqual(self.observe(2, matchId=2)["eventCount"], 0)
        history = self.observe(110, matchId=2)
        self.assertTrue(old_ids.isdisjoint({e["eventId"] for e in history["timeline"]}))
        self.assertNotIn("task_completed", [e["event"] for e in history["timeline"]])

    def test_clear_all_removes_all_journal_files_without_touching_other_files(self):
        self.observe(0, nearbyPlayers=[{"playerId": 1, "playerName": "旧玩家", "x": 2, "y": 2}])
        first = self.journal.path
        marker = self.journal.root / "unrelated.txt"
        marker.write_text("keep", encoding="utf-8")
        self.observe(1, matchId=2)
        result = self.journal.clear(all_matches=True)
        self.assertEqual(result["clearedMatches"], 2)
        self.assertFalse((first / "summary.json").exists())
        self.assertFalse(list(self.journal.root.glob("*/events*.jsonl")))
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertIn("clearedAt", (self.journal.root / "control.json").read_text(encoding="utf-8"))
        self.assertEqual(j.Journal(self.journal.root).query()["lastSeenPlayers"], [])

    def test_disabled_journal_after_restart_does_not_expose_saved_history(self):
        self.observe(0, nearbyPlayers=[{"playerId": 1, "playerName": "保存过", "x": 2, "y": 2}])
        self.journal.flush()
        restored = j.Journal(self.journal.root, enabled=False)
        self.assertGreater(restored.query()["storedEventCount"], 0)
        self.assertEqual(restored.query()["lastSeenPlayers"], [])
        self.assertIn("暂停", restored.query()["notice"])


if __name__ == "__main__":
    unittest.main()
