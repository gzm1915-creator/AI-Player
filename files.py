"""Shared telemetry reads and bounded retries for Windows file hand-offs."""

import json
import os
import time
import uuid
from pathlib import Path


def open_shared(path):
    if os.name != "nt":
        return Path(path).open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(Path(path)), 0x80000000, 1 | 2 | 4, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        close(handle)
        raise
    return os.fdopen(descriptor, "rb")


def load(path):
    try:
        with open_shared(path) as stream:
            raw = stream.read()
        value = json.loads(raw.decode("utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def replace_file(source, destination):
    if os.name == "nt" and destination.exists():
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        replace = kernel.ReplaceFileW
        replace.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        replace.restype = wintypes.BOOL
        if replace(str(destination), str(source), None, 2, None, None):
            return
        error = ctypes.get_last_error()
        if error != 2:
            raise ctypes.WinError(error)
    source.replace(destination)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        for attempt in range(10):
            try:
                replace_file(temporary, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)
