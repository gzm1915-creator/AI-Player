import importlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

package = types.ModuleType("spatial_fixture")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
b = importlib.import_module("spatial_fixture.bridge")
s = importlib.import_module("spatial_fixture.spatial")


class SpatialEvidenceTests(unittest.TestCase):
    def test_anchor_names_preserve_engine_geometry_and_normal_chinese_terms(self):
        self.assertEqual(s.anchor_label("skeld.left-engine.align-engine"), "上引擎室·校准引擎")
        self.assertEqual(s.anchor_label("skeld.right-engine.fuel-engine"), "下引擎室·为引擎加燃料")
        self.assertEqual(s.anchor_label("skeld.cockpit.chart-course"), "导航室·规划航线")
        self.assertEqual(s.anchor_label("skeld.admin.vent-cleaning"), "管理室·清理通风口")
        self.assertEqual(s.anchor_label("engine.lower"), "下引擎室·入口")

    def test_live_summary_cannot_present_disconnect_rule_as_current_fact(self):
        summary = s.current_summary(
            {
                "connected": True,
                "telemetryAt": "new-sample",
                "location": {"x": 8.32, "y": 0.84, "areaEstimate": {"room": "Weapons"}},
                "nearbyPlayers": [{"playerId": 5, "playerName": "假人5"}],
            }
        )
        self.assertIn("connected=true", summary)
        self.assertNotIn("connected=false", summary)
        self.assertIn("8.32, 0.84", summary)
        self.assertIn("武器室", summary)
        self.assertIn("假人5", summary)
        self.assertIn("new-sample", summary)
        offline = s.current_summary(
            {"connected": False, "location": {"x": 99, "y": 88}, "nearbyPlayers": [{"playerName": "旧玩家"}]}
        )
        self.assertIn("connected=false", offline)
        self.assertNotIn("99", offline)
        self.assertNotIn("旧玩家", offline)

    def test_route_geometry_update_refreshes_position_labels_without_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = b.Bridge(tmp)
            b.atomic(
                bridge.root / "latest.json",
                {
                    "sentAtUtc": datetime.now(timezone.utc).isoformat(),
                    "sessionId": "12345678-1234-1234-1234-123456789012",
                    "game": {"phase": "TASKS", "mapId": "TheSkeld"},
                    "localPlayer": {"position": {"space": "unity_world", "x": 1, "y": 0}},
                },
            )
            route_path = bridge.root / "data/skeld-route-observations.json"
            b.atomic(
                route_path, {"anchors": [{"anchorId": "power", "roomId": "Weapons", "position": {"x": 2, "y": 0}}]}
            )
            self.assertEqual(bridge.read()["nearestAnchor"]["distance"], 1)
            b.atomic(
                route_path, {"anchors": [{"anchorId": "power", "roomId": "Weapons", "position": {"x": 1, "y": 0}}]}
            )
            bridge.route_data_stamp = -1
            self.assertEqual(bridge.read()["nearestAnchor"]["distance"], 0)

    def test_route_summary_keeps_time_order_and_meeting_boundaries(self):
        def point(at, anchor, room, x, segment=False):
            return {
                "at": at,
                "position": {"x": x, "y": 0},
                "segmentStart": segment,
                "nearestAnchor": {"anchorId": anchor, "room": room, "distance": 0.1},
            }

        summary = s.route_summary(
            [
                point("t1", "cafe", "Cafeteria", 0, True),
                point("t2", "cafe", "Cafeteria", 0.2),
                point("t3", "nav", "Navigation", 5),
                point("t4", "cafe", "Cafeteria", 0, True),
            ]
        )
        self.assertEqual([v["anchorId"] for v in summary["visits"]], ["cafe", "nav", "cafe"])
        self.assertEqual(summary["visits"][0]["lastAt"], "t2")
        self.assertIn("餐厅", summary["text"])
        self.assertIn("导航室", summary["text"])
        self.assertIn("新观测段", summary["text"])

    def test_model_observation_is_self_contained_and_separates_planned_route(self):
        data = s.model_state(
            {
                "connected": True,
                "self": {"position": {"x": 1, "y": 2}},
                "runtimeStatus": {"logPath": "private-file"},
                "history": {
                    "logDirectory": "private-directory",
                    "route": [0] * 2048,
                    "routeSummary": {"text": "餐厅 → 导航室"},
                },
                "navigation": {
                    "action": "navigate",
                    "state": "running",
                    "waypointIndex": 3,
                    "plannedRoute": list(range(100)),
                },
                "playerDetection": {"status": "ok", "cameraDiagnostics": "verbose"},
            }
        )
        encoded = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("private-", encoded)
        self.assertNotIn("verbose", encoded)
        self.assertEqual(data["history"]["routeSummary"]["text"], "餐厅 → 导航室")
        self.assertEqual(data["navigation"]["nextWaypoints"], list(range(3, 11)))
        self.assertLess(len(encoded), 2500)

    def test_stopped_navigation_never_looks_like_remaining_walked_route(self):
        for action, status in (("stop", "completed"), ("navigate", "completed"), ("navigate", "failed")):
            data = s.model_state({"navigation": {"action": action, "state": status, "plannedRoute": list(range(50))}})
            self.assertEqual(data["navigation"]["nextWaypoints"], [])

    def test_all_packaged_navigation_choices_have_human_chinese_names(self):
        route = Path(__file__).resolve().parents[1] / "game-plugin/data/skeld-route-observations.json"
        anchors = json.loads(route.read_text(encoding="utf-8"))["anchors"]
        choices = s.navigation_options(anchors)
        self.assertEqual({a["anchorId"] for a in choices}, {a["anchorId"] for a in anchors})
        self.assertEqual(len({a["label"] for a in choices}), len(choices))
        for option in choices:
            self.assertTrue(any("\u4e00" <= c <= "\u9fff" for c in option["label"]))
            self.assertNotIn("skeld.", option["label"])
        self.assertTrue(
            next(a for a in choices if a["anchorId"] == "skeld.admin.swipe-card")["label"].startswith("做任务：管理室")
        )

    def test_transient_read_failure_uses_only_still_fresh_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = b.Bridge(tmp)
            sample = {
                "sentAtUtc": datetime.now(timezone.utc).isoformat(),
                "sessionId": "12345678-1234-1234-1234-123456789012",
                "game": {"phase": "TASKS"},
                "localPlayer": {"position": {"space": "unity_world", "x": 1, "y": 2}},
            }
            b.atomic(bridge.root / "latest.json", sample)
            self.assertTrue(bridge.read()["connected"])
            original = b.load

            def unavailable(path):
                return {} if Path(path).name == "latest.json" else original(path)

            with patch.object(b, "load", side_effect=unavailable):
                state = bridge.read()
                self.assertTrue(state["connected"])
                self.assertEqual(state["telemetrySource"], "cached_fresh_sample")
                bridge.last_telemetry["sentAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
                expired = bridge.read()
                self.assertFalse(expired["connected"])
                self.assertNotIn("position", expired["self"])
                self.assertEqual(expired["location"]["source"], "unavailable")


if __name__ == "__main__":
    unittest.main()
