import asyncio
import copy
import importlib.util
import json
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]
test_package = types.ModuleType("among_us_test")
test_package.__path__ = [str(PLUGIN)]
sys.modules["among_us_test"] = test_package
spec = importlib.util.spec_from_file_location("among_us_test.bridge", PLUGIN / "bridge.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="neko-portable-")
        self.addCleanup(self.tmp.cleanup)
        self.game = Path(self.tmp.name) / "custom game path 游戏"
        self.root = self.game / "BepInEx/plugins/AUTelemetryProbe"
        self.root.mkdir(parents=True)
        self.bridge = b.Bridge(self.game)
        self.session = "12345678-1234-1234-1234-123456789012"
        self.t = {
            "sentAtUtc": self.now(),
            "sessionId": self.session,
            "game": {"phase": "MEETING", "mapId": "TheSkeld"},
            "ui": {"meetingOpen": True},
            "localPlayer": {"playerId": 0, "canMove": True},
            "nearbyPlayers": [{"playerId": 1, "playerName": "小红", "x": 1, "y": 2}],
        }
        self.m = {"updatedAtUtc": self.now(), "meetingSequence": 1, "meetingOpen": True, "voteGate": {"ready": True}}
        self.save()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def save(self):
        b.atomic(self.root / "latest.json", self.t)
        b.atomic(self.bridge.meeting_path, self.m)

    def chat(self, events):
        p = self.root / "memory" / self.session[:8] / "meetings/meeting-0001/transcript.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in events), encoding="utf-8")

    def test_foreign_user_paths_and_meeting_send(self):
        result = self.bridge.say("你好小红", self.session, 1)
        self.assertEqual(result["status"], "submitted")
        command = b.load(self.bridge.command_path)
        self.assertEqual(command["chatText"], "你好小红")
        self.assertEqual(command["action"], "send_chat")

    def test_stale_telemetry_cannot_send(self):
        self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.save()
        with self.assertRaises(ValueError):
            self.bridge.say("旧消息", self.session, 1)
        self.assertFalse(self.bridge.command_path.exists())

    def test_old_meeting_or_session_cannot_send(self):
        for session, meeting in [(self.session, 0), ("different", 1)]:
            with self.assertRaises(ValueError):
                self.bridge.say("旧消息", session, meeting)

    def test_text_only_ai_message_binds_fresh_meeting(self):
        self.bridge.say("自动绑定当前会议")
        command = b.load(self.bridge.command_path)
        self.assertEqual(command["expectedSessionId"], self.session)
        self.assertEqual(command["expectedMeetingId"], 1)
        self.assertIn("issuedAtUtc", command)

    def test_disconnect_invalidates_previous_location_once(self):
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 1, "y": 2}
        self.save()
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.save()
        monitor.tick()
        payload = json.loads(calls[-1]["parts"][0]["text"].split("\n", 1)[1])
        self.assertFalse(payload["connected"])
        self.assertNotIn("position", payload["self"])
        self.assertEqual(payload["playerDetection"]["status"], "telemetry_stale")
        self.assertEqual(payload["location"]["source"], "unavailable")
        count = len(calls)
        monitor.tick()
        self.assertEqual(len(calls), count)

    def test_reconnect_overrides_stale_dialogue_with_fresh_facts(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 8.32, "y": 0.84}
        self.save()
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        self.assertTrue(calls[0]["parts"][0]["text"].startswith("本次游戏遥测连接正常（connected=true）"))
        self.assertNotIn("connected=false", calls[0]["parts"][0]["text"])
        self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.save()
        monitor.tick()
        self.assertFalse(monitor.health()["lastPushedObservation"]["connected"])
        self.t["sentAtUtc"] = self.now()
        self.save()
        with patch.object(b.time, "monotonic", return_value=monitor.last_push + 7):
            monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        payload = json.loads(calls[-1]["parts"][0]["text"].split("\n", 1)[1])
        self.assertIn("telemetry_reconnected", payload["reviewReasons"])
        self.assertTrue(payload["connected"])
        self.assertTrue(monitor.health()["lastPushedObservation"]["connected"])

    def test_closed_meeting_cannot_send(self):
        self.m["meetingOpen"] = False
        self.save()
        with self.assertRaises(ValueError):
            self.bridge.say("迟到消息", self.session, 1)

    def test_configured_protocol_paths(self):
        cfg = self.game / "BepInEx/config/com.dsh.autelprobe.cfg"
        cfg.parent.mkdir(parents=True)
        custom = self.game / "other folder/command.json"
        cfg.write_text("[InternalNavigation]\nCommandPath = " + str(custom), encoding="utf-8-sig")
        bridge = b.Bridge(self.game)
        bridge.say("路径测试", self.session, 1)
        self.assertTrue(custom.exists())

    def test_vote_gate(self):
        self.m["voteGate"]["ready"] = False
        self.save()
        with self.assertRaises(ValueError):
            self.bridge.vote(-1, self.session, 1)

    def test_no_self_reply_loop(self):
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kwargs: calls.append(kwargs))
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        self.chat(
            [{"event": "chat", "index": 1, "speakerId": 0, "speaker": "Neko", "text": "自己", "isLocalPlayer": True}]
        )
        monitor.last_push = monitor.last_response = 0
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")

    def test_other_player_chat_and_cooldown(self):
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kwargs: calls.append(kwargs))
        monitor.tick()
        self.chat(
            [
                {
                    "event": "chat",
                    "index": 1,
                    "speakerId": 1,
                    "speaker": "小红",
                    "text": "你在哪里",
                    "isLocalPlayer": False,
                }
            ]
        )
        monitor.last_response = time.monotonic()  # A preceding reply is still cooling down.
        monitor.tick()
        self.assertEqual(len(calls), 1)
        monitor.last_response = 0
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "respond")
        self.assertIn("你在哪里", calls[-1]["parts"][0]["text"])
        monitor.last_response = monitor.last_push = 0
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")

    def test_movement_uses_packaged_route(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.save()
        b.atomic(
            self.root / "data/skeld-route-observations.json",
            {"anchors": [{"anchorId": "cafeteria.test", "roomId": "Cafeteria"}]},
        )
        self.bridge.navigate("cafeteria.test")
        command = b.load(self.bridge.command_path)
        self.assertEqual(command["action"], "navigate")
        self.assertTrue(Path(command["routePath"]).is_relative_to(self.game))
        self.assertTrue(b.load(self.bridge.control_path)["autoNavigation"])

    def test_task_navigation_requests_interaction_at_task_location(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.save()
        b.atomic(
            self.root / "data/skeld-route-observations.json",
            {"anchors": [{"anchorId": "cafeteria.task", "roomId": "Cafeteria"}]},
        )
        self.bridge.navigate("cafeteria.task", {"x": 2.25, "y": -1.5})
        command = b.load(self.bridge.command_path)
        self.assertTrue(command["interactAfterArrival"])
        self.assertEqual(command["waypointTolerance"], 0.18)
        self.assertEqual(command["finalTolerance"], 0.10)
        self.assertEqual(command["taskStopRadius"], 0.25)
        self.assertFalse(command["stopOnTaskButton"])
        self.assertEqual((command["interactTargetX"], command["interactTargetY"]), (2.25, -1.5))

    def test_unknown_anchor_cannot_write(self):
        with self.assertRaises(ValueError):
            self.bridge.navigate("invented")
        self.assertFalse(self.bridge.command_path.exists())

    def test_plugin_facade_loads_with_documented_sdk_shape(self):
        # This is a contract fixture, not a claim to run N.E.K.O.'s real SDK.
        sdk = types.ModuleType("plugin.sdk.plugin")

        class Base:
            def __init__(self, ctx):
                self.config = ctx.config
                self.push_message = ctx.push_message
                self.data_path = lambda: ctx.data_path

        sdk.NekoPluginBase = Base
        sdk.neko_plugin = lambda cls: cls
        sdk.Ok = lambda value: value
        sdk.ui = types.SimpleNamespace(context=lambda **_: lambda fn: fn, action=lambda **_: lambda fn: fn)
        for decorator in ("llm_tool", "plugin_entry", "lifecycle"):
            setattr(sdk, decorator, lambda **kwargs: lambda fn: fn)
        package = types.ModuleType("among_us_neko_fixture")
        package.__path__ = [str(PLUGIN)]
        with patch.dict(sys.modules, {"plugin.sdk.plugin": sdk, "among_us_neko_fixture": package}):
            fspec = importlib.util.spec_from_file_location(
                "among_us_neko_fixture", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
            )
            mod = importlib.util.module_from_spec(fspec)
            fspec.loader.exec_module(mod)

            async def config_dump(**_):
                return {"among_us": {"game_dir": str(self.game)}}

            obj = mod.AmongUsNekoPlugin(
                types.SimpleNamespace(
                    config=types.SimpleNamespace(dump=config_dump),
                    push_message=lambda **_: None,
                    data_path=Path(self.tmp.name) / "private",
                )
            )
            asyncio.run(obj.startup())
            self.addCleanup(obj.shutdown)
            state = asyncio.run(obj.among_us_state())
            self.assertTrue(state["connected"])
            sent = asyncio.run(obj.among_us_say(text="Neko测试", session_id=self.session, meeting_id=1))
            self.assertEqual(sent["status"], "submitted")
            obj.bridge.last_command_at = 0
            independent = asyncio.run(obj.send_meeting(text="独立AI入口测试"))
            self.assertEqual(independent["status"], "submitted")
            self.assertEqual(b.load(obj.bridge.command_path)["chatText"], "独立AI入口测试")
            with patch.object(obj.bridge, "vote", return_value={"status": "submitted"}) as cast:
                vote = asyncio.run(obj.vote(player_id=-1, session_id=self.session, meeting_id=1))
                self.assertEqual(vote["status"], "submitted")
                cast.assert_called_once_with(-1, self.session, 1)
            asyncio.run(obj.control(operation="vote_off", ui_token=obj.ui_token))
            self.assertFalse(obj.monitor.auto_vote)
            self.assertFalse(b.load(obj._settings_path())["auto_vote"])
            asyncio.run(obj.control(operation="vote_on", ui_token=obj.ui_token))
            self.assertTrue(obj.monitor.auto_vote)
            self.assertIn("error", asyncio.run(obj.kill()))
            self.assertIn("error", asyncio.run(obj.sabotage()))
            with self.assertRaises(ValueError):
                asyncio.run(obj.control(operation="start_all", ui_token=obj.ui_token))
            self.assertFalse(obj.monitor.pilot.enabled)
            with patch.object(obj.monitor.solver, "preflight"):
                asyncio.run(obj.control(operation="start_all", ui_token=obj.ui_token))
            self.assertTrue(obj.monitor.solver.enabled)
            self.assertFalse(b.load(obj.bridge.control_path)["minigameControl"])
            asyncio.run(obj.control(operation="stop", ui_token=obj.ui_token))
            self.assertFalse(b.load(obj.bridge.control_path)["minigameControl"])
            obj.monitor.paused = True
            self.t["game"]["phase"] = "TASKS"
            self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 0, "y": 0}
            self.t["sentAtUtc"] = self.now()
            self.save()
            dashboard = asyncio.run(obj.dashboard())
            record_id = dashboard["game"]["history"]["recordId"]
            with self.assertRaises(ValueError):
                asyncio.run(obj.control(operation="history_clear_all", ui_token="wrong-token"))
            with self.assertRaises(ValueError):
                asyncio.run(
                    obj.control(
                        operation="history_clear_current", ui_token=obj.ui_token, history_record_id="previous-record"
                    )
                )
            asyncio.run(obj.control(operation="history_pause", ui_token=obj.ui_token))
            self.assertFalse(b.load(obj._settings_path())["history_enabled"])
            self.assertEqual(asyncio.run(obj.among_us_history())["lastSeenPlayers"], [])
            asyncio.run(obj.control(operation="history_resume", ui_token=obj.ui_token))
            result = asyncio.run(
                obj.control(operation="history_clear_current", ui_token=obj.ui_token, history_record_id=record_id)
            )
            self.assertEqual(result["scope"], "displayed_match")
            self.assertNotEqual(obj.bridge.journal.doc["recordId"], record_id)

    def test_failed_push_retries_meeting_event(self):
        self.chat(
            [{"event": "chat", "index": 1, "speakerId": 1, "speaker": "小红", "text": "你好", "isLocalPlayer": False}]
        )
        calls = []

        def push(**kwargs):
            if not calls:
                calls.append("failed")
                raise ConnectionError("host temporarily disconnected")
            calls.append(kwargs)

        monitor = b.Monitor(self.bridge, push)
        with self.assertRaises(ConnectionError):
            monitor.tick()
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "respond")
        self.assertEqual(monitor.push_count, 1)

    def test_move_completion_updates_context_silently_once(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.save()
        b.atomic(self.bridge.nav_path, {"commandId": "neko-test", "state": "running"})
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        b.atomic(self.bridge.nav_path, {"commandId": "neko-test", "state": "completed"})
        monitor.last_push -= 2
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        self.assertIn("navigation_completed", monitor.health()["lastPushedObservation"]["reviewReasons"])
        count = len(calls)
        monitor.tick()
        self.assertEqual(len(calls), count)

    def test_unchanged_telemetry_does_not_flood_context(self):
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        monitor.last_push = time.monotonic() - 6
        self.t["sentAtUtc"] = self.now()
        self.save()
        monitor.tick()
        self.assertEqual(len(calls), 1)

    def test_background_recovers_from_push_exception(self):
        calls = []

        def push(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise ConnectionError("test disconnect")

        monitor = b.Monitor(self.bridge, push)
        monitor.start()
        try:
            deadline = time.monotonic() + 3
            while monitor.push_count == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(monitor.health()["alive"])
            self.assertEqual(monitor.error_count, 1)
            self.assertEqual(monitor.push_count, 1)
        finally:
            monitor.close()
        self.assertFalse(monitor.health()["alive"])

    def test_short_encounter_is_persisted_and_queryable_after_leaving(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 0, "y": 0}
        self.t["ui"]["meetingOpen"] = False
        self.save()
        self.bridge.read()
        self.t["nearbyPlayers"] = []
        self.t["sentAtUtc"] = self.now()
        self.save()
        self.bridge.read()
        history = self.bridge.history("encounters")
        self.assertEqual(history["lastSeenPlayers"][0]["player"]["playerName"], "小红")
        self.assertEqual(len(history["encounters"]), 1)
        self.bridge.journal.flush()
        # A plugin restart and a closed game must not erase the last known history.
        restored = b.Bridge(self.game)
        self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.save()
        self.assertEqual(restored.history("encounters")["lastSeenPlayers"][0]["player"]["playerId"], 1)

    def test_three_bot_sightings_reach_ai_dashboard_and_survive_restart(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 0, "y": 0}
        self.t["ui"]["meetingOpen"] = False
        self.t["nearbyPlayers"] = []
        self.save()
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        for pid, name in enumerate(("假人一", "假人二", "假人三"), 1):
            self.t["nearbyPlayers"] = [{"playerId": pid, "playerName": name, "colorName": "红", "x": pid, "y": 2}]
            self.t["playerDetection"] = {"status": "ok", "cameraName": "World", "visible": 1}
            self.t["sentAtUtc"] = self.now()
            self.save()
            monitor.last_push = 0
            monitor.tick()
            self.assertEqual(calls[-1]["ai_behavior"], "read")
            self.assertIn(name, calls[-1]["parts"][0]["text"])
        state = self.bridge.read()  # Same data source as the hosted panel.
        self.assertEqual(state["playerDetection"]["cameraName"], "World")
        self.assertEqual(len(state["history"]["lastSeenPlayers"]), 3)
        self.assertEqual(len(state["history"]["encounters"]), 3)
        self.t["nearbyPlayers"] = []
        self.t["sentAtUtc"] = self.now()
        self.save()
        self.assertEqual(self.bridge.read()["nearbyPlayers"], [])
        self.bridge.journal.flush()
        restored = b.Bridge(self.game).read()
        self.assertEqual(restored["nearbyPlayers"], [])
        self.assertEqual({p["player"]["playerId"] for p in restored["history"]["lastSeenPlayers"]}, {1, 2, 3})
        self.assertTrue(all(p["lastSeenAt"] and p["selfPosition"] for p in restored["history"]["lastSeenPlayers"]))

    def test_task_evidence_and_meeting_route_are_not_confused(self):
        from among_us_test.journal import Journal

        journal = Journal(Path(self.tmp.name) / "journal-test")
        base = {
            "connected": True,
            "phase": "TASKS",
            "sessionId": self.session,
            "matchId": 1,
            "mapId": "TheSkeld",
            "self": {"position": {"x": 0, "y": 0}},
            "nearbyPlayers": [],
            "tasks": [{"taskId": 1, "taskType": "FixWiring", "step": 0, "complete": False}],
        }

        def observe(t, **patches):
            state = {
                **copy.deepcopy(base),
                **patches,
                "telemetryAt": (datetime.now(timezone.utc) - timedelta(seconds=100 - t)).isoformat(),
            }
            journal.observe(state)

        observe(0)
        observe(1, self={"position": {"x": 1, "y": 0}})
        observe(2, tasks=[{"taskId": 1, "taskType": "FixWiring", "step": 1, "complete": False}])
        observe(3, tasks=[])  # Missing is not complete.
        observe(4, tasks=[{"taskId": 1, "taskType": "FixWiring", "step": 3, "complete": True}])
        observe(5, phase="MEETING", self={"position": {"x": 100, "y": 0}})
        observe(
            6,
            self={"position": {"x": 100, "y": 0}},
            tasks=[{"taskId": 1, "taskType": "FixWiring", "step": 3, "complete": True}],
        )
        history = journal.query()
        self.assertEqual(
            [e["event"] for e in history["taskEvents"]], ["task_first_observed", "task_progress", "task_completed"]
        )
        self.assertLess(history["distance"], 10)  # Meeting teleport never counts as walking.
        self.assertTrue(history["route"][-1]["segmentStart"])
        previous_count = history["routePointCount"]
        observe(7, matchId=2)
        self.assertEqual(journal.query()["routePointCount"], 1)
        self.assertGreater(previous_count, 1)

    def test_duplicate_polling_does_not_duplicate_records(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["localPlayer"]["position"] = {"x": 0, "y": 0}
        self.save()
        a = self.bridge.read()["history"]["eventCount"]
        for _ in range(5):
            self.assertEqual(self.bridge.read()["history"]["eventCount"], a)

    def test_encounter_silently_updates_ai_before_periodic_context_update(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["localPlayer"]["position"] = {"x": 0, "y": 0}
        self.t["ui"]["meetingOpen"] = False
        self.t["nearbyPlayers"] = []
        self.save()
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        self.t["nearbyPlayers"] = [{"playerId": 6, "playerName": "假人6", "x": 1, "y": 2}]
        self.t["sentAtUtc"] = self.now()
        self.save()
        monitor.last_push -= 2
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        self.assertIn("假人6", calls[-1]["parts"][0]["text"])

    def test_task_history_update_waits_only_for_short_coalescing_window_and_is_silent(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 0, "y": 0}
        self.t["ui"].update(
            meetingOpen=False,
            ownTasks=[{"taskId": 1, "taskType": "FixWiring", "step": 0, "maxStep": 3, "complete": False}],
        )
        self.t["nearbyPlayers"] = []
        self.save()
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        self.t["ui"]["ownTasks"][0]["step"] = 1
        self.t["sentAtUtc"] = self.now()
        self.save()
        monitor.tick()  # Event is retained during the 0.5s coalescing window.
        self.assertEqual(len(calls), 1)
        with patch.object(b.time, "monotonic", return_value=monitor.last_push + 0.6):
            monitor.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        self.assertIn("0→1/3", calls[-1]["parts"][0]["text"])
        self.assertIn("action_record_updated", monitor.health()["lastPushedObservation"]["reviewReasons"])

    def test_user_clear_and_history_pause_notify_ai_silently_even_when_listener_paused(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 0, "y": 0}
        self.t["ui"]["meetingOpen"] = False
        self.save()
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        monitor.paused = True
        self.bridge.journal.clear()
        monitor.history_changed("history_clear_current")
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        self.assertIn("清理前", calls[-1]["parts"][0]["text"])
        data = json.loads(calls[-1]["parts"][0]["text"].split("\n", 1)[1])
        self.assertEqual(data["history"]["lastSeenPlayers"], [])
        self.bridge.journal.set_enabled(False)
        monitor.history_changed("history_pause")
        self.assertIn("用户已暂停行动记录", calls[-1]["parts"][0]["text"])

    def test_state_reports_relative_player_position_and_bounded_area_estimate(self):
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 1.0, "y": 2.0}
        self.t["nearbyPlayers"] = [{"playerId": 6, "playerName": "假人6", "x": 2.0, "y": 2.2}]
        route = {"anchors": [{"anchorId": "cafeteria", "roomId": "Cafeteria", "position": {"x": 1.2, "y": 2.1}}]}
        b.atomic(self.root / "data/skeld-route-observations.json", route)
        self.save()
        state = self.bridge.read()
        self.assertEqual((state["location"]["x"], state["location"]["y"]), (1.0, 2.0))
        self.assertEqual(state["location"]["areaEstimate"]["room"], "Cafeteria")
        self.assertEqual(state["nearbyPlayers"][0]["relativeToSelf"]["direction"], "+X")
        self.assertAlmostEqual(state["nearbyPlayers"][0]["relativeToSelf"]["distance"], 1.02, places=2)

    def test_far_anchor_does_not_claim_a_room(self):
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 50.0, "y": 50.0}
        b.atomic(
            self.root / "data/skeld-route-observations.json",
            {"anchors": [{"anchorId": "cafeteria", "roomId": "Cafeteria", "position": {"x": 0.0, "y": 0.0}}]},
        )
        self.save()
        state = self.bridge.read()
        self.assertIsNone(state["location"]["areaEstimate"])
        self.assertGreater(state["location"]["nearestAnchor"]["distance"], 1.5)

    def test_movement_and_stationary_heartbeat_never_force_speech(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 0.0, "y": 0.0}
        self.t["nearbyPlayers"] = []
        self.save()
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        self.t["localPlayer"]["position"]["x"] = 1.0
        self.t["sentAtUtc"] = self.now()
        self.save()
        monitor.last_push -= 2
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        monitor.last_review = time.monotonic() - 21
        monitor.last_response = time.monotonic() - 21
        monitor.last_push -= 2
        self.t["sentAtUtc"] = self.now()
        self.save()
        monitor.tick()
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        self.assertTrue(all(c["ai_behavior"] == "read" for c in calls))

    def test_small_steps_accumulate_and_review_events_survive_cooldown(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.t["localPlayer"]["position"] = {"space": "unity_world", "x": 0.0, "y": 0.0}
        self.t["nearbyPlayers"] = []
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))

        def tick(at, x):
            self.t["localPlayer"]["position"]["x"] = x
            self.save()
            with patch("among_us_test.bridge.time.monotonic", return_value=at):
                monitor.tick()

        tick(100, 0)
        tick(101, 0.3)
        tick(102, 0.6)
        self.assertEqual(len(calls), 1)
        tick(103, 0.9)
        self.assertEqual(calls[-1]["ai_behavior"], "read")
        self.assertIn("self_moved", json.loads(calls[-1]["parts"][0]["text"].split("\n", 1)[1])["reviewReasons"])
        tick(104, 1.8)
        self.assertEqual(len(calls), 3)
        tick(109, 1.8)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1]["ai_behavior"], "read")

    def test_player_motion_and_leaving_trigger_review_without_user_query(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.t["nearbyPlayers"] = []
        calls = []
        monitor = b.Monitor(self.bridge, lambda **kw: calls.append(kw))

        def tick(at, players):
            self.t["nearbyPlayers"] = players
            self.save()
            with patch("among_us_test.bridge.time.monotonic", return_value=at):
                monitor.tick()

        tick(100, [])
        tick(101, [{"playerId": 6, "x": 1.0, "y": 0.0}])
        tick(102, [{"playerId": 6, "x": 1.4, "y": 0.0}])
        tick(103, [{"playerId": 6, "x": 1.8, "y": 0.0}])
        tick(104, [{"playerId": 6, "x": 2.2, "y": 0.0}])
        tick(107, [{"playerId": 6, "x": 2.2, "y": 0.0}])
        payload = json.loads(calls[-1]["parts"][0]["text"].split("\n", 1)[1])
        self.assertIn("player_moved", payload["reviewReasons"])
        tick(113, [])
        payload = json.loads(calls[-1]["parts"][0]["text"].split("\n", 1)[1])
        self.assertIn("visible_players_changed", payload["reviewReasons"])
        monitor.auto_reply = False
        tick(140, [{"playerId": 6, "x": 1, "y": 0}])
        self.assertEqual(calls[-1]["ai_behavior"], "read")

    def test_autopilot_selects_only_owned_incomplete_task_anchor(self):
        from among_us_test.autopilot import Autopilot

        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.t["localPlayer"]["position"] = {"x": 0, "y": 0}
        self.t["ui"]["ownTasks"] = [
            {"taskId": 3, "taskType": "FixWiring", "complete": False, "locations": [{"x": 5, "y": 0}]}
        ]
        self.save()
        anchors = [
            {"anchorId": "not-a-task", "kind": "waypoint", "position": {"x": 1, "y": 0}},
            {"anchorId": "task-a", "kind": "task", "position": {"x": 5.1, "y": 0}},
            {"anchorId": "task-b", "kind": "task", "position": {"x": 9, "y": 0}},
        ]
        self.bridge.route_anchors = lambda: anchors
        self.bridge.anchors = lambda: [{"anchorId": a["anchorId"]} for a in anchors]
        submitted = []
        self.bridge.navigate = lambda target, position=None: submitted.append((target, position)) or {
            "commandId": "auto-1",
            "status": "submitted",
        }
        self.bridge.set_runtime("autoNavigation", True)
        pilot = Autopilot(self.bridge)
        pilot.set_enabled(True)
        pilot.tick(self.bridge.read())
        self.assertEqual(pilot.target, "task-a")
        self.assertEqual(submitted[0], ("task-a", {"x": 5, "y": 0, "taskId": 3}))
        self.assertEqual(pilot.state, "submitted")
        with patch("among_us_test.autopilot.time.monotonic", return_value=pilot.submitted_at + 6):
            pilot.tick(self.bridge.read())
        self.assertTrue(pilot.enabled)
        self.assertIsNone(pilot.pending)
        self.assertEqual(pilot.state, "waiting")
        self.assertEqual(pilot.recovery_count, 1)

    def test_autopilot_completes_target_and_does_not_repeat_it(self):
        from among_us_test.autopilot import Autopilot

        bridge = types.SimpleNamespace(
            route_anchors=lambda: [
                {"anchorId": "a", "kind": "task", "position": {"x": 1, "y": 0}},
                {"anchorId": "b", "kind": "task", "position": {"x": 9, "y": 0}},
            ],
            navigate=lambda target, position=None: {"commandId": target + "-cmd"},
        )
        pilot = Autopilot(bridge)
        pilot.set_enabled(True)
        state = {
            "connected": True,
            "sessionId": self.session,
            "matchId": 1,
            "mapId": "TheSkeld",
            "phase": "TASKS",
            "self": {"canMove": True, "position": {"x": 0, "y": 0}},
            "tasks": [{"complete": False, "locations": [{"x": 1, "y": 0}, {"x": 9, "y": 0}]}],
            "navigation": {"state": "idle"},
        }
        with patch("among_us_test.autopilot.time.monotonic", return_value=100):
            pilot.tick(state)
        self.assertEqual(pilot.target, "a")
        state["navigation"] = {"commandId": "a-cmd", "state": "running"}
        with patch("among_us_test.autopilot.time.monotonic", return_value=101):
            pilot.tick(state)
        self.assertEqual(pilot.state, "moving")
        state["navigation"]["state"] = "completed"
        with patch("among_us_test.autopilot.time.monotonic", return_value=102):
            pilot.tick(state)
        with patch("among_us_test.autopilot.time.monotonic", return_value=106):
            pilot.tick(state)
        self.assertEqual(pilot.target, "b")
        pilot.set_enabled(False)
        with patch("among_us_test.autopilot.time.monotonic", return_value=200):
            pilot.tick(state)
        self.assertIsNone(pilot.pending)

    def test_autopilot_waits_for_meeting_and_stops_after_unconfirmed_command(self):
        from among_us_test.autopilot import Autopilot

        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.t["localPlayer"]["position"] = {"x": 0, "y": 0}
        self.t["ui"]["ownTasks"] = [
            {"taskId": 1, "taskType": "FixWiring", "complete": False, "locations": [{"x": 1, "y": 0}]}
        ]
        self.save()
        pilot = Autopilot(self.bridge)
        pilot.set_enabled(True)
        state = self.bridge.read()
        state["meetingOpen"] = True
        pilot.tick(state)
        self.assertEqual(pilot.state, "waiting")
        self.assertIsNone(pilot.pending)
        pilot.tick(self.bridge.read())
        self.assertIsNone(pilot.pending)  # No route anchors are installed in this fixture.

    def test_runtime_halt_disables_navigation_and_body_reporting(self):
        self.bridge.set_runtime("autoNavigation", True)
        self.bridge.set_runtime("autoReportBodies", True)
        self.bridge.set_runtime("minigameControl", True)
        self.bridge.halt()
        control = b.load(self.bridge.control_path)
        self.assertFalse(control["autoNavigation"])
        self.assertFalse(control["autoReportBodies"])
        self.assertFalse(control["minigameControl"])

    def test_task_interaction_binds_expected_task_and_exposes_open_result(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.save()
        b.atomic(self.root / "data/skeld-route-observations.json", {"anchors": [{"anchorId": "task"}]})
        self.bridge.navigate("task", {"x": 1, "y": 2, "taskId": 3})
        self.assertEqual(b.load(self.bridge.command_path)["interactTaskId"], 3)
        b.atomic(self.bridge.nav_path, {"state": "completed", "completionMode": "interaction_opened"})
        self.assertEqual(self.bridge.read()["navigation"]["completionMode"], "interaction_opened")

    def test_autopilot_retries_unfinished_task_instead_of_blacklisting_arrival(self):
        from among_us_test.autopilot import Autopilot

        calls = []
        bridge = types.SimpleNamespace(
            route_anchors=lambda: [{"anchorId": "a", "kind": "task", "position": {"x": 1, "y": 0}}],
            navigate=lambda target, position=None: calls.append(target) or {"commandId": "cmd"},
        )
        pilot = Autopilot(bridge)
        pilot.set_enabled(True)
        state = {
            "connected": True,
            "sessionId": self.session,
            "matchId": 1,
            "mapId": "TheSkeld",
            "phase": "TASKS",
            "self": {"canMove": True, "position": {"x": 0, "y": 0}},
            "navigation": {"state": "idle"},
            "tasks": [{"taskId": 1, "step": 0, "complete": False, "locations": [{"x": 1, "y": 0}]}],
        }
        with patch("among_us_test.autopilot.time.monotonic", return_value=100):
            pilot.tick(state)
        state["navigation"] = {"commandId": "cmd", "state": "completed"}
        with patch("among_us_test.autopilot.time.monotonic", return_value=101):
            pilot.tick(state)
        with patch("among_us_test.autopilot.time.monotonic", return_value=110):
            pilot.tick(state)
        self.assertEqual(calls, ["a"])
        with patch("among_us_test.autopilot.time.monotonic", return_value=132):
            pilot.tick(state)
        self.assertEqual(calls, ["a", "a"])

    def test_manual_task_anchor_also_opens_only_a_matching_owned_task(self):
        self.t["game"]["phase"] = "TASKS"
        self.t["ui"]["meetingOpen"] = False
        self.t["ui"]["ownTasks"] = [{"taskId": 7, "complete": False, "locations": [{"x": 2, "y": 3}]}]
        self.save()
        b.atomic(
            self.root / "data/skeld-route-observations.json",
            {"anchors": [{"anchorId": "task", "kind": "task", "position": {"x": 2.1, "y": 3}}]},
        )
        self.bridge.navigate("task")
        command = b.load(self.bridge.command_path)
        self.assertTrue(command["interactAfterArrival"])
        self.assertEqual(command["interactTaskId"], 7)

    def test_failed_minigame_stage_is_skipped_at_limit_but_next_stage_is_allowed(self):
        from among_us_test.autopilot import Autopilot

        calls = []
        bridge = types.SimpleNamespace(
            route_anchors=lambda: [{"anchorId": "a", "kind": "task", "position": {"x": 1, "y": 0}}],
            navigate=lambda target, position=None: calls.append(target) or {"commandId": "cmd"},
        )
        pilot = Autopilot(bridge)
        pilot.set_enabled(True)
        state = {
            "connected": True,
            "sessionId": self.session,
            "matchId": 1,
            "mapId": "TheSkeld",
            "phase": "TASKS",
            "self": {"canMove": True, "position": {"x": 0, "y": 0}},
            "navigation": {"state": "idle"},
            "tasks": [{"taskId": 1, "step": 0, "complete": False, "locations": [{"x": 1, "y": 0}]}],
            "runtimeStatus": {"minigameAutomation": {"taskId": 1, "initialStep": 0, "state": "failed", "attempts": 3}},
        }
        pilot.tick(state)
        self.assertEqual(calls, [])
        state["tasks"][0]["step"] = 1
        pilot.tick(state)
        self.assertEqual(calls, ["a"])


if __name__ == "__main__":
    unittest.main()
