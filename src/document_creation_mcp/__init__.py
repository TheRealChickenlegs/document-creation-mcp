from __future__ import annotations

from pathlib import Path

from .config import Settings, get_settings
from .models import (
    ImageSpec,
    PresentationPlan,
    SlideSpec,
)
from .pptx_builder import build_presentation
from .theme_manager import ThemeManager

__all__ = [
    "Settings",
    "get_settings",
    "ImageSpec",
    "PresentationPlan",
    "SlideSpec",
    "build_presentation",
    "ThemeManager",
]

_THEME_DIR = Path(__file__).parent.parent.parent / "themes"


def get_theme_manager() -> ThemeManager:
    return ThemeManager(_THEME_DIR)
