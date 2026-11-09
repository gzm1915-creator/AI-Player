"""Run the real installer only against synthetic application directories."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell required")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="neko-install-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.game = self.base / "other user's game 游戏"
        self.neko = self.base / "other neko/plugins"
        self.neko.mkdir(parents=True)
        (self.game / "BepInEx/core").mkdir(parents=True)
        for name in ("Among Us.exe", "GameAssembly.dll", "BepInEx/core/BepInEx.Unity.IL2CPP.dll"):
            (self.game / name).touch()

    def run_install(self):
        # Mock only process detection. The actual copy, path validation,
        # integrity checks and TOML writing run unmodified inside the fixture.
        command = "function Get-Process { param($Name,$ErrorAction) }; & " + quote(ROOT / "install.ps1") + " -GameDir " + quote(self.game)
        environment = dict(os.environ)
        # A PowerShell 7 parent may export its own module paths to Windows PS5.
        # Let Windows PowerShell initialize the module paths for its version.
        environment.pop("PSModulePath", None)
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command], capture_output=True, text=True, timeout=30, env=environment)

    def test_fresh_install_and_preservation(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.game / "BepInEx/plugins/AUTelemetryProbe"
        self.assertEqual(hashlib.sha256((dest / "AUTelemetryProbe.dll").read_bytes()).digest(), hashlib.sha256((ROOT / "game-plugin/AUTelemetryProbe.dll").read_bytes()).digest())
        self.assertEqual(list(self.neko.iterdir()), [])
        self.assertTrue((dest / "data/skeld-route-observations.json").exists())
        self.assertFalse((self.neko / "core").exists())
        repeat = self.run_install()
        self.assertNotEqual(repeat.returncode, 0)
        self.assertIn("already exists", repeat.stderr)
        self.assertEqual(list(self.neko.iterdir()), [])

    def test_missing_bepinex_rejected_before_copy(self):
        (self.game / "BepInEx/core/BepInEx.Unity.IL2CPP.dll").unlink()
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Install BepInEx", result.stderr)
        self.assertFalse((self.neko / "among_us_neko").exists())
        self.assertFalse((self.game / "BepInEx/plugins").exists())

    def test_existing_configuration_preserved(self):
        config = self.game / "BepInEx/config/com.dsh.autelprobe.cfg"
        config.parent.mkdir(parents=True)
        config.write_text("preserve me", encoding="utf-8")
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing AUTelemetryProbe configuration", result.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), "preserve me")
        self.assertFalse((self.neko / "among_us_neko").exists())


if __name__ == "__main__":
    unittest.main()
