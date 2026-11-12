from __future__ import annotations

from . import skill


def run_skill(
    window: str = "Among Us",
    fps: float = 30.0,
    backend: str = "auto",
    status_path: str | None = None,
) -> None:
    """Run the StartReactor skill after its minigame has been opened."""
    arguments = [
        "--live",
        "--window",
        window,
        "--fps",
        str(fps),
        "--backend",
        backend,
    ]
    if status_path:
        arguments.extend(["--status-json", status_path])
    code = skill.main(arguments)
    if code not in (None, 0):
        raise RuntimeError(f"StartReactor skill exited with code {code}")


__all__ = ["run_skill", "skill"]
