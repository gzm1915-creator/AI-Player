"""Backward-compatible import for the packaged StartReactor skill."""

from skills.start_reactor import skill as _skill

__all__ = _skill.__all__
globals().update({name: getattr(_skill, name) for name in __all__})
