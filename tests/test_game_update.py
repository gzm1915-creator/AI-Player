"""Run the real game update script only against synthetic game directories."""
import os
import json
import shutil
import subprocess
import unittest
from pathlib import Path
import test_install as fixture

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('powershell.exe'), 'Windows PowerShell required')
class GameUpdateTests(unittest.TestCase):
    def setUp(self):
        fixture.InstallTests.setUp(self)
        self.target = self.game / 'BepInEx/plugins/AUTelemetryProbe/AUTelemetryProbe.dll'
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'old-game-component')
        (self.target.parent / 'runtime-control.json').write_text('{"autoNavigation":false}')
        (self.target.parent / 'route.json').write_text('preserve recorded routes')

    def run_update(self, script=None, check_only=False, running=False):
        command = 'function Get-Process { param($Name,$ErrorAction) ' + ('1' if running else '') + ' }; & '
        command += fixture.quote(script or ROOT / 'update-game.ps1') + ' -GameDir ' + fixture.quote(self.game)
        if check_only: command += ' -CheckOnly'
        environment = dict(os.environ)
        environment.pop('PSModulePath', None)
        return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
                              capture_output=True, text=True, timeout=30, env=environment)

    def test_update_backs_up_dll_and_preserves_routes_and_settings(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), (ROOT / 'game-plugin/AUTelemetryProbe.dll').read_bytes())
        backups = list(self.target.parent.glob('*.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'old-game-component')
        self.assertEqual((self.target.parent / 'route.json').read_text(), 'preserve recorded routes')
        self.assertEqual((self.target.parent / 'runtime-control.json').read_text(), '{"autoNavigation":false}')

    def test_running_game_is_refused_without_any_overwrite(self):
        result = self.run_update(running=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), b'old-game-component')
        self.assertFalse(list(self.target.parent.glob('*.backup-*')))

    def test_check_only_never_writes_the_existing_game(self):
        result = self.run_update(check_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), b'old-game-component')
        self.assertFalse(list(self.target.parent.glob('*.backup-*')))

    def test_bad_source_hash_is_rejected_before_copy(self):
        package = self.base / 'damaged-package'
        (package / 'game-plugin').mkdir(parents=True)
        shutil.copy2(ROOT / 'update-game.ps1', package / 'update-game.ps1')
        (package / 'game-plugin/AUTelemetryProbe.dll').write_bytes(b'broken')
        (package / 'SHA256SUMS.txt').write_text('0' * 64 + '  game-plugin/AUTelemetryProbe.dll\n')
        result = self.run_update(package / 'update-game.ps1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), b'old-game-component')
        self.assertFalse(list(self.target.parent.glob('*.backup-*')))

    def route_fixture(self):
        path = self.target.parent / 'data/skeld-route-observations.json'
        path.parent.mkdir()
        data = {"mapId": "TheSkeld", "custom": {"keep": [1, 2, "extra"]}, "anchors": [
            {"anchorId": "weapons.smooth_arrival", "position": {"x": 9.05, "y": 1.04}, "custom": "keep"}],
            "routeSegments": [{"segmentId": "recorded", "toAnchorId": "weapons.smooth_arrival",
                               "status": "observed_success", "path": [{"x": 3, "y": 1}, {"x": 9.05, "y": 1.04}]}]}
        path.write_text(json.dumps(data), encoding='utf-8')
        return path, data

    def test_upgrade_adds_missing_console_alias_and_preserves_custom_geometry(self):
        path, before = self.route_fixture()
        original = path.read_bytes()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(path.read_text(encoding='utf-8-sig'))
        alias = after['anchors'].pop()
        self.assertEqual(alias['anchorId'], 'skeld.weapons.clear-asteroids')
        self.assertEqual(alias['position'], before['anchors'][0]['position'])
        self.assertEqual(after, before)
        backups = list(path.parent.glob('skeld-route-observations.json.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_existing_custom_console_alias_and_check_only_preserve_route_bytes(self):
        path, data = self.route_fixture()
        original = path.read_bytes()
        self.assertEqual(self.run_update(check_only=True).returncode, 0)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(path.parent.glob('*.backup-*')))
        data['anchors'].append({'anchorId':'skeld.weapons.clear-asteroids', 'position':{'x':9, 'y':2}, 'custom':True})
        path.write_text(json.dumps(data), encoding='utf-8')
        original = path.read_bytes()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_bytes(), original)

    def test_unrecorded_route_node_does_not_invent_a_task_route(self):
        path, data = self.route_fixture()
        data['routeSegments'][0]['status'] = 'candidate'
        path.write_text(json.dumps(data), encoding='utf-8')
        original = path.read_bytes()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_bytes(), original)
