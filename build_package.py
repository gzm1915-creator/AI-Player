"""Developer packaging tool. Source paths are build inputs, never runtime defaults."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

VERSION = "0.1.35"


def build(args):
    root = Path(__file__).resolve().parent
    game = root / "game-plugin"
    game.mkdir(exist_ok=True)
    shutil.copy2(args.probe, game / "AUTelemetryProbe.dll")
    route = json.loads(Path(args.route).read_text(encoding="utf-8"))
    # Share geometry, not the developer's runtime/profile/session/evidence data.
    sanitized = {
        "schemaVersion": route.get("schemaVersion", "1.0.0"), "mapId": "TheSkeld",
        "anchors": [{k: a[k] for k in ("anchorId", "position", "roomId", "kind") if k in a} for a in route["anchors"]],
        "routeSegments": [{k: s[k] for k in ("segmentId", "fromAnchorId", "toAnchorId", "path", "routePoints", "status") if k in s} for s in route["routeSegments"]],
    }
    (game / "data").mkdir(exist_ok=True)
    (game / "data/skeld-route-observations.json").write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
    (game / "runtime-control.json").write_text(json.dumps({"revision": 0, "autoNavigation": False, "autoReportBodies": False, "visualCapture": False, "meetingControl": True, "useVents": False, "minigameControl": False}), encoding="utf-8")
    if args.probe_source:
        source = root / "game-source"
        source.mkdir(exist_ok=True)
        for file in Path(args.probe_source).glob("*.cs"):
            shutil.copy2(file, source / file.name)
        project = Path(args.probe_source) / "AUTelemetryProbe.csproj"
        text = project.read_text(encoding="utf-8-sig")
        # Building requires an explicit GameRoot, never the author's machine path.
        lines = [line for line in text.splitlines() if '<GameRoot Condition=' not in line]
        (source / project.name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if args.neko_package:
        shutil.copy2(args.neko_package, root / f"among_us_neko-{VERSION}.neko-plugin")
    files = sorted(p for p in root.rglob("*") if p.is_file() and not {"__pycache__", "bin", "obj", "outputs"}.intersection(p.relative_to(root).parts) and p.name != "SHA256SUMS.txt" and (p.suffix != ".neko-plugin" or p.name == f"among_us_neko-{VERSION}.neko-plugin"))
    manifest = root / "SHA256SUMS.txt"
    manifest.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}\n" for p in files), encoding="utf-8")
    archive = output / f"AmongUs-Virtual-Player-{VERSION}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for file in [*files, manifest]:
            bundle.write(file, Path(f"AmongUs-Virtual-Player-{VERSION}") / file.relative_to(root))
    print(f"Package: {archive}; files={len(files)+1}; bytes={archive.stat().st_size}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", required=True, help="Compiled AUTelemetryProbe.dll")
    parser.add_argument("--route", required=True, help="Skeld route observations JSON")
    parser.add_argument("--probe-source", help="Optional C# source directory")
    parser.add_argument("--output", required=True)
    parser.add_argument("--neko-package", help="Native .neko-plugin built with N.E.K.O. plugin CLI")
    build(parser.parse_args())
