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
| `create_presentation(plan_json)` | Build a deck from a `PresentationPlan` JSON; returns file path. |

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
| `COMFY_MCP_COMMAND` | `["python","-m","comfy_mcp_server"]` | How to launch the ComfyUI MCP server (stdio). |
| `COMFY_MCP_TOOL` | `generate_image` | Name of the image tool in that server. |
| `COMFY_MCP_TIMEOUT` | `300` | Seconds to wait for image generation. |
| `DOC_MCP_DISABLE_IMAGES` | `false` | Skip all image generation. |

## Run

```bash
document-creation-mcp            # stdio transport (recommended for Open WebUI)
# or: python -m document_creation_mcp.server
```

## Open WebUI setup

1. Start your ComfyUI MCP server separately (the command above must reach it).
2. In Open WebUI → Admin → Tools → Add MCP server, point at this server
   (stdio command: `document-creation-mcp`, or an SSE URL if you wrap it).
3. The model can now call `create_presentation` (after doing web search and
   drafting the plan) and `generate_image` for bespoke visuals.

## Themes

Themes live in `themes/*.yaml`:

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
Add a new YAML file to register a new theme automatically.

## Slide plan schema

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
