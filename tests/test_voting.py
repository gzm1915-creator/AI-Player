"""Synthetic voting decisions: no model requests or real game actions."""
import json
import time
from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import patch
import test_bridge as fixture

b = fixture.b


class VotingTests(unittest.TestCase):
    now = staticmethod(fixture.BridgeTests.now)
    save = fixture.BridgeTests.save
    chat = fixture.BridgeTests.chat

    def setUp(self):
        fixture.BridgeTests.setUp(self)
        self.m["voting"] = {"supported": True, "phase": "NotVoted", "votingOpen": True,
            "localAlive": True, "hasVoted": False, "canVote": True, "state": "not_submitted",
            "candidates": [{"playerId": 0, "playerName": "自己", "eligible": True},
                           {"playerId": 1, "playerName": "小红", "eligible": True},
                           {"playerId": 2, "playerName": "断线", "eligible": False}]}
        b.atomic(self.bridge.control_path, {"meetingControl": True})
        self.save()
        self.calls = []
        self.monitor = b.Monitor(self.bridge, lambda **kw: self.calls.append(kw))

    def payload(self):
        return json.loads(self.calls[-1]["parts"][0]["text"].split("\n", 1)[1])

    def test_silent_meeting_requests_actual_vote_or_skip_without_chat(self):
        self.monitor.tick()
        self.assertEqual(self.calls[-1]["ai_behavior"], "respond")
        decision = self.payload()["meetingDecision"]
        self.assertEqual(decision["skipPlayerId"], -1)
        self.assertEqual([p["playerId"] for p in decision["candidates"]], [0, 1])
        self.assertEqual(decision["sessionId"], self.session)
        self.assertIn("vote_decision_required", self.payload()["reviewReasons"])
        self.assertFalse(self.bridge.command_path.exists())  # The model chooses, not the monitor.

    def test_reminders_bounded_and_new_meeting_resets(self):
        start = time.monotonic()
        for seconds in (0, 1, 14, 15, 29, 30, 45, 60):
            with patch.object(b.time, "monotonic", return_value=start + seconds):
                self.monitor.tick()
        self.assertEqual(sum(c["ai_behavior"] == "respond" for c in self.calls), 3)
        self.assertEqual(self.monitor.health()["meetingDecision"]["state"], "attention_required")
        self.m["meetingSequence"] = 2
        self.save()
        self.monitor.tick()
        self.assertEqual(self.payload()["meetingDecision"]["attempt"], 1)
        self.assertEqual(self.payload()["meetingDecision"]["meetingId"], 2)

    def test_no_wakeup_for_discussion_dead_recorded_or_missing_snapshot(self):
        for changes in ({"votingOpen": False}, {"localAlive": False}, {"hasVoted": True}, {"supported": False}, {"canVote": False}):
            with self.subTest(changes=changes):
                original = dict(self.m["voting"])
                self.m["voting"].update(changes)
                self.save()
                calls = []
                b.Monitor(self.bridge, lambda **kw: calls.append(kw)).tick()
                self.assertTrue(all(c["ai_behavior"] == "read" for c in calls))
                self.m["voting"] = original

    def test_gate_and_auto_vote_disable_wakeup(self):
        self.m["voteGate"]["ready"] = False
        self.save()
        self.monitor.tick()
        self.assertEqual(self.calls[-1]["ai_behavior"], "read")
        self.m["voteGate"]["ready"] = True
        self.save()
        self.monitor.auto_vote = False
        self.monitor.tick()
        self.assertEqual(self.monitor.vote_reminder.attempts, 0)
        self.monitor.auto_vote = True
        self.monitor.auto_reply = False
        self.monitor.tick()
        self.assertEqual(self.calls[-1]["ai_behavior"], "respond")

    def test_short_voting_deadline_does_not_wait_full_fifteen_seconds_for_retry(self):
        self.m["voting"]["remainingSeconds"] = 9
        self.save()
        start = time.monotonic()
        with patch.object(b.time, "monotonic", return_value=start):
            self.monitor.tick()
        with patch.object(b.time, "monotonic", return_value=start + 5):
            self.monitor.tick()
        self.assertEqual(self.monitor.vote_reminder.attempts, 2)
        self.assertEqual(self.monitor.health()["meetingDecision"]["reminderIntervalSeconds"], 5)

    def test_meeting_control_off_and_pause_suppress_decision(self):
        b.atomic(self.bridge.control_path, {"meetingControl": False})
        self.monitor.tick()
        self.assertEqual(self.calls[-1]["ai_behavior"], "read")
        self.monitor.paused = True
        b.atomic(self.bridge.control_path, {"meetingControl": True})
        self.monitor.tick()
        self.assertEqual(self.monitor.vote_reminder.attempts, 0)

    def test_push_failure_does_not_consume_reminder(self):
        def fail(**_):
            raise ConnectionError("synthetic host disconnect")
        self.monitor.push = fail
        with self.assertRaises(ConnectionError):
            self.monitor.tick()
        self.assertEqual(self.monitor.vote_reminder.attempts, 0)
        self.monitor.push = lambda **kw: self.calls.append(kw)
        self.monitor.tick()
        self.assertEqual(self.payload()["meetingDecision"]["attempt"], 1)

    def test_self_chat_cannot_trigger_vote_loop_or_replace_queued_decision(self):
        self.monitor.tick()
        decision_key = self.calls[-1]["coalesce_key"]
        self.chat([{"event": "chat", "index": 1, "speakerId": 0, "isLocalPlayer": True, "text": "我投票了"}])
        self.monitor.last_push = 0
        self.monitor.tick()
        self.assertEqual(self.calls[-1]["ai_behavior"], "read")
        self.assertNotEqual(self.calls[-1]["coalesce_key"], decision_key)
        self.assertEqual(self.monitor.vote_reminder.attempts, 1)

    def test_vote_submit_pending_and_recorded_are_separate(self):
        first = self.bridge.vote(-1, self.session, 1)
        self.assertEqual(first["status"], "submitted")
        self.assertEqual(b.load(self.bridge.command_path)["targetNumber"], -1)
        second = self.bridge.vote(1, self.session, 1)
        self.assertEqual(second["status"], "pending")
        self.assertEqual(first["commandId"], second["commandId"])
        self.assertFalse(self.bridge.read()["voting"]["hasVoted"])
        self.monitor.tick()
        self.assertEqual(self.calls[-1]["ai_behavior"], "read")
        self.m["voting"].update(hasVoted=True, canVote=False, state="recorded", targetPlayerId=-1)
        self.save()
        self.assertEqual(self.bridge.vote(1, self.session, 1)["status"], "recorded")

    def test_command_completion_alone_cannot_confirm_vote(self):
        sent = self.bridge.vote(1, self.session, 1)
        b.atomic(self.bridge.nav_path, {"commandId": sent["commandId"], "state": "completed", "completionMode": "vote_submitted"})
        self.bridge.pending_vote["submitted"] -= 6
        voting = self.bridge.read()["voting"]
        self.assertEqual(voting["state"], "unconfirmed")
        self.assertFalse(voting["hasVoted"])

    def test_failed_or_unconfirmed_vote_can_be_reconsidered(self):
        sent = self.bridge.vote(1, self.session, 1)
        b.atomic(self.bridge.nav_path, {"commandId": sent["commandId"], "state": "vote_cast_failed", "error": "test refusal"})
        self.assertEqual(self.bridge.read()["voting"]["state"], "failed")
        self.bridge.last_command_at = 0
        self.assertNotEqual(self.bridge.vote(-1, self.session, 1)["commandId"], sent["commandId"])

    def test_stale_ids_dead_disconnected_candidates_and_invalid_types_rejected(self):
        for target, session, meeting in [(1, "old", 1), (1, self.session, 0), (2, self.session, 1),
                (True, self.session, 1), (1, self.session, True), (255, self.session, 1), (-2, self.session, 1)]:
            with self.subTest(target=target, session=session, meeting=meeting), self.assertRaises(ValueError):
                self.bridge.vote(target, session, meeting)
        self.assertFalse(self.bridge.command_path.exists())

    def test_closed_meeting_and_reconnect_do_not_replay_old_decision(self):
        self.monitor.tick()
        self.m["meetingOpen"] = False
        self.save()
        self.monitor.last_push = 0
        self.monitor.tick()
        self.assertEqual(self.calls[-1]["ai_behavior"], "read")
        with self.assertRaises(ValueError):
            self.bridge.vote(-1, self.session, 1)

    def test_disconnection_updates_decision_health_without_losing_deduplication(self):
        self.monitor.tick()
        self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.save()
        self.monitor.tick()
        self.assertEqual(self.monitor.health()["meetingDecision"]["state"], "disconnected")
        self.assertEqual(self.monitor.health()["meetingDecision"]["maxAttempts"], 3)
        self.t["sentAtUtc"] = self.now()
        self.save()
        self.monitor.tick()
        self.assertEqual(self.monitor.vote_reminder.attempts, 1)
        self.assertEqual(self.calls[-1]["ai_behavior"], "read")


if __name__ == "__main__":
    unittest.main()
