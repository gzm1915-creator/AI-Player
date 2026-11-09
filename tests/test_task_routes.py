"""Replay the actual weapons console mismatch and keep shared tasks compatible."""
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("task_route_fixture")
package.__path__ = [str(ROOT / "plugin/among_us_neko")]
sys.modules[package.__name__] = package
routes = importlib.import_module("task_route_fixture.task_routes")
b = importlib.import_module("task_route_fixture.bridge")


class TaskRouteTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / "game-plugin/data/skeld-route-observations.json").read_text(encoding="utf-8"))
        self.asteroids = {"taskId": 1, "taskType": "ClearAsteroids", "step": 0, "maxStep": 20,
                          "complete": False, "locations": [{"x": 9.087601, "y": 1.812}]}
        self.commands = []
        self.bridge = types.SimpleNamespace(
            route_anchors=lambda: self.catalog["anchors"],
            navigate=lambda anchor, position=None: self.commands.append((anchor, position)) or {"commandId": "asteroid-test"})
        self.state = {"connected": True, "sessionId": "fixture", "matchId": 1, "mapId": "TheSkeld", "phase": "TASKS",
                      "self": {"canMove": True, "position": {"x": 0, "y": 2}}, "tasks": [self.asteroids],
                      "navigation": {"state": "idle"}, "runtimeControl": {"autoNavigation": True}}

    def test_actual_asteroid_console_uses_its_standing_point(self):
        pilot = b.Autopilot(self.bridge)
        pilot.set_enabled(True)
        with patch("time.monotonic", return_value=100):
            pilot.tick(self.state)
        self.assertEqual(self.commands[0][0], "skeld.weapons.clear-asteroids")
        self.assertEqual(self.commands[0][1]["taskId"], 1)

    def test_missing_asteroid_standing_point_never_borrows_the_data_console(self):
        self.catalog["anchors"] = [a for a in self.catalog["anchors"] if a["anchorId"] != "skeld.weapons.clear-asteroids"]
        pilot = b.Autopilot(self.bridge)
        pilot.set_enabled(True)
        with patch("time.monotonic", return_value=100):
            pilot.tick(self.state)
        self.assertEqual(self.commands, [])

    def test_shared_console_supports_download_and_upload(self):
        anchor = {"anchorId": "skeld.admin.upload-data"}
        for kind in ("DownloadData", "UploadData"):
            self.assertTrue(routes.anchor_accepts_task(anchor, {"taskType": kind}))
        self.assertFalse(routes.anchor_accepts_task(anchor, self.asteroids))

    def test_manual_navigation_cannot_bind_the_wrong_task_to_the_named_console(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = b.Bridge(Path(temp))
            bridge.read = lambda: self.state
            bridge.route_anchors = self.bridge.route_anchors
            with self.assertRaisesRegex(ValueError, "其他种类"):
                bridge.navigate("skeld.weapons.upload-data", {**self.asteroids["locations"][0], "taskId": 1})
            self.assertFalse(bridge.command_path.exists())

    def test_repeated_stall_has_two_replans_instead_of_twelve_default_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = b.Bridge(Path(temp))
            bridge.read = lambda: self.state
            bridge.route_anchors = self.bridge.route_anchors
            bridge.navigate("skeld.weapons.clear-asteroids", {**self.asteroids["locations"][0], "taskId": 1})
            self.assertEqual(b.load(bridge.command_path)["maxRecoveries"], 2)

    def test_asteroid_route_stays_on_an_existing_verified_route_node(self):
        point = next(a for a in self.catalog["anchors"] if a["anchorId"] == "skeld.weapons.clear-asteroids")["position"]
        recorded_ids = {s.get(key) for s in self.catalog["routeSegments"] if s.get("status") == "observed_success"
                        for key in ("fromAnchorId", "toAnchorId")}
        matching = [a for a in self.catalog["anchors"] if a["anchorId"] in recorded_ids and a["position"] == point]
        self.assertTrue(matching, "new task standing point must already be recorded in the walking network")

    def test_visual_task_standing_point_is_never_an_impostor_cover_or_patrol(self):
        pilot = b.Autopilot(self.bridge)
        anchor = next(a for a in self.catalog["anchors"] if a["anchorId"] == "skeld.weapons.clear-asteroids")
        self.assertFalse(pilot.impostor.inside_room(anchor))
        cover = pilot.impostor.cover_targets(self.state, self.catalog["anchors"], 100)
        self.assertFalse(any(a["anchorId"] == anchor["anchorId"] or task["taskType"] == "ClearAsteroids"
                             for a, task in cover))
        self.assertEqual(len(cover), 3)  # Two optional wiring stops and communications data.


if __name__ == "__main__":
    unittest.main()
