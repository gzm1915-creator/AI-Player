"""Replay bounded disguises, one-time extra stops and room-first patrols."""
import copy
import unittest
from unittest.mock import patch
import test_impostor as fixture
import test_room_sweep as circuits

b = fixture.b


class CoverLimitTests(unittest.TestCase):
    setUp = fixture.ImpostorTests.setUp
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack
    anchor = staticmethod(circuits.CircuitReplayTests.anchor)
    circuit = circuits.CircuitReplayTests.circuit
    arrive = circuits.CircuitReplayTests.arrive

    def test_confirmed_cover_counts_once_and_pause_meeting_keep_allowance(self):
        self.t["nearbyPlayers"] = []
        self.tick(100)
        self.t["localPlayer"]["position"].update(self.anchors[0]["position"])
        self.ack()
        self.tick(102)
        imp = self.pilot.impostor
        self.assertEqual(imp.cover_counts, {"7:SwipeCard": 1})
        self.tick(103)
        self.assertEqual(imp.cover_counts, {"7:SwipeCard": 1})
        self.t["game"]["phase"] = "MEETING"
        self.tick(104)
        self.pilot.set_enabled(False)
        self.pilot.set_enabled(True)
        self.assertEqual(imp.cover_counts, {"7:SwipeCard": 1})
        self.t["game"].update(phase="TASKS", matchId=2)
        self.tick(110)
        self.assertEqual(imp.cover_counts, {})

    def test_failed_or_wrong_pose_arrival_does_not_consume_allowance(self):
        self.t["nearbyPlayers"] = []
        self.tick(100)
        self.ack("failed")
        self.tick(102)
        self.assertEqual(self.pilot.impostor.cover_counts, {})
        self.tick(200)
        self.ack()
        self.t["localPlayer"]["position"].update(x=100, y=100)
        self.tick(202)
        self.assertEqual(self.pilot.impostor.cover_counts, {})

    def test_task_limit_survives_step_changes_and_different_console(self):
        self.circuit()
        self.save()
        state = self.bridge.read()
        task = state["tasks"][0]
        imp = self.pilot.impostor
        key = imp.cover_key(task)
        imp.cover_counts[key] = 2
        task["step"] = 2
        task["locations"] = [self.anchors[1]["position"]]
        self.assertFalse(any(t.get("taskId") == task["taskId"] for _, t in imp.cover_targets(state, self.anchors, 1000)))

    def test_retired_cover_point_is_not_reused_as_a_patrol_stop(self):
        self.circuit()
        self.save()
        imp = self.pilot.impostor
        point = self.anchors[0]
        imp.cover_anchor_counts[point["anchorId"]] = 2
        self.assertIsNone(imp.patrol_target(self.bridge.read(), [point], 10000))

    def test_two_confirmed_patrols_follow_cover_before_next_cover(self):
        self.circuit()
        self.tick(100)
        self.arrive(102)
        for start, end in ((119, 122), (139, 142)):
            self.tick(start)
            self.assertEqual(self.pilot.impostor.intent, "patrol")
            self.arrive(end)
        self.assertEqual(self.pilot.impostor.patrols_since_cover, 2)

    def test_long_circuit_caps_covers_and_inspects_every_available_room(self):
        self.circuit()
        self.tick(100)
        visits = []
        with patch("random.choice", side_effect=lambda items: items[0]):
            for i in range(60):
                intent = self.pilot.impostor.intent
                room = self.arrive(102 + i * 22)["roomId"]
                visits.append((intent, room))
                self.tick(120 + i * 22)
        imp = self.pilot.impostor
        self.assertEqual({room for _, room in visits}, {a["roomId"] for a in self.anchors})
        self.assertTrue(imp.cover_counts)
        self.assertTrue(all(count <= 2 for count in imp.cover_counts.values()))
        self.assertGreater(sum(intent == "patrol" for intent, _ in visits), 40)
        # No repeat of an ordinary cover can take priority over a fresh room.
        seen = set()
        for intent, room in visits:
            if len(seen) < len({a["roomId"] for a in self.anchors}) and intent == "cover":
                self.assertNotIn(room, seen)
            seen.add(room)

    def extras(self):
        self.anchors = [
            {"anchorId": "skeld." + name + ".fix-wiring", "roomId": room, "kind": "task", "position": {"x": x, "y": 0}}
            for name, room, x in (("electrical", "Electrical", -8), ("admin", "Admin", 2),
                                  ("storage", "Storage", -2), ("cafeteria", "Cafeteria", 1))]
        self.anchors += [{"anchorId": "skeld.comms.upload-data", "roomId": "Communications", "kind": "task",
                          "position": {"x": 5, "y": -15}}]
        self.t["nearbyPlayers"] = []
        self.t["ui"]["ownTasks"] = []
        self.save()
        return self.bridge.read()

    def test_two_random_wiring_and_comms_exist_without_task_bar(self):
        state = self.extras()
        imp = self.pilot.impostor
        with patch("random.sample", side_effect=lambda items, count: items[:count]) as sample:
            choices = imp.cover_targets(state, self.anchors, 100)
            plan = list(imp.extra_cover_plan)
            self.assertEqual(len(choices), 3)
            self.assertEqual(len([a for a in plan if "fix-wiring" in a]), 2)
            self.assertIn("skeld.comms.upload-data", plan)
            self.assertTrue(all(task["source"] == "extra_cover_point" for _, task in choices))
            imp.cover_targets(state, self.anchors, 200)
            sample.assert_called_once()
        self.assertFalse(any("approach" in aid for aid in plan))

    def test_extra_points_have_one_shared_allowance_with_actual_task_bar(self):
        state = self.extras()
        imp = self.pilot.impostor
        choices = imp.cover_targets(state, self.anchors, 100)
        for anchor, task in choices:
            aid = anchor["anchorId"]
            imp.cover_anchor_counts[aid] = 1
            state["tasks"].append({"taskId": len(state["tasks"]), "taskType": task["taskType"],
                                   "locations": [anchor["position"]]})
        self.assertEqual(imp.cover_targets(state, self.anchors, 1000), [])
        self.assertIsNone(imp.patrol_target(state, [a for a, _ in choices], 1000))

    def test_extra_stops_never_open_real_minigames_and_keep_plan_on_resume(self):
        state = self.extras()
        imp = self.pilot.impostor
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        command = self.tick(100)
        self.assertEqual(imp.intent, "cover")
        self.assertEqual(imp.fake_task["source"], "extra_cover_point")
        self.assertFalse(command["interactAfterArrival"])
        self.assertNotIn("interactTaskId", command)
        self.arrive(102)
        self.assertEqual(imp.cover_anchor_counts[command["targetAnchorId"]], 1)
        plan = list(imp.extra_cover_plan)
        self.pilot.set_enabled(False)
        self.pilot.set_enabled(True)
        self.assertEqual(imp.extra_cover_plan, plan)

    def test_medical_entry_is_a_walk_through_check_without_a_scan_stop(self):
        self.circuit()
        self.t["ui"]["ownTasks"] = []
        self.anchors = [{"anchorId": "medical.entry", "roomId": "MedBay", "kind": "waypoint", "position": {"x": -9, "y": -4}}]
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": self.anchors})
        self.assertEqual(self.tick(100)["targetAnchorId"], "medical.entry")
        self.arrive(102)
        self.assertEqual(self.pilot.impostor.sweep.current, "MedBay")
        self.assertEqual(self.pilot.impostor.patrol_stop_seconds, 0)
        self.assertEqual(self.pilot.impostor.cover_counts, {})
