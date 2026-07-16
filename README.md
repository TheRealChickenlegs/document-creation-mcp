# document-creation-mcp

MCP server for AI-driven document creation, starting with **PowerPoint decks**.

The server is the *execution layer*: it builds `.pptx` files from a structured
slide plan, applies consistent design themes, and auto-generates images via your
existing **ComfyUI MCP server**. The orchestrating model (e.g. in Open WebUI) does
any web research and composes the slide plan, then calls these tools.

## Tools

| Tool | Purpose |
|------|---------|
| `list_themes()` | List available design theme names. |
| `get_theme(name)` | Return a theme's colors/fonts/image-style. |
| `generate_image(prompt, theme, size, ...)` | Generate one image via ComfyUI MCP; returns local path. |
| `create_presentation(plan)` | Build a deck from a `PresentationPlan` object; returns file path. |
| `list_comfy_models()` | List checkpoints/samplers/schedulers available on the ComfyUI HTTP API. |

`create_presentation` will auto-generate any image that has an `image.prompt`
(using ComfyUI), and embed existing files/URLs when `image.source` is set.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Configure (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `DOC_MCP_OUTPUT_DIR` | `output` | Where `.pptx` files are written. |
| `DOC_MCP_IMAGE_DIR` | `output/images` | Where generated images are cached. |
| `DOC_MCP_TRANSPORT` | `stdio` | Transport for the server itself: `stdio`, `sse`, or `streamable-http`. |
| `DOC_MCP_HOST` | `127.0.0.1` | Bind address when serving over HTTP/SSE. Defaults to localhost for security; set `0.0.0.0` to expose on the network (e.g. from Docker). |
| `DOC_MCP_PORT` | `8000` | Port the server listens on for `sse` / `streamable-http`. |
| `DOC_MCP_STREAMABLE_HTTP_PATH` | `/mcp` | Endpoint path for `streamable-http`. Set to `/` if your client POSTs to the server root (e.g. some MetaMCP configurations). |
| `DOC_MCP_STATELESS_HTTP` | `true` | Run streamable-http in stateless mode (no session). Recommended behind proxies (MetaMCP/Open WebUI) to avoid `404` on requests without a session id. Set `false` for strict stateful sessions. |
| `DOC_MCP_THEME_DIR` | _(bundled)_ | Directory of `*.yaml` theme files, merged on top of the bundled themes. Set this (e.g. a mounted volume) to add or override themes. |
| `IMAGE_BACKEND` | `mcp` | Image source: `mcp` (remote ComfyUI MCP server) or `comfy_api` (ComfyUI HTTP API directly). |
| **MCP backend** (`IMAGE_BACKEND=mcp`) | | |
| `COMFY_MCP_URL` | _(none)_ | Address of your running ComfyUI MCP server, e.g. `http://comfyui-mcp:8000/mcp` or `.../sse`. |
| `COMFY_MCP_API_KEY` | _(none)_ | Bearer token sent as `Authorization: Bearer <key>` (if the server requires auth). |
| `COMFY_MCP_TRANSPORT` | `auto` | `auto` (detect from URL), `streamable-http`, or `sse`. |
| `COMFY_MCP_TOOL` | `generate_image` | Name of the image tool in that server. |
| `COMFY_MCP_COMMAND` | _(fallback)_ | Only used if `COMFY_MCP_URL` is unset, to spawn a stdio subprocess. |
| **Direct API backend** (`IMAGE_BACKEND=comfy_api`) | | |
| `COMFY_API_URL` | _(none)_ | Base URL of the ComfyUI instance, e.g. `http://comfyui:8188`. |
| `COMFY_API_KEY` | _(none)_ | Optional bearer token for the ComfyUI endpoint. |
| `COMFY_API_CHECKPOINT` | _(auto)_ | Checkpoint to load. When unset, auto-selected from installed checkpoints (SDXL-style preferred). |
| `COMFY_API_STEPS` / `COMFY_API_CFG` | `25` / `7.0` | KSampler steps / CFG scale. |
| `COMFY_API_SAMPLER` / `COMFY_API_SCHEDULER` | `euler` / `normal` | KSampler sampler / scheduler (auto-matched to installed values). |
| `COMFY_API_SEED` | `0` | Seed (`0` = random per request). |
| `COMFY_API_AUTODISCOVER` | `true` | When enabled (default), the `comfy_api` backend queries `/object_info` and auto-detects **every** installed model — checkpoints, VAE, LoRA, ControlNet, IP-Adapter, CLIP-Vision and upscalers — then builds a consistency pipeline (IP-Adapter style lock + ControlNet composition + upscale) using only what is present. No workflow JSON is required. |
| `COMFY_MCP_TIMEOUT` | `300` | Seconds to wait for image generation (both backends). |
| `DOC_MCP_DISABLE_IMAGES` | `false` | Skip all image generation. |
| `DOC_MCP_RETURN_BASE64` | `true` | When `true`, `create_presentation` includes the `.pptx` as base64 (`download` field) in its result so it is retrievable through the chat client without host access. Set `false` to return only the path (e.g. when the output dir is a mounted volume you read directly). |
| **MinIO / S3 retrieval** (`MINIO_ENDPOINT` set) | | |
| `MINIO_ENDPOINT` | _(none)_ | MinIO/S3 endpoint, e.g. `minio:9000` or `localhost:9000`. Enables upload whenever set. |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | _(none)_ | S3 access key id / secret access key (the MinIO **username** / **password**). Set these to match the credentials used elsewhere (e.g. an n8n S3 node). Optional only for anonymous / proxy-authenticated instances. |
| `MINIO_BUCKET` | `presentations` | Target bucket (created if missing). A `path/like/this` value is split into bucket `path` + prefix `like/this/`. |
| `MINIO_USE_HTTPS` | `false` | Use HTTPS to the endpoint (usually `false` internally). |
| `MINIO_REGION` | `us-east-1` | Region (default `us-east-1`; the value is cosmetic for a local install). |
| `MINIO_PUBLIC_URL` | _(none)_ | If set (e.g. `https://minio.example.com` **or** `https://minio.example.com/media`), a direct link is returned; otherwise a presigned GET URL is generated. By default the bucket is appended to the path (`{public_url}/{bucket}/{object}` — the standard MinIO path-style reverse-proxy layout). |
| `MINIO_PUBLIC_INCLUDES_BUCKET` | `false` | Set `true` if `MINIO_PUBLIC_URL` already contains the bucket segment. |
| `MINIO_PUBLIC_READ` | `true` | Upload objects with a public-read grant so browsers / Open WebUI can fetch them directly (same as n8n's S3 `grantRead: true`). |
| `MINIO_PRESIGNED_EXPIRY_HOURS` | `168` | Lifetime (hours) of the presigned URL when no public URL is set. |
| `MINIO_PREFIX` | _(none)_ | Object-name prefix inside the bucket, e.g. `decks/`. |

## Run

```bash
document-creation-mcp            # stdio transport (recommended for Open WebUI)
# or: python -m document_creation_mcp.server
```

## Docker

Build and run the server inside a container.

```bash
# Build the image
docker build -t document-creation-mcp .

# Run with streamable-http transport (default in the Dockerfile)
docker run -p 8000:8000 \
  -e COMFY_MCP_COMMAND='["python","-m","comfy_mcp_server"]' \
  -v "$(pwd)/output:/app/output" \
  document-creation-mcp
```

Or use the provided Compose file:

```bash
docker compose up --build
```

The container serves on port `8000` using `streamable-http` by default. Generated
`.pptx` files are written to `/app/output` (mount `./output` to retrieve them).
Override `DOC_MCP_TRANSPORT=stdio` if you instead want the container spawned as a
stdio MCP server by its parent.

## Open WebUI setup

1. Start your ComfyUI MCP server separately (the command above must reach it).
2. In Open WebUI → Admin → Tools → Add MCP server, point at this server
   (stdio command: `document-creation-mcp`, or an SSE URL if you wrap it).
3. The model can now call `create_presentation` (after doing web search and
   drafting the plan) and `generate_image` for bespoke visuals.

When running this server in Docker, register it as an HTTP/SSE MCP server
pointing at `http://<host>:8000/mcp` (streamable-http) or `/sse` instead of the
stdio command.

### Connecting over HTTP (Open WebUI / MetaMCP)

- The server only listens for `sse` / `streamable-http` when
  `DOC_MCP_TRANSPORT` is set to one of those (the Docker image defaults to
  `streamable-http`).
- It must be **reachable** from the client: set `DOC_MCP_HOST=0.0.0.0` and
  publish the port (e.g. `ports: ["3335:3335"]` in Compose with
  `DOC_MCP_PORT=3335`). A `Connection refused` means the container isn't up,
  isn't on that port, or is bound to `127.0.0.1`.
- The client URL must include the endpoint **path**:
  - default → `http://<host>:<port>/mcp`
  - if your client POSTs to the server root (some MetaMCP setups do), set
    `DOC_MCP_STREAMABLE_HTTP_PATH=/` and use `http://<host>:<port>/`.
  - A `404 Not Found` on a POST means the path didn't match — adjust
    `DOC_MCP_STREAMABLE_HTTP_PATH` or add `/mcp` to the URL. Intermittent `404`s
    on `/mcp` (especially from different client IPs) are usually stateful-session
    rejects from a proxy; set `DOC_MCP_STATELESS_HTTP=true` (the default) so each
    request is handled without a session.

### Retrieving generated files

`create_presentation` writes the `.pptx` to `DOC_MCP_OUTPUT_DIR` and returns its
path. Because the server runs in a container, that path is internal — to get the
file:

- **MinIO (best for shared access):** set `MINIO_ENDPOINT` (with keys). The tool
  uploads the file and returns a `download.url` — a direct link if
  `MINIO_PUBLIC_URL` is set, otherwise a presigned GET URL. Anyone with the link
  can fetch the deck; the container needs the `minio` extra installed (the Docker
  image includes it).
- **Base64 (no infra):** keep `DOC_MCP_RETURN_BASE64=true` (default). The tool
  result includes a `download` field with `filename`, `mime_type` and base64
  `data`. Save/decode that to get the file through the chat client (Open WebUI).
- **Mounted volume:** mount `DOC_MCP_OUTPUT_DIR` (the Compose file mounts
  `./output:/app/output`) and read `./output/<name>.pptx` from the host, then set
  `DOC_MCP_RETURN_BASE64=false` to avoid the base64 in context.

The `download` object may contain any combination of `url`, `data` and
`minio_error` (if an upload failed but the deck was still built).

## Themes

Factory themes ship **inside the package** at `src/document_creation_mcp/themes/*.yaml`
(`dark_tech`, `corporate`, `minimal`, `academic`), so they are always available
after install, including in Docker.

```yaml
name: dark_tech
colors:
  background: "#0B0E14"
  primary: "#4F8CFF"
  accent: "#00E0C6"
  text: "#E6EAF2"
  muted: "#8A93A6"
fonts:
  heading: "Montserrat"
  body: "Inter"
image_style: "cinematic, neon accents, dark moody background, 8k, highly detailed"
layout_default: title_and_content
logo: null
```

`image_style` is appended to every generated image prompt for visual consistency.

**Add or override themes:** set `DOC_MCP_THEME_DIR` to a directory of `*.yaml`
files (e.g. a mounted volume in Docker). Its themes are merged on top of the
bundled ones, so a file with the same `name` overrides a factory theme.

> Note: `style_reference_image` / `controlnet.reference_image` paths are resolved
> at runtime — in Docker, mount those assets and use absolute paths (or paths
> relative to the container working directory). The optional advanced workflow
> `COMFY_API_WORKFLOW` similarly needs to be mounted into the container.

To refresh after editing theme files, the server reloads them on startup; there
is also a `list_themes()` tool to confirm what is loaded.

## Automatic, consistent image generation (no workflow files)

`IMAGE_BACKEND=comfy_api` drives ComfyUI **directly** and needs **no workflow
JSON**. On first generation it queries the instance's `/object_info` to discover
**every** installed model — checkpoints, VAE, LoRA, ControlNet, IP-Adapter,
CLIP-Vision and upscalers — then assembles a consistency pipeline in code using
only what is present:

```
Checkpoint ─► [IP-Adapter] ─► [ControlNet] ─► KSampler ─► VAEDecode
                                                    │
                                           [Upscale] ─► SaveImage
```

Bracketed nodes are inserted conditionally, so a bare text-to-image graph is
used when only a checkpoint is available, and the full IP-Adapter + ControlNet +
upscale pipeline kicks in automatically as more models are installed. There is
nothing to configure by hand.

### What to install (more = more consistent)

| Purpose | Suggested model(s) | Effect when present |
|---------|--------------------|---------------------|
| **Base checkpoint** | `juggernautXL_v9Rundiffusion.safetensors` (general), `RealVisXL` (photoreal/corporate), `DreamShaper XL` (stylised) | Auto-selected (SDXL-style preferred). |
| **VAE** | `sdxl_vae.safetensors` | Auto-loaded if found. |
| **CLIP Vision** | `CLIP-ViT-H-14-laion2b-s32B-b79K.safetensors` | Enables IP-Adapter. |
| **IP-Adapter** | `ip-adapter-plus_sdxl_vit-h.safetensors` | Locks every slide to one deck-wide style. |
| **ControlNet** | `controlnet-union-sdxl-1.0.safetensors` | Keeps subjects off-centre so text stays readable. |
| **Upscaler** | `4x-UltraSharp.pth` / `4x_NMKD-Siax_200k.pth` | Sharper projector-grade output. |
| **Custom nodes** | `ComfyUI_IPAdapter_plus`, `ComfyUI_ControlNet_Union` | Provide the IP-Adapter / ControlNet nodes. |

### How consistency is enforced

1. **Auto style reference.** If a theme sets `style_reference_image` it is used;
   otherwise the backend generates one anchor image per deck from the theme's
   `image_style` and feeds it to IP-Adapter, so all slides share a look with no
   supplied asset.
2. **Per-role presets** (`ImageSpec.target`):
   - `background` → stronger ControlNet + auto dim/blur post-process for legibility.
   - `content` → balanced style + composition lock.
   - `icon` → lighter composition control, placed as a small top-right asset.
3. **Shared negative prompt + palette.** The deck/theme `negative_prompt` and the
   theme `image_style` suffix are applied to every prompt.
4. **Graceful degradation.** Missing ControlNet / IP-Adapter / upscaler → that
   stage is skipped; the graph always submits successfully.

### Optional theme tuning

```yaml
name: dark_tech
image_style: "cinematic, neon accents, dark moody background, 8k, highly detailed"
style_reference_image: "themes/refs/dark_tech_style.png"  # optional; auto-generated if omitted
ip_adapter_weight: 0.7
ip_adapter_weight_type: null   # optional; omit so the node uses its own default
                               # (enum varies between IP-Adapter versions)
controlnet:
  enabled: true
  type: depth          # depth | canny | openpose | tile
  strength: 0.6
  reference_image: "themes/refs/dark_tech_comp.png"
upscale_model: "4x-UltraSharp.pth"   # optional; auto-selected if omitted
negative_prompt: "watermark, text, blurry, low quality, jpeg artifacts"
background_post: "dim"   # light blur + dark overlay so text stays readable
```

Set `COMFY_API_AUTODISCOVER=false` only if you want to pin specific model names
via the `COMFY_API_*` env vars instead of auto-detection.

## Slide plan schema

`create_presentation` takes the plan as a JSON **object** (not a string). Slide
text is given as `bullets` (a list of strings) or `content` (a string, which is
split on newlines, or a list).

```json
{
  "title": "Deck title",
  "theme": "dark_tech",
  "output_filename": "my_deck",
  "bucket": "presentations",
  "slides": [
    {"title": "Intro", "layout": "title", "subtitle": "An AI deck"},
    {"title": "Topic", "bullets": ["Point 1"], "image": {"prompt": "futuristic city"}},
    {"title": "Deep dive", "layout": "image_full", "image": {"prompt": "data flow"}}
  ]
}
```

Layouts: `title`, `title_and_content`, `two_column`, `image_full`, `section`.
The optional `bucket` field sets the MinIO bucket for this deck (overrides
`MINIO_BUCKET` env).
