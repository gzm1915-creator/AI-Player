from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path


class TesseractNotFoundError(RuntimeError):
    pass


def _find_tesseract() -> str:
    configured = os.environ.get("TESSERACT_CMD")
    candidates = [
        configured,
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return "tesseract"


class _Tesseract:
    tesseract_cmd = _find_tesseract()


pytesseract = _Tesseract()


def _config_arguments(config: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(config)
    # shlex's POSIX mode eats Windows backslashes; non-POSIX mode retains
    # literal quotes. Use the native argv parser and keep shell=False.
    import ctypes
    from ctypes import wintypes

    parser = ctypes.WinDLL("shell32", use_last_error=True).CommandLineToArgvW
    parser.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    argv = parser("tesseract " + config, ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[index] for index in range(1, count.value)]
    finally:
        free(ctypes.cast(argv, ctypes.c_void_p))


def image_to_string(
    image,
    lang: str = "eng",
    config: str = "",
) -> str:
    if not hasattr(image, "save"):
        from PIL import Image

        image = Image.fromarray(image)
    command = [
        pytesseract.tesseract_cmd,
        "",
        "stdout",
        "-l",
        lang,
    ]
    if config:
        command.extend(_config_arguments(config))

    with tempfile.TemporaryDirectory(prefix="au-tesseract-") as directory:
        image_path = Path(directory) / "input.png"
        image.save(image_path)
        command[1] = str(image_path)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    if result.returncode != 0:
        raise TesseractNotFoundError(result.stderr.strip() or f"Tesseract exited with code {result.returncode}")
    return result.stdout.strip()
