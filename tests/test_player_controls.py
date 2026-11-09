"""Manual vent commands, pre-kill readiness and persistent native crime facts."""
import asyncio
import copy
import importlib.util
import sys
import types
import unittest
from unittest.mock import patch
import test_impostor as fixture

b = fixture.b


class PlayerControlTests(unittest.TestCase):
    setUp = fixture.ImpostorTests.setUp
    save = fixture.ImpostorTests.save
    tick = fixture.ImpostorTests.tick
    ack = fixture.ImpostorTests.ack
    start_pursuit = fixture.ImpostorTests.start_pursuit

    def vents(self):
        self.t["ventRole"] = {"roleType": "Impostor", "canVent": True, "inVent": False, "currentVentId": -1}
        self.t["ventTravel"] = {"supported": True, "manualControlSupported": True, "busy": False, "state": "idle"}
        self.t["vents"] = [{"id": 0, "x": 0, "y": 0, "leftId": 1, "usableDistance": 1},
                           {"id": 1, "x": 10, "y": 0, "leftId": 0, "usableDistance": 1}]
        self.bridge.set_runtime("useVents", True)
        self.save()

    def test_manual_enter_is_explicit_and_no_target_exit_required(self):
        self.vents()
        result = self.bridge.manual_vent("钻管道")
        command = b.load(self.bridge.vent_path)
        self.assertEqual((command["action"], command["entryVentId"], command["targetVentId"]), ("enter", 0, 0))
        self.assertEqual(result["status"], "submitted")
        self.assertFalse(result["completed"])
        self.assertIn("留在管道内", result["summary"])

    def test_manual_move_stays_inside_and_exit_has_own_command(self):
        self.vents()
        self.t["ventRole"].update(inVent=True, currentVentId=0)
        self.t["localPlayer"]["canMove"] = False
        self.save()
        self.bridge.manual_vent("管道移动 1")
        self.assertEqual(b.load(self.bridge.vent_path)["action"], "move")
        self.bridge.last_command_at = 0
        self.bridge.manual_vent("出管道")
        self.assertEqual(b.load(self.bridge.vent_path)["action"], "exit")

    def test_manual_parser_rejects_unknown_commands_without_side_effects(self):
        self.vents()
        for command in ("随便钻", "钻管道 -1", "管道移动", "管道传送 X", "/vent enter; anything"):
            with self.assertRaises(ValueError): self.bridge.manual_vent(command)
        self.assertFalse(self.bridge.vent_path.exists())

    def test_manual_enter_rejects_old_component_far_entry_and_move_from_outside(self):
        self.vents()
        for case in ("old", "far", "outside_move"):
            old = copy.deepcopy(self.t)
            if case == "old": self.t["ventTravel"]["manualControlSupported"] = False
            if case == "far": self.t["localPlayer"]["position"]["x"] = 50
            self.save()
            with self.assertRaises(ValueError): self.bridge.manual_vent("管道移动 1" if case == "outside_move" else "钻管道")
            self.assertFalse(self.bridge.vent_path.exists())
            self.t = old

    def test_manual_completion_does_not_claim_safe_exit(self):
        self.vents()
        self.t["ventRole"].update(inVent=True, currentVentId=0)
        self.t["ventTravel"].update(action="enter", state="completed", heldInside=True, safeExitConfirmed=False, reason="已钻入并保持隐藏")
        self.save()
        result = self.bridge.vent()
        self.assertTrue(result["completed"])
        self.assertFalse(result["ventTravel"]["safeExitConfirmed"])

    def test_bridge_kill_waits_for_native_safety_readiness(self):
        self.t["ownRole"].update(killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "requiredSeconds": 3.5, "clearSeconds": 2, "ready": False}])
        self.save()
        with self.assertRaises(ValueError): self.bridge.kill(1, self.t["sessionId"], 1)
        self.assertFalse(self.bridge.command_path.exists())
        self.t["ownRole"]["killSafety"][0].update(clearSeconds=3.5, ready=True)
        self.save()
        self.assertEqual(self.bridge.kill(1, self.t["sessionId"], 1)["status"], "submitted")

    def test_three_second_risk_window_does_not_wait_for_ten_second_memory(self):
        self.t["ownRole"].update(killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "requiredSeconds": 3, "clearSeconds": 4, "ready": True}])
        self.t["recentPlayers"] = [{"playerId": 3, "x": 2, "y": 0, "ageSeconds": 4, "uncertaintyRadius": 6}]
        self.save()
        self.assertEqual(self.bridge.kill(1, self.t["sessionId"], 1)["status"], "submitted")

    def test_running_pursuit_holds_until_native_safety_is_ready(self):
        self.t["ownRole"].update(killSafetySupported=True, killSafety=[{"targetPlayerId": 1, "requiredSeconds": 4, "clearSeconds": 1, "ready": False}])
        chase = self.start_pursuit()
        self.t["ownRole"].update(canKill=True, killTargetId=1)
        self.assertEqual(self.tick(102)["commandId"], chase["commandId"])
        self.t["ownRole"]["killSafety"][0].update(clearSeconds=4, ready=True)
        self.assertEqual(self.tick(104)["action"], "impostor_kill")

    def test_crime_scene_excludes_cover_and_ai_direct_navigation(self):
        self.t["crimeScenes"] = [{"x": 1, "y": 0, "targetPlayerId": 1, "avoidRadius": 3}]
        self.save()
        self.assertEqual(self.pilot.impostor.cover_targets(self.bridge.read(), self.anchors, 100), [])
        with self.assertRaises(ValueError): self.bridge.navigate("skeld.admin.swipe-card", interact=False)
        self.assertFalse(self.bridge.command_path.exists())

    def test_crime_scene_facts_survive_controller_reset_and_meeting(self):
        self.t["crimeScenes"] = [{"x": 1, "y": 0, "targetPlayerId": 1, "avoidRadius": 3, "source": "native_local_murder"}]
        self.save()
        self.pilot.impostor.reset()
        self.assertEqual(self.bridge.map_context()["crimeScenes"], self.t["crimeScenes"])
        self.t["game"]["phase"] = "MEETING"
        self.save()
        self.assertEqual(self.bridge.read()["crimeScenes"], self.t["crimeScenes"])

    def test_native_crime_avoidance_not_cleared_with_readable_history(self):
        self.t["crimeScenes"] = [{"x": 1, "y": 0, "targetPlayerId": 1, "avoidRadius": 3}]
        self.save()
        self.bridge.read()
        self.bridge.journal.clear()
        self.assertEqual(self.bridge.read()["crimeScenes"], self.t["crimeScenes"])

    def test_manual_facade_pauses_autopilot_after_submission_and_panel_needs_token(self):
        self.vents()
        sdk = types.ModuleType("plugin.sdk.plugin")
        sdk.NekoPluginBase = type("Base", (), {"__init__": lambda obj, ctx: None})
        sdk.neko_plugin = lambda cls: cls
        sdk.Ok = lambda value: value
        sdk.ui = types.SimpleNamespace(context=lambda **_: lambda fn: fn, action=lambda **_: lambda fn: fn)
        for name in ("llm_tool", "plugin_entry", "lifecycle"):
            setattr(sdk, name, lambda **_: lambda fn: fn)
        root = fixture.Path(__file__).resolve().parents[1] / "plugin/among_us_neko"
        with patch.dict(sys.modules, {"plugin.sdk.plugin": sdk}):
            spec = importlib.util.spec_from_file_location("manual_controls_fixture", root / "__init__.py", submodule_search_locations=[str(root)])
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {"manual_controls_fixture": module}): spec.loader.exec_module(module)
            obj = module.AmongUsNekoPlugin(None)
            obj.bridge = self.bridge
            obj.monitor = types.SimpleNamespace(pilot=self.pilot, solver=types.SimpleNamespace(set_enabled=lambda on: None))
            with self.assertRaises(ValueError):
                asyncio.run(obj.control(operation="manual_vent", text="钻管道"))
            self.assertTrue(self.pilot.enabled)
            invalid = asyncio.run(obj.manual_vent(command="错误命令"))
            self.assertIn("error", invalid)
            self.assertTrue(self.pilot.enabled)
            result = asyncio.run(obj.control(operation="manual_vent", text="钻管道", ui_token=obj.ui_token))
            self.assertEqual(result["status"], "submitted")
            self.assertTrue(result["autopilotPaused"])
            self.assertFalse(self.pilot.enabled)
            self.assertEqual(b.load(self.bridge.vent_path)["action"], "enter")

