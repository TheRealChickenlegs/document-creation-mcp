from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ImageSpec(BaseModel):
    """Specification for an image to generate (via ComfyUI) or reference directly."""

    prompt: str
    # When True, the active theme's `image_style` suffix is appended automatically.
    use_theme_style: bool = True
    # Image generation size, e.g. "1024x1024". Only used when generating.
    size: str = "1024x1024"
    # Negative prompt passed through to ComfyUI when generating.
    negative_prompt: str | None = None
    # Where the image lives on the slide.
    target: Literal["content", "background"] = "content"
    # Optional explicit path/URL. If set, no generation happens and this is used directly.
    source: str | None = None


class SlideSpec(BaseModel):
    title: str
    subtitle: str | None = None
    bullets: list[str] | None = None
    image: ImageSpec | None = None
    layout: Literal[
        "title",
        "title_and_content",
        "two_column",
        "image_full",
        "section",
    ] = "title_and_content"


class PresentationPlan(BaseModel):
    """Structured description of a deck. The orchestrating model produces this."""

    title: str
    theme: str = "dark_tech"
    slides: list[SlideSpec] = Field(default_factory=list)
    # Optional filename; ".pptx" is appended if missing.
    output_filename: str | None = None
