"""Replay a roaming impostor: leave an area, join moving traffic, then detach."""
import importlib
import unittest
from unittest.mock import patch
import test_impostor as fixture

b=fixture.b
roaming=importlib.import_module("impostor_fixture.impostor_roaming")
sweeps=importlib.import_module("impostor_fixture.room_sweep")


def person(pid,x,vx=1,visible=True):
    return {"playerId":pid,"x":x,"y":0,"velocityX":vx,"velocityY":0,
            "speed":abs(vx),"visibleNow":visible,"suspectedTaskStop":False}


def state(x=0):
    return {"connected":True,"self":{"playerId":0,"position":{"x":x,"y":0}},
            "playerDetection":{"status":"ok"},"nearbyPlayers":[],"crimeScenes":[]}


class RangeTests(unittest.TestCase):
    def test_different_rooms_in_the_same_area_do_not_reset_the_deadline(self):
        policy=roaming.PatrolRange()
        for now in range(66):
            sample=state(-12 if now%2 else -20)
            policy.observe(sample,now)
        self.assertTrue(policy.due)
        self.assertFalse(policy.accepts({"position":{"x":-8,"y":8}}))
        self.assertTrue(policy.accepts({"position":{"x":14,"y":0}}))

    def test_a_brief_boundary_crossing_does_not_renew_local_roaming(self):
        policy=roaming.PatrolRange()
        for now in range(65): policy.observe(state(-8),now)
        policy.observe(state(-6),65)
        policy.observe(state(-8),66)
        self.assertTrue(policy.due)
        self.assertEqual(policy.area,"west")

    def test_a_sustained_crossing_opens_a_new_area_budget(self):
        policy=roaming.PatrolRange()
        policy.observe(state(-10),0)
        for now in range(1,5): policy.observe(state(0),now)
        self.assertEqual(policy.area,"central")
        self.assertFalse(policy.due)

    def test_disconnection_or_unobserved_gap_is_not_proof_of_remaining_in_an_area(self):
        policy=roaming.PatrolRange()
        policy.observe(state(),0)
        policy.observe(state(),100)
        self.assertFalse(policy.due)
        policy.observe({"connected":False},101)
        self.assertEqual(policy.elapsed,0)


class TrafficTests(unittest.TestCase):
    def test_only_a_moving_group_of_two_visible_others_can_start(self):
        flow=roaming.TrafficFollow()
        flow.observe(state(),[person(1,5)],100)
        self.assertFalse(flow.can_start(100))
        flow.observe(state(),[person(1,5),person(2,6,visible=False)],101)
        self.assertFalse(flow.can_start(101))
        flow.observe(state(),[person(1,5),person(2,6)],102)
        self.assertTrue(flow.can_start(102))

    def test_stationary_task_players_and_opposing_traffic_are_not_followed(self):
        flow=roaming.TrafficFollow()
        for people in ([person(1,5,0),person(2,6,0)],[person(1,5,1),person(2,6,-1)]):
            flow.observe(state(),people,100)
            self.assertFalse(flow.can_start(100))

    def test_a_group_spreading_out_is_no_longer_a_coherent_flow(self):
        flow=roaming.TrafficFollow()
        flow.observe(state(),[person(1,5),person(2,6)],100)
        with patch("random.uniform",return_value=18): flow.begin(100)
        flow.observe(state(),[person(1,4),person(2,12)],101)
        self.assertIsNotNone(flow.end_reason(101,roaming.PatrolRange()))

    def test_group_changes_do_not_extend_the_duration_or_cooldown(self):
        flow=roaming.TrafficFollow()
        flow.observe(state(),[person(1,5),person(2,6)],100)
        with patch("random.uniform",return_value=18): flow.begin(100)
        flow.observe(state(),[person(1,7),person(2,8),person(3,8)],110)
        self.assertEqual(flow.until,118)
        self.assertIsNotNone(flow.end_reason(118,roaming.PatrolRange()))
        with patch("random.uniform",return_value=40): flow.finish(118,"结束")
        self.assertFalse(flow.can_start(140))
        self.assertTrue(flow.can_start(159))

    def test_flow_destination_respects_recent_rooms_and_old_crime_scenes(self):
        flow=roaming.TrafficFollow()
        flow.observe(state(),[person(1,5),person(2,6)],100)
        sweep=sweeps.RoomSweep(); sweep.arrive("Weapons")
        anchors=[{"anchorId":"recent","kind":"waypoint","roomId":"Weapons","position":{"x":6,"y":0}},
                 {"anchorId":"body","kind":"waypoint","position":{"x":7,"y":0}},
                 {"anchorId":"safe","kind":"waypoint","position":{"x":8,"y":0}}]
        sample=state(); sample["crimeScenes"]=[{"x":7,"y":0,"avoidRadius":.5}]
        choice=flow.destination(sample,anchors,sweep,roaming.PatrolRange(),{},100)
        self.assertEqual(choice["anchorId"],"safe")


class ImpostorFlowReplayTests(unittest.TestCase):
    setUp=fixture.ImpostorTests.setUp
    save=fixture.ImpostorTests.save
    ack=fixture.ImpostorTests.ack

    def tick(self,now):
        self.save()
        with patch("time.monotonic",return_value=now),patch("random.uniform",side_effect=lambda a,b:(a+b)/2):
            self.pilot.tick(self.bridge.read())
        return b.load(self.bridge.command_path)

    def prepare(self):
        self.pilot.role_identity=(self.t["sessionId"],1,"Impostor")
        imp=self.pilot.impostor
        imp.identity=(self.t["sessionId"],1,"TheSkeld"); imp.opening_done=True
        self.t["ownRole"].update(canKill=False,killCooldown=20)
        self.t["nearbyPlayers"]=[person(1,5),person(3,6)]
        self.anchors=[{"anchorId":"hall.flow","kind":"waypoint","roomId":"Hallway_BigY","position":{"x":7,"y":0}},
                      {"anchorId":"hall.next","kind":"waypoint","roomId":"Hallway_BigY","position":{"x":10,"y":0}},
                      {"anchorId":"room.west","kind":"waypoint","roomId":"Reactor","position":{"x":-20,"y":0}},
                      {"anchorId":"local.task","kind":"task","roomId":"Storage","position":{"x":1,"y":0}}]
        b.atomic(self.bridge.root/"data/skeld-route-observations.json",{"anchors":self.anchors})
        return imp

    def test_impostor_submits_a_non_task_route_to_a_real_moving_group(self):
        imp=self.prepare()
        command=self.tick(100)
        self.assertEqual(imp.intent,"flow")
        self.assertEqual(command["action"],"navigate")
        self.assertFalse(command["interactAfterArrival"])
        self.assertNotIn("targetNumber",command)
        self.assertEqual(imp.traffic.until,118)

    def test_a_group_stopping_at_a_task_ends_following_and_cancels_owned_route(self):
        imp=self.prepare(); self.tick(100); self.ack("running")
        self.t["nearbyPlayers"]=[person(1,5,0),person(3,6,0)]
        command=self.tick(102)
        self.assertEqual(command["action"],"stop")
        self.assertFalse(imp.traffic.active)
        self.assertIsNone(imp.pending)
        self.assertTrue(self.pilot.enabled)

    def test_replanning_tracks_group_motion_without_restarting_its_deadline(self):
        imp=self.prepare(); self.tick(100); self.ack("running")
        self.t["nearbyPlayers"]=[person(1,8),person(3,9)]
        self.assertEqual(self.tick(102)["action"],"stop")
        self.ack()
        command=self.tick(104)
        self.assertEqual(command["targetAnchorId"],"hall.next")
        self.assertEqual(imp.traffic.until,118)

    def test_expired_area_forces_a_different_area_even_with_a_nearby_fake_task(self):
        imp=self.prepare()
        self.t["nearbyPlayers"]=[]
        for now in range(100,166): imp.range_policy.observe(state(0),now)
        command=self.tick(166)
        self.assertNotEqual(command["targetAnchorId"],"local.task")
        self.assertEqual(command["targetAnchorId"],"room.west")
        self.assertTrue(imp.range_policy.due)

    def test_expired_area_stops_a_running_local_patrol_before_selecting_remote(self):
        imp=self.prepare(); self.t["nearbyPlayers"]=[]
        self.save()
        imp.route(self.bridge.read(),self.anchors[-1],"patrol",100)
        self.ack("running")
        for now in range(100,166): imp.range_policy.observe(state(0),now)
        self.assertEqual(self.tick(166)["action"],"stop")
        self.assertTrue(self.pilot.enabled)

    def test_actual_kill_receipt_still_takes_priority_over_following(self):
        imp=self.prepare()
        self.t["nearbyPlayers"]=[]
        imp.kill_command="native-kill"
        self.t["impostorAction"]={"commandId":"native-kill","state":"completed","position":{"x":0,"y":0}}
        self.tick(100)
        self.assertEqual(imp.phase,"escape")
        self.assertFalse(imp.traffic.active)

    def test_owned_follow_failure_cools_target_and_does_not_disable_autoplay(self):
        imp=self.prepare(); command=self.tick(100); self.ack("failed")
        self.tick(102)
        self.assertGreater(imp.visited[command["targetAnchorId"]],102)
        self.assertEqual(imp.traffic.failures,1)
        self.assertTrue(self.pilot.enabled)

    def test_following_ends_at_its_deadline_even_if_the_group_is_still_moving(self):
        imp=self.prepare(); self.tick(100); self.ack("running")
        command=self.tick(118)
        self.assertEqual(command["action"],"stop")
        self.assertFalse(imp.traffic.active)
        self.assertTrue(self.pilot.enabled)
