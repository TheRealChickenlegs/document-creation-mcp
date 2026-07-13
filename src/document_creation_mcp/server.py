from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import get_theme_manager
from .config import get_settings
from .models import ImageSpec, PresentationPlan, SlideSpec
from .pptx_builder import build_presentation

_HOST = os.environ.get("DOC_MCP_HOST", "127.0.0.1")
_PORT = int(os.environ.get("DOC_MCP_PORT", "8000"))
# Path the streamable-HTTP endpoint is served at. Clients that POST to the
# server root (e.g. some MetaMCP setups) need this set to "/".
_STREAMABLE_HTTP_PATH = os.environ.get("DOC_MCP_STREAMABLE_HTTP_PATH", "/mcp")
# Stateless mode handles each request standalone (no session), which is far
# more reliable behind proxies like MetaMCP/Open WebUI that may POST from
# different nodes or without a session id (avoids 404s on /mcp).
_STATELESS_HTTP = os.environ.get("DOC_MCP_STATELESS_HTTP", "true").lower() == "true"

mcp = FastMCP(
    "document-creation-mcp",
    host=_HOST,
    port=_PORT,
    streamable_http_path=_STREAMABLE_HTTP_PATH,
    stateless_http=_STATELESS_HTTP,
)


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
        full_prompt, negative_prompt=negative_prompt, size=size, theme=theme_obj
    )


@mcp.tool()
async def list_comfy_models() -> str:
    """List models available on the ComfyUI HTTP API (checkpoints/samplers/schedulers).

    Useful to see what the direct `comfy_api` backend can use, and to pick a
    value for COMFY_API_CHECKPOINT. Requires IMAGE_BACKEND=comfy_api and
    COMFY_API_URL to be set.
    """
    from . import comfy_client

    try:
        models = await comfy_client.discover_comfy_models()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps(models)


@mcp.tool()
async def create_presentation(plan: PresentationPlan) -> str:
    """Create a PowerPoint deck from a structured plan and return the file path.

    The orchestrating model is expected to do any web research and produce the
    slide plan. Pass the plan as a JSON object matching PresentationPlan:

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

    Slide text may be provided as `bullets` or `content` (a string is split on
    newlines; a list is accepted as-is). Images declared with a `prompt` are
    auto-generated via ComfyUI. To reuse an existing image, set `image.source`
    to a local path or URL instead.
    """
    settings = get_settings()
    try:
        theme = get_theme_manager().get(plan.theme)
    except KeyError as exc:
        return json.dumps({"error": str(exc)})

    out_path = await build_presentation(plan, theme, settings.output_dir)
    result = {
        "status": "ok",
        "path": str(out_path),
        "slide_count": len(plan.slides),
        "theme": plan.theme,
    }

    download = {
        "filename": out_path.name,
        "mime_type": (
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
    }
    if settings.minio_enabled:
        try:
            from . import storage

            download["url"] = storage.upload_file(out_path, bucket_override=plan.bucket)
        except Exception as exc:  # noqa: BLE001
            download["minio_error"] = str(exc)
    if settings.return_base64:
        import base64

        download["data"] = base64.b64encode(out_path.read_bytes()).decode("ascii")
    result["download"] = download
    return json.dumps(result)


def main() -> None:
    transport = os.environ.get("DOC_MCP_TRANSPORT", "stdio")
    if transport in ("sse", "streamable-http"):
        mcp.run(transport=transport)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
