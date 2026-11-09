from __future__ import annotations

import ctypes
import os
import time


PAUSE = 0.1
FAILSAFE = False

_user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_BASE_WIDTH = 1920.0
_BASE_HEIGHT = 1080.0
_TARGET_X = int(os.environ.get("AU_STUDYBOT_SCREEN_X", "0"))
_TARGET_Y = int(os.environ.get("AU_STUDYBOT_SCREEN_Y", "0"))
_TARGET_WIDTH = float(os.environ.get("AU_STUDYBOT_SCREEN_W", "1920"))
_TARGET_HEIGHT = float(os.environ.get("AU_STUDYBOT_SCREEN_H", "1080"))


def _focus_game_window() -> None:
    window = int(os.environ.get("AU_STUDYBOT_HWND", "0"))
    if not window:
        raise RuntimeError("Among Us window has not been selected")
    _user32.ShowWindow(window, 9)
    _user32.SetForegroundWindow(window)
    time.sleep(0.03)


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _pause(seconds: float | None = None) -> None:
    time.sleep(PAUSE if seconds is None else max(0.0, seconds))


def size() -> tuple[int, int]:
    return (
        int(_user32.GetSystemMetrics(0)),
        int(_user32.GetSystemMetrics(1)),
    )


def position() -> tuple[int, int]:
    point = _Point()
    _user32.GetCursorPos(ctypes.byref(point))
    base_x = (point.x - _TARGET_X) * _BASE_WIDTH / max(1.0, _TARGET_WIDTH)
    base_y = (point.y - _TARGET_Y) * _BASE_HEIGHT / max(1.0, _TARGET_HEIGHT)
    return round(base_x), round(base_y)


def _screen_point(x: int, y: int) -> tuple[int, int]:
    return (
        _TARGET_X + round(x * _TARGET_WIDTH / _BASE_WIDTH),
        _TARGET_Y + round(y * _TARGET_HEIGHT / _BASE_HEIGHT),
    )


def moveTo(
    x: int | tuple[int, int],
    y: int | None = None,
    duration: float = 0.0,
    _pause: float | None = None,
) -> None:
    if y is None:
        x, y = x
    start_x, start_y = position()
    target_x, target_y = _screen_point(x, y)
    steps = max(1, int(max(0.0, duration) * 60))
    for index in range(steps + 1):
        progress = index / steps
        current_base_x = start_x + (x - start_x) * progress
        current_base_y = start_y + (y - start_y) * progress
        current_x, current_y = _screen_point(
            round(current_base_x),
            round(current_base_y),
        )
        _user32.SetCursorPos(int(current_x), int(current_y))
        if steps > 1:
            time.sleep(max(0.0, duration) / steps)
    globals()["_pause"](_pause)


def mouseDown(
    x: int | tuple[int, int] | None = None,
    y: int | None = None,
    button: str = "left",
) -> None:
    _focus_game_window()
    if y is None and x is not None and not isinstance(x, int):
        x, y = x
    if x is not None and y is not None:
        moveTo(x, y, _pause=0)
    flag = _MOUSEEVENTF_RIGHTDOWN if button == "right" else _MOUSEEVENTF_LEFTDOWN
    _user32.mouse_event(flag, 0, 0, 0, 0)
    _pause()


def mouseUp(
    x: int | tuple[int, int] | None = None,
    y: int | None = None,
    button: str = "left",
) -> None:
    if y is None and x is not None and not isinstance(x, int):
        x, y = x
    if x is not None and y is not None:
        moveTo(x, y, _pause=0)
    flag = _MOUSEEVENTF_RIGHTUP if button == "right" else _MOUSEEVENTF_LEFTUP
    _user32.mouse_event(flag, 0, 0, 0, 0)
    _pause()


def click(
    x: int | tuple[int, int] | None = None,
    y: int | None = None,
    button: str = "left",
    _pause: float | None = None,
) -> None:
    if y is None and x is not None and not isinstance(x, int):
        x, y = x
    if x is not None and y is not None:
        moveTo(x, y, _pause=0)
    mouseDown(button=button)
    mouseUp(button=button)
    globals()["_pause"](_pause)


def drag(
    x: int = 0,
    y: int = 0,
    duration: float = 0.0,
    button: str = "left",
) -> None:
    _focus_game_window()
    start_x, start_y = position()
    mouseDown(button=button)
    moveTo(start_x + x, start_y + y, duration=duration, _pause=0)
    mouseUp(button=button)


def dragTo(
    x: int | tuple[int, int],
    y: int | None = None,
    duration: float = 0.0,
    button: str = "left",
) -> None:
    _focus_game_window()
    if y is None:
        x, y = x
    mouseDown(button=button)
    moveTo(x, y, duration=duration, _pause=0)
    mouseUp(button=button)


def keyDown(_key: str) -> None:
    raise NotImplementedError("StudyBot task adapter does not require keyboard input")


def keyUp(_key: str) -> None:
    raise NotImplementedError("StudyBot task adapter does not require keyboard input")


def press(_key: str) -> None:
    raise NotImplementedError("StudyBot task adapter does not require keyboard input")
