"""Regression cases for the 0.1.37 second review, including lock concurrency."""

import asyncio
import importlib
import io
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_bridge as fixture
import test_review_stability as first_review

b = fixture.b


class BackgroundSkillCheckTests(unittest.TestCase):
    setUp = fixture.BridgeTests.setUp
    now = staticmethod(fixture.BridgeTests.now)
    save = fixture.BridgeTests.save
    plugin = first_review.FacadeResponsivenessTests.plugin

    def test_slow_check_does_not_block_dashboard_observer_chat_or_stop(self):
        for operation in ("studybot_config", "start_all", "pilot_on", "runtime"):
            with self.subTest(operation=operation):
                module, obj = self.plugin()
                entered, release = threading.Event(), threading.Event()

                def slow(*_):
                    entered.set()
                    release.wait(3)
                    return {"engine": "StudyBot", "state": "ready", "skills": ["FixWiring"]}

                async def scenario():
                    await obj.startup()
                    solver = obj.monitor.solver
                    thread = None
                    try:
                        with patch.object(solver, "command", return_value=["python"]), patch.object(
                            solver, "_probe", side_effect=slow
                        ):
                            await asyncio.wait_for(
                                obj.control(
                                    operation=operation, ui_token=obj.ui_token, flag="minigameControl", enabled=True
                                ), 1
                            )
                            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                            thread = solver.preflight_thread
                            self.assertFalse(solver.enabled)
                            dashboard = await asyncio.wait_for(obj.dashboard(), 1)
                            self.assertEqual(dashboard["observer"]["studybot"]["state"], "checking")
                            await asyncio.wait_for(asyncio.to_thread(obj.monitor.tick), 1)
                            sent = await asyncio.wait_for(obj.send_meeting(text="后台检查期间会议仍可发言"), 1)
                            self.assertEqual(sent["status"], "submitted")
                            await asyncio.wait_for(obj.control(operation="stop", ui_token=obj.ui_token), 1)
                            release.set()
                            await asyncio.to_thread(thread.join, 1)
                            self.assertFalse(thread.is_alive())
                            self.assertFalse(solver.enabled, "a stale check must not undo stop")
                            self.assertEqual(solver.result["state"], "disabled")
                    finally:
                        release.set()
                        if thread:
                            await asyncio.to_thread(thread.join, 1)
                        await obj.shutdown()

                with patch.object(module.Monitor, "start"):
                    asyncio.run(scenario())

    def test_missing_skill_config_allows_navigation_for_both_roles(self):
        for impostor in (False, True):
            for operation in ("start_all", "pilot_on"):
                with self.subTest(impostor=impostor, operation=operation):
                    module, obj = self.plugin()

                    async def scenario():
                        await obj.startup()
                        try:
                            with patch.object(obj.bridge, "read", return_value={"ownRole": {"isImpostor": impostor}}):
                                await obj.control(operation=operation, ui_token=obj.ui_token)
                            self.assertTrue(obj.monitor.pilot.enabled)
                            self.assertFalse(obj.monitor.solver.enabled)
                            self.assertEqual(obj.monitor.solver.result["state"], "blocked")
                        finally:
                            await obj.shutdown()

                    with patch.object(module.Monitor, "start"):
                        asyncio.run(scenario())

    def test_reconfiguration_ignores_old_success_and_coalesces_queued_checks(self):
        solver = b.StudyBot(self.bridge)
        old_entered, old_release = threading.Event(), threading.Event()
        new_entered, new_release = threading.Event(), threading.Event()
        calls = []

        def probe(command, _):
            calls.append(command[0])
            entered, release = (old_entered, old_release) if command[0] == "old" else (new_entered, new_release)
            entered.set()
            release.wait(3)
            return {"engine": "StudyBot", "state": "ready", "skills": [command[0]]}

        with patch.object(solver, "command", side_effect=[["old"], ["discarded"], ["new"]]), patch.object(
            solver, "_probe", side_effect=probe
        ):
            thread = None
            try:
                with self.bridge.lock:
                    solver.request_preflight(enable=True)
                    thread = solver.preflight_thread
                self.assertTrue(old_entered.wait(1))
                with self.bridge.lock:
                    solver.set_enabled(False)
                    solver.request_preflight(enable=True)
                    solver.request_preflight(enable=True)
                old_release.set()
                self.assertTrue(new_entered.wait(1))
                self.assertFalse(solver.enabled)
                self.assertEqual(solver.status()["state"], "checking")
                new_release.set()
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertTrue(solver.enabled)
                self.assertEqual(solver.result["skills"], ["new"])
                self.assertEqual(calls, ["old", "new"])
            finally:
                old_release.set()
                new_release.set()
                if thread:
                    thread.join(1)
                solver.set_enabled(False)

    def test_probe_timeout_is_bounded_and_releases_only_its_own_enable_request(self):
        module = importlib.import_module(b.__package__ + ".studybot")
        solver = b.StudyBot(self.bridge)
        with patch.object(solver, "command", return_value=["python"]), patch.object(
            module.subprocess, "run", side_effect=module.subprocess.TimeoutExpired("python", 8)
        ) as run:
            with self.bridge.lock:
                solver.request_preflight(enable=True)
                thread = solver.preflight_thread
            thread.join(1)
            self.assertEqual(run.call_args.kwargs["timeout"], 8)
            self.assertFalse(solver.enabled)
            self.assertEqual(solver.result["state"], "blocked")


class FollowupProtocolTests(unittest.TestCase):
    setUpClass = classmethod(first_review.ChildProtocolTests.setUpClass.__func__)
    setUp = first_review.ChildProtocolTests.setUp

    def test_reactor_history_bounds_reads_and_skips_malformed_rows(self):
        def row(lit, stamp, opened=True):
            return json.dumps({"sessionId": "test", "sentAtUtc": stamp,
                               "ui": {"minigameOpen": opened, "startReactorLitIndices": lit}}).encode() + b"\n"

        raw = b"x" * (1024 * 1024) + b"\n"
        raw += row([], "1", False) + row(["2"], "2") + row([], "3")
        raw += b'[]\n{"sessionId":"test","ui":7}\n'
        raw += row(["bad"], "4") + row([{}], "5") + row([True], "6") + row([9], "7")
        raw += row([4], "8") + b'{"unfinished":'
        (self.root / "telemetry.jsonl").write_bytes(raw)
        requests = []

        class Reader(io.BytesIO):
            def read(self, size=-1):
                requests.append(size)
                return super().read(size)

        with patch.object(self.runner.protocol, "open_shared", side_effect=lambda _: Reader(raw)):
            self.assertEqual(self.runner._recent_reactor_flash_history("test"), [2, 4])
        self.assertEqual(requests, [256 * 1024])

    def test_reactor_manifest_shared_reader_keeps_partial_line_offset(self):
        directory = self.root / "visual" / "test" / "task-bursts" / "start-reactor-1"
        directory.mkdir(parents=True)
        path = directory / "reactor-capture.jsonl"
        path.write_bytes(b'[]\n{"event":"capture","index":1}\n{"event":')
        with patch.object(self.runner.protocol, "open_shared", wraps=self.runner.protocol.open_shared) as reader, patch.object(
            self.runner, "read_latest_telemetry", return_value={"sessionId": "test"}
        ):
            self.assertEqual(self.runner.reactor_capture_events(), [{"event": "capture", "index": 1}])
        reader.assert_called_once_with(path)
        with path.open("ab") as stream:
            stream.write(b'"capture","index":2}\n')
        with patch.object(self.runner, "read_latest_telemetry", return_value={"sessionId": "test"}):
            self.assertEqual(self.runner.reactor_capture_events(), [{"event": "capture", "index": 2}])

    def test_optional_reactor_status_uses_shared_atomic_retries(self):
        path = Path(fixture.PLUGIN) / "studybot_runner/skills/start_reactor/skill.py"
        spec = importlib.util.spec_from_file_location("review_reactor_skill", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: module}):
            spec.loader.exec_module(module)
        target = self.root / "reactor-status.json"
        replace = module._protocol.replace_file
        attempts = []

        def contested(source, destination):
            attempts.append(True)
            if len(attempts) < 7:
                raise PermissionError("game HUD reader")
            return replace(source, destination)

        with patch.object(module._protocol, "replace_file", side_effect=contested), patch.object(
            module._protocol.time, "sleep"
        ):
            module._write_status_file(str(target), {"state": "ready"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["state"], "ready")
        self.assertEqual(len(attempts), 7)


class FollowupObserverTests(unittest.TestCase):
    setUp = fixture.BridgeTests.setUp
    now = staticmethod(fixture.BridgeTests.now)
    save = fixture.BridgeTests.save

    def test_stopped_live_monitor_rejects_resume_and_can_restart_after_exit(self):
        monitor = b.Monitor(self.bridge, lambda **_: None)
        monitor.thread = Mock()
        monitor.thread.is_alive.return_value = True
        monitor.stop.set()
        with self.assertRaisesRegex(ValueError, "仍在退出"):
            monitor.start()
        self.assertTrue(monitor.stop.is_set())
        monitor.thread.is_alive.return_value = False
        with patch.object(monitor, "run") as run:
            monitor.start()
            monitor.thread.join(1)
        run.assert_called_once()
        self.assertFalse(monitor.stop.is_set())
        monitor.close()

    def test_missing_route_reports_component_data_instead_of_unknown_anchor(self):
        self.t["game"].update(phase="TASKS", mapId="TheSkeld")
        self.t["localPlayer"]["canMove"] = True
        self.save()
        with self.assertRaisesRegex(ValueError, "重新安装游戏组件"):
            self.bridge.navigate("missing-anchor")

    def test_journal_flush_retries_sharing_conflicts(self):
        self.bridge.read()
        files = importlib.import_module(b.__package__ + ".files")
        replace = files.replace_file
        attempts = []

        def contested(source, target):
            attempts.append(True)
            if len(attempts) < 7:
                raise PermissionError("shared reader")
            return replace(source, target)

        self.bridge.journal.dirty = True
        with patch.object(files, "replace_file", side_effect=contested), patch.object(files.time, "sleep"):
            self.bridge.journal.flush()
        self.assertEqual(len(attempts), 7)
        self.assertFalse(self.bridge.journal.dirty)
        self.assertTrue((self.bridge.journal.path / "summary.json").is_file())

    def test_player_name_in_action_description_is_bounded_and_labeled(self):
        spatial = importlib.import_module(b.__package__ + ".spatial")
        text = spatial.describe_event({"event": "player_encounter", "player": {"playerName": "X" * 2000}})
        self.assertIn("玩家昵称（外部数据）", text)
        self.assertEqual(text.count("X"), 80)

    @unittest.skipUnless(os.name == "nt", "uses the Windows command line parser")
    def test_ocr_config_preserves_windows_paths_and_removes_argument_quotes(self):
        path = Path(fixture.PLUGIN) / "studybot_runner/studybot-adapter-deps/pytesseract.py"
        spec = importlib.util.spec_from_file_location("review_tesseract", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            module._config_arguments(r'--tessdata-dir "C:\Program Files\Tesseract-OCR\tessdata" --psm 7 -c tessedit_char_whitelist=123'),
            ["--tessdata-dir", r"C:\Program Files\Tesseract-OCR\tessdata", "--psm", "7", "-c", "tessedit_char_whitelist=123"],
        )

    def test_vent_reachability_matches_paths_for_directed_network(self):
        vents = importlib.import_module(b.__package__ + ".vents")
        nodes = [{"id": 1, "leftId": 2}, {"id": 2, "leftId": 3}, {"id": 3}, {"id": 4, "leftId": 1}]
        mapping = {v["id"]: v for v in nodes}
        for node in nodes:
            expected = {other["id"] for other in nodes if vents.path_between(nodes, node["id"], other["id"])}
            self.assertEqual(vents.reachable_vents(mapping, node["id"]), expected)
