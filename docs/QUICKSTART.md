# First 15 minutes with Sublayer

Sublayer is a **Windows desktop tool for composing and previewing OpenUSD scenes with a visual node graph**. This guide gives you a small, reversible first experiment before trying Blender or Substance Painter round-tripping.

## Before you start

- Windows PC and a GPU compatible with your chosen Hydra renderer.
- A USD file you have permission to use (`.usd`, `.usda`, or `.usdc`).
- Download the latest available Windows installer from [GitHub Releases](https://github.com/ruchirlives/Sublayer/releases). **Check that a Windows installer asset is attached before following these steps.** If there is no installer for your version, use [Build from source](../README.md#option-2-building-from-source-windows).
- The first-run runtime wizard installs OpenUSD/Storm separately. NVIDIA Omniverse RTX is **optional** and uses a separate runtime. Follow the wizard; some downloads can be sizeable.

## Your first scene

1. Install and launch Sublayer, then complete **Sublayer Runtime Setup** if prompted.
2. Create a **Source** node and browse to your USD scene.
3. Add a **Transform** node. Connect **Source → Transform**.
4. Add an **Output** node and connect **Transform → Output**.
5. Select or pin the connected output for preview, and choose **Storm** in the Hydra renderer selector.
6. Change a transform parameter and check the scene preview. If the stage is black or blank, try the **Auto/Solid** material-preview modes and check missing texture diagnostics.
7. Save your project as a `.sublayer` file. It stores graph settings and links to your original assets; it does **not** copy or overwrite the source USD.

**Success looks like:** a displayed USD stage, a working three-node graph, a visible transform change and a saved project that reopens.

## Next: make it useful

- **Scene layout:** Add Instancer or Scatter nodes to place reusable assets.
- **Look development:** Try MaterialX and Material Bind nodes, with Storm for quick preview and Aurora for higher-quality results.
- **Blender workflow:** Read the README's Blender round-trip notes before publishing adjustments back to USD. Native files remain the editing source.
- **Substance Painter workflow:** Install the Sublayer bridge through the supported integration workflow. Substance Painter is not included.
- **MCP:** The optional local MCP server enables headless USD inspection and rendering.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| No viewport image | OpenUSD runtime installed; Storm delegate detected; valid USD path |
| Geometry displays but looks wrong | Missing shader assets, missing texture paths, Auto/Solid material preview |
| RTX unavailable | Optional NVIDIA runtime is not part of the installer |
| File import fails | Try a minimal USD file and include the precise error and runtime versions |

If the workflow fails, [open a bug report](https://github.com/ruchirlives/Sublayer/issues/new/choose) with Windows version, GPU, renderer, runtime version, reproduction steps and a screenshot. Please do not upload confidential project assets.

## Scope

Sublayer is evolving. Some workflows are documented as headless-tested but still require desktop acceptance. ZBrush and Marvelous Designer profiles are templates, not proof of a complete integration. See the [README](../README.md) for precise limitations.
