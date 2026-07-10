from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import get_theme_manager
from .config import get_settings
from .models import ImageSpec, PresentationPlan, SlideSpec
from .pptx_builder import build_presentation

mcp = FastMCP("document-creation-mcp")


@mcp.tool()
def list_themes() -> str:
    """List the available design theme names."""
    names = get_theme_manager().names()
    return json.dumps({"themes": names})


@mcp.tool()
def get_theme(name: str) -> str:
    """Return the full definition (colors, fonts, image style) of a theme."""
    return json.dumps(get_theme_manager().as_dict(name))


@mcp.tool()
async def generate_image(
    prompt: str,
    theme: str = "dark_tech",
    size: str = "1024x1024",
    negative_prompt: str | None = None,
    target: str = "content",
) -> str:
    """Generate a single image via the ComfyUI MCP server and return its local path.

    Args:
        prompt: Base image description.
        theme: Theme whose `image_style` is appended for consistency.
        size: Output size, e.g. "1024x1024".
        negative_prompt: Optional negative prompt.
        target: "content" or "background".
    """
    from . import comfy_client

    theme_obj = get_theme_manager().get(theme)
    full_prompt = prompt
    if theme_obj.image_style:
        full_prompt = f"{prompt}, {theme_obj.image_style}"
    return await comfy_client.generate_image(
        full_prompt, negative_prompt=negative_prompt, size=size
    )


@mcp.tool()
async def create_presentation(plan_json: str) -> str:
    """Create a PowerPoint deck from a structured plan and return the file path.

    The orchestrating model is expected to do any web research and produce the
    slide plan. `plan_json` is a JSON string matching PresentationPlan:

    {
      "title": "Deck title",
      "theme": "dark_tech",
      "output_filename": "my_deck",
      "slides": [
        {"title": "Intro", "layout": "title",
         "subtitle": "An AI-generated deck"},
        {"title": "Topic", "bullets": ["Point 1", "Point 2"],
         "image": {"prompt": "futuristic city", "target": "content"}},
        {"title": "Deep dive", "layout": "image_full",
         "image": {"prompt": "abstract data flow"}}
      ]
    }

    Images declared with a `prompt` are auto-generated via ComfyUI. To reuse an
    existing image, set `image.source` to a local path or URL instead.
    """
    settings = get_settings()
    try:
        plan = PresentationPlan.model_validate(json.loads(plan_json))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Invalid plan: {exc}"})

    theme = get_theme_manager().get(plan.theme)
    out_path = await build_presentation(plan, theme, settings.output_dir)
    return json.dumps(
        {
            "status": "ok",
            "path": str(out_path),
            "slide_count": len(plan.slides),
            "theme": plan.theme,
        }
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
