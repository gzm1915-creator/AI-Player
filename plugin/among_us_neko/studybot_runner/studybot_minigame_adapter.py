from __future__ import annotations

import argparse
import ctypes
import importlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from ctypes import wintypes
from pathlib import Path


DEPENDENCY_ROOT = Path(__file__).resolve().parent / "studybot-adapter-deps"
if DEPENDENCY_ROOT.is_dir():
    sys.path.insert(0, str(DEPENDENCY_ROOT))

import cv2
import numpy as np
from controller_lock import acquire_named_mutex, release_named_mutex
ADAPTER_MUTEX_NAME = r"Local\AmongUsAI_MinigameAdapter"
PLUGIN_ROOT = Path(os.environ["AU_PLUGIN_ROOT"])
PROJECT_ROOT = Path(os.environ["AU_STUDYBOT_STATE_DIR"])
NAVIGATION_COMMAND_PATH = Path(os.environ["AU_NAV_COMMAND"])
NAVIGATION_STATUS_PATH = Path(os.environ["AU_NAV_STATUS"])


def read_latest_telemetry() -> dict:
    for _ in range(20):
        try:
            with (PLUGIN_ROOT / "latest.json").open(
                "r",
                encoding="utf-8",
            ) as stream:
                return json.load(stream)
        except (OSError, json.JSONDecodeError):
            time.sleep(0.05)
    raise OSError("Telemetry is unavailable.")


# StudyBot imports both `cv2` and the historical `cv2.cv2` module path.
if "cv2.cv2" not in sys.modules:
    sys.modules["cv2.cv2"] = cv2


METHOD_BY_TASK = {
    "FixLights": "sabotage_electrical",
    "RestoreOxy": "sabotage_oxygen",
    "FixComms": "sabotage_communication",
    "ResetReactor": "sabotage_reactor",
    "DivertPower": "divert_power",
    "AcceptPower": "accept_power",
    "SwipeCard": "swipe_card",
    "UploadData": "download_upload",
    "DownloadData": "download_upload",
    "FuelEngines": "fuel_engines",
    "EmptyGarbage": "empty_chute",
    "EmptyChute": "empty_chute",
    "FixWiring": "fix_wires",
    "PrimeShields": "prime_shields",
    "InspectSample": "inspect_sample",
    "AlignEngineOutput": "align_engine_output",
    "ClearAsteroids": "clear_asteroids",
    "CleanO2Filter": "clean_O2_filter",
    "CalibrateDistributor": "calibrate_distributor",
    "StartReactor": "start_reactor",
    "VentCleaning": "vent_cleaning",
    "StabilizeSteering": "stabilize_steering",
    "ChartCourse": "chart_course",
    "UnlockManifolds": "unlock_manifold",
    "SubmitScan": "submit_scan",
}


def enable_oxygen_opencv(tasks_module):
    """The original oxygen solver needs full OpenCV; never silently use the shim."""
    saved_path = list(sys.path)
    saved_modules = {name: value for name, value in sys.modules.items() if name == "cv2" or name.startswith("cv2.")}
    try:
        sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != DEPENDENCY_ROOT.resolve()]
        for name in saved_modules:
            sys.modules.pop(name, None)
        full_cv2 = importlib.import_module("cv2")
        if not hasattr(full_cv2, "imread"):
            raise ImportError("full OpenCV is unavailable")
        full_cv2.cv2 = full_cv2
        tasks_module.cv2 = full_cv2
        # Compatibility for the preserved NumPy 1.x-era task source.
        if not hasattr(np, "int0"):
            np.int0 = np.intp
    except ImportError as exc:
        sys.modules.update(saved_modules)
        raise RuntimeError("氧气修复需要在技能 Python 安装 opencv-python，并安装 Tesseract OCR；其他技能仍可使用。") from exc
    finally:
        sys.path[:] = saved_path
SWIPE_DRAG_SECONDS = 0.40
UNLOCK_CELL_ORIGINS = [
    (585, 395),
    (737, 395),
    (890, 395),
    (1040, 395),
    (1195, 395),
    (585, 548),
    (737, 548),
    (890, 548),
    (1040, 548),
    (1195, 548),
]
UNLOCK_BUTTON_CENTERS = [
    (660, 450),
    (820, 450),
    (960, 450),
    (1120, 450),
    (1260, 450),
    (660, 620),
    (820, 620),
    (960, 620),
    (1120, 620),
    (1260, 620),
]
UNLOCK_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "work"
    / "game-assets-private"
    / "initialmaps-ripped"
    / "Assets"
    / "Texture2D"
    / "UnlockManifolds.png"
)
UNLOCK_TEMPLATE_BOXES = {
    1: (128, 175, 211, 255),
    2: (212, 175, 294, 255),
    3: (43, 175, 126, 255),
    4: (77, 95, 158, 175),
    5: (293, 175, 378, 255),
    6: (211, 598, 294, 676),
    7: (378, 175, 465, 255),
    8: (128, 598, 212, 676),
    9: (465, 175, 550, 255),
    10: (550, 175, 632, 255),
}
INSPECT_SAMPLE_TUBE_X = [733, 846, 963, 1074, 1187]
VENT_CLEANING_LAYOUT_PATH = PROJECT_ROOT / "vent-cleaning-layout.json"


def request_close_minigame() -> None:
    import uuid

    try:
        if not read_latest_telemetry().get("ui", {}).get(
            "minigameOpen"
        ):
            return
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return

    NAVIGATION_COMMAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = NAVIGATION_COMMAND_PATH.with_suffix(
        f".{uuid.uuid4().hex}.tmp"
    )
    temporary.write_text(
        json.dumps(
            {
                "commandId": uuid.uuid4().hex,
                "action": "close_minigame",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(NAVIGATION_COMMAND_PATH)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            if not read_latest_telemetry().get("ui", {}).get(
                "minigameOpen"
            ):
                return
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return
        time.sleep(0.1)

    helper = PROJECT_ROOT / "work" / "close_game_minigame.py"
    if helper.is_file():
        try:
            subprocess.run(
                [sys.executable, str(helper)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def send_plugin_action(
    action: str,
    *,
    timeout_seconds: float = 2.0,
    **fields: object,
) -> bool:
    import uuid

    sample_action = action in ("start_inspect_sample", "select_inspect_sample")
    command_id = ("neko-" if sample_action else "") + uuid.uuid4().hex
    if sample_action:
        telemetry = read_latest_telemetry()
        fields = {"expectedSessionId": telemetry.get("sessionId"),
                  "expectedMatchId": telemetry.get("game", {}).get("matchId"),
                  "issuedAtUtc": datetime.now(timezone.utc).isoformat(), **fields}
    command = {
        "commandId": command_id,
        "action": action,
        **fields,
    }
    if sample_action:
        # Use the same Windows-safe file hand-off as the parent plugin.
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location("sample_protocol_files", Path(__file__).resolve().parent.parent / "files.py")
        protocol = module_from_spec(spec)
        spec.loader.exec_module(protocol)
        protocol.atomic(NAVIGATION_COMMAND_PATH, command)
        return wait_plugin_action(command_id, timeout_seconds)
    NAVIGATION_COMMAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = NAVIGATION_COMMAND_PATH.with_suffix(
        f".{uuid.uuid4().hex}.tmp"
    )
    temporary.write_text(
        json.dumps(
            command,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(NAVIGATION_COMMAND_PATH)

    return wait_plugin_action(command_id, timeout_seconds)


def wait_plugin_action(command_id, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            status = json.loads(
                NAVIGATION_STATUS_PATH.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            time.sleep(0.02)
            continue
        if status.get("commandId") != command_id:
            time.sleep(0.02)
            continue
        return status.get("state") == "completed"
    return False


def find_game_window() -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.EnumWindows.restype = ctypes.c_bool
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowRect.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.RECT),
    ]
    user32.GetWindowRect.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    candidates: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)
    def callback(window, _):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(
            window,
            ctypes.byref(process_id),
        )
        process = kernel32.OpenProcess(
            0x1000,
            False,
            process_id.value,
        )
        if not process:
            return True
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(
                process,
                0,
                buffer,
                ctypes.byref(size),
            ):
                return True
            if Path(buffer.value).name.lower() != "among us.exe":
                return True
        finally:
            kernel32.CloseHandle(process)

        rect = wintypes.RECT()
        if not user32.GetWindowRect(window, ctypes.byref(rect)):
            return True
        area = max(0, rect.right - rect.left) * max(
            0,
            rect.bottom - rect.top,
        )
        if area > 0:
            candidates.append((area, int(window)))
        return True

    user32.EnumWindows(callback, None)
    if not candidates:
        return 0
    return max(candidates)[1]


def configure_game_surface() -> None:
    user32 = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    window = find_game_window()
    if not window:
        raise RuntimeError("Among Us window not found")
    os.environ["AU_STUDYBOT_HWND"] = str(window)
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.RECT),
    ]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.POINT),
    ]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.ShowWindow(window, 9)
    user32.SetForegroundWindow(window)
    time.sleep(0.15)

    rect = wintypes.RECT()
    if not user32.GetClientRect(window, ctypes.byref(rect)):
        return
    point = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(window, ctypes.byref(point)):
        return

    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return
    os.environ["AU_STUDYBOT_SCREEN_X"] = str(point.x)
    os.environ["AU_STUDYBOT_SCREEN_Y"] = str(point.y)
    os.environ["AU_STUDYBOT_SCREEN_W"] = str(width)
    os.environ["AU_STUDYBOT_SCREEN_H"] = str(height)


def capture_game_region(
    screen_x: int,
    screen_y: int,
    width: int,
    height: int,
):
    from PIL import Image
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        source_copy = 0x00CC0020
        capture_blt = 0x40000000
        if not gdi32.BitBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            screen_x,
            screen_y,
            source_copy | capture_blt,
        ):
            raise OSError("BitBlt failed while capturing the game window")

        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BitmapInfo(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BitmapInfoHeader),
                ("bmiColors", wintypes.DWORD * 3),
            ]

        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            pixels,
            ctypes.byref(info),
            0,
        ):
            raise OSError("GetDIBits failed while capturing the game window")
        return Image.frombuffer(
            "RGB",
            (width, height),
            pixels,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def patch_image_grab() -> None:
    from PIL import ImageGrab

    target_x = int(os.environ.get("AU_STUDYBOT_SCREEN_X", "0"))
    target_y = int(os.environ.get("AU_STUDYBOT_SCREEN_Y", "0"))
    target_w = int(os.environ.get("AU_STUDYBOT_SCREEN_W", "1920"))
    target_h = int(os.environ.get("AU_STUDYBOT_SCREEN_H", "1080"))

    def grab(bbox=None, *args, **kwargs):
        base_left, base_top, base_right, base_bottom = (
            (0, 0, 1920, 1080) if bbox is None else tuple(map(float, bbox))
        )
        scale_x = target_w / 1920.0
        scale_y = target_h / 1080.0
        screen_bbox = (
            target_x + round(base_left * scale_x),
            target_y + round(base_top * scale_y),
            target_x + round(base_right * scale_x),
            target_y + round(base_bottom * scale_y),
        )
        try:
            image = capture_game_region(
                screen_bbox[0],
                screen_bbox[1],
                screen_bbox[2] - screen_bbox[0],
                screen_bbox[3] - screen_bbox[1],
            )
        except OSError:
            image, _ = latest_visual_snapshot()
            left, top, right, bottom = (
                (0, 0, 1920, 1080) if bbox is None else tuple(map(float, bbox))
            )
            return image.crop(
                (round(left), round(top), round(right), round(bottom))
            )
        expected_size = (
            max(1, round(base_right - base_left)),
            max(1, round(base_bottom - base_top)),
        )
        if image.size != expected_size:
            image = image.resize(expected_size)
        return image

    ImageGrab.grab = grab


def latest_visual_snapshot():
    from PIL import Image

    telemetry = read_latest_telemetry()
    session = str(telemetry["sessionId"])[:8]
    image_root = PLUGIN_ROOT / "visual" / session
    images = sorted(
        image_root.rglob("*.png"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not images:
        raise OSError(f"No plugin visual frame is available under {image_root}")

    path = images[0]
    if time.time() - path.stat().st_mtime > 15.0:
        raise OSError(f"Newest plugin visual frame is stale: {path}")
    signature = (str(path), path.stat().st_mtime_ns)
    with Image.open(path) as source:
        image = source.convert("RGB")
        if image.size != (1920, 1080):
            image = image.resize(
                (1920, 1080),
                Image.Resampling.BILINEAR,
            )
        else:
            image = image.copy()
    return image, signature


def telemetry_indexed_points(
    field_name: str,
    count: int,
) -> list[tuple[float, float]] | None:
    try:
        telemetry = read_latest_telemetry()
        raw_points = telemetry["ui"].get(field_name, [])
        by_index: dict[int, tuple[float, float]] = {}
        for raw in raw_points:
            index = int(raw["index"])
            x = float(raw["x"])
            y = float(raw["y"])
            if (
                index < 0
                or index >= count
                or index in by_index
                or not np.isfinite(x)
                or not np.isfinite(y)
                or x < 0
                or x > 1920
                or y < 0
                or y > 1080
            ):
                return None
            by_index[index] = (x, y)
    except (
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return None
    if sorted(by_index) != list(range(count)):
        return None
    return [by_index[index] for index in range(count)]


def telemetry_upload_button() -> tuple[float, float] | None:
    try:
        raw = read_latest_telemetry()["ui"].get("uploadButton")
        if not isinstance(raw, dict):
            return None
        x = float(raw["x"])
        y = float(raw["y"])
    except (
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return None
    if (
        not np.isfinite(x)
        or not np.isfinite(y)
        or x < 0
        or x > 1920
        or y < 0
        or y > 1080
    ):
        return None
    return x, y


REACTOR_LIGHT_POINTS = [
    (500, 450),
    (650, 450),
    (790, 450),
    (500, 600),
    (650, 600),
    (790, 600),
    (500, 750),
    (650, 750),
    (790, 750),
]
REACTOR_BUTTON_POINTS = [
    (1140, 450),
    (1260, 450),
    (1400, 450),
    (1140, 600),
    (1260, 600),
    (1400, 600),
    (1140, 750),
    (1260, 750),
    (1400, 750),
]
REACTOR_ON_RGB = np.array([68, 168, 255], dtype=np.int16)
_REACTOR_MANIFEST_OFFSETS: dict[str, int] = {}


def reactor_capture_events() -> list[dict]:
    try:
        telemetry = read_latest_telemetry()
        session = str(telemetry["sessionId"])[:8]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return []
    burst_root = PLUGIN_ROOT / "visual" / session / "task-bursts"
    if not burst_root.is_dir():
        return []
    manifests = sorted(
        burst_root.glob("start-reactor-*/reactor-capture.jsonl"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not manifests:
        return []
    manifest = manifests[0]
    try:
        size = manifest.stat().st_size
        offset = _REACTOR_MANIFEST_OFFSETS.get(str(manifest), 0)
        if offset > size:
            offset = 0
        events: list[dict] = []
        with manifest.open("rb") as stream:
            stream.seek(offset)
            consumed = 0
            for raw_line in stream:
                if not raw_line.endswith(b"\n"):
                    break
                consumed += len(raw_line)
                try:
                    event = json.loads(
                        raw_line.decode("utf-8", errors="replace")
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if event.get("event") == "capture":
                    events.append(event)
            _REACTOR_MANIFEST_OFFSETS[str(manifest)] = offset + consumed
        return events
    except OSError:
        return []


def load_reactor_capture_frame(event: dict):
    from PIL import Image

    try:
        path = Path(str(event["image"]))
        with Image.open(path) as source:
            image = source.convert("RGB")
            if image.size != (1920, 1080):
                image = image.resize(
                    (1920, 1080),
                    Image.Resampling.BILINEAR,
                )
            else:
                image = image.copy()
        return image
    except (OSError, KeyError, TypeError):
        return None


def detect_reactor_lit_indices(image) -> tuple[int | None, list[float]]:
    frame = np.asarray(image, dtype=np.int16)
    scores: list[float] = []
    for x, y in REACTOR_LIGHT_POINTS:
        patch = frame[max(0, y - 5):y + 6, max(0, x - 5):x + 6]
        if patch.size == 0:
            scores.append(0.0)
            continue
        distance = np.max(
            np.abs(patch - REACTOR_ON_RGB),
            axis=2,
        )
        scores.append(float(np.mean(distance <= 42)))
    ranked = sorted(
        enumerate(scores),
        key=lambda item: item[1],
        reverse=True,
    )
    best_index, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score >= 0.035 and (
        second_score <= 0.01 or best_score >= second_score * 1.35
    ):
        return best_index, scores
    return None, scores


def direct_reactor_frame():
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=(0, 0, 1920, 1080))


def latest_reactor_visual_state():
    for event in reversed(reactor_capture_events()):
        image = load_reactor_capture_frame(event)
        if image is None:
            continue
        visual_index, scores = detect_reactor_lit_indices(image)
        metadata_lit = tuple(
            int(value)
            for value in event.get("litIndices", [])
        )
        metadata_index = (
            metadata_lit[0] if len(metadata_lit) == 1 else None
        )
        if visual_index is not None:
            return visual_index, "screenshot", scores, event
        if metadata_index is not None:
            return metadata_index, "telemetry_fallback", scores, event
        return None, "idle", scores, event

    try:
        image = direct_reactor_frame()
    except OSError:
        return None, "unavailable", [0.0] * 9, {}
    visual_index, scores = detect_reactor_lit_indices(image)
    return visual_index, "direct_screenshot", scores, {}


def reactor_task(telemetry: dict, task_id: int | None) -> dict | None:
    return next(
        (
            value
            for value in telemetry.get("ui", {}).get("ownTasks", [])
            if (
                task_id is None
                or int(value.get("taskId", -1)) == task_id
            )
        ),
        None,
    )


def reactor_task_progressed(
    task: dict | None,
    initial_step: int,
    target_max_step: int,
) -> bool:
    return (
        task is None
        or bool(task.get("complete"))
        or int(task.get("step", 0)) >= target_max_step
    )


def click_reactor_sequence(sequence: list[int]) -> None:
    for index in sequence:
        if index < 0 or index >= len(REACTOR_BUTTON_POINTS):
            raise RuntimeError(f"Invalid reactor button index: {index}")
        if not send_plugin_action(
            "click_reactor_button",
            timeout_seconds=2.5,
            targetNumber=index,
        ):
            raise RuntimeError(
                f"StartReactor button click failed: {index}"
            )
        print(
            f"start_reactor feedback_click={index}",
            flush=True,
        )
        time.sleep(0.30)


def wait_reactor_round_feedback(
    task_id: int | None,
    initial_step: int,
    target_max_step: int,
    final_round: bool,
) -> bool:
    deadline = time.monotonic() + (4.5 if final_round else 2.5)
    while time.monotonic() < deadline:
        time.sleep(0.06)
        telemetry = read_latest_telemetry()
        task = reactor_task(telemetry, task_id)
        if reactor_task_progressed(task, initial_step, target_max_step):
            return True
        if not telemetry.get("ui", {}).get("minigameOpen"):
            return False
        lit = telemetry.get("ui", {}).get("startReactorLitIndices", [])
        if lit and not final_round:
            return True
    return False


def solve_start_reactor_visual(
    task_id: int | None,
    initial_step: int,
) -> None:
    deadline = time.monotonic() + 70.0
    target_max_step = max(1, initial_step + 1)
    round_index = 0
    captured: list[int] = []
    last_lit: int | None = None
    quiet_since: float | None = None
    saw_open = False
    closed_since: float | None = None

    while time.monotonic() < deadline:
        telemetry = read_latest_telemetry()
        task = reactor_task(telemetry, task_id)
        if task is not None:
            target_max_step = max(
                target_max_step,
                int(task.get("maxStep", target_max_step)),
            )
        if reactor_task_progressed(
            task,
            initial_step,
            target_max_step,
        ):
            print("start_reactor task_progressed", flush=True)
            return
        if not telemetry.get("ui", {}).get("minigameOpen"):
            if saw_open:
                if closed_since is None:
                    closed_since = time.monotonic()
                elif time.monotonic() - closed_since >= 1.5:
                    raise RuntimeError(
                        "StartReactor closed before task progression"
                    )
            time.sleep(0.05)
            continue
        saw_open = True
        closed_since = None

        lit, source, scores, capture_event = latest_reactor_visual_state()
        now = time.monotonic()
        if lit is not None:
            quiet_since = None
            if lit != last_lit:
                captured.append(lit)
                print(
                    f"start_reactor visual_capture={lit} "
                    f"source={source} sequence={captured} "
                    f"scores={[round(value, 3) for value in scores]}",
                    flush=True,
                )
        elif last_lit is not None:
            quiet_since = now
        last_lit = lit

        expected_length = min(5, round_index + 1)
        telemetry_sequence = [
            int(value)
            for value in telemetry.get("ui", {}).get(
                "startReactorSequence",
                [],
            )
        ]
        sequence_ready = (
            quiet_since is not None
            and now - quiet_since >= 0.55
            and (
                len(captured) >= expected_length
                or len(telemetry_sequence) == expected_length
            )
        )
        if not sequence_ready:
            time.sleep(0.025)
            continue

        visual_sequence = captured[-expected_length:]
        if len(telemetry_sequence) == expected_length:
            sequence = telemetry_sequence
            if (
                len(visual_sequence) == expected_length
                and visual_sequence != sequence
            ):
                print(
                    f"start_reactor visual_mismatch "
                    f"visual={visual_sequence} crosscheck={sequence}",
                    flush=True,
                )
        elif len(visual_sequence) == expected_length:
            sequence = visual_sequence
        else:
            time.sleep(0.025)
            continue

        print(
            f"start_reactor round={round_index + 1} "
            f"recognized={sequence} feedback_port=plugin",
            flush=True,
        )
        click_reactor_sequence(sequence)
        final_round = round_index >= 4
        if not wait_reactor_round_feedback(
            task_id,
            initial_step,
            target_max_step,
            final_round,
        ):
            raise RuntimeError(
                f"StartReactor round {round_index + 1} "
                "did not produce UI feedback"
            )
        if final_round:
            print("start_reactor task_progressed", flush=True)
            return

        round_index += 1
        captured.clear()
        last_lit = None
        quiet_since = None
        time.sleep(0.35)

    raise RuntimeError("StartReactor visual solver did not progress")


class ReactorTelemetryButtonMapper:
    """Map the visual button index to the live Among Us client rectangle."""

    def __init__(self, fallback_mapper) -> None:
        self.fallback = fallback_mapper
        self.origin = (0.0, 0.0)
        self.size = (1920.0, 1080.0)

    def set_viewport(
        self,
        origin_x: float,
        origin_y: float,
        width: float,
        height: float,
    ) -> None:
        self.origin = (float(origin_x), float(origin_y))
        self.size = (float(width), float(height))
        self.fallback.set_viewport(origin_x, origin_y, width, height)

    def map_button(self, index: int) -> tuple[int, int]:
        try:
            telemetry = read_latest_telemetry()
            points = telemetry.get("ui", {}).get(
                "startReactorButtons",
                [],
            )
            if len(points) == 9:
                point = points[index - 1]
                x = float(point["x"])
                y = float(point["y"])
                ox, oy = self.origin
                width, height = self.size
                return (
                    round(ox + x * width / 1920.0),
                    round(oy + y * height / 1080.0),
                )
        except (
            OSError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            IndexError,
        ):
            pass
        return self.fallback.map_button(index)


class ReactorButtonIndexMapper:
    """Expose a reactor button index as controller-compatible coordinates."""

    @staticmethod
    def map_button(index: int) -> tuple[int, int]:
        return index, 0


class ReactorPluginMouse:
    """Forward visual controller clicks through the plugin action bridge."""

    def click(self, x: int, y: int, delay: float = 0.0) -> None:
        if x < 1 or x > 9:
            raise RuntimeError(f"Invalid reactor button index: {x}")
        if not send_plugin_action(
            "click_reactor_button",
            timeout_seconds=2.5,
            targetNumber=int(x),
        ):
            raise RuntimeError(
                f"StartReactor plugin click failed: {x}"
            )
        if delay:
            time.sleep(delay)


def _reactor_progress_count(ui: dict) -> int:
    """Return the number of green progress lights on the left panel."""
    colors = ui.get("startReactorIndicatorColors") or []
    return sum(
        1
        for color in colors
        if (
            isinstance(color, dict)
            and float(color.get("g", 0.0)) >= 0.6
            and float(color.get("r", 1.0)) < 0.4
            and float(color.get("b", 1.0)) < 0.4
        )
    )


def _reactor_task_complete(telemetry: dict, task_id: int | None) -> bool:
    task = reactor_task(telemetry, task_id)
    return bool(task and task.get("complete"))


def _recent_reactor_flash_history(
    session_id: str,
) -> list[int]:
    """Recover flashes from the latest open minigame segment."""
    rows: list[tuple[str, bool, tuple[int, ...]]] = []
    for path in PLUGIN_ROOT.glob("telemetry*.jsonl"):
        try:
            with path.open("rb") as stream:
                raw_lines = stream.read().splitlines()
        except OSError:
            continue
        for raw_line in raw_lines:
            try:
                value = json.loads(
                    raw_line.decode("utf-8", errors="replace")
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if value.get("sessionId") != session_id:
                continue
            ui = value.get("ui") or {}
            rows.append(
                (
                    str(value.get("sentAtUtc", "")),
                    bool(ui.get("minigameOpen")),
                    tuple(
                        int(index)
                        for index in (
                            ui.get("startReactorLitIndices") or []
                        )
                    ),
                )
            )

    rows.sort(key=lambda row: row[0])
    sequence: list[int] = []
    last_lit: tuple[int, ...] = ()
    for _, minigame_open, lit in rows:
        if not minigame_open:
            sequence.clear()
            last_lit = ()
            continue
        if len(lit) == 1 and not last_lit:
            sequence.append(lit[0])
        last_lit = lit
    return sequence


def run_start_reactor_skill(task_id: int | None = None) -> None:
    """Watch each flash round, then visibly press its buttons one by one."""
    deadline = time.monotonic() + 120.0
    captured: list[int] = []
    last_lit: tuple[int, ...] = ()
    quiet_since: float | None = None
    saw_open = False
    closed_since: float | None = None

    # Adapter startup can lag behind the short first flash. Recover recent
    # flashes from the telemetry journal before starting live detection.
    initial_telemetry = read_latest_telemetry()
    initial_ui = initial_telemetry.get("ui", {})
    initial_progress = _reactor_progress_count(initial_ui)
    initial_history = _recent_reactor_flash_history(
        str(initial_telemetry.get("sessionId", ""))
    )
    if initial_history:
        expected_initial = (
            min(5, initial_progress)
            if initial_progress > 0
            else min(5, len(initial_history))
        )
        captured.extend(initial_history[-expected_initial:])
        print(
            f"start_reactor telemetry_history={captured} "
            f"progress={initial_progress}",
            flush=True,
        )
        last_lit = tuple(
            int(value)
            for value in (initial_ui.get("startReactorLitIndices") or [])
        )
        if not last_lit:
            quiet_since = time.monotonic()

    while time.monotonic() < deadline:
        telemetry = read_latest_telemetry()
        ui = telemetry.get("ui", {})
        if _reactor_task_complete(telemetry, task_id):
            print("start_reactor task_progressed", flush=True)
            return

        if not ui.get("minigameOpen"):
            if saw_open:
                closed_since = closed_since or time.monotonic()
                if time.monotonic() - closed_since >= 1.5:
                    raise RuntimeError(
                        "StartReactor closed before task progression"
                    )
            time.sleep(0.05)
            continue
        saw_open = True
        closed_since = None

        progress = _reactor_progress_count(ui)
        expected_length = (
            min(5, progress)
            if progress > 0
            else min(5, max(1, len(captured)))
        )

        current_lit = tuple(
            int(value)
            for value in (ui.get("startReactorLitIndices") or [])
        )
        now = time.monotonic()
        if current_lit:
            quiet_since = None
        elif captured and quiet_since is None:
            quiet_since = now

        lit = current_lit
        if len(lit) == 1:
            if not last_lit:
                captured.append(lit[0])
                print(
                    f"start_reactor visual_capture={lit[0]} "
                    f"round={expected_length} sequence={captured}",
                    flush=True,
                )
            quiet_since = None
        elif len(lit) > 1:
            raise RuntimeError(
                "StartReactor error flash detected; sequence reset"
            )
        elif last_lit:
            quiet_since = now
        last_lit = lit

        if (
            len(captured) < expected_length
            or quiet_since is None
            or now - quiet_since < 0.45
        ):
            time.sleep(0.025)
            continue

        sequence = captured[-expected_length:]
        print(
            f"start_reactor round={expected_length} "
            f"recognized={sequence} feedback_port=mouse",
            flush=True,
        )
        click_reactor_sequence(sequence)

        # Ignore the blue feedback flashes produced by our own clicks. If the
        # next round has already started when the cooldown ends, the main loop
        # will observe its higher green-light count and capture its first flash.
        feedback_until = time.monotonic() + 0.85
        while time.monotonic() < feedback_until:
            feedback_telemetry = read_latest_telemetry()
            if _reactor_task_complete(feedback_telemetry, task_id):
                print("start_reactor task_progressed", flush=True)
                return
            reactor_capture_events()  # discard our click-feedback captures
            time.sleep(0.05)

        captured = []
        last_lit = ()
        quiet_since = None

    raise RuntimeError("StartReactor visual solver timed out")


def solve_start_reactor_external_visual(
    task_id: int | None,
    initial_step: int,
) -> None:
    """Run the provided ROI/GPU vision and state-machine implementation live."""
    from runtime.tasks.start_reactor import (
        BASE_HEIGHT,
        BASE_WIDTH,
        CoordinateMapper,
        MouseController,
        ReactorController,
        ReactorVision,
        WindowCapture,
        _fit_to_base,
    )

    deadline = time.monotonic() + 75.0
    target_max_step = max(1, initial_step + 1)
    hwnd = find_game_window()
    if not hwnd:
        try:
            hwnd = WindowCapture.find_window("Among Us")
        except RuntimeError:
            hwnd = 0
    vision = ReactorVision(backend="auto", work_size=128)
    capture = None
    if hwnd:
        capture = WindowCapture(hwnd)
        origin_x, origin_y, width, height = capture.client_rect()
        fallback_mapper = CoordinateMapper("window")
        mapper = ReactorTelemetryButtonMapper(fallback_mapper)
        mapper.set_viewport(origin_x, origin_y, width, height)
        mouse = MouseController()
        controller = ReactorController(
            vision,
            mapper,
            mouse,
            click_delay=0.12,
            press_trigger="dim",
            error_policy="multi_lit",
            auto_click=True,
        )
        capture_mode = "window"
    else:
        controller = ReactorController(
            vision,
            ReactorButtonIndexMapper(),
            ReactorPluginMouse(),
            click_delay=0.04,
            press_trigger="dim",
            error_policy="multi_lit",
            auto_click=True,
        )
        capture_mode = "plugin_capture"
    frame_period = 1.0 / 30.0
    next_task_poll = 0.0
    saw_open = False
    closed_since: float | None = None
    finished_since: float | None = None

    print(
        f"start_reactor external_visual backend={vision.backend} "
        f"work_size={vision.work_size} "
        f"capture_mode={capture_mode}",
        flush=True,
    )

    while time.monotonic() < deadline:
        started = time.monotonic()
        if started >= next_task_poll:
            telemetry = read_latest_telemetry()
            task = reactor_task(telemetry, task_id)
            if task is not None:
                target_max_step = max(
                    target_max_step,
                    int(task.get("maxStep", target_max_step)),
                )
            if reactor_task_progressed(
                task,
                initial_step,
                target_max_step,
            ):
                print(
                    "start_reactor external_visual task_progressed",
                    flush=True,
                )
                return
            if not telemetry.get("ui", {}).get("minigameOpen"):
                if saw_open:
                    if closed_since is None:
                        closed_since = started
                    elif started - closed_since >= 1.5:
                        raise RuntimeError(
                            "StartReactor external visual closed "
                            "before task progression"
                        )
            else:
                saw_open = True
                closed_since = None
            next_task_poll = started + 0.1

        frames = []
        if capture is not None:
            frames.append(capture.grab())
        else:
            events = reactor_capture_events()
            if not events:
                time.sleep(0.02)
                continue
            for event in events:
                captured_image = load_reactor_capture_frame(event)
                if captured_image is None:
                    continue
                frame = np.ascontiguousarray(
                    np.asarray(captured_image)[:, :, ::-1]
                )
                frames.append(frame)
            if not frames:
                time.sleep(0.02)
                continue

        for frame in frames:
            if frame.shape[1] != BASE_WIDTH or frame.shape[0] != BASE_HEIGHT:
                frame = _fit_to_base(frame)
            observation = vision.process(frame)
            result = controller.update(observation)
            if result is not None:
                print(
                    f"start_reactor external_visual {result}",
                    flush=True,
                )
            if observation.error_flash:
                print(
                    "start_reactor external_visual error_flash "
                    f"buttons={observation.lit_buttons()}",
                    flush=True,
                )
        if controller._finished:
            if finished_since is None:
                finished_since = started
            elif started - finished_since >= 2.0:
                telemetry = read_latest_telemetry()
                task = reactor_task(telemetry, task_id)
                if reactor_task_progressed(
                    task,
                    initial_step,
                    target_max_step,
                ):
                    print(
                        "start_reactor external_visual task_progressed",
                        flush=True,
                    )
                    return
                raise RuntimeError(
                    "StartReactor panel closed without task progression"
                )
        else:
            finished_since = None

        elapsed = time.monotonic() - started
        if elapsed < frame_period:
            time.sleep(frame_period - elapsed)

    raise RuntimeError("StartReactor external visual timed out")


def solve_upload_data(
    pyautogui,
    task_id: int | None,
    task_step: int,
) -> None:
    deadline = time.monotonic() + 26.0
    clicked = False
    last_progress_text = ""
    saw_progress = False
    closed_since: float | None = None
    task_missing_since: float | None = None

    while time.monotonic() < deadline:
        telemetry = read_latest_telemetry()
        tasks = telemetry.get("ui", {}).get("ownTasks", [])
        task = next(
            (
                value
                for value in tasks
                if (
                    task_id is None
                    or int(value.get("taskId", -1)) == task_id
                )
            ),
            None,
        )
        if task is not None and (
            bool(task.get("complete"))
            or int(task.get("step", 0)) > task_step
        ):
            print("upload_data task_progressed", flush=True)
            return
        if task is None:
            if task_missing_since is None:
                task_missing_since = time.monotonic()
            elif (
                saw_progress
                and time.monotonic() - task_missing_since >= 1.5
            ):
                print("upload_data task_removed_after_progress", flush=True)
                return
        else:
            task_missing_since = None

        if not telemetry.get("ui", {}).get("minigameOpen"):
            if closed_since is None:
                closed_since = time.monotonic()
            if time.monotonic() - closed_since >= 3.0:
                raise RuntimeError(
                    "UploadData closed before task progression"
                )
            time.sleep(0.05)
            continue
        closed_since = None

        progress_text = str(
            telemetry.get("ui", {}).get("uploadProgressText", "")
        )
        try:
            progress_percent = int(
                progress_text.strip().rstrip("%")
            )
        except ValueError:
            progress_percent = 0
        if progress_percent > 0:
            saw_progress = True
        button = telemetry_upload_button()
        if not clicked and task is not None:
            dispatched = send_plugin_action(
                "click_minigame_object",
                targetObjectName="dataTransfer_uploadButton",
                timeout_seconds=2.5,
            )
            if not dispatched and button is not None:
                pyautogui.moveTo(button[0], button[1])
                pyautogui.click()
                dispatched = True
            if not dispatched:
                raise RuntimeError(
                    "UploadData button is unavailable"
                )
            clicked = True
            print(
                f"upload_data started progress={progress_text!r}",
                flush=True,
            )
        last_progress_text = progress_text
        time.sleep(0.10)

    raise RuntimeError("UploadData did not progress before timeout")


def solve_vent_cleaning(
    pyautogui,
    task_id: int | None,
    initial_step: int,
) -> None:
    deadline = time.monotonic() + 20.0
    opened_at: float | None = None
    last_open_at = 0.0
    cleaned_clicks = 0

    while time.monotonic() < deadline:
        telemetry = read_latest_telemetry()
        tasks = telemetry.get("ui", {}).get("ownTasks", [])
        task = next(
            (
                value
                for value in tasks
                if (
                    task_id is None
                    or int(value.get("taskId", -1)) == task_id
                )
            ),
            None,
        )
        if (
            task is None
            or bool(task.get("complete"))
            or int(task.get("step", 0)) > initial_step
        ):
            print("vent_cleaning task_progressed", flush=True)
            return
        if not telemetry.get("ui", {}).get("minigameOpen"):
            if opened_at is None:
                opened_at = time.monotonic()
            elif time.monotonic() - opened_at >= 1.5:
                raise RuntimeError(
                    "VentCleaning closed before task progression"
                )
            time.sleep(0.05)
            continue
        opened_at = None

        vent_open = bool(
            telemetry.get("ui", {}).get("ventCleaningVentOpen")
        )
        if not vent_open:
            if time.monotonic() - last_open_at >= 0.45:
                send_plugin_action("open_vent_cleaning")
                last_open_at = time.monotonic()
            time.sleep(0.06)
            continue

        dirt_count = int(
            telemetry.get("ui", {}).get("ventCleaningDirtCount", 0)
        )
        cleaned_count = int(
            telemetry.get("ui", {}).get(
                "ventCleaningCleanedCount",
                0,
            )
        )
        positions = list(
            telemetry.get("ui", {}).get(
                "ventCleaningDirtPositions",
                [],
            )
        )
        print(
            f"vent_cleaning state total={dirt_count} "
            f"cleaned={cleaned_count} "
            f"active={len(positions)}",
            flush=True,
        )
        if dirt_count > 0 and cleaned_count >= dirt_count:
            time.sleep(0.15)
            continue
        if not positions:
            time.sleep(0.08)
            continue

        # activeChildren is a live list; clean index zero so the list always
        # shifts down. Each command removes one visible dirt patch.
        if not send_plugin_action(
            "clean_vent_dirt",
            targetNumber=0,
            timeout_seconds=2.0,
        ):
            raise RuntimeError("VentCleaning dirt click failed")
        cleaned_clicks += 1
        print(
            f"vent_cleaning cleaned_patch={cleaned_clicks} "
            f"remaining={len(positions) - 1}",
            flush=True,
        )
        time.sleep(0.10)

    raise RuntimeError("VentCleaning did not progress before timeout")


def solve_inspect_sample(
    pyautogui,
    task_id: int | None,
    task_step: int,
) -> None:
    from PIL import ImageGrab, ImageStat

    initial = read_latest_telemetry()
    session, match = initial.get("sessionId"), initial.get("game", {}).get("matchId")
    instance = initial.get("ui", {}).get("openMinigameInstanceId")
    original_task = next((t for t in initial.get("ui", {}).get("ownTasks", []) if t.get("taskId") == task_id), {})
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("sample_task_receipts", Path(__file__).resolve().parent.parent / "task_receipts.py")
    receipts = module_from_spec(spec)
    spec.loader.exec_module(receipts)
    def current_task():
        latest = read_latest_telemetry()
        ui = latest.get("ui", {})
        if (latest.get("sessionId") != session or latest.get("game", {}).get("matchId") != match
                or not ui.get("minigameOpen") or ui.get("openMinigameInstanceId") != instance
                or ui.get("openMinigameTaskId") != task_id or ui.get("meetingOpen")):
            raise RuntimeError("InspectSample window or match changed")
        task = next((t for t in ui.get("ownTasks", []) if t.get("taskId") == task_id), {})
        if (task.get("sample") or {}).get("supported") is not True:
            raise RuntimeError("InspectSample requires the native culture timer (0.6.87)")
        return task

    task = current_task()
    phase = task["sample"].get("phase")
    if task.get("complete") or phase in ("processing", "complete"):
        return  # The parent closes this window and continues other tasks.
    if phase == "not_started":
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            task = current_task()
            if task.get("complete") or task["sample"].get("phase") in ("processing", "ready", "complete"):
                print("studybot_adapter inspect_sample_culture_confirmed", flush=True)
                return
            send_plugin_action("start_inspect_sample", interactTaskId=task_id,
                               expectedMinigameInstanceId=instance, timeout_seconds=.5)
            time.sleep(.2)
        raise RuntimeError("InspectSample culture start was not confirmed; leaving instead of waiting")
    if phase != "ready":
        raise RuntimeError("InspectSample native phase is unavailable")

    deadline = time.monotonic() + 6
    stable_index: int | None = None
    stable_count = 0
    best_colors: list[tuple[float, float, float]] = []
    live_tubes = telemetry_indexed_points("inspectSampleTubes", 5)
    tube_points = (
        live_tubes
        if live_tubes is not None
        else [
            (float(center_x), 590.0)
            for center_x in INSPECT_SAMPLE_TUBE_X
        ]
    )

    while time.monotonic() < deadline:
        task = current_task()
        if task.get("complete"):
            return
        if task["sample"].get("phase") == "processing":
            return
        image = ImageGrab.grab()
        colors = []
        red_counts = []
        for center_x, center_y in tube_points:
            crop = image.crop(
                (
                    round(center_x - 9),
                    round(center_y - 28),
                    round(center_x + 9),
                    round(center_y + 28),
                )
            )
            colors.append(tuple(ImageStat.Stat(crop).mean))
            pixels = np.asarray(crop, dtype=np.int16)
            red_counts.append(
                int(
                    np.sum(
                        (pixels[:, :, 0] >= 190)
                        & (
                            pixels[:, :, 0]
                            > pixels[:, :, 1] + 35
                        )
                        & (
                            pixels[:, :, 0]
                            > pixels[:, :, 2] + 35
                        )
                    )
                )
            )

        median = tuple(
            sorted(color[channel] for color in colors)[2]
            for channel in range(3)
        )
        scores = [
            sum((color[channel] - median[channel]) ** 2 for channel in range(3))
            ** 0.5
            for color in colors
        ]
        red_candidate = max(
            range(len(red_counts)),
            key=red_counts.__getitem__,
        )
        red_is_distinct = (
            red_counts[red_candidate] >= 35
            and red_counts[red_candidate]
            >= max(
                2 * max(
                    red_counts[:red_candidate]
                    + red_counts[red_candidate + 1:]
                ),
                1,
            )
        )
        candidate = (
            red_candidate
            if red_is_distinct
            else max(range(len(scores)), key=scores.__getitem__)
        )
        best_colors = colors

        if red_is_distinct or scores[candidate] >= 18.0:
            if stable_index == candidate:
                stable_count += 1
            else:
                stable_index = candidate
                stable_count = 1
        else:
            stable_index = None
            stable_count = 0

        if stable_index is not None and stable_count >= 3:
            print(
                "inspect_sample "
                f"tube={candidate + 1} "
                f"score={scores[candidate]:.2f} "
                f"redPixels={red_counts[candidate]} "
                f"colors={best_colors}",
                flush=True,
            )
            clicked = send_plugin_action(
                "select_inspect_sample", targetNumber=candidate, interactTaskId=task_id,
                expectedMinigameInstanceId=instance,
            )
            if not clicked:
                raise RuntimeError("InspectSample selection control was rejected")
            verify_deadline = time.monotonic() + 3.0
            while time.monotonic() < verify_deadline:
                time.sleep(0.1)
                current = read_latest_telemetry()
                if current.get("sessionId") != session or current.get("game", {}).get("matchId") != match:
                    raise RuntimeError("InspectSample match changed before completion was confirmed")
                selected_task = next(
                    (
                        value
                        for value in current.get("ui", {}).get(
                            "ownTasks",
                            [],
                        )
                        if (
                            task_id is None
                            or int(value.get("taskId", -1)) == task_id
                        )
                    ),
                    None,
                )
                # Closing the window or a refreshed task list is not proof.
                # Import by file path: this runner is also a standalone child.
                receipt_state = {"sessionId": session, "matchId": match,
                    "telemetryAt": current.get("sentAtUtc"), "self": current.get("localPlayer", {}),
                    "taskCompletions": current.get("ui", {}).get("taskCompletions", [])}
                same_instance = (selected_task and selected_task.get("taskType") == "InspectSample" and
                    (not original_task.get("taskInstanceId") or selected_task.get("taskInstanceId") == original_task["taskInstanceId"]))
                if (same_instance and selected_task.get("complete")) or receipts.receipt_for(receipt_state, original_task):
                    return
            raise RuntimeError(
                "InspectSample selection has no native completion evidence"
            )
        time.sleep(0.3)

    print(
        f"inspect_sample timeout colors={best_colors}",
        file=sys.stderr,
        flush=True,
    )
    raise RuntimeError("InspectSample did not reveal a stable color outlier")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--task-type", default="")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--step", type=int, default=0)
    parser.add_argument("--start-at", default="")
    parser.add_argument("--task-id", type=int)
    return parser.parse_args()


def resolve_method(task_type: str, step: int) -> str | None:
    if task_type in {"DivertPower", "AcceptPower"}:
        return "accept_power" if step > 0 or task_type == "AcceptPower" else "divert_power"
    return METHOD_BY_TASK.get(task_type)


def normalize_unlock_number(value: str) -> int | None:
    token = re.sub(r"[^0-9NIO]", "", value.upper())
    if token in {"10", "N", "IO", "I0", "1O", "L0"}:
        return 10
    if len(token) == 1 and token in "123456789":
        return int(token)
    return None


def normalize_glyph_mask(image, inner_margin: int = 10):
    from PIL import ImageOps

    gray = ImageOps.grayscale(image)
    width, height = gray.size
    margin = max(0, min(inner_margin, width // 4, height // 4))
    gray = gray.crop((margin, margin, width - margin, height - margin))
    array = np.asarray(gray)
    mask = array < 105
    ys, xs = np.where(mask)
    if len(xs) < 15:
        return None
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    glyph = gray.crop((x0, y0, x1, y1)).resize((32, 48))
    glyph_array = np.asarray(glyph)
    normalized = glyph_array < 140
    if normalized.sum() < 12:
        return None
    return normalized


def unlock_templates():
    from PIL import Image

    atlas = Image.open(UNLOCK_TEMPLATE_PATH).convert("RGB")
    templates = {}
    for number, box in UNLOCK_TEMPLATE_BOXES.items():
        panel = atlas.crop(box)
        mask = normalize_glyph_mask(panel, inner_margin=18)
        if mask is None:
            raise RuntimeError(f"Could not build unlock template {number}")
        templates[number] = mask
    return templates


def classify_unlock_cell(image, templates) -> int:
    mask = normalize_glyph_mask(image, inner_margin=18)
    if mask is None:
        raise ValueError("Unlock button glyph is empty")
    scores = {}
    for number, template in templates.items():
        mismatch = np.logical_xor(mask, template).mean()
        scores[number] = float(mismatch)
    number, score = min(scores.items(), key=lambda item: item[1])
    if score > 0.34:
        raise ValueError(f"Ambiguous unlock glyph: best={number} score={score:.3f}")
    return number


def detect_unlock_layout() -> list[int]:
    from PIL import ImageGrab

    templates = unlock_templates()
    deadline = time.monotonic() + 15.0
    last_error = ""
    last_signature = None
    while time.monotonic() < deadline:
        try:
            _, signature = latest_visual_snapshot()
            if signature == last_signature:
                time.sleep(0.3)
                continue
            last_signature = signature
            image = ImageGrab.grab(bbox=(0, 0, 1920, 1080)).convert("RGB")
            detected = []
            for center_x, center_y in UNLOCK_BUTTON_CENTERS:
                cell = image.crop(
                    (
                        center_x - 42,
                        center_y - 42,
                        center_x + 42,
                        center_y + 42,
                    )
                )
                detected.append(classify_unlock_cell(cell, templates))
            if sorted(detected) == list(range(1, 11)):
                print(f"unlock_manifold template order={detected}", flush=True)
                return detected
            last_error = f"Non-permutation: {detected}"
        except Exception as exception:
            last_error = str(exception)
        time.sleep(0.3)
    raise RuntimeError(f"UnlockManifolds template matching failed: {last_error}")


def telemetry_unlock_layout() -> list[int] | None:
    try:
        telemetry = read_latest_telemetry()
        order = [
            int(value)
            for value in telemetry["ui"].get("unlockManifoldsOrder", [])
        ]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if len(order) == 10 and sorted(order) == list(range(1, 11)):
        return order
    return None


def telemetry_unlock_buttons() -> dict[int, tuple[float, float]] | None:
    """Return the live screen position for each displayed number.

    The minigame's Buttons array is in logical number order, but its
    transforms are shuffled on screen. Never infer click positions from
    the array index.
    """
    try:
        telemetry = read_latest_telemetry()
        raw_buttons = telemetry["ui"].get("unlockManifoldsButtons", [])
        by_number: dict[int, tuple[float, float]] = {}
        for raw in raw_buttons:
            number = int(raw["number"])
            x = float(raw["x"])
            y = float(raw["y"])
            if (
                number < 1
                or number > 10
                or number in by_number
                or not np.isfinite(x)
                or not np.isfinite(y)
                or x < 0
                or x > 1920
                or y < 0
                or y > 1080
            ):
                return None
            by_number[number] = (x, y)
    except (
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return None
    if sorted(by_number) != list(range(1, 11)):
        return None
    return by_number


def ocr_unlock_cells() -> list[int]:
    from PIL import Image, ImageEnhance, ImageGrab, ImageOps
    import pytesseract

    cells = []
    for index, (x, y) in enumerate(UNLOCK_CELL_ORIGINS):
        cell = ImageGrab.grab(bbox=(x + 10, y + 10, x + 125, y + 125))
        preview_path = (
            Path(__file__).resolve().parent
            / "outputs"
            / "tasks"
            / f"unlock-cell-{index + 1}.png"
        )
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        cell.save(preview_path)
        cells.append(cell.convert("RGB"))

    config = "--psm 10 -c tessedit_char_whitelist=0123456789N"
    variants = []
    for cell in cells:
        gray = ImageOps.grayscale(cell)
        gray = ImageOps.autocontrast(gray)
        variants.append(
            [
                gray,
                ImageEnhance.Contrast(gray).enhance(2.0),
                gray.point(lambda pixel: 255 if pixel >= 150 else 0),
                gray.point(lambda pixel: 255 if pixel >= 205 else 0),
                ImageOps.invert(gray),
            ]
        )

    last_values: list[str] = []
    for variant_index in range(len(variants[0])):
        numbers: list[int] = []
        last_values = []
        for cell_variants in variants:
            image = cell_variants[variant_index].resize(
                (
                    cell_variants[variant_index].width * 4,
                    cell_variants[variant_index].height * 4,
                )
            )
            text = pytesseract.image_to_string(image, config=config)
            last_values.append(text)
            number = normalize_unlock_number(text)
            if number is None:
                break
            numbers.append(number)

        if sorted(numbers) == list(range(1, 11)):
            print(
                f"unlock_manifold OCR order={numbers} variant={variant_index}",
                flush=True,
            )
            return numbers

    raise RuntimeError(f"UnlockManifolds OCR failed: {last_values}")


def solve_unlock_manifolds(pyautogui) -> None:
    for number in range(1, 11):
        if not send_plugin_action(
            "click_unlock_number",
            targetNumber=number,
        ):
            raise RuntimeError(
                f"UnlockManifolds plugin click failed: {number}"
            )
        print(
            f"unlock_manifold click={number} via_plugin=true",
            flush=True,
        )
        time.sleep(0.18)
    time.sleep(2.0)
    print("unlock_manifold clicked numbers=12345678910", flush=True)


def telemetry_start_reactor_state() -> tuple[list[int], list[tuple[float, float]], bool]:
    try:
        telemetry = read_latest_telemetry()
        ui = telemetry["ui"]
        sequence = [
            int(value)
            for value in ui.get("startReactorSequence", [])
        ]
        buttons = [
            (float(point["x"]), float(point["y"]))
            for point in ui.get("startReactorButtons", [])
        ]
        animating = bool(ui.get("startReactorAnimating"))
        return sequence, buttons, animating
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return [], [], False


def solve_start_reactor(
    pyautogui,
    task_id: int | None,
) -> None:
    deadline = time.monotonic() + 48.0
    last_clicked: tuple[int, ...] | None = None
    saw_sequence = False

    while time.monotonic() < deadline:
        telemetry = read_latest_telemetry()
        tasks = telemetry.get("ui", {}).get("ownTasks", [])
        task = next(
            (
                value
                for value in tasks
                if task_id is None or int(value.get("taskId", -1)) == task_id
            ),
            None,
        )
        if task is not None and bool(task.get("complete")):
            print("start_reactor task_complete", flush=True)
            return

        sequence, buttons, animating = telemetry_start_reactor_state()
        if sequence and len(buttons) == 9:
            saw_sequence = True
            current = tuple(sequence)
            if (
                not animating
                and current != last_clicked
                and all(0 <= index < 9 for index in current)
            ):
                print(f"start_reactor sequence={list(current)}", flush=True)
                for index in current:
                    if not send_plugin_action(
                        "click_reactor_button",
                        targetNumber=index,
                    ):
                        raise RuntimeError(
                            f"StartReactor plugin click failed: {index}"
                        )
                    print(
                        f"start_reactor click={index} via_plugin=true",
                        flush=True,
                    )
                    time.sleep(0.22)
                last_clicked = current
                time.sleep(0.45)
        elif not sequence:
            last_clicked = None
        time.sleep(0.08)

    if not saw_sequence:
        raise RuntimeError("StartReactor telemetry sequence was unavailable")


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    tasks_path = root / "src" / "tasks.py"
    if not tasks_path.is_file():
        raise FileNotFoundError(f"StudyBot src/tasks.py not found: {tasks_path}")

    if args.preflight:
        sys.path.insert(0, str(root))
        os.chdir(root)
        module = importlib.import_module("src.tasks")
        from runtime.tasks.start_reactor import ReactorVision, ReactorController
        internal = {"DownloadData", "UploadData", "StartReactor", "UnlockManifolds", "VentCleaning", "InspectSample"}
        missing = [name for task, name in METHOD_BY_TASK.items() if task not in internal and not hasattr(module.TaskManager, name)]
        if missing:
            raise RuntimeError("Missing StudyBot methods: " + ", ".join(missing))
        print(json.dumps({"engine": "StudyBot", "root": str(root), "skills": sorted(METHOD_BY_TASK), "adapter": str(Path(__file__).resolve())}), flush=True)
        return 0
    method_name = resolve_method(args.task_type, args.step)
    if method_name is None:
        print(
            f"unsupported StudyBot task type: {args.task_type} step={args.step}",
            file=sys.stderr,
        )
        return 3

    configure_game_surface()
    patch_image_grab()
    sys.path.insert(0, str(root))
    os.chdir(root)
    tasks_module = importlib.import_module("src.tasks")
    if args.task_type == "RestoreOxy":
        enable_oxygen_opencv(tasks_module)
    pyautogui = importlib.import_module("pyautogui")
    manager = tasks_module.TaskManager()
    internal_skill_types = {
        "DownloadData",
        "UploadData",
        "StartReactor",
        "UnlockManifolds",
        "VentCleaning",
        "InspectSample",
    }
    method = None
    if args.task_type not in internal_skill_types:
        method = getattr(manager, method_name, None)
        if method is None:
            raise AttributeError(
                f"StudyBot TaskManager.{method_name} not found"
            )

    surface = {
        "x": os.environ.get("AU_STUDYBOT_SCREEN_X", "0"),
        "y": os.environ.get("AU_STUDYBOT_SCREEN_Y", "0"),
        "w": os.environ.get("AU_STUDYBOT_SCREEN_W", "1920"),
        "h": os.environ.get("AU_STUDYBOT_SCREEN_H", "1080"),
        "desktop": pyautogui.size(),
    }
    print(
        f"studybot_adapter task={args.task_type} step={args.step} "
        f"startAt={args.start_at} method={method_name} surface={surface}",
        flush=True,
    )
    # Among Us animates the minigame into place; StudyBot assumes it is settled.
    time.sleep(0.9)
    if args.task_type == "SwipeCard":
        pyautogui.moveTo(777, 814, _pause=0)
        pyautogui.click(_pause=0)
        time.sleep(0.35)
        pyautogui.moveTo(540, 400, _pause=0)
        pyautogui.drag(
            900,
            0,
            SWIPE_DRAG_SECONDS,
            button="left",
        )
        time.sleep(1.0)
    elif args.task_type == "InspectSample":
        solve_inspect_sample(pyautogui, args.task_id, args.step)
    elif args.task_type == "VentCleaning":
        solve_vent_cleaning(
            pyautogui,
            args.task_id,
            args.step,
        )
    elif args.task_type == "UnlockManifolds":
        solve_unlock_manifolds(pyautogui)
    elif args.task_type in {"DownloadData", "UploadData"}:
        solve_upload_data(
            pyautogui,
            args.task_id,
            args.step,
        )
    elif args.task_type == "StartReactor":
        run_start_reactor_skill(args.task_id)
    else:
        method()
    print("studybot_adapter complete", flush=True)
    return 0


def run_single_instance() -> int:
    lock = acquire_named_mutex(ADAPTER_MUTEX_NAME)
    if lock is None:
        print(
            "studybot_adapter already_running",
            file=sys.stderr,
            flush=True,
        )
        return 75

    try:
        try:
            return main()
        except Exception:
            traceback.print_exc()
            return 1
        finally:
            if "--preflight" not in sys.argv and "InspectSample" not in sys.argv:
                request_close_minigame()
    finally:
        release_named_mutex(lock)


if __name__ == "__main__":
    raise SystemExit(run_single_instance())
