"""Replay missed single-player opportunities and local patrol starvation."""
import unittest
from unittest.mock import patch
import test_impostor as fixture
import test_room_sweep as circuit_fixture

b = fixture.b


class LoneOpportunityTests(unittest.TestCase):
    setUp = fixture.ImpostorTests.setUp
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack

    def target(self):
        self.t["nearbyPlayers"] = [{"playerId": 1, "x": 6, "y": 0}]
        self.t["ownRole"].update(canKill=False, killTargetId=None,
            killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "ready": False}])

    def test_first_sighting_approaches_before_opening_fake_task(self):
        self.target()
        command = self.tick(100)
        self.assertEqual(command["action"], "pursue_player")
        self.assertEqual(command["targetNumber"], 1)
        self.assertFalse(self.pilot.impostor.opening_done)
        self.assertIsNone(self.pilot.impostor.kill_command)

    def test_first_cover_route_stops_then_hands_over_to_live_pursuit(self):
        self.t["nearbyPlayers"] = []
        route = self.tick(100)
        self.assertEqual(route["action"], "navigate")
        self.ack("running")
        self.target()
        self.assertEqual(self.tick(101)["action"], "stop")
        self.assertIsNone(self.pilot.impostor.pending)
        self.ack()
        chase = self.tick(102)
        self.assertEqual(chase["action"], "pursue_player")
        self.ack("running")
        # Native distance alone is insufficient while observation is not ready.
        self.t["ownRole"].update(canKill=True, killTargetId=1)
        self.assertEqual(self.tick(103)["commandId"], chase["commandId"])
        self.t["ownRole"]["killSafety"][0]["ready"] = True
        self.assertEqual(self.tick(104)["action"], "impostor_kill")
        self.assertFalse(any(e["event"] == "impostor_kill_confirmed"
                             for e in self.bridge.history()["timeline"]))

    def test_cover_hold_yields_to_approach_without_restarting_its_timer(self):
        self.t["nearbyPlayers"] = []
        self.tick(100)
        self.ack()
        self.tick(102)
        self.assertGreater(self.pilot.impostor.dwell_until, 103)
        self.target()
        self.assertEqual(self.tick(103)["action"], "pursue_player")
        self.assertEqual(self.pilot.impostor.dwell_until, 0)

    def test_witness_during_approach_stops_before_any_strike(self):
        self.target()
        self.tick(100)
        self.ack("running")
        self.t["nearbyPlayers"].append({"playerId": 3, "x": 5, "y": 0})
        self.t["ownRole"].update(canKill=True, killTargetId=1,
            killSafety=[{"targetPlayerId": 1, "ready": True}])
        self.assertEqual(self.tick(101)["action"], "stop")
        self.assertIsNone(self.pilot.impostor.kill_command)

    def test_single_target_is_not_approached_during_cooldown(self):
        self.target()
        self.t["ownRole"]["killCooldown"] = 8
        self.assertNotEqual(self.tick(100).get("action"), "pursue_player")


class GlobalRoomCoverageTests(unittest.TestCase):
    setUp = fixture.ImpostorTests.setUp
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack
    anchor = staticmethod(circuit_fixture.CircuitReplayTests.anchor)
    circuit = circuit_fixture.CircuitReplayTests.circuit
    arrive = circuit_fixture.CircuitReplayTests.arrive

    def test_unseen_remote_room_outranks_repeated_neighbor(self):
        self.circuit()
        imp = self.pilot.impostor
        for room in ("Electrical", "Cafeteria", "Weapons", "O2", "Navigation", "Storage"):
            imp.sweep.arrive(room)
        near = self.anchor("Electrical", 7)
        far = self.anchor("Reactor", -30)
        self.save()
        self.assertEqual(imp.patrol_target(self.bridge.read(), [near, far], 100), far)

    def test_adjacent_fake_task_cannot_starve_unseen_rooms(self):
        self.circuit()
        self.tick(100)
        self.arrive(102)
        imp = self.pilot.impostor
        # Both old fake-task rooms have already been visited; fresh rooms exist.
        imp.sweep.visits.update(Admin=1, Weapons=1)
        command = self.tick(109)
        self.assertEqual(imp.intent, "patrol")
        room = next(a["roomId"] for a in self.anchors if a["anchorId"] == command["targetAnchorId"])
        self.assertNotIn(room, ("Storage", "Admin", "Weapons"))

    def test_long_circuit_covers_remote_rooms_with_only_local_fake_tasks(self):
        self.circuit()
        self.tick(100)
        rooms = []
        with patch("random.choice", side_effect=lambda items: items[0]):
            for i in range(30):
                rooms.append(self.arrive(102 + i * 20)["roomId"])
                self.tick(119 + i * 20)
        self.assertEqual(set(rooms), {a["roomId"] for a in self.anchors})
        self.assertGreaterEqual(len(set(rooms[:15])), 9)
        for i, room in enumerate(rooms):
            if room in rooms[:i]:
                previous = max(j for j in range(i) if rooms[j] == room)
                self.assertGreaterEqual(len(set(rooms[previous + 1:i])), 4)
        visits = self.pilot.impostor.sweep.status()["roomVisits"]
        self.assertTrue(all(visits.get(room, 0) > 0 for room in set(rooms)))
        self.pilot.set_enabled(False)
        self.pilot.set_enabled(True)
        self.assertEqual(self.pilot.impostor.sweep.status()["roomVisits"], visits)
