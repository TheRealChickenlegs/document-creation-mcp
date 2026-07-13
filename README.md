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
| `COMFY_API_WORKFLOW` | _(built-in)_ | Path to a JSON workflow template using `{{prompt}}`, `{{negative_prompt}}`, `{{width}}`, `{{height}}`, `{{seed}}`, `{{checkpoint}}`, `{{steps}}`, `{{cfg}}`, `{{sampler}}`, `{{scheduler}}`. |
| `COMFY_API_CHECKPOINT` | `sd_xl_base_1.0.safetensors` | Checkpoint loaded by the default workflow. |
| `COMFY_API_STEPS` / `COMFY_API_CFG` | `25` / `7.0` | KSampler steps / CFG scale. |
| `COMFY_API_SAMPLER` / `COMFY_API_SCHEDULER` | `euler` / `normal` | KSampler sampler / scheduler. |
| `COMFY_API_SEED` | `0` | Seed (`0` = random per request). |
| `COMFY_API_AUTODISCOVER` | `true` | When enabled, the `comfy_api` backend queries `/object_info` and auto-selects an installed checkpoint (preferring SDXL-style names) plus a valid sampler/scheduler, so no manual model config is needed. |
| `COMFY_MCP_TIMEOUT` | `300` | Seconds to wait for image generation (both backends). |
| `DOC_MCP_DISABLE_IMAGES` | `false` | Skip all image generation. |

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

## Recommended ComfyUI model stack for consistent decks

To get cohesive, on-brand imagery across an entire deck (not just one-off
pictures), run an **SDXL** base with **IP-Adapter** for style/subject consistency
and **ControlNet** for composition control, then **upscale** for projector-grade
output. The pipeline below is what `IMAGE_BACKEND=comfy_api` is designed to drive
(see `comfy_workflows/presentation_sdxl.json` for a ready-to-adapt template).

### What to install

| Purpose | Suggested model(s) | Notes |
|---------|--------------------|-------|
| **Base checkpoint** | `juggernautXL_v9Rundiffusion.safetensors` (general), or `RealVisXL` (photoreal/corporate), or `DreamShaper XL` (stylised) | Pick **one** per deck; set it as `COMFY_API_CHECKPOINT`. |
| **VAE** | `sdxl_vae.safetensors` | Usually bundled with the checkpoint. |
| **CLIP Vision** (IP-Adapter dependency) | `CLIP-ViT-H-14-laion2b-s32B-b79K.safetensors` | Required by IP-Adapter. |
| **IP-Adapter** | `ip-adapter-plus_sdxl_vit-h.safetensors` (style+composition) and/or `ip-adapter-plus-face_sdxl_vit-h.safetensors` (face/character lock) | Drives consistency from a single **style reference image**. |
| **ControlNet** | `controlnet-union-sdxl-1.0.safetensors` (all-in-one: depth/canny/pose/tile) | One file covers every composition mode. |
| **Upscaler** | `4x-UltraSharp.pth` (or `4x_NMKD-Siax_200k.pth`) | ESRGAN; sharpens the final 16:9 output. |
| **(Optional) Style LoRA** | any brand/style LoRA | Extra brand lock on top of IP-Adapter. |
| **Custom nodes** | `ComfyUI_IPAdapter_plus`, `ComfyUI_ControlNet_Union` (or `ComfyUI-Advanced-ControlNet`) | Provide the `IPAdapter` / `ControlNetApplyAdvanced` nodes used by the template. |

> The exact node class names/inputs vary slightly between custom-node versions.
> Treat `comfy_workflows/presentation_sdxl.json` as a **reference** to adapt to
> your installed node pack, then point `COMFY_API_WORKFLOW` at it.

### How consistency is enforced (the strategy)

1. **One style reference image per theme.** Store a reference image in the theme
   (e.g. `style_reference_image`) and feed it to IP-Adapter at a moderate weight
   (~0.6–0.8). Every slide image in that deck inherits the same look/colour
   mood — this is the single biggest lever for cohesion.
2. **Shared negative prompt + colour palette.** Bake a deck-wide negative
   (`watermark, text, blurry, low quality, jpeg artifacts`) and append the
   theme's `image_style` to every prompt (already done by `generate_image`).
3. **Composition control with ControlNet.** Use a "subject-off-centre" control
   image (or Union `type: depth/canny`) so subjects sit left/right, leaving
   negative space for titles and bullet text — especially for `image_full`
   backgrounds.
4. **Fixed resolutions per role.** Backgrounds: **16:9 → 1344×768** (or
   1536×864). Content/side images: **1:1 → 1024×1024** or **4:3 → 1152×896**.
   All divisible by 8 for SDXL.
5. **Upscale once at the end** for crisp projection.
6. **(Optional) per-deck seed base.** Generate with a fixed base seed + per-slide
   offset for reproducible backgrounds while keeping variety.

### Planned integration (not yet wired)

Once the models above are installed locally, the call path will be extended to
use them automatically via theme options, e.g.:

```yaml
name: dark_tech
# ...existing colors/fonts...
image_style: "cinematic, neon accents, dark moody background, 8k, highly detailed"
style_reference_image: "themes/refs/dark_tech_style.png"  # fed to IP-Adapter
ip_adapter_weight: 0.7
controlnet:
  enabled: true
  type: depth          # depth | canny | openpose | tile
  strength: 0.6
  reference_image: "themes/refs/dark_tech_comp.png"
upscale_model: "4x-UltraSharp.pth"
negative_prompt: "watermark, text, blurry, low quality, jpeg artifacts"
background_post: "dim"   # light blur + dark overlay so text stays readable
```

The `comfy_api` backend will then load `COMFY_API_WORKFLOW` (the IP-Adapter +
ControlNet + upscale graph) and substitute `{{style_image}}`, `{{ip_weight}}`,
`{{control_image}}`, `{{control_strength}}`, `{{upscale_model}}` from the theme,
alongside the existing `{{prompt}}` / `{{width}}` / `{{height}}` / `{{seed}}`
placeholders. Background images will also get a subtle dim/blur post-process so
titles read clearly. See `comfy_workflows/presentation_sdxl.json`.

## Slide plan schema

`create_presentation` takes the plan as a JSON **object** (not a string). Slide
text is given as `bullets` (a list of strings) or `content` (a string, which is
split on newlines, or a list).

```json
{
  "title": "Deck title",
  "theme": "dark_tech",
  "output_filename": "my_deck",
  "slides": [
    {"title": "Intro", "layout": "title", "subtitle": "An AI deck"},
    {"title": "Topic", "bullets": ["Point 1"], "image": {"prompt": "futuristic city"}},
    {"title": "Deep dive", "layout": "image_full", "image": {"prompt": "data flow"}}
  ]
}
```

Layouts: `title`, `title_and_content`, `two_column`, `image_full`, `section`.
