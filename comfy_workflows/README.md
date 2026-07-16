# ComfyUI integration (built in code — no workflow files)

This directory previously held a hand-written workflow JSON
(`presentation_sdxl.json`). That template has been **removed**: the
`comfy_api` backend now **constructs the ComfyUI API graph in code**
(`comfy_client.build_consistency_workflow`) from whatever models the
instance reports via `/object_info`.

## Why there is no template anymore

A static JSON pins specific model filenames (`ip-adapter-plus_sdxl_vit-h…`,
`controlnet-union-sdxl-1.0…`, `4x-UltraSharp.pth`) and a fixed node graph. If
your install used different names — or lacked a stage entirely — submission
failed or silently dropped consistency. Building the graph in code instead
means:

- **Auto-discovery** of every loader type: `CheckpointLoaderSimple`,
  `VAELoader`, `LoraLoader`, `ControlNetLoader`, `IPAdapterModelLoader`,
  `UpscaleModelLoader`, `CLIPVisionLoader`, plus `KSampler` samplers/schedulers.
- **Conditional nodes** — IP-Adapter, ControlNet and Upscale are only inserted
  when their models are discovered, so the same code drives a bare T2I graph or
  a full style+composition+upscale pipeline.
- **Auto style reference** — if the theme sets no `style_reference_image`, one
  anchor image is generated per deck and fed to IP-Adapter, so consistency
  needs no supplied asset.
- **Per-role presets** — `target` (`content` / `background` / `icon`) tunes
  ControlNet strength and post-processing.

## Required custom nodes (only if you want that stage)

- `ComfyUI_IPAdapter_plus` (provides `IPAdapter` / `IPAdapterModelLoader` /
  `CLIPVisionLoader`) → style consistency.
- `ComfyUI_ControlNet_Union` (provides `ControlNetLoader` /
  `ControlNetApplyAdvanced`) → composition control.
- Any ESRGAN upscaler → `UpscaleModelLoader` / `ImageUpscaleWithModel`.

Each is optional; the graph degrades gracefully when a stage is missing.
