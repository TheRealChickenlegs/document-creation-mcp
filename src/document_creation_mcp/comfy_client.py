from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import get_settings


class ComfyClientError(RuntimeError):
    """Raised when image generation via the ComfyUI MCP server fails."""


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 1024, 1024


def _resolve_transport(url: str, configured: str) -> str:
    if configured != "auto":
        return configured
    return "sse" if url.rstrip("/").endswith("/sse") else "streamable-http"


async def generate_image(
    prompt: str,
    *,
    negative_prompt: str | None = None,
    size: str = "1024x1024",
    tool_name: str | None = None,
) -> str:
    """Generate an image via a running ComfyUI MCP server and return a local path.

    Connects over the network (streamable-http or SSE) to the configured server,
    invokes its image tool, then downloads/decodes the result into the image
    cache directory.
    """
    settings = get_settings()
    if settings.disable_images:
        raise ComfyClientError("Image generation is disabled (DOC_MCP_DISABLE_IMAGES=true).")
    if not settings.comfy_mcp_url:
        raise ComfyClientError(
            "COMFY_MCP_URL is not set. Point it at your running ComfyUI MCP server."
        )

    from mcp import ClientSession

    tool = tool_name or settings.comfy_image_tool
    width, height = _parse_size(size)
    transport = _resolve_transport(settings.comfy_mcp_url, settings.comfy_mcp_transport)
    headers = settings.comfy_auth_headers()

    if transport == "sse":
        from mcp.client.sse import sse_client

        client_cm = sse_client(settings.comfy_mcp_url, headers=headers or None)
    else:
        from mcp.client.streamable_http import streamablehttp_client

        client_cm = streamablehttp_client(
            settings.comfy_mcp_url, headers=headers or None
        )

    async with client_cm as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            arguments = {
                "prompt": prompt,
                "negative_prompt": negative_prompt or "",
                "width": width,
                "height": height,
                "size": size,
            }
            result = await session.call_tool(tool, arguments)
            return await _extract_image(result, settings.image_cache_dir)


async def _extract_image(result, cache_dir: Path) -> str:
    """Pull an image path/URL/base64 out of an MCP tool result and resolve to a local file."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    for item in getattr(result, "content", []) or []:
        # 1) Embedded image content (base64).
        data = getattr(item, "data", None)
        mime = getattr(item, "mime_type", "") or ""
        if data and mime.startswith("image/"):
            ext = mime.split("/")[-1].split(";")[0]
            path = cache_dir / f"img_{_short_hash(result)}_{len(list(cache_dir.glob('*')))}.{ext}"
            path.write_bytes(base64.b64decode(data))
            return str(path)

        # 2) Text content: JSON, a path, or a URL.
        text = getattr(item, "text", None)
        if not text:
            continue

        candidate = _coerce_to_path(text, cache_dir)
        if candidate:
            return candidate

    raise ComfyClientError(
        "ComfyUI MCP server returned no usable image. Result: "
        + json.dumps([getattr(i, "text", "") for i in getattr(result, "content", [])])
    )


def _coerce_to_path(text: str, cache_dir: Path) -> str | None:
    # Try JSON first (common: {"images": ["path_or_url"], ...}).
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for key in ("image_path", "path", "image", "url", "images", "output"):
                val = obj.get(key)
                if isinstance(val, str):
                    return _resolve_source(val, cache_dir)
                if isinstance(val, list) and val and isinstance(val[0], str):
                    return _resolve_source(val[0], cache_dir)
        if isinstance(obj, list) and obj and isinstance(obj[0], str):
            return _resolve_source(obj[0], cache_dir)
    except json.JSONDecodeError:
        pass

    # Plain path or URL on a single line.
    line = text.strip().splitlines()[0].strip().strip('"\'')
    if line:
        return _resolve_source(line, cache_dir)
    return None


def _resolve_source(source: str, cache_dir: Path) -> str:
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return _download(source, cache_dir)
    path = Path(source)
    if path.exists():
        return str(path.resolve())
    # Some servers return relative paths.
    if (Path.cwd() / path).exists():
        return str((Path.cwd() / path).resolve())
    raise ComfyClientError(f"Image source not found or unsupported: {source}")


def _download(url: str, cache_dir: Path) -> str:
    out = cache_dir / f"img_download_{abs(hash(url))}.png"

    async def _get() -> bytes:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url, headers=get_settings().comfy_auth_headers() or None)
            resp.raise_for_status()
            return resp.content

    import asyncio

    data = asyncio.get_event_loop().run_until_complete(_get())
    out.write_bytes(data)
    return str(out)


def _short_hash(obj: object) -> str:
    return abs(hash(json.dumps(str(obj), default=str))) % 100000
