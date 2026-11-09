"""Pipe-network and controller ownership regressions, without game input."""
import asyncio
import copy
import importlib
import importlib.util
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "plugin/among_us_neko"
package = types.ModuleType("vent_fixture")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
b = importlib.import_module("vent_fixture.bridge")
v = importlib.import_module("vent_fixture.vents")
s = importlib.import_module("vent_fixture.spatial")


class VentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bridge = b.Bridge(tmp.name)
        self.bridge.root.mkdir(parents=True)
        self.t = {"sessionId": "12345678-1234-1234-1234-123456789012",
                  "game": {"phase": "TASKS", "matchId": 1, "mapId": "TheSkeld"},
                  "localPlayer": {"canMove": True, "position": {"space": "unity_world", "x": 0, "y": 0}},
                  "ownRole": {"roleType": "Impostor", "isImpostor": True, "isDead": False},
                  "ventRole": {"roleType": "Impostor", "canVent": True, "inVent": False, "currentVentId": -1},
                  "ventTravel": {"supported": True, "busy": False, "state": "idle"},
                  "vents": [{"id": i, "x": i * 10, "y": 0, "leftId": i - 1 if i < 3 else -1,
                             "rightId": i + 1 if i < 2 else -1, "centerId": -1, "usableDistance": 1}
                            for i in range(4)], "ui": {"ownTasks": []}}
        b.atomic(self.bridge.root / "data/skeld-route-observations.json", {"anchors": [
            {"anchorId": "skeld.admin.vent", "kind": "vent", "roomId": "Admin", "position": {"x": 0, "y": 0}},
            {"anchorId": "skeld.admin.swipe-card", "kind": "task", "roomId": "Admin", "position": {"x": 1, "y": 0}}]})
        self.bridge.set_runtime("autoNavigation", True)
        self.bridge.set_runtime("useVents", True)
        self.save()

    def save(self):
        self.t["sentAtUtc"] = datetime.now(timezone.utc).isoformat()
        b.atomic(self.bridge.root / "latest.json", self.t)

    def test_status_bfs_chinese_labels_and_no_write(self):
        state = self.bridge.vent()
        self.assertEqual(v.path_between(state["vents"], 0, 2), [0, 1, 2])
        self.assertEqual(v.path_between(state["vents"], 0, 3), [])
        self.assertIn("管理室", state["vents"][0]["label"])
        self.assertEqual(state["vents"][0]["connectedVentIds"], [0, 1, 2])
        self.assertFalse(self.bridge.vent_path.exists())
        self.assertFalse(state["completed"])
        self.assertIn("未执行", state["summary"])

    def test_approach_needs_new_component_and_never_claims_transport_complete(self):
        with self.assertRaises(ValueError): self.bridge.vent("approach", entry_vent_id=0)
        self.t["ventTravel"].update(approachSupported=True, escapeSupported=True)
        self.save()
        result = self.bridge.vent("approach", entry_vent_id=0)
        self.assertFalse(result["completed"])
        self.assertEqual(b.load(self.bridge.command_path)["action"], "navigate_vent")
        self.assertFalse(self.bridge.vent_path.exists())
        self.bridge.last_command_at = 0
        result = self.bridge.vent("travel", 2, guarded_exit=True, observe_seconds=15)
        self.assertFalse(result["completed"])
        self.assertEqual(b.load(self.bridge.vent_path)["observeSeconds"], 15)

    def test_independent_command_and_pending_owns_movement(self):
        b.atomic(self.bridge.command_path, {"commandId": "prior-nav", "action": "navigate"})
        result = self.bridge.vent("travel", 2)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["route"], [0, 1, 2])
        self.assertEqual(b.load(self.bridge.command_path)["commandId"], "prior-nav")
        command = b.load(self.bridge.vent_path)
        self.assertEqual(command["expectedMatchId"], 1)
        self.assertEqual(self.bridge.read()["ventTravel"]["state"], "submitted")
        pilot = b.Autopilot(self.bridge)
        pilot.enabled = True
        pilot.tick(self.bridge.read())
        self.assertEqual(pilot.state, "waiting")
        self.assertEqual(b.load(self.bridge.command_path)["commandId"], "prior-nav")
        with self.assertRaises(ValueError):
            self.bridge.vent("travel", 1)
        with self.assertRaises(ValueError):
            self.bridge.navigate("skeld.admin.swipe-card")

    def test_guards_do_not_write_requests(self):
        for case in ("old_component", "stale", "meeting", "minigame", "dead", "role", "permission", "navigation", "range", "disconnected", "bool_id", "invented_id"):
            with self.subTest(case=case):
                original = copy.deepcopy(self.t)
                target = 2
                if case == "old_component": self.t.pop("ventTravel")
                if case == "meeting": self.t["game"]["phase"] = "MEETING"
                if case == "minigame": self.t["ui"]["minigameOpen"] = True
                if case == "dead": self.t["ownRole"]["isDead"] = True
                if case == "role": self.t["ventRole"]["roleType"] = "Engineer"
                if case == "permission": self.bridge.set_runtime("useVents", False)
                if case == "navigation": self.bridge.set_runtime("autoNavigation", False)
                if case == "range": self.t["localPlayer"]["position"]["x"] = 50
                if case == "disconnected": target = 3
                if case == "bool_id": target = True
                if case == "invented_id": target = 99
                self.save()
                if case == "stale":
                    self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
                    b.atomic(self.bridge.root / "latest.json", self.t)
                with self.assertRaises(ValueError): self.bridge.vent("travel", target)
                self.assertFalse(self.bridge.vent_path.exists())
                self.t = original
                self.bridge.set_runtime("useVents", True)
                self.bridge.set_runtime("autoNavigation", True)

    def test_inside_can_move_false_and_exit_with_gates_disabled(self):
        self.t["ventRole"].update(inVent=True, currentVentId=1)
        self.t["localPlayer"]["canMove"] = False
        self.save()
        self.assertEqual(self.bridge.vent("travel", 2)["route"], [1, 2])
        self.bridge.set_runtime("useVents", False)
        self.bridge.set_runtime("autoNavigation", False)
        self.bridge.last_command_at = 0
        self.assertEqual(self.bridge.vent("exit")["status"], "submitted")
        self.assertEqual(b.load(self.bridge.vent_path)["action"], "exit")

    def test_pending_timeout_is_failure_not_completion_and_match_clears(self):
        self.bridge.vent("travel", 2)
        self.bridge.pending_vent["submitted"] -= 4
        state = self.bridge.read()
        self.assertEqual(state["ventTravel"]["state"], "failed")
        self.assertFalse(state["ventTravel"]["busy"])
        self.t["game"]["matchId"] += 1
        self.save()
        self.assertEqual(self.bridge.read()["ventTravel"]["state"], "idle")
        self.assertIsNone(self.bridge.pending_vent)

    def test_native_ack_replaces_pending_and_stale_clears_pipe_observations(self):
        command = self.bridge.vent("travel", 2)["commandId"]
        self.t["ventTravel"].update(commandId=command, state="running", busy=True)
        self.save()
        self.assertEqual(self.bridge.read()["ventTravel"]["state"], "running")
        self.assertIsNone(self.bridge.pending_vent)
        self.t["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        b.atomic(self.bridge.root / "latest.json", self.t)
        state = self.bridge.read()
        self.assertEqual(state["vents"], [])
        self.assertEqual(state["ventRole"], {})
        self.assertEqual(state["ventTravel"], {})

    def test_journal_does_not_count_pipe_displacement(self):
        epoch = datetime.now(timezone.utc) - timedelta(seconds=30)
        def observe(index, x, transit=False, receipt=None):
            state = {"connected": True, "sessionId": "session", "matchId": 1, "mapId": "TheSkeld", "phase": "TASKS",
                     "telemetryAt": (epoch + timedelta(seconds=index)).isoformat(), "self": {"position": {"x": x, "y": 0}},
                     "ventRole": {"inVent": transit}, "ventTravel": receipt or {"busy": transit}, "tasks": []}
            self.bridge.journal.observe(state)
        observe(0, 0)
        observe(1, 1)
        observe(2, 1, True)
        observe(3, 50, True)
        done = {"commandId": "vent-test", "state": "completed", "entryVentId": 0, "targetVentId": 2,
                "route": [0, 1, 2], "safeExitConfirmed": True}
        observe(4, 50, receipt=done)
        observe(5, 51, receipt=done)
        history = self.bridge.journal.query()
        self.assertEqual(history["distance"], 2)
        self.assertEqual(len(history["route"]), 4)
        self.assertTrue(history["route"][2]["segmentStart"])
        self.assertEqual(sum(e["event"] == "vent_completed" for e in history["timeline"]), 1)
        self.assertEqual(history["taskEvents"], [])
        self.assertIn("已确认安全出管", s.action_record(history)["text"])

    def test_unconfirmed_bridge_timeout_is_not_native_failure_evidence(self):
        state = self.bridge.read()
        state["ventTravel"] = {"commandId": "unknown", "state": "failed", "stage": "unconfirmed"}
        state["telemetryAt"] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        self.bridge.journal.observe(state)
        self.assertFalse(any(e["event"] == "vent_failed" for e in self.bridge.journal.query()["timeline"]))

    def test_ai_entry_uses_own_skill_and_panel_still_requires_token(self):
        sdk = types.ModuleType("plugin.sdk.plugin")
        class Base:
            def __init__(self, ctx): pass
        sdk.NekoPluginBase = Base
        sdk.neko_plugin = lambda cls: cls
        sdk.Ok = lambda value: value
        sdk.ui = types.SimpleNamespace(context=lambda **_: lambda fn: fn, action=lambda **_: lambda fn: fn)
        for dec in ("llm_tool", "plugin_entry", "lifecycle"):
            setattr(sdk, dec, lambda **_: lambda fn: fn)
        with patch.dict(sys.modules, {"plugin.sdk.plugin": sdk}):
            spec = importlib.util.spec_from_file_location("vent_facade_fixture", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
            mod = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {"vent_facade_fixture": mod}):
                spec.loader.exec_module(mod)
            obj = mod.AmongUsNekoPlugin(None)
            obj.bridge = self.bridge
            self.assertIn("vents", asyncio.run(obj.vent()))
            policy = asyncio.run(obj.escape_policy(enabled=True))
            self.assertEqual(policy["status"], "configured")
            self.assertFalse(policy["completed"])
            self.assertTrue(b.load(self.bridge.control_path)["impostorVentEscape"])
            self.assertFalse(self.bridge.vent_path.exists())
            with self.assertRaises(ValueError):
                asyncio.run(obj.control(operation="vent_travel", target_vent_id=2))
            result = asyncio.run(obj.vent(action="travel", target_vent_id=2))
            self.assertEqual(result["status"], "submitted")


if __name__ == "__main__": unittest.main()
