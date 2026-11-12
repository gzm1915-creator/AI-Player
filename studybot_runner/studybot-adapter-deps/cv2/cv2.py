"""Preserve the historical ``from cv2 import cv2`` compatibility namespace."""

import importlib as _importlib

_parent = _importlib.import_module(__package__)
__all__ = [name for name in vars(_parent) if not name.startswith("_")]
globals().update({name: getattr(_parent, name) for name in __all__})
