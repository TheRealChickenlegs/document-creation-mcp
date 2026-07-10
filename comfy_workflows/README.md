# Reference ComfyUI workflow for consistent decks

`presentation_sdxl.json` is a **reference** API-format workflow for the
`comfy_api` backend. It wires the recommended consistency pipeline:

```
Checkpoint ─► IP-Adapter (style reference) ─► ControlNet (composition)
                                                │
KSampler ◄──────────────────────────────────────┘
   │
VAEDecode ─► Upscale ─► SaveImage
```

## Placeholders

The backend substitutes these (exact-match string values are coerced to the
right type by `comfy_client._substitute`):

| Placeholder | Source |
|-------------|--------|
| `{{prompt}}` | image prompt (+ theme `image_style`) |
| `{{negative_prompt}}` | deck/theme negative prompt |
| `{{width}}` / `{{height}}` | image size (divisible by 8) |
| `{{seed}}` | per-request seed |
| `{{checkpoint}}` | `COMFY_API_CHECKPOINT` / auto-discovered |
| `{{steps}}` / `{{cfg}}` / `{{sampler}}` / `{{scheduler}}` | sampler settings |
| `{{style_image}}` | theme `style_reference_image` (IP-Adapter) |
| `{{ip_weight}}` | theme `ip_adapter_weight` |
| `{{control_image}}` | theme `controlnet.reference_image` |
| `{{control_strength}}` | theme `controlnet.strength` |
| `{{upscale_model}}` | theme `upscale_model` |

## Required custom nodes / models

- `ComfyUI_IPAdapter_plus` (provides `IPAdapter` / `IPAdapterModelLoader`)
- `ComfyUI_ControlNet_Union` (provides the Union `ControlNetLoader` +
  `ControlNetApplyAdvanced`; note the `type` input selects depth/canny/pose/tile)
- `CLIP-ViT-H-14-laion2b-safetensors` (IP-Adapter dependency)
- `ip-adapter-plus_sdxl_vit-h.safetensors`
- `controlnet-union-sdxl-1.0.safetensors`
- `4x-UltraSharp.pth`

## Adapt before use

Node class names and input keys vary between custom-node versions. Adjust the
graph to match your installed node pack, then point `COMFY_API_WORKFLOW` at this
file. Remove the Upscale node (14/15 → 12) if you don't want upscaling.
