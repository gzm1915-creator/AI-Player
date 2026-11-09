"""Exercise telemetry loss, schema damage and Windows hand-off contention."""
import copy
import importlib
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import test_bridge as fixture

b = fixture.b
files = importlib.import_module(b.__package__ + ".files")


class FaultRecoveryTests(unittest.TestCase):
    setUp = fixture.BridgeTests.setUp
    now = staticmethod(fixture.BridgeTests.now)
    save = fixture.BridgeTests.save

    def test_malformed_optional_fields_cannot_kill_observer_and_recovery_is_automatic(self):
        original = copy.deepcopy(self.t)
        for field in ("game", "ui", "localPlayer", "nearbyPlayers", "vents", "playerDetection"):
            with self.subTest(field=field):
                self.t = copy.deepcopy(original)
                self.t[field] = None
                self.save()
                state = self.bridge.read()
                self.assertFalse(state["connected"])
                self.assertEqual(state["nearbyPlayers"], [])
                self.assertEqual(state["location"]["source"], "unavailable")
        self.t = original
        self.save()
        self.assertTrue(self.bridge.read()["connected"])

    def test_invalid_meeting_sequence_fails_closed_instead_of_crashing(self):
        for value in (None, True, "bad", [], -1):
            with self.subTest(value=value):
                self.m["meetingSequence"] = value
                self.save()
                self.assertFalse(self.bridge.read()["meetingOpen"])
                with self.assertRaises(ValueError):
                    self.bridge.say("不应发送")

    def test_missing_session_is_not_an_actionable_connection(self):
        self.t["sessionId"] = ""
        self.save()
        self.assertFalse(self.bridge.read()["connected"])

    def test_corrupt_task_array_never_starts_a_skill_or_reuses_stale_location(self):
        self.t["ui"]["ownTasks"] = [None]
        self.save()
        state = self.bridge.read()
        self.assertFalse(state["connected"])
        self.assertEqual(state["tasks"], [])

    def test_corrupt_position_does_not_propagate_to_navigation(self):
        self.t["localPlayer"]["position"] = [1, 2]
        self.save()
        state = self.bridge.read()
        self.assertFalse(state["connected"])
        self.assertNotIn("position", state["self"])

    def test_damaged_roster_disables_voting_without_losing_chat(self):
        for candidates in (None, [None], [{"playerId": True, "eligible": True}],
                           [{"playerId": 1, "eligible": "false"}]):
            with self.subTest(candidates=candidates):
                self.m["voting"] = {"supported": True, "canVote": True, "candidates": candidates}
                self.save()
                state = self.bridge.read()
                self.assertTrue(state["connected"])
                self.assertTrue(state["meetingOpen"])
                self.assertFalse(state["capabilities"]["meetingVote"]["available"])
                self.assertEqual(state["voting"]["candidates"], [])

    def test_invalid_navigation_status_does_not_interrupt_next_telemetry_read(self):
        for value in (None, [], {}, 1):
            with self.subTest(value=value):
                b.atomic(self.bridge.nav_path, {"state": value})
                self.assertTrue(self.bridge.read()["connected"])

    def test_navigation_receipt_fallback_expires_and_cannot_cross_matches(self):
        from datetime import datetime, timezone, timedelta
        receipt = {"commandId": "neko-running", "state": "running", "updatedAtUtc": self.now()}
        b.atomic(self.bridge.nav_path, receipt)
        self.bridge.read()
        self.bridge.nav_path.unlink()
        self.assertEqual(self.bridge.read()["navigation"]["commandId"], "neko-running")
        self.t["game"]["matchId"] = 2
        self.save()
        self.assertIsNone(self.bridge.read()["navigation"]["commandId"])
        receipt["updatedAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        b.atomic(self.bridge.nav_path, receipt)
        self.bridge.read()
        self.bridge.nav_path.unlink()
        self.assertIsNone(self.bridge.read()["navigation"]["commandId"])

    def test_fresh_foreign_controller_receipt_overrides_our_cached_route(self):
        for command_id in ("neko-owned", "another-controller"):
            b.atomic(self.bridge.nav_path, {"commandId": command_id, "state": "running", "updatedAtUtc": self.now()})
            self.assertEqual(self.bridge.read()["navigation"]["commandId"], command_id)

    def test_slow_host_notification_does_not_lock_out_stop_controls(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def push(**_):
            entered.set()
            release.wait(3)
        monitor = b.Monitor(self.bridge, push)
        def control():
            try:
                self.bridge.halt()
                stopped.set()
            except Exception as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=monitor.tick, daemon=True)
        stopper = threading.Thread(target=control, daemon=True)
        try:
            worker.start()
            self.assertTrue(entered.wait(1))
            stopper.start()
            self.assertTrue(stopped.wait(.5), "model transport blocked the game stop control")
        finally:
            release.set()
            worker.join(2)
            if stopper.ident is not None:
                stopper.join(2)
        self.assertFalse(errors)
        self.assertFalse(b.load(self.bridge.control_path)["autoNavigation"])

    @unittest.skipUnless(os.name == "nt", "Windows sharing behavior")
    def test_reader_allows_game_to_replace_snapshot_while_handle_is_open(self):
        path = self.root / "shared.json"
        b.atomic(path, {"revision": 1})
        with files.open_shared(path) as reader:
            b.atomic(path, {"revision": 2})
            self.assertIn(b'1', reader.read())
        self.assertEqual(b.load(path), {"revision": 2})

    def test_short_permission_conflict_retries_without_losing_command(self):
        path = self.root / "retry.json"
        replace = files.replace_file
        attempts = []
        def contested(source, destination):
            attempts.append(True)
            if len(attempts) == 1:
                raise PermissionError("synthetic sharing conflict")
            return replace(source, destination)
        with patch.object(files, "replace_file", contested), patch.object(files.time, "sleep"):
            b.atomic(path, {"action": "test"})
        self.assertEqual(b.load(path), {"action": "test"})
        self.assertEqual(len(attempts), 2)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_permanent_permission_failure_is_bounded_and_leaves_old_file_intact(self):
        path = self.root / "preserved.json"
        b.atomic(path, {"revision": 1})
        with patch.object(files, "replace_file", side_effect=PermissionError("synthetic permanent lock")) as replace, patch.object(files.time, "sleep"):
            with self.assertRaises(PermissionError):
                b.atomic(path, {"revision": 2})
            self.assertEqual(replace.call_count, 5)
        self.assertEqual(b.load(path), {"revision": 1})
        self.assertFalse(list(self.root.glob("*.tmp")))
