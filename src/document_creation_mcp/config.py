from __future__ import annotations

import os
from pathlib import Path


class Settings:
    """Runtime configuration loaded from environment variables.

    All values have sane local defaults so the server runs out of the box.
    """

    def __init__(self) -> None:
        base = Path(os.environ.get("DOC_MCP_OUTPUT_DIR", "output"))
        self.output_dir: Path = base.expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Where generated images are cached between runs.
        self.image_cache_dir: Path = (
            Path(os.environ.get("DOC_MCP_IMAGE_DIR", str(self.output_dir / "images")))
            .expanduser()
            .resolve()
        )
        self.image_cache_dir.mkdir(parents=True, exist_ok=True)

        # Which backend generates images.
        #   "mcp"       -> call a remote ComfyUI MCP server (over HTTP/SSE)
        #   "comfy_api" -> call the ComfyUI HTTP API directly
        self.image_backend: str = os.environ.get("IMAGE_BACKEND", "mcp").lower()

        # --- Remote ComfyUI MCP server (backend = "mcp") ---
        # Network address of an already-running ComfyUI MCP server.
        # e.g. "http://comfyui-mcp:8000/mcp" (streamable-http) or
        #      "http://comfyui-mcp:8000/sse" (SSE).
        self.comfy_mcp_url: str | None = os.environ.get("COMFY_MCP_URL")
        self.comfy_mcp_api_key: str | None = os.environ.get("COMFY_MCP_API_KEY")
        self.comfy_mcp_transport: str = os.environ.get(
            "COMFY_MCP_TRANSPORT", "auto"
        ).lower()

        # Optional fallback: launch the ComfyUI MCP server as a subprocess (stdio).
        raw = os.environ.get(
            "COMFY_MCP_COMMAND",
            '["python", "-m", "comfy_mcp_server"]',
        )
        self.comfy_mcp_command: list[str] = _parse_command(raw)

        # Name of the image-generation tool exposed by the ComfyUI MCP server.
        self.comfy_image_tool: str = os.environ.get("COMFY_MCP_TOOL", "generate_image")

        # --- Direct ComfyUI HTTP API (backend = "comfy_api") ---
        # Base URL of the ComfyUI instance, e.g. "http://comfyui:8188".
        self.comfy_api_url: str | None = os.environ.get("COMFY_API_URL")
        self.comfy_api_key: str | None = os.environ.get("COMFY_API_KEY")
        # Optional path to a JSON workflow template using {{placeholders}}.
        self.comfy_api_workflow: str | None = os.environ.get("COMFY_API_WORKFLOW")
        self.comfy_api_checkpoint: str = os.environ.get(
            "COMFY_API_CHECKPOINT", "sd_xl_base_1.0.safetensors"
        )
        self.comfy_api_steps: int = int(os.environ.get("COMFY_API_STEPS", "25"))
        self.comfy_api_cfg: float = float(os.environ.get("COMFY_API_CFG", "7.0"))
        self.comfy_api_sampler: str = os.environ.get("COMFY_API_SAMPLER", "euler")
        self.comfy_api_scheduler: str = os.environ.get("COMFY_API_SCHEDULER", "normal")
        self.comfy_api_seed: int = int(os.environ.get("COMFY_API_SEED", "0"))
        # Auto-discover installed checkpoints/samplers from the ComfyUI API so
        # the backend needs no manual model configuration.
        self.comfy_api_autodiscover: bool = (
            os.environ.get("COMFY_API_AUTODISCOVER", "true").lower() == "true"
        )

        # Seconds to wait for the ComfyUI MCP/API server to respond.
        self.comfy_timeout: float = float(os.environ.get("COMFY_MCP_TIMEOUT", "300"))

        # If true, image generation is skipped and image specs are ignored.
        self.disable_images: bool = (
            os.environ.get("DOC_MCP_DISABLE_IMAGES", "false").lower() == "true"
        )

    def comfy_auth_headers(self) -> dict[str, str]:
        key = self.comfy_mcp_api_key or self.comfy_api_key
        if key:
            return {"Authorization": f"Bearer {key}"}
        return {}


def _parse_command(raw: str) -> list[str]:
    import json

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed
    except json.JSONDecodeError:
        pass
    # Fall back to shell-style split.
    return raw.split()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
