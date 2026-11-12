"""Replay bounded forecasts, task-aware grouping, and safe pursuit handoffs."""

import copy
import importlib
import math
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import test_impostor as fixture

b = fixture.b
prediction = importlib.import_module("impostor_fixture.prediction")
social = importlib.import_module("impostor_fixture.social")


def person(pid, x, y=0, vx=1, vy=0):
    return {"playerId": pid, "x": x, "y": y, "velocityX": vx, "velocityY": vy}


def task(tid=1, x=20, complete=False, step=0):
    return {"taskId": tid, "taskType": "FixWiring", "locations": [{"x": x, "y": 0}], "complete": complete, "step": step}


def observation():
    return {
        "connected": True,
        "sessionId": "test",
        "matchId": 1,
        "mapId": "TheSkeld",
        "meetingId": 0,
        "phase": "TASKS",
        "meetingOpen": False,
        "playerDetection": {"status": "ok"},
        "self": {"playerId": 0, "position": {"x": 0, "y": 0}, "canMove": True},
        "ownRole": {"roleType": "Crewmate"},
        "tasks": [task()],
        "nearbyPlayers": [person(1, 7), person(2, 8)],
        "history": {"taskBatch": 1},
        "navigation": {},
    }


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.f = prediction.PlayerForecast()
        self.s = observation()
        self.s["nearbyPlayers"] = [person(1, 5, vx=2)]
        self.serial = 0

    def update(self, now):
        self.serial += 1
        self.s["telemetryAt"] = "frame-" + str(self.serial)
        self.f.update(self.s, now)

    def test_motion_lead_and_lost_target_expire_without_mutating_observations(self):
        original = copy.deepcopy(self.s["nearbyPlayers"])
        self.update(100)
        live = self.f.get(1, 100)
        self.assertAlmostEqual(live["x"], 5.8)
        self.assertTrue(live["visibleNow"])
        self.assertEqual(live["positionSource"], "motion_forecast")
        self.assertEqual(self.s["nearbyPlayers"], original)
        self.s["nearbyPlayers"] = []
        self.update(101)
        lost = self.f.get(1, 101)
        self.assertFalse(lost["visibleNow"])
        self.assertGreater(lost["x"], live["x"])
        self.assertEqual(lost["lastObservedPosition"], {"x": 5, "y": 0})
        self.assertGreater(self.f.get(1, 102)["uncertaintyRadius"], lost["uncertaintyRadius"])
        self.assertIsNone(self.f.get(1, 103.01))
        self.assertEqual(self.s["nearbyPlayers"], [])

    def test_duplicate_cached_frame_does_not_refresh_visibility_or_deadline(self):
        self.update(100)
        self.f.update(self.s, 102)
        self.assertFalse(self.f.get(1, 102)["visibleNow"])
        self.f.update(self.s, 104)
        self.assertIsNone(self.f.get(1, 104))

    def test_turn_smoothing_and_stop_do_not_keep_extrapolating_stopped_player(self):
        self.update(100)
        self.s["nearbyPlayers"] = [person(1, 5.4, vx=0, vy=2)]
        self.update(100.2)
        turn = self.f.get(1, 100.2)
        self.assertGreater(turn["velocityY"], turn["velocityX"])
        self.s["nearbyPlayers"] = [person(1, 5.4, vx=0, vy=0)]
        self.update(100.4)
        self.assertEqual(self.f.get(1, 100.4)["x"], 5.4)
        self.s["nearbyPlayers"] = []
        self.update(100.5)
        self.assertIsNone(self.f.get(1, 100.5))

    def test_missing_velocity_uses_two_real_samples_and_teleport_resets_it(self):
        self.s["nearbyPlayers"] = [{"playerId": 1, "x": 5, "y": 0}]
        self.update(100)
        self.s["nearbyPlayers"][0]["x"] = 5.25
        self.update(100.25)
        self.assertGreater(self.f.get(1, 100.25)["velocityX"], 0)
        self.s["nearbyPlayers"] = [person(1, 50, vx=100)]
        self.update(100.35)
        self.assertEqual(self.f.get(1, 100.35)["speed"], 0)

    def test_speed_and_total_displacement_are_bounded(self):
        self.s["nearbyPlayers"][0].update(velocityX=100, velocityY=100)
        self.update(100)
        self.s["nearbyPlayers"] = []
        self.update(102)
        estimate = self.f.get(1, 102)
        self.assertLessEqual(estimate["speed"], 6)
        self.assertLessEqual(math.hypot(estimate["x"] - 5, estimate["y"]), 4.500001)

    def test_stationary_task_stop_is_only_a_timed_inference(self):
        self.s["nearbyPlayers"] = [person(1, 5, vx=0)]
        anchors = [{"kind": "task", "position": {"x": 5, "y": 0}}]
        self.update(100)
        self.assertFalse(self.f.get(1, 100, anchors)["suspectedTaskStop"])
        self.update(101)
        self.update(102.1)
        estimate = self.f.get(1, 102.1, anchors)
        self.assertTrue(estimate["suspectedTaskStop"])
        self.assertNotIn("doingTask", estimate)
        self.assertFalse(self.f.get(1, 102.1, [])["suspectedTaskStop"])

    def test_disconnect_meeting_death_detection_and_identity_clear_forecasts(self):
        cases = [
            dict(connected=False),
            dict(meetingOpen=True),
            dict(phase="MEETING"),
            dict(ownRole={"isDead": True}),
            dict(playerDetection={"status": "camera_unavailable"}),
            dict(sessionId="next"),
            dict(matchId=2),
            dict(mapId="Other"),
            dict(meetingId=1),
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.setUp()
                self.update(100)
                self.s.update(changes, nearbyPlayers=[])
                self.update(100.1)
                self.assertEqual(self.f.players(100.1), [])

    def test_dead_vent_and_invalid_positions_do_not_remain_prediction_targets(self):
        for changes in ({"isDead": True}, {"inVent": True}, {"x": float("nan")}):
            with self.subTest(changes=changes):
                self.setUp()
                self.update(100)
                self.s["nearbyPlayers"][0].update(changes)
                self.update(100.1)
                self.assertIsNone(self.f.get(1, 100.1))

    def test_historical_only_players_never_seed_forecasts(self):
        self.s["recentPlayers"] = self.s["nearbyPlayers"]
        self.s["nearbyPlayers"] = []
        self.update(100)
        self.assertEqual(self.f.players(100), [])

    def test_old_file_sample_does_not_get_a_new_three_second_lifetime(self):
        self.s["telemetryAt"] = (datetime.now(timezone.utc) - timedelta(seconds=3.2)).isoformat()
        self.f.update(self.s, 100)
        self.assertEqual(self.f.players(100), [])

    def test_out_of_order_sample_cannot_replace_newer_motion(self):
        stamp = datetime.now(timezone.utc)
        self.s["telemetryAt"] = stamp.isoformat()
        self.f.update(self.s, 100)
        self.s["telemetryAt"] = (stamp - timedelta(seconds=0.2)).isoformat()
        self.s["nearbyPlayers"][0]["x"] = -10
        self.f.update(self.s, 100.1)
        self.assertEqual(self.f.get(1, 100.1)["lastObservedPosition"]["x"], 5)


class CrewPolicyTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Mock()
        self.bridge.navigate.side_effect = lambda *_a, **_kw: {"commandId": "route"}
        self.c = social.CrewSocial(self.bridge)
        self.s = observation()
        self.anchors = [
            {"anchorId": "group.door", "kind": "trace_waypoint", "position": {"x": 7.5, "y": 0}},
            {"anchorId": "other.task", "kind": "task", "position": {"x": 7, "y": 0}},
            {"anchorId": "other.task2", "kind": "task", "position": {"x": 8, "y": 0}},
            {"anchorId": "own.task", "kind": "task", "position": {"x": 20, "y": 0}},
        ]
        self.serial = 0

    def observe(self, at):
        self.serial += 1
        self.s["telemetryAt"] = "sample-" + str(self.serial)
        self.c.observe(self.s, at, self.anchors)

    def test_last_one_or_two_far_tasks_prefer_group_without_opening_task(self):
        for count in (1, 2):
            with self.subTest(count=count):
                self.setUp()
                self.s["tasks"] = [task(i, 20 + i) for i in range(count)]
                self.observe(100)
                self.assertTrue(self.c.tick(self.s, self.anchors, 100))
                self.bridge.navigate.assert_called_once_with("group.door", interact=False)
                self.assertEqual(self.c.remaining, count)
                self.assertFalse(self.c.task_priority)

    def test_ten_unit_boundary_detaches_and_task_commit_survives_following_frame(self):
        self.observe(100)
        self.c.tick(self.s, self.anchors, 100)
        self.s["navigation"] = {"commandId": "route", "state": "running"}
        self.s["self"]["position"]["x"] = 9.99
        self.observe(101)
        self.assertFalse(self.c.task_priority)
        self.s["self"]["position"]["x"] = 10
        self.observe(101.5)
        self.assertTrue(self.c.task_priority)
        self.c.tick(self.s, self.anchors, 101.5)
        self.bridge.command.assert_called_once_with("stop")
        self.s["self"]["position"]["x"] = 0
        self.observe(102)
        self.assertTrue(self.c.task_priority)
        self.assertEqual(self.c.task_commit, (1, 0))

    def test_ninety_seconds_limit_survives_group_changes_and_meeting(self):
        self.s["self"]["position"]["x"] = 5
        self.observe(100)
        self.c.tick(self.s, self.anchors, 100)
        self.s["nearbyPlayers"] = [person(3, 6), person(4, 7)]
        self.observe(160)
        self.assertEqual(self.c.follow_since, 100)
        self.s.update(meetingOpen=True, phase="MEETING")
        self.observe(170)
        self.s.update(meetingOpen=False, phase="TASKS", meetingId=1)
        self.observe(190)
        self.assertTrue(self.c.force_task)
        self.assertFalse(self.c.tick(self.s, self.anchors, 190))
        self.assertEqual(self.c.task_commit, (1, 0))

    def test_actual_task_progress_releases_commit_and_restarts_follow_budget(self):
        self.observe(100)
        self.observe(190)
        self.assertTrue(self.c.force_task)
        self.s["tasks"][0].update(step=1, locations=[{"x": 22, "y": 0}])
        self.observe(191)
        self.assertFalse(self.c.task_priority)
        self.assertEqual(self.c.follow_since, 191)
        self.assertIsNone(self.c.task_commit)

    def test_complete_catalog_and_missing_task_list_have_different_policies(self):
        self.s["tasks"] = [task(complete=True)]
        self.observe(100)
        self.assertEqual(self.c.stage, "done")
        self.assertTrue(self.c.tick(self.s, self.anchors, 100))
        self.s["tasks"] = []
        self.s["navigation"] = {"commandId": "route", "state": "running"}
        self.observe(101)
        self.assertEqual(self.c.stage, "unknown")
        self.assertTrue(self.c.tick(self.s, self.anchors, 101))
        self.bridge.command.assert_called_once_with("stop")
        self.assertFalse(self.c.tick(self.s, self.anchors, 101.5))

    def test_stop_near_task_is_inferred_and_does_not_hold_own_tasks(self):
        self.s["self"]["position"]["x"] = 5
        self.s["nearbyPlayers"] = [person(1, 7, vx=0), person(2, 8, vx=0)]
        for now in (100, 101, 102.1):
            self.observe(now)
        self.assertTrue(all(p["suspectedTaskStop"] for p in self.c.estimates))
        self.assertEqual(self.c.group, [])
        self.assertFalse(self.c.tick(self.s, self.anchors, 102.1))
        self.bridge.navigate.assert_not_called()

    def test_finished_player_moves_to_watch_point_once_then_observes(self):
        self.s["tasks"] = [task(complete=True)]
        self.s["self"]["position"]["x"] = 7
        self.s["nearbyPlayers"] = [person(1, 7, vx=0), person(2, 8, vx=0)]
        self.anchors.append({"anchorId": "watch", "kind": "waypoint", "position": {"x": 4, "y": 2}})
        for now in (100, 101, 102.1):
            self.observe(now)
        self.c.tick(self.s, self.anchors, 102.1)
        self.bridge.navigate.assert_called_once_with("watch", interact=False)
        self.s["self"]["position"] = {"x": 4, "y": 2}
        self.s["navigation"] = {"commandId": "route", "state": "completed"}
        self.observe(103)
        self.c.tick(self.s, self.anchors, 103)
        for now in (104, 105, 106, 108):
            self.observe(now)
            self.c.tick(self.s, self.anchors, now)
        self.assertEqual(self.bridge.navigate.call_count, 1)
        self.assertEqual(self.c.phase, "observing")

    def test_lost_moving_group_follows_briefly_then_stops_without_new_sightings(self):
        self.observe(100)
        self.c.tick(self.s, self.anchors, 100)
        self.s["navigation"] = {"commandId": "route", "state": "running"}
        self.s["nearbyPlayers"] = []
        self.observe(100.5)
        self.c.tick(self.s, self.anchors, 100.5)
        self.assertEqual(len(self.c.group), 2)
        self.assertTrue(all(not p["visibleNow"] for p in self.c.group))
        self.bridge.journal.record_action.assert_not_called()
        self.observe(103.1)
        self.c.tick(self.s, self.anchors, 103.1)
        self.bridge.command.assert_called_once_with("stop")
        self.assertEqual(self.s["nearbyPlayers"], [])

    def test_group_review_and_route_replanning_are_throttled(self):
        with patch.object(social, "_group", wraps=social._group) as grouping:
            for now in (100, 100.05, 100.1, 100.2, 100.26):
                self.observe(now)
            self.assertEqual(grouping.call_count, 2)
        self.c.tick(self.s, self.anchors, 100.26)
        self.s["navigation"] = {"commandId": "route", "state": "running"}
        self.s["nearbyPlayers"] = [person(1, 10), person(2, 11)]
        self.observe(101)
        self.c.tick(self.s, self.anchors, 101)
        self.bridge.command.assert_not_called()
        self.observe(101.6)
        self.c.tick(self.s, self.anchors, 101.6)
        self.bridge.command.assert_called_once_with("stop")

    def test_small_motion_does_not_replan_even_after_minimum_interval(self):
        self.observe(100)
        self.c.tick(self.s, self.anchors, 100)
        self.s["navigation"] = {"commandId": "route", "state": "running"}
        self.s["nearbyPlayers"] = [person(1, 7.5), person(2, 8.5)]
        self.observe(102)
        self.c.tick(self.s, self.anchors, 102)
        self.bridge.command.assert_not_called()

    def test_early_nearby_task_and_missing_safe_waypoint_favor_tasks(self):
        self.s["tasks"] = [task(1, 4), task(2, 20), task(3, 25)]
        self.observe(100)
        self.assertFalse(self.c.tick(self.s, self.anchors, 100))
        self.assertFalse(self.c.can_preempt_task(self.s, self.anchors, 100))
        self.s["tasks"] = [task(1, 20)]
        self.observe(101)
        self.assertFalse(self.c.tick(self.s, [a for a in self.anchors if a["kind"] == "task"], 101))
        self.bridge.navigate.assert_not_called()

    def test_largest_compact_group_and_distinct_live_members(self):
        self.s["nearbyPlayers"] = [person(i, x) for i, x in enumerate((1, 1.5, 7, 7.5, 8), 1)]
        self.s["nearbyPlayers"] += [person(1, 1), {**person(6, 1), "isDead": True}, {**person(7, 1), "inVent": True}]
        self.s["recentPlayers"] = [person(8, 1)]
        self.observe(100)
        self.assertEqual({p["playerId"] for p in self.c.group}, {3, 4, 5})
        self.assertEqual(len(self.c.estimates), 5)

    def test_task_batch_and_match_change_clear_temporary_commit_and_forecast(self):
        self.s["tasks"] = [task(x=2)]
        self.observe(100)
        self.assertTrue(self.c.task_priority)
        self.s["tasks"] = [task(x=20)]
        self.s["history"]["taskBatch"] = 2
        self.s["nearbyPlayers"] = []
        self.observe(101)
        self.assertFalse(self.c.task_priority)
        self.assertEqual(self.c.estimates, [])
        self.s["matchId"] = 2
        self.observe(102)
        self.assertEqual(self.c.started, 102)
        self.assertEqual(self.c.status()["followElapsedSeconds"], 0)


class AutopilotPredictiveTests(unittest.TestCase):
    setUp = fixture.ImpostorTests.setUp
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack
    start_pursuit = fixture.ImpostorTests.start_pursuit

    def crew(self, players=None):
        self.t["ownRole"] = {"roleType": "Crewmate", "isDead": False}
        self.t["nearbyPlayers"] = [person(1, 7), person(2, 8)] if players is None else players
        self.t["ui"]["ownTasks"] = [task()]
        self.anchors = [
            {"anchorId": "own.task", "kind": "task", "position": {"x": 20, "y": 0}},
            {"anchorId": "group.door", "kind": "trace_waypoint", "position": {"x": 7.5, "y": 0}},
        ]
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})

    def test_far_task_route_can_yield_to_new_group_but_open_minigame_cannot(self):
        self.crew([])
        command = self.tick(100)
        self.assertEqual(command["interactTaskId"], 1)
        self.ack("running")
        self.t["nearbyPlayers"] = [person(1, 7), person(2, 8)]
        self.t["ui"].update(minigameOpen=True, openMinigameTaskId=1)
        self.t["localPlayer"]["canMove"] = False
        self.assertEqual(self.tick(101)["commandId"], command["commandId"])
        self.t["ui"]["minigameOpen"] = False
        self.t["localPlayer"]["canMove"] = True
        # The task was already handed to the skill owner; a foreign route is protected.
        b.atomic(self.bridge.command_path, {"commandId": "foreign", "action": "navigate"})
        b.atomic(self.bridge.nav_path, {"commandId": "foreign", "state": "running"})
        self.assertEqual(self.tick(102)["commandId"], "foreign")

    def test_unopened_far_task_yields_then_waits_for_stop_before_following(self):
        self.crew([])
        self.tick(100)
        self.ack("running")
        self.t["nearbyPlayers"] = [person(1, 7), person(2, 8)]
        self.assertEqual(self.tick(102)["action"], "stop")
        self.assertEqual(self.tick(102.5)["action"], "stop")
        self.ack()
        self.assertEqual(self.tick(105)["targetAnchorId"], "group.door")

    def test_late_grouping_detaches_on_task_range_and_does_not_return(self):
        self.crew()
        self.assertEqual(self.tick(100)["targetAnchorId"], "group.door")
        self.ack("running")
        self.t["localPlayer"]["position"]["x"] = 10
        self.assertEqual(self.tick(101)["action"], "stop")
        self.ack()
        self.assertEqual(self.tick(102)["interactTaskId"], 1)
        self.ack("running")
        self.t["localPlayer"]["position"]["x"] = 9
        self.assertEqual(self.tick(103)["action"], "navigate")
        self.assertEqual(self.pilot.social.task_commit, (1, 0))

    def searching(self):
        self.t["nearbyPlayers"] = [person(1, 5, vx=2)]
        self.anchors.append({"anchorId": "search.door", "kind": "trace_waypoint", "position": {"x": 7, "y": 0}})
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        self.start_pursuit()
        self.t["nearbyPlayers"] = []
        self.assertEqual(self.tick(100.5)["action"], "stop")
        self.assertEqual(self.pilot.impostor.phase, "search")
        self.ack()
        command = self.tick(100.9)
        self.assertEqual(command["action"], "navigate")
        self.assertEqual(command["targetAnchorId"], "search.door")
        self.assertFalse(command["interactAfterArrival"])
        self.ack("running")
        return command

    def test_invisible_impostor_target_uses_bounded_search_and_never_kills(self):
        self.searching()
        self.t["ownRole"].update(canKill=True, killTargetId=1)
        self.tick(101.5)
        self.assertIsNone(self.pilot.impostor.kill_command)
        self.assertEqual(self.bridge.read()["nearbyPlayers"], [])
        self.assertEqual(self.tick(103.1)["action"], "stop")
        self.assertIsNone(self.pilot.impostor.search_plan)
        history = self.bridge.history()["timeline"]
        self.assertFalse(any(e["event"] == "impostor_kill_confirmed" for e in history))

    def test_reacquired_target_stops_prediction_before_live_pursuit(self):
        self.searching()
        self.t["nearbyPlayers"] = [person(1, 6.5, vx=1)]
        self.assertEqual(self.tick(101.2)["action"], "stop")
        self.ack()
        self.assertEqual(self.tick(101.6)["action"], "pursue_player")
        self.assertIsNone(self.pilot.impostor.search_plan)

    def test_search_cancels_for_witness_detection_failure_or_crime_scene(self):
        for case in ("witness", "detection", "crime"):
            with self.subTest(case=case):
                self.setUp()
                self.searching()
                if case == "witness":
                    self.t["recentPlayers"] = [{**person(3, 7), "ageSeconds": 1}]
                if case == "detection":
                    self.t["playerDetection"]["status"] = "camera_unavailable"
                if case == "crime":
                    self.t["crimeScenes"] = [{"x": 7, "y": 0, "avoidRadius": 3}]
                self.assertEqual(self.tick(101.3)["action"], "stop")
                self.assertIsNone(self.pilot.impostor.search_plan)

    def test_search_never_overwrites_an_unconfirmed_stop(self):
        self.t["nearbyPlayers"] = [person(1, 5, vx=2)]
        self.start_pursuit()
        self.t["nearbyPlayers"] = []
        stop = self.tick(100.5)
        self.assertEqual(self.tick(101)["commandId"], stop["commandId"])
        self.assertIsNone(self.pilot.impostor.pending)

    def test_route_file_parsed_once_until_changed_removed_or_recreated(self):
        path = self.bridge.root / "data/skeld-route-observations.json"
        original_load = b.load
        with patch.object(b, "load", wraps=original_load) as reader:
            for _ in range(20):
                self.bridge.route_anchors()
            self.assertEqual(sum(c.args[0] == path for c in reader.call_args_list), 1)
            self.anchors.append({"anchorId": "new", "position": {"x": 10, "y": 1}})
            b.atomic(path, {"anchors": self.anchors})
            os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
            self.assertEqual(len(self.bridge.route_anchors()), len(self.anchors))
            path.unlink()
            self.assertEqual(self.bridge.route_anchors(), [])
            b.atomic(path, {"anchors": self.anchors[:1]})
            self.assertEqual(len(self.bridge.route_anchors()), 1)


if __name__ == "__main__":
    unittest.main()
