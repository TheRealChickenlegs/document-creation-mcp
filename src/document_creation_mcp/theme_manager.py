from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError


class Theme(BaseModel):
    name: str
    colors: dict[str, str] = Field(
        default_factory=lambda: {
            "background": "#FFFFFF",
            "primary": "#1F4E79",
            "accent": "#C00000",
            "text": "#222222",
            "muted": "#666666",
        }
    )
    fonts: dict[str, str] = Field(
        default_factory=lambda: {"heading": "Calibri", "body": "Calibri"}
    )
    # Appended to every generated image prompt for visual consistency.
    image_style: str = ""
    layout_default: str = "title_and_content"
    # Optional logo placed on every slide (path or URL).
    logo: str | None = None


class ThemeManager:
    """Loads and caches theme definitions from a directory of YAML files."""

    def __init__(self, themes_dir: str | Path) -> None:
        self.themes_dir = Path(themes_dir)
        self._cache: dict[str, Theme] = {}
        self.reload()

    def reload(self) -> None:
        self._cache.clear()
        if not self.themes_dir.exists():
            return
        for path in self.themes_dir.glob("*.y*ml"):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                theme = Theme(**data)
                self._cache[theme.name] = theme
            except (yaml.YAMLError, ValidationError) as exc:
                raise ValueError(f"Invalid theme file {path}: {exc}") from exc

    def names(self) -> list[str]:
        return sorted(self._cache.keys())

    def get(self, name: str) -> Theme:
        if name not in self._cache:
            available = ", ".join(self.names()) or "(none)"
            raise KeyError(f"Unknown theme '{name}'. Available: {available}")
        return self._cache[name]

    def as_dict(self, name: str) -> dict[str, Any]:
        return self.get(name).model_dump()
