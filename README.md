# Sublayer

Sublayer is a Tauri + React node-graph USD composition and viewport desktop application with an OpenUSD / Hydra Storm worker and optional NVIDIA Omniverse RTX (`ovrtx`) path-traced rendering.

## Prerequisites

- **Node.js & npm** (for the React + TypeScript + Vite frontend)
- **Rust & Cargo** (`edition = "2024"`, Tauri v2 desktop backend, Windows WebView2 & MSVC build tools)
- **Python + OpenUSD** (for the USD graph, MaterialX, hair simulation, and Hydra Storm viewport worker)
- **Optional: NVIDIA Omniverse RTX (`ovrtx` / `ovstage`)** (for real-time and path-traced RTX viewport rendering)

## Installation

### 1. Install Frontend Dependencies

```powershell
npm install
```

### 2. Configure OpenUSD / Hydra Python Environment

The Rust backend launches the primary USD worker using:

- `ORCHFLOW_PYTHON` — Path to the Python executable (defaults to `python` on `PATH`)
- `ORCHFLOW_USD_ROOT` — Path to your OpenUSD installation root (adds `lib/python` to `PYTHONPATH`, `bin`/`lib` to `PATH`, and defaults `PXR_MTLX_STDLIB_SEARCH_PATHS` to `libraries`)

Example (PowerShell):

```powershell
$env:ORCHFLOW_PYTHON = "path\to\python.exe"
$env:ORCHFLOW_USD_ROOT = "I:\AI\OpenUSD-runtime\25.08"
```

> **Note:** For `.abc` (Alembic) source stages when `usdAbc` is not in the primary OpenUSD build, `worker/usd_graph.py` can invoke an imaging runtime via `ORCHFLOW_IMAGING_PYTHON` or `.venv-imaging\Scripts\python.exe`.

### 3. Optional: Configure NVIDIA Omniverse RTX (`ovrtx`)

Omniverse RTX runs in a separate isolated Python environment (`.venv-ovrtx`) to avoid ABI or symbol collisions with upstream OpenUSD / Hydra.

1. Create `.venv-ovrtx` at the repository root (or point `ORCHFLOW_OVRTX_PYTHON` to an existing Python executable):

   ```powershell
   python -m venv .venv-ovrtx
   .\.venv-ovrtx\Scripts\Activate.ps1
   pip install numpy pillow
   ```

2. Install the `ovrtx`, `ovstage`, and OpenUSD (`pxr`) packages into `.venv-ovrtx` according to your Omniverse RTX SDK distribution.
3. Point `ORCHFLOW_OVRTX_ROOT` to the external Omniverse RTX runtime directory containing `runtime.json` (defaults to `I:/AI/OrchFlow-renderers/ovrtx`).

> See `LICENSE-EXCEPTION.md` and the header in `worker/ovrtx_worker.py` regarding GPL-3.0-or-later linking exception terms for the proprietary `ovrtx` and `ovstage` libraries.

## Building & Launching

### Build and Launch (Recommended)

Runs the frontend build, compiles the desktop binary (`sublayer-updated.exe`) and MCP binary (`orchflow-mcp.exe`) to `I:/AI/OrchFlow-build/debug/`, and launches the app:

```powershell
.\scripts\build-and-launch.ps1
```

### Build Only

```powershell
.\scripts\build.ps1
```

### Development / Tests

- **Frontend dev server:** `npm run dev`
- **Frontend build:** `npm run build`
- **Frontend tests:** `npm test`
- **Rust tests:** `cargo test --manifest-path src-tauri/Cargo.toml`
