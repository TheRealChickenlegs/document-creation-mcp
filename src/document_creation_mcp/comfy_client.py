from __future__ import annotations

import base64
import json
import random
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import get_settings


class ComfyClientError(RuntimeError):
    """Raised when image generation via the ComfyUI backend fails."""


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 1024, 1024


async def generate_image(
    prompt: str,
    *,
    negative_prompt: str | None = None,
    size: str = "1024x1024",
    tool_name: str | None = None,
    theme=None,
) -> str:
    """Generate an image and return a local file path.

    Dispatches to the configured backend:
      - "mcp"       -> remote ComfyUI MCP server (HTTP/SSE)
      - "comfy_api" -> ComfyUI HTTP API directly

    When `theme` (a Theme) is supplied, its `negative_prompt` is merged in and,
    for the `comfy_api` backend, its style-reference / ControlNet / upscale
    settings drive the consistency pipeline.
    """
    settings = get_settings()
    if settings.disable_images:
        raise ComfyClientError("Image generation is disabled (DOC_MCP_DISABLE_IMAGES=true).")

    # Merge the deck/theme-wide negative prompt.
    parts = [p for p in (negative_prompt, getattr(theme, "negative_prompt", None)) if p]
    effective_neg = "; ".join(parts) or None

    width, height = _parse_size(size)
    if settings.image_backend == "comfy_api":
        return await generate_image_via_api(prompt, effective_neg, width, height, theme)
    return await generate_image_via_mcp(prompt, effective_neg, width, height, tool_name)


# --------------------------------------------------------------------------- #
# Backend: remote ComfyUI MCP server (HTTP / SSE)
# --------------------------------------------------------------------------- #


def _resolve_transport(url: str, configured: str) -> str:
    if configured != "auto":
        return configured
    return "sse" if url.rstrip("/").endswith("/sse") else "streamable-http"


async def generate_image_via_mcp(
    prompt: str,
    negative_prompt: str | None,
    width: int,
    height: int,
    tool_name: str | None,
) -> str:
    settings = get_settings()
    if not settings.comfy_mcp_url:
        raise ComfyClientError(
            "COMFY_MCP_URL is not set. Point it at your running ComfyUI MCP server."
        )

    from mcp import ClientSession

    tool = tool_name or settings.comfy_image_tool
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
                "size": f"{width}x{height}",
            }
            result = await session.call_tool(tool, arguments)
            return await _extract_image(result, settings.image_cache_dir)


# --------------------------------------------------------------------------- #
# Backend: direct ComfyUI HTTP API
# --------------------------------------------------------------------------- #


def _default_workflow() -> dict:
    """A minimal text-to-image graph (SD1.5 / SDXL) using {{placeholders}}."""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "{{checkpoint}}"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "{{prompt}}", "clip": ["1", 0]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "{{negative_prompt}}", "clip": ["1", 0]},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "seed": "{{seed}}",
                "steps": "{{steps}}",
                "cfg": "{{cfg}}",
                "sampler_name": "{{sampler}}",
                "scheduler": "{{scheduler}}",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["5", 0],
            },
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": "{{width}}", "height": "{{height}}", "batch_size": 1},
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["4", 0], "vae": ["1", 0]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0]},
        },
    }


def _coerce(value):
    if isinstance(value, bool):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _substitute(obj, mapping: dict):
    if isinstance(obj, dict):
        return {k: _substitute(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, mapping) for v in obj]
    if isinstance(obj, str) and obj in mapping:
        return _coerce(mapping[obj])
    return obj


def _build_workflow(
    raw: dict,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    *,
    checkpoint: str,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    seed: int,
    style_image: str = "",
    ip_weight: float = 0.7,
    control_image: str = "",
    control_strength: float = 0.0,
    control_type: str = "depth",
    upscale_model: str = "",
) -> dict:
    mapping = {
        "{{prompt}}": prompt,
        "{{negative_prompt}}": negative or "",
        "{{width}}": width,
        "{{height}}": height,
        "{{seed}}": seed,
        "{{checkpoint}}": checkpoint,
        "{{steps}}": steps,
        "{{cfg}}": cfg,
        "{{sampler}}": sampler,
        "{{scheduler}}": scheduler,
        "{{style_image}}": style_image,
        "{{ip_weight}}": ip_weight,
        "{{control_image}}": control_image,
        "{{control_strength}}": control_strength,
        "{{control_type}}": control_type,
        "{{upscale_model}}": upscale_model,
    }
    return _substitute(raw, mapping)


# Auto-discovery of available models so the backend works with zero config.
_DISCOVERY_CACHE: dict | None = None


async def discover_comfy_models() -> dict:
    """Query the ComfyUI API for installed checkpoints, samplers and schedulers."""
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE is not None:
        return _DISCOVERY_CACHE

    settings = get_settings()
    if not settings.comfy_api_url:
        raise ComfyClientError("COMFY_API_URL is not set; cannot discover models.")
    base = settings.comfy_api_url.rstrip("/")
    headers = settings.comfy_auth_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{base}/object_info", headers=headers or None)
        resp.raise_for_status()
        data = resp.json()

    def _options(node: str, key: str) -> list[str]:
        node_data = data.get(node, {})
        required = node_data.get("input", {}).get("required", {})
        entry = required.get(key, [[]])
        vals = entry[0] if isinstance(entry, list) and entry and isinstance(entry[0], list) else []
        return [str(v) for v in vals]

    models = {
        "checkpoints": _options("CheckpointLoaderSimple", "ckpt_name"),
        "samplers": _options("KSampler", "sampler_name"),
        "schedulers": _options("KSampler", "scheduler"),
    }
    _DISCOVERY_CACHE = models
    return models


def _pick_checkpoint(discovered: list[str], configured: str) -> str:
    """Prefer an explicit config; otherwise auto-select (SDXL-style first)."""
    if configured:
        return configured
    if not discovered:
        return ""
    for name in discovered:
        if "xl" in name.lower():
            return name
    return discovered[0]


def _coerce_to_known(value: str, discovered: list[str], fallback: str) -> str:
    if discovered and value not in discovered:
        return discovered[0]
    return value


async def generate_image_via_api(
    prompt: str,
    negative_prompt: str | None,
    width: int,
    height: int,
    theme=None,
) -> str:
    settings = get_settings()
    if not settings.comfy_api_url:
        raise ComfyClientError("COMFY_API_URL is not set for the comfy_api backend.")

    base = settings.comfy_api_url.rstrip("/")
    headers = settings.comfy_auth_headers()

    # --- Consistency values from the theme (IP-Adapter / ControlNet / upscale) ---
    style_image = getattr(theme, "style_reference_image", None) or ""
    ip_weight = getattr(theme, "ip_adapter_weight", 0.7) or 0.7
    cn = getattr(theme, "controlnet", None)
    control_enabled = bool(cn and getattr(cn, "enabled", False))
    control_image = (getattr(cn, "reference_image", None) if cn else None) or style_image
    control_strength = getattr(cn, "strength", 0.6) if control_enabled else 0.0
    control_type = getattr(cn, "type", "depth") or "depth"
    upscale_model = getattr(theme, "upscale_model", None) or "4x-UltraSharp.pth"

    # Use the user's advanced workflow template only when it can actually run:
    # it requires a style-reference image for the IP-Adapter LoadImage node.
    use_advanced = bool(settings.comfy_api_workflow and style_image)
    if use_advanced:
        raw = json.loads(Path(settings.comfy_api_workflow).read_text(encoding="utf-8"))
    else:
        raw = _default_workflow()

    checkpoint = settings.comfy_api_checkpoint
    sampler = settings.comfy_api_sampler
    scheduler = settings.comfy_api_scheduler
    if settings.comfy_api_autodiscover:
        models = await discover_comfy_models()
        checkpoint = _pick_checkpoint(models["checkpoints"], checkpoint)
        sampler = _coerce_to_known(sampler, models["samplers"], sampler)
        scheduler = _coerce_to_known(scheduler, models["schedulers"], scheduler)
        if not checkpoint:
            raise ComfyClientError(
                "No checkpoints found on the ComfyUI server and COMFY_API_CHECKPOINT is unset."
            )

    seed = settings.comfy_api_seed or random.randint(0, 2**32 - 1)
    workflow = _build_workflow(
        raw,
        prompt,
        negative_prompt,
        width,
        height,
        checkpoint=checkpoint,
        steps=settings.comfy_api_steps,
        cfg=settings.comfy_api_cfg,
        sampler=sampler,
        scheduler=scheduler,
        seed=seed,
        style_image=style_image,
        ip_weight=ip_weight,
        control_image=control_image,
        control_strength=control_strength,
        control_type=control_type,
        upscale_model=upscale_model,
    )
    client_id = uuid.uuid4().hex

    async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
        resp = await client.post(
            f"{base}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            headers=headers or None,
        )
        resp.raise_for_status()
        prompt_id = resp.json().get("prompt_id")
        if not prompt_id:
            raise ComfyClientError("ComfyUI /prompt did not return a prompt_id.")

        outputs = await _wait_for_completion(client, base, prompt_id, headers)

        image_meta = _find_image(outputs)
        if not image_meta:
            raise ComfyClientError(f"ComfyUI returned no image. outputs={outputs}")

        view = await client.get(f"{base}/view", params=image_meta, headers=headers or None)
        view.raise_for_status()
        out_path = settings.image_cache_dir / f"img_{prompt_id}.png"
        out_path.write_bytes(view.content)
        return str(out_path)


async def _wait_for_completion(client, base: str, prompt_id: str, headers) -> dict:
    settings = get_settings()
    import asyncio

    deadline = settings.comfy_timeout
    waited = 0.0
    while waited < deadline:
        await asyncio.sleep(2)
        waited += 2
        hist = await client.get(f"{base}/history/{prompt_id}", headers=headers or None)
        hist.raise_for_status()
        data = hist.json()
        if prompt_id in data:
            entry = data[prompt_id]
            if entry.get("status", {}).get("status_str") == "error":
                raise ComfyClientError(f"ComfyUI job failed: {entry.get('status')}")
            if entry.get("outputs"):
                return entry["outputs"]
    raise ComfyClientError("Timed out waiting for ComfyUI image generation.")


def _find_image(outputs: dict) -> dict | None:
    for node_out in outputs.values():
        images = node_out.get("images")
        if images:
            img = images[0]
            return {
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", ""),
            }
    return None


# --------------------------------------------------------------------------- #
# Shared result parsing (MCP backend)
# --------------------------------------------------------------------------- #


async def _extract_image(result, cache_dir: Path) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)

    for item in getattr(result, "content", []) or []:
        data = getattr(item, "data", None)
        mime = getattr(item, "mime_type", "") or ""
        if data and mime.startswith("image/"):
            ext = mime.split("/")[-1].split(";")[0]
            path = cache_dir / f"img_{_short_hash(result)}_{len(list(cache_dir.glob('*')))}.{ext}"
            path.write_bytes(base64.b64decode(data))
            return str(path)

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
