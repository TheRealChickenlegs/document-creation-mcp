from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError


class ControlNetConfig(BaseModel):
    enabled: bool = False
    type: str = "depth"  # depth | canny | openpose | tile (Union control type)
    strength: float = 0.6
    reference_image: str | None = None


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

    # --- Image-consistency settings (used by the comfy_api backend) ---
    # A single reference image fed to IP-Adapter so every deck image shares
    # the same style/colour mood. Path or URL.
    style_reference_image: str | None = None
    ip_adapter_weight: float = 0.7
    controlnet: ControlNetConfig = Field(default_factory=ControlNetConfig)
    # ESRGAN model name for the final upscale, e.g. "4x-UltraSharp.pth".
    upscale_model: str | None = None
    # Deck-wide negative prompt appended to every image.
    negative_prompt: str | None = None
    # Post-process applied to background/full-bleed images so text stays
    # readable: "dim", "blur", or "dim+blur".
    background_post: str | None = None


class ThemeManager:
    """Loads and caches theme definitions from one or more YAML directories.

    Later directories override earlier ones with the same theme name, so an
    external/user directory can override the bundled factory themes.
    """

    def __init__(self, themes_dirs: str | Path | list[str | Path]) -> None:
        if isinstance(themes_dirs, (str, Path)):
            themes_dirs = [themes_dirs]
        self.themes_dirs = [Path(d) for d in themes_dirs]
        self._cache: dict[str, Theme] = {}
        self.reload()

    def reload(self) -> None:
        self._cache.clear()
        for directory in self.themes_dirs:
            if not directory.exists():
                continue
            for path in directory.glob("*.y*ml"):
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
