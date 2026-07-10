from __future__ import annotations

import os
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

# Bundled factory themes ship inside the package so they are available after
# install (including in Docker). An external directory can be supplied via
# DOC_MCP_THEME_DIR and is merged on top of the bundled themes.
_BUNDLED_THEMES = Path(__file__).parent / "themes"
_USER_THEME_DIR = os.environ.get("DOC_MCP_THEME_DIR")
_THEME_DIRS = [_USER_THEME_DIR] if _USER_THEME_DIR else []
if _BUNDLED_THEMES not in _THEME_DIRS:
    _THEME_DIRS.append(_BUNDLED_THEMES)


def get_theme_manager() -> ThemeManager:
    return ThemeManager(_THEME_DIRS)
