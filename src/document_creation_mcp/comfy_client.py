from __future__ import annotations

import base64
import hashlib
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
    target: str = "content",
) -> str:
    """Generate an image and return a local file path.

    Dispatches to the configured backend:
      - "mcp"       -> remote ComfyUI MCP server (HTTP/SSE)
      - "comfy_api" -> ComfyUI HTTP API directly

    When `theme` (a Theme) is supplied, its `negative_prompt` is merged in and,
    for the `comfy_api` backend, its style-reference / ControlNet / upscale
    settings drive the consistency pipeline. ``target`` (content / background /
    icon) selects the per-role consistency preset for the comfy_api backend.
    """
    settings = get_settings()
    if settings.disable_images:
        raise ComfyClientError("Image generation is disabled (DOC_MCP_DISABLE_IMAGES=true).")

    # Merge the deck/theme-wide negative prompt.
    parts = [p for p in (negative_prompt, getattr(theme, "negative_prompt", None)) if p]
    effective_neg = "; ".join(parts) or None

    width, height = _parse_size(size)
    if theme is not None:
        # Thread the role to the comfy_api builder (read in generate_image_via_api).
        theme._current_target = target
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


# --------------------------------------------------------------------------- #
# In-code workflow construction (no hand-written template required)
# --------------------------------------------------------------------------- #
#
# The pipeline is assembled dynamically from what the ComfyUI instance actually
# has installed, so decks get consistency (IP-Adapter style lock + ControlNet
# composition + upscale) automatically, with graceful degradation whenever a
# capability is missing.
#
#   Checkpoint ─► [IP-Adapter] ─► [ControlNet] ─► KSampler ─► VAEDecode
#                                                       │
#                                              [Upscale] ─► SaveImage
#
# Brackets denote optional nodes that are only inserted when the corresponding
# model is discovered. Every step that cannot run is simply skipped, so the
# graph always submits successfully.

# Candidate substrings used to auto-select the "best" model of each kind.
_IPADAPTER_CANDIDATES = ["ip-adapter-plus_sdxl", "ip-adapter-plus-face_sdxl", "ip-adapter_sdxl", "ipadapter"]
_CONTROLNET_CANDIDATES = ["controlnet-union-sdxl", "controlnet-union", "controlnet"]
_UPSCALE_CANDIDATES = ["4x-ultrasharp", "4x_nmkd", "4x", "esrgan", "upscale"]
_CLIPVISION_CANDIDATES = ["clip-vit-h", "clip-vit-big-g", "clip-vision"]


def build_consistency_workflow(
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    checkpoint: str,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    seed: int,
    models: dict[str, list[str]],
    style_image: str = "",
    ip_weight: float = 0.7,
    ip_weight_type: str | None = None,
    control_image: str = "",
    control_strength: float = 0.0,
    control_type: str = "depth",
    upscale_model: str = "",
    vae: str = "",
    target: str = "content",
    enable_ip: bool | None = None,
    enable_cn: bool | None = None,
    enable_upscale: bool | None = None,
) -> dict:
    """Construct a ComfyUI API workflow graph from discovered models.

    Nodes are added conditionally based on what ``models`` reports as installed,
    so the same function drives a bare text-to-image graph or a full
    IP-Adapter + ControlNet + upscale pipeline. Returns the API-format dict.

    ``enable_ip`` / ``enable_cn`` / ``enable_upscale`` let a caller force a stage
    on/off; when ``None`` the stage is enabled only if its model is discovered
    (and a reference image is available for IP/CN). This is used to retry with a
    progressively simpler graph when an optional stage crashes on a particular
    ComfyUI/IP-Adapter version.
    """
    ip_model = _pick_from_list(models.get("ipadapters", []), _IPADAPTER_CANDIDATES)
    cn_model = _pick_from_list(models.get("controlnets", []), _CONTROLNET_CANDIDATES)
    up_model = upscale_model or _pick_from_list(
        models.get("upscalers", []), _UPSCALE_CANDIDATES
    )
    clip_model = _pick_from_list(models.get("clip_vision", []), _CLIPVISION_CANDIDATES)

    auto_ip = bool(ip_model and style_image and clip_model)
    auto_cn = bool(cn_model and control_image)
    auto_up = bool(up_model)
    use_ip = enable_ip if enable_ip is not None else auto_ip
    use_cn = enable_cn if enable_cn is not None else auto_cn
    use_upscale = enable_upscale if enable_upscale is not None else auto_up

    g: dict[str, dict] = {}
    n = 0

    def add(class_type: str, **inputs):
        nonlocal n
        n += 1
        g[str(n)] = {"class_type": class_type, "inputs": inputs}
        return str(n)

    # ComfyUI's cond_has_hooks crashes (IndexError on an empty string) when a
    # CLIPTextEncode receives an empty prompt, producing a zero-length
    # conditioning. Never emit an empty string here.
    prompt_text = (prompt or " ").strip() or " "
    negative_text = (negative or " ").strip() or " "

    ckpt = add("CheckpointLoaderSimple", ckpt_name=checkpoint)
    pos = add("CLIPTextEncode", text=prompt_text, clip=[ckpt, 0])
    neg = add("CLIPTextEncode", text=negative_text, clip=[ckpt, 0])

    model_out = [ckpt, 0]

    # --- IP-Adapter: lock the deck-wide style from a reference image ---
    if use_ip:
        clipvision = add("CLIPVisionLoader", clip_name=clip_model)
        ip = add("IPAdapterModelLoader", model_name=ip_model)
        ip_inputs = dict(
            model=[ckpt, 0],
            ipadapter=[ip, 0],
            image=add("LoadImage", image=style_image),
            clip_vision=[clipvision, 0],
            weight=float(ip_weight),
            start_at=0.0,
            end_at=1.0,
        )
        # Only send version-sensitive fields when explicitly configured. The
        # IPAdapter node's accepted `weight_type`/`embeds_scaling` enum varies
        # between ComfyUI_IPAdapter_plus releases; omitting them lets the node
        # use its own default and avoids invalid-enum conditioning crashes.
        if ip_weight_type:
            ip_inputs["weight_type"] = ip_weight_type
        ip_applied = add("IPAdapter", **ip_inputs)
        model_out = [ip_applied, 0]

    # --- ControlNet: steer composition (off-centre subject for text space) ---
    if use_cn:
        cn = add("ControlNetLoader", ckpt_name=cn_model)
        cn_applied = add(
            "ControlNetApplyAdvanced",
            positive=[pos, 0],
            negative=[neg, 0],
            control_net=[cn, 0],
            image=add("LoadImage", image=control_image),
            strength=float(control_strength),
            start_percent=0.0,
            end_percent=1.0,
            type=control_type,
        )
        pos, neg = [cn_applied, 0], [cn_applied, 1]

    vae_out = [ckpt, 1] if not vae else [add("VAELoader", vae_name=vae), 0]

    latent = add(
        "EmptyLatentImage", width=int(width), height=int(height), batch_size=1
    )
    sampler = add(
        "KSampler",
        seed=int(seed),
        steps=int(steps),
        cfg=float(cfg),
        sampler_name=sampler,
        scheduler=scheduler,
        denoise=1.0,
        model=model_out,
        positive=pos,
        negative=neg,
        latent_image=[latent, 0],
    )
    decoded = add("VAEDecode", samples=[sampler, 0], vae=vae_out)

    if use_upscale:
        up = add("UpscaleModelLoader", model_name=up_model)
        upscaled = add(
            "ImageUpscaleWithModel", upscale_model=[up, 0], image=[decoded, 0]
        )
        add("SaveImage", images=[upscaled, 0], filename_prefix="doc_mcp")
    else:
        add("SaveImage", images=[decoded, 0], filename_prefix="doc_mcp")

    return g


# Auto-discovery of available models so the backend works with zero config.
_DISCOVERY_CACHE: dict | None = None

# (node class, input key) pairs we probe to learn what a ComfyUI instance has
# installed. Each maps to a capability the consistency pipeline can use.
_DISCOVERY_NODES: dict[str, tuple[str, str]] = {
    "checkpoints": ("CheckpointLoaderSimple", "ckpt_name"),
    "vae": ("VAELoader", "vae_name"),
    "loras": ("LoraLoader", "lora_name"),
    "controlnets": ("ControlNetLoader", "ckpt_name"),
    "ipadapters": ("IPAdapterModelLoader", "model_name"),
    "upscalers": ("UpscaleModelLoader", "model_name"),
    "clip_vision": ("CLIPVisionLoader", "clip_name"),
    "samplers": ("KSampler", "sampler_name"),
    "schedulers": ("KSampler", "scheduler"),
}


async def discover_comfy_models(force: bool = False) -> dict:
    """Query the ComfyUI API for every model type it can load.

    Returns a dict of capability -> list of installed model names, e.g.
    ``{"checkpoints": [...], "controlnets": [...], "ipadapters": [...], ...}``.
    This lets the backend build a consistency pipeline from whatever the
    instance actually has, with no hand-written workflow JSON required.

    Results are cached for the process lifetime. Pass ``force=True`` to bypass
    the cache (e.g. after installing new models on the ComfyUI server).
    """
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE is not None and not force:
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

    models = {cap: _options(node, key) for cap, (node, key) in _DISCOVERY_NODES.items()}
    _DISCOVERY_CACHE = models
    return models


def _pick_checkpoint(discovered: list[str], configured: str) -> str:
    """Choose a checkpoint from the discovered list.

    If ``configured`` exactly matches an installed checkpoint it is used (so an
    explicit, correct override is honoured). Otherwise we auto-select from the
    discovered models (SDXL-style preferred), ignoring a configured name that
    does not exist on the server — this is what makes zero-config discovery
    actually work when the default does not match the instance's filenames.
    """
    pool = discovered or []
    if configured and configured in pool:
        return configured
    if not pool:
        # No discovery data: best effort with whatever was configured.
        return configured
    low = [n.lower() for n in pool]
    # Prefer an SDXL *base* checkpoint; avoid turbo/refiner/sd3 fast variants.
    for name, lname in zip(pool, low):
        if "base" in lname and ("xl" in lname or "sdxl" in lname):
            return name
    for name, lname in zip(pool, low):
        if "xl" in lname and "turbo" not in lname and "refiner" not in lname:
            return name
    for name, lname in zip(pool, low):
        if "turbo" not in lname and "refiner" not in lname and "sd3" not in lname:
            return name
    return pool[0]


def _pick_from_list(discovered: list[str], candidates: list[str], configured: str | None = None) -> str:
    """Pick the first discovered model matching a candidate (case-insensitive substring).

    Falls back to ``configured`` if it is present in the discovered list, then to
    the first discovered model, then to "" (caller decides whether to skip).
    """
    pool = discovered or []
    if configured and configured in pool:
        return configured
    lowered = [c.lower() for c in candidates]
    for name in pool:
        low = name.lower()
        if any(c in low for c in lowered):
            return name
    return pool[0] if pool else ""


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

    # --- Per-target consistency presets -------------------------------------
    # `target` is threaded through from ImageSpec (content / background / icon).
    target = getattr(theme, "_current_target", None) or "content"
    # --- Consistency values from the theme (IP-Adapter / ControlNet / upscale) ---
    ip_weight = getattr(theme, "ip_adapter_weight", 0.7) or 0.7
    ip_weight_type = getattr(theme, "ip_adapter_weight_type", None)
    cn = getattr(theme, "controlnet", None)
    control_type = getattr(cn, "type", "depth") or "depth"
    # Defaults tuned per role; a theme may override strength/type via `controlnet`.
    control_strength = {
        "background": 0.6,
        "icon": 0.35,
        "content": 0.5,
    }.get(target, 0.5)
    if cn and getattr(cn, "enabled", False):
        control_strength = getattr(cn, "strength", control_strength)
        control_type = getattr(cn, "type", control_type) or control_type

    upscale_model = getattr(theme, "upscale_model", None) or ""

    # --- Discover everything the instance has, then auto-wire the pipeline --
    if settings.comfy_api_autodiscover:
        models = await discover_comfy_models()
    else:
        models = {
            "checkpoints": [settings.comfy_api_checkpoint] if settings.comfy_api_checkpoint else [],
            "samplers": [settings.comfy_api_sampler],
            "schedulers": [settings.comfy_api_scheduler],
        }
    checkpoint = _pick_checkpoint(models.get("checkpoints", []), settings.comfy_api_checkpoint)
    sampler = _coerce_to_known(settings.comfy_api_sampler, models.get("samplers", []), settings.comfy_api_sampler)
    scheduler = _coerce_to_known(settings.comfy_api_scheduler, models.get("schedulers", []), settings.comfy_api_scheduler)
    vae = _pick_from_list(models.get("vae", []), ["sdxl_vae", "vae"])
    if not checkpoint:
        raise ComfyClientError(
            "No checkpoints found on the ComfyUI server and COMFY_API_CHECKPOINT is unset."
        )

    # --- Auto style reference (IP-Adapter): use the theme's image if provided,
    # otherwise generate one anchor image per deck so every slide shares a look.
    style_image = _resolve_style_reference(theme, target)
    if not style_image and models.get("ipadapters"):
        style_image = await _ensure_anchor_image(
            base, headers, settings, models, checkpoint, sampler, scheduler, theme, width, height
        )
    # ComfyUI and this server may be separate containers: upload any local
    # reference image to ComfyUI's input folder so LoadImage can read it.
    if style_image:
        style_image = await _to_comfy_input_name(base, headers, settings, style_image)

    # --- ControlNet composition reference: reuse the style image (or theme
    # controlnet.reference_image) so subjects sit off-centre for text space.
    control_image = (getattr(cn, "reference_image", None) if cn else None) or style_image
    if control_image:
        control_image = await _to_comfy_input_name(base, headers, settings, control_image)

    seed = settings.comfy_api_seed or random.randint(0, 2**32 - 1)

    common = dict(
        prompt=prompt,
        negative=negative_prompt or "",
        width=width,
        height=height,
        checkpoint=checkpoint,
        steps=settings.comfy_api_steps,
        cfg=settings.comfy_api_cfg,
        sampler=sampler,
        scheduler=scheduler,
        seed=seed,
        models=models,
        style_image=style_image,
        ip_weight=ip_weight,
        ip_weight_type=ip_weight_type,
        control_image=control_image,
        control_strength=control_strength,
        control_type=control_type,
        upscale_model=upscale_model,
        vae=vae,
        target=target,
    )

    # Try the richest pipeline first, then progressively drop optional stages
    # (upscale -> ControlNet -> IP-Adapter) if one of them crashes on this
    # ComfyUI / custom-node version. The final attempt is always bare
    # text-to-image, which every SDXL setup supports, so we never fail to
    # produce an image purely because of an incompatible consistency stage.
    stages = [
        {"enable_ip": None, "enable_cn": None, "enable_upscale": None},
        {"enable_ip": None, "enable_cn": None, "enable_upscale": False},
        {"enable_ip": None, "enable_cn": False, "enable_upscale": False},
        {"enable_ip": False, "enable_cn": False, "enable_upscale": False},
    ]
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
        for i, toggles in enumerate(stages):
            workflow = build_consistency_workflow(**common, **toggles)
            try:
                out_path = await _submit_and_fetch(
                    client, base, headers, workflow, settings
                )
                if i > 0:
                    print(
                        f"[warn] image generated with simplified pipeline "
                        f"(stage {i}: dropped "
                        f"{'upscale' if i==1 else 'controlnet' if i==2 else 'ip-adapter'}); "
                        "a consistency stage is incompatible with this ComfyUI setup."
                    )
                return str(out_path)
            except ComfyClientError as exc:
                last_exc = exc
                continue
    raise last_exc or ComfyClientError("ComfyUI image generation failed.")


async def _submit_and_fetch(client, base: str, headers, workflow: dict, settings) -> str:
    """Submit a workflow to ComfyUI and return the local path of the output image."""
    client_id = uuid.uuid4().hex
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


# Cache one anchor (style-reference) image per deck so all slides share it.
_ANCHOR_CACHE: dict[str, str] = {}


def _resolve_style_reference(theme, target: str) -> str:
    """Return a style-reference image path/URL if explicitly set on the theme."""
    return getattr(theme, "style_reference_image", None) or ""


async def _to_comfy_input_name(
    base: str, headers, settings, image_ref: str
) -> str:
    """Resolve an image reference into a name ComfyUI's LoadImage can read.

    The MCP server and ComfyUI often run in separate containers/filesystems, so
    a local path on the MCP side is invisible to ComfyUI. For any local file we
    upload it to ComfyUI's ``input/`` folder via ``/upload/image`` and return
    the stored basename (which ``LoadImage`` resolves under ``input/``). URLs
    and names that already look like ComfyUI inputs are returned unchanged.
    """
    if not image_ref:
        return image_ref
    parsed = urlparse(image_ref)
    # Already a bare ComfyUI input name (no scheme, no directory) -> use as-is.
    if parsed.scheme == "" and "/" not in parsed.path and "\\" not in parsed.path:
        return image_ref
    # Remote URL: ComfyUI cannot fetch it directly; download then upload.
    local = image_ref
    if parsed.scheme in ("http", "https"):
        local = await _download(image_ref, settings.image_cache_dir)
    path = Path(local)
    if not path.exists():
        # Nothing we can do; let ComfyUI report the missing file.
        return image_ref
    return await _upload_to_comfy(base, headers, settings, path)


async def _upload_to_comfy(base: str, headers, settings, path: Path) -> str:
    """Upload a local image to ComfyUI's input folder; return its stored name."""
    async with httpx.AsyncClient(timeout=60) as client:
        files = {
            "image": (path.name, path.read_bytes(), "image/png"),
            "overwrite": "true",
        }
        resp = await client.post(
            f"{base}/upload/image", files=files, headers=headers or None
        )
        resp.raise_for_status()
        data = resp.json()
    # ComfyUI returns {"name": "x.png", "subfolder": "", "type": "input"}.
    name = data.get("name") or path.name
    return name


async def _ensure_anchor_image(
    base: str, headers, settings, models, checkpoint, sampler, scheduler, theme, width, height
) -> str:
    """Generate (and cache) a single anchor image to drive IP-Adapter consistency.

    Produces one cohesive reference per deck from the theme's ``image_style`` so
    every subsequent slide image inherits the same palette/mood — no workflow
    JSON or pre-supplied asset required.
    """
    cache_key = f"{getattr(theme, 'name', '')}:{theme.image_style}"
    if cache_key in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[cache_key]

    anchor_prompt = f"abstract cohesive brand texture, {theme.image_style}".strip(", ")
    # Build a minimal (no-IPA) graph for the anchor so we don't recurse, then
    # submit it through the same staged fallback used for slides so a transient
    # ComfyUI node crash doesn't kill the whole deck before image generation.
    common = dict(
        prompt=anchor_prompt,
        negative="text, watermark, blurry, low quality",
        width=min(width, 1024),
        height=min(height, 1024),
        checkpoint=checkpoint,
        steps=settings.comfy_api_steps,
        cfg=settings.comfy_api_cfg,
        sampler=sampler,
        scheduler=scheduler,
        seed=random.randint(0, 2**32 - 1),
        models=models,
        style_image="",
        control_image="",
        ip_weight=0.0,
        ip_weight_type=None,
        control_strength=0.0,
        control_type=None,
        upscale_model=None,
        vae=None,
        target="background",
    )
    stages = [
        {"enable_ip": None, "enable_cn": None, "enable_upscale": None},
        {"enable_ip": None, "enable_cn": None, "enable_upscale": False},
        {"enable_ip": None, "enable_cn": False, "enable_upscale": False},
        {"enable_ip": False, "enable_cn": False, "enable_upscale": False},
    ]
    async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
        for toggles in stages:
            wf = build_consistency_workflow(**common, **toggles)
            try:
                out_path = await _submit_and_fetch(client, base, headers, wf, settings)
                break
            except ComfyClientError:
                continue
        else:
            # All stages failed: no anchor available; slides fall back to plain
            # text-to-image without a shared style reference.
            return ""
        # Keep the anchor on ComfyUI's side: upload to its input folder and
        # cache the name LoadImage can resolve (avoids a local round-trip and a
        # second upload per slide in separate-container deployments).
        tmp = settings.image_cache_dir / f"anchor_{uuid.uuid4().hex}.png"
        tmp.write_bytes(Path(out_path).read_bytes())
        name = await _upload_to_comfy(base, headers, settings, tmp)
        _ANCHOR_CACHE[cache_key] = name
        return name


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
                # Extract the concise failure info ComfyUI embeds rather than
                # dumping the whole (huge) status object.
                msg = "ComfyUI job failed"
                for msg_pair in entry.get("status", {}).get("messages", []):
                    if len(msg_pair) >= 2 and isinstance(msg_pair[1], dict):
                        exc = msg_pair[1]
                        if exc.get("exception_type") or exc.get("exception_message"):
                            msg = (
                                f"ComfyUI node {exc.get('node_type', '?')} "
                                f"({exc.get('exception_type', 'error')}): "
                                f"{exc.get('exception_message', '')}"
                            )
                            break
                raise ComfyClientError(msg)
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
            path = cache_dir / f"img_{uuid.uuid4().hex}.{ext}"
            path.write_bytes(base64.b64decode(data))
            return str(path)

        text = getattr(item, "text", None)
        if not text:
            continue
        candidate = await _coerce_to_path(text, cache_dir)
        if candidate:
            return candidate

    raise ComfyClientError(
        "ComfyUI MCP server returned no usable image. Result: "
        + json.dumps([getattr(i, "text", "") for i in getattr(result, "content", [])])
    )


async def _coerce_to_path(text: str, cache_dir: Path) -> str | None:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for key in ("image_path", "path", "image", "url", "images", "output"):
                val = obj.get(key)
                if isinstance(val, str):
                    return await _resolve_source(val, cache_dir)
                if isinstance(val, list) and val and isinstance(val[0], str):
                    return await _resolve_source(val[0], cache_dir)
        if isinstance(obj, list) and obj and isinstance(obj[0], str):
            return await _resolve_source(obj[0], cache_dir)
    except json.JSONDecodeError:
        pass

    line = text.strip().splitlines()[0].strip().strip('"\'')
    if line:
        return await _resolve_source(line, cache_dir)
    return None


async def _resolve_source(source: str, cache_dir: Path) -> str:
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return await _download(source, cache_dir)
    path = Path(source)
    if path.exists():
        return str(path.resolve())
    if (Path.cwd() / path).exists():
        return str((Path.cwd() / path).resolve())
    raise ComfyClientError(f"Image source not found or unsupported: {source}")


async def _download(url: str, cache_dir: Path) -> str:
    out = cache_dir / f"img_download_{hashlib.md5(url.encode('utf-8')).hexdigest()}.png"
    # Reuse a cached file if we already fetched this URL.
    if out.exists():
        return str(out)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=get_settings().comfy_auth_headers() or None)
        resp.raise_for_status()
        out.write_bytes(resp.content)
    return str(out)
