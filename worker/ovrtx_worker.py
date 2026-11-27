# SPDX-License-Identifier: GPL-3.0-or-later WITH GPL-3.0-linking-exception
#
# Additional Permission under GNU GPL version 3 section 7:
# As a special exception, the copyright holders of Sublayer give you permission
# to combine and run this file with the external, separately installed NVIDIA
# Omniverse RTX runtime libraries (`ovrtx` and `ovstage`), and to distribute
# Sublayer under GPL-3.0-or-later without including those proprietary libraries
# in the Corresponding Source (see LICENSE-EXCEPTION.md).
"""Standalone NVIDIA Omniverse RTX (ovrtx) worker process.

Runs in an isolated Python environment (.venv-ovrtx) to avoid ABI or symbol
collisions with upstream OpenUSD / Hydra. Communicates with the Rust backend
via the ORCHFLOW: stdio JSON protocol.
"""
import sys
import faulthandler
faulthandler.enable(file=sys.stderr)

import base64
import copy
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import time
import threading
from pathlib import Path

# Ensure worker folder is in sys.path for local helper imports
_worker_dir = os.path.dirname(os.path.abspath(__file__))
if _worker_dir not in sys.path:
    sys.path.insert(0, _worker_dir)

os.environ.setdefault('ORCHFLOW_SKIP_SDR_CHECK', '1')

try:
    import numpy as np
    import ovrtx
    import ovstage
    from PIL import Image
    from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdLux, UsdRender
    import usd_graph
    usd_graph._INTERACTIVE_MANUAL_SIM = True
    from material_preview import missing_shader_assets, invalid_shader_inputs, preserve_mdl_preview, selective_material_preview
    from ovrtx_runtime import discover_ovrtx_runtime
    import ovrtx_material_delta
    from viewport_bounds import compute_visible_geometry_bounds, compute_selection_bounds, isolate_prim_in_stage, apply_viewport_visibility, raycast_prim_hit, transform_gizmo_translation
except ImportError as exc:
    sys.stderr.write(f"Ovrtx worker import error: {exc}\n")

_PROTOCOL = "ORCHFLOW:"
_TRACE_PHASES = os.environ.get("ORCHFLOW_OVRTX_DEBUG") == "1"
_viewport = None
_phase = ("waiting for request", time.monotonic(), 60)
_RENDER_MODES = {
    "realtime": "RealTimePathTracing",
    "pathTracing": "PathTracing",
    "pathtracing": "PathTracing",
}
_VIEWPORT_PRODUCTS = ("/Render/RenderProduct_Viewport", "/Render/RenderProduct_ViewportOrtho")


_STDOUT_LOCK = threading.Lock()
_RAW_STDOUT = sys.stdout


class _LockedStdout:
    """Thread-safe stdout proxy so worker responses and Win32 pop-up events never interleave mid-line."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    @property
    def buffer(self):
        return self._wrapped.buffer

    def write(self, data):
        with _STDOUT_LOCK:
            res = self._wrapped.write(data)
            return res

    def flush(self):
        with _STDOUT_LOCK:
            return self._wrapped.flush()

    def isatty(self):
        return self._wrapped.isatty()

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


def _write_stdout_bytes(payload: bytes):
    with _STDOUT_LOCK:
        _RAW_STDOUT.flush()
        _RAW_STDOUT.buffer.write(payload)
        _RAW_STDOUT.buffer.flush()


def report_status(phase_name, details=None):
    try:
        payload = {"phase": phase_name}
        if details:
            payload["details"] = details
        _write_stdout_bytes(("ORCHFLOW_STATUS:" + json.dumps(payload) + "\n").encode("utf-8"))
    except Exception:
        pass


def _emit_popup_input(evt):
    try:
        _write_stdout_bytes(("ORCHFLOW_STATUS:" + json.dumps({"popupInput": evt}) + "\n").encode("utf-8"))
    except Exception:
        pass


_POSTFX_DEFAULTS = {
    "tonemapOperator": "acesApproximation",
    "tonemapColorMode": "sRGBLinear",
    "exposureKey": 0.25,
    "whiteScale": 40.2,
    "maxWhiteLuminance": 10.0,
    "whitepoint": [1.0, 1.0, 1.0],
    "dither": 0.0,
    "irayBurnHighlights": 0.7,
    "irayCrushBlacks": 0.5,
    "iraySaturation": 1.0,
    "gradeEnabled": False,
    "gradeBlackPoint": [0.0, 0.0, 0.0],
    "gradeWhitePoint": [1.0, 1.0, 1.0],
    "gradeLift": [0.0, 0.0, 0.0],
    "gradeGain": [1.0, 1.0, 1.0],
    "gradeMultiply": [1.0, 1.0, 1.0],
    "gradeOffset": [0.0, 0.0, 0.0],
    "gradeGamma": [1.0, 1.0, 1.0],
    "gradeContrast": [1.0, 1.0, 1.0],
    "gradeSaturation": [1.0, 1.0, 1.0],
    "autoExposureEnabled": False,
    "autoExposureWhitePointScale": 10.0,
    "autoExposureAdaptationSpeed": 3.5,
    "autoExposureHistogramFilter": "median",
    "autoExposureMinLuminance": 50.0,
    "autoExposureMaxLuminance": 100000.0,
    "bloomEnabled": False,
    "bloomScale": 1.0,
    "bloomCutoff": 0.0,
    "bloomCutoffFuzziness": 0.5,
    "bloomEnergyConservingBlend": False,
    "bloomFStop": 5.0,
    "bloomFocalLength": 35.0,
    "bloomBlades": 5,
    "bloomApertureCircular": False,
    "bloomApertureRotation": 5.0,
    "bloomAnisoFlareWeight": 0.6,
    "bloomHaloFlareWeight": 0.01,
    "bloomIsotropicFlareWeight": 0.4,
    "chromabEnabled": False,
    "chromabStrengthR": -0.055,
    "chromabStrengthG": -0.075,
    "chromabStrengthB": 0.015,
    "chromabUseLanczos": False,
    "vignetteEnabled": False,
    "vignetteStrength": 0.7,
    "vignetteSize": 107.0,
    "filmGrainEnabled": False,
    "filmGrainAmount": 0.05,
    "filmGrainSize": 1.6,
    "sharpness": 0.5,
}


def resolve_graph_postfx(graph):
    """Return merged PostFX settings for the active subgraph (downstream wins), or None."""
    if not isinstance(graph, dict):
        return None
    target = graph.get("targetNodeId")
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not target or not nodes:
        return None
    nmap = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("id")}
    inc = {}
    for e in edges:
        if isinstance(e, dict) and e.get("source") and e.get("target"):
            inc.setdefault(e["target"], []).append(e["source"])
    visited = set()
    order = []

    def dfs(nid):
        if nid in visited or nid not in nmap:
            return
        visited.add(nid)
        for src in inc.get(nid, ()):
            dfs(src)
        order.append(nid)

    dfs(target)
    merged = None
    for nid in order:
        n = nmap[nid]
        op = n.get("operation") or (n.get("data") or {}).get("operation")
        if op == "postfx":
            raw = n.get("postFx")
            if raw is None and isinstance(n.get("data"), dict):
                raw = n["data"].get("postFx")
            if merged is None:
                merged = dict(_POSTFX_DEFAULTS)
            if isinstance(raw, dict):
                merged.update(raw)
    return merged


def _postfx_vec3(val, default):
    if isinstance(val, (list, tuple)) and len(val) == 3:
        return (float(val[0]), float(val[1]), float(val[2]))
    return (float(default[0]), float(default[1]), float(default[2]))


def build_postfx_attr_specs(cfg):
    """Return list of (attr_name, kind, value) for omni:rtx:post:* & autoExposure:*."""
    c = dict(_POSTFX_DEFAULTS)
    if isinstance(cfg, dict):
        c.update(cfg)
    cutoff = float(c["bloomCutoff"])
    vignette_on = bool(c["vignetteEnabled"])
    grain_on = bool(c["filmGrainEnabled"])
    return [
        # Tone mapping
        ("omni:rtx:post:tonemap:operator", "token", str(c["tonemapOperator"])),
        ("omni:rtx:post:tonemap:colorMode", "token", str(c["tonemapColorMode"])),
        ("omni:rtx:post:tonemap:exposureKey", "float", float(c["exposureKey"])),
        ("omni:rtx:post:tonemap:whiteScale", "float", float(c["whiteScale"])),
        ("omni:rtx:post:tonemap:maxWhiteLuminance", "float", float(c["maxWhiteLuminance"])),
        ("omni:rtx:post:tonemap:whitepoint", "color3f", _postfx_vec3(c["whitepoint"], (1.0, 1.0, 1.0))),
        ("omni:rtx:post:tonemap:dither", "float", float(c["dither"])),
        ("omni:rtx:post:tonemap:irayReinhard:burnHighlights", "float", float(c["irayBurnHighlights"])),
        ("omni:rtx:post:tonemap:irayReinhard:crushBlacks", "float", float(c["irayCrushBlacks"])),
        ("omni:rtx:post:tonemap:irayReinhard:saturation", "float", float(c["iraySaturation"])),
        # Color grading
        ("omni:rtx:post:grade:enabled", "bool", bool(c["gradeEnabled"])),
        ("omni:rtx:post:grade:blackPoint", "color3f", _postfx_vec3(c["gradeBlackPoint"], (0.0, 0.0, 0.0))),
        ("omni:rtx:post:grade:whitePoint", "color3f", _postfx_vec3(c["gradeWhitePoint"], (1.0, 1.0, 1.0))),
        ("omni:rtx:post:grade:lift", "color3f", _postfx_vec3(c["gradeLift"], (0.0, 0.0, 0.0))),
        ("omni:rtx:post:grade:gain", "color3f", _postfx_vec3(c["gradeGain"], (1.0, 1.0, 1.0))),
        ("omni:rtx:post:grade:multiply", "color3f", _postfx_vec3(c["gradeMultiply"], (1.0, 1.0, 1.0))),
        ("omni:rtx:post:grade:offset", "color3f", _postfx_vec3(c["gradeOffset"], (0.0, 0.0, 0.0))),
        ("omni:rtx:post:grade:gamma", "color3f", _postfx_vec3(c["gradeGamma"], (1.0, 1.0, 1.0))),
        ("omni:rtx:post:grade:contrast", "color3f", _postfx_vec3(c["gradeContrast"], (1.0, 1.0, 1.0))),
        ("omni:rtx:post:grade:saturation", "color3f", _postfx_vec3(c["gradeSaturation"], (1.0, 1.0, 1.0))),
        # Auto exposure
        ("omni:rtx:autoExposure:enabled", "bool", bool(c["autoExposureEnabled"])),
        ("omni:rtx:autoExposure:whitePointScale", "float", float(c["autoExposureWhitePointScale"])),
        ("omni:rtx:autoExposure:adaptationSpeed", "float", float(c["autoExposureAdaptationSpeed"])),
        ("omni:rtx:autoExposure:histogramFilter", "token", str(c["autoExposureHistogramFilter"])),
        ("omni:rtx:autoExposure:histogramClamping:minLuminance", "float", float(c["autoExposureMinLuminance"])),
        ("omni:rtx:autoExposure:histogramClamping:maxLuminance", "float", float(c["autoExposureMaxLuminance"])),
        # Bloom & lens flares
        ("omni:rtx:post:bloom:enabled", "bool", bool(c["bloomEnabled"])),
        ("omni:rtx:post:bloom:scale", "float", float(c["bloomScale"])),
        ("omni:rtx:post:bloom:cutoff", "float3", (cutoff, cutoff, cutoff)),
        ("omni:rtx:post:bloom:cutoffFuzziness", "float", float(c["bloomCutoffFuzziness"])),
        ("omni:rtx:post:bloom:energyConservingBlend", "bool", bool(c["bloomEnergyConservingBlend"])),
        ("omni:rtx:post:bloom:fStop", "float", float(c["bloomFStop"])),
        ("omni:rtx:post:bloom:focalLength", "float", float(c["bloomFocalLength"])),
        ("omni:rtx:post:bloom:aperture:blades", "int", int(c["bloomBlades"])),
        ("omni:rtx:post:bloom:apertureShapeCircular", "bool", bool(c["bloomApertureCircular"])),
        ("omni:rtx:post:bloom:aperture:rotation", "float", float(c["bloomApertureRotation"])),
        ("omni:rtx:post:lensFlares:anisoFlareWeight", "float", float(c["bloomAnisoFlareWeight"])),
        ("omni:rtx:post:lensFlares:haloFlareWeight", "float", float(c["bloomHaloFlareWeight"])),
        ("omni:rtx:post:lensFlares:isotropicFlareWeight", "float", float(c["bloomIsotropicFlareWeight"])),
        # Chromatic aberration
        ("omni:rtx:post:chromab:enabled", "bool", bool(c["chromabEnabled"])),
        ("omni:rtx:post:chromab:strengthR", "float", float(c["chromabStrengthR"])),
        ("omni:rtx:post:chromab:strengthG", "float", float(c["chromabStrengthG"])),
        ("omni:rtx:post:chromab:strengthB", "float", float(c["chromabStrengthB"])),
        ("omni:rtx:post:chromab:useLanczosSampler", "bool", bool(c["chromabUseLanczos"])),
        # Vignette & film grain (tvNoise)
        ("omni:rtx:post:tvNoise:enabled", "bool", bool(vignette_on or grain_on)),
        ("omni:rtx:post:tvNoise:vignetting:enabled", "bool", vignette_on),
        ("omni:rtx:post:tvNoise:vignetting:strength", "float", float(c["vignetteStrength"])),
        ("omni:rtx:post:tvNoise:vignetting:size", "float", float(c["vignetteSize"])),
        ("omni:rtx:post:tvNoise:filmGrain:enabled", "bool", grain_on),
        ("omni:rtx:post:tvNoise:filmGrain:amount", "float", float(c["filmGrainAmount"])),
        ("omni:rtx:post:tvNoise:filmGrain:size", "float", float(c["filmGrainSize"])),
        # Sharpening
        ("omni:rtx:post:aa:sharpness", "float", float(c["sharpness"])),
    ]


def apply_postfx_to_render_product(product_prim, cfg):
    """Author PostFX attributes onto a UsdRender.Product prim."""
    if cfg is None:
        return
    from pxr import Gf, Sdf
    type_map = {
        "token": Sdf.ValueTypeNames.Token,
        "bool": Sdf.ValueTypeNames.Bool,
        "int": Sdf.ValueTypeNames.Int,
        "float": Sdf.ValueTypeNames.Float,
        "float3": Sdf.ValueTypeNames.Float3,
        "color3f": Sdf.ValueTypeNames.Color3f,
    }
    for attr_name, kind, val in build_postfx_attr_specs(cfg):
        sdf_type = type_map[kind]
        if kind in ("float3", "color3f"):
            val = Gf.Vec3f(val[0], val[1], val[2])
        product_prim.CreateAttribute(attr_name, sdf_type, custom=False).Set(val)


def phase(name, timeout=60, report=True):
    global _phase
    _phase = (name, time.monotonic(), timeout)
    if _TRACE_PHASES:
        print("OVRTX phase: " + name, file=sys.stderr, flush=True)
    if report:
        report_status(f"NVIDIA RTX: {name}")


def request_watchdog(done):
    while not done.wait(1):
        name, started, timeout = _phase
        if time.monotonic() - started > timeout:
            message = f"NVIDIA RTX stalled during {name} for {timeout}s. Its isolated worker was stopped; switch renderer or retry."
            print(message, file=sys.stderr, flush=True)
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            _write_stdout_bytes((_PROTOCOL + json.dumps({"error": message}) + "\n").encode("utf-8"))
            os._exit(124)


def reuse_presented_image(frame, request):
    image_id = frame.get("viewportImageId")
    if image_id and image_id == request.get("presentedViewportImageId"):
        return {
            **{key: value for key, value in frame.items() if key not in ("_binary_bytes", "image")},
            "reusedViewportImage": True,
        }
    return frame


def export_preview_snapshot(stage):
    runtime = os.environ.get("ORCHFLOW_OVRTX_ROOT", "I:/AI/OrchFlow-renderers/ovrtx")
    scratch = os.environ.get("ORCHFLOW_OVRTX_SCRATCH") or os.path.join(runtime, "scratch")
    path = None
    try:
        os.makedirs(scratch, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="orchflow_ovrtx_", suffix=".usdc", dir=scratch)
        os.close(fd)
        if not stage.Export(path) or os.path.getsize(path) == 0:
            raise OSError("OpenUSD did not write a preview snapshot")
        return path
    except (OSError, Tf.ErrorException) as error:
        if path is not None:
            try:
                os.remove(path)
            except OSError:
                pass
        print(f"NVIDIA preview export failed in {scratch}: {error}", file=sys.stderr, flush=True)
        try:
            free = f" ({shutil.disk_usage(scratch).free / 1024**3:.2f} GiB free)"
        except OSError:
            free = ""
        raise RuntimeError(f"Could not write NVIDIA preview snapshot in {scratch}{free}. Check available space and folder access.") from None


_vram_cache = {"ts": 0.0, "used": None, "total": None}
_nvml_state = {"init": False, "lib": None, "handle": None}


def _init_nvml():
    if _nvml_state["init"]:
        return _nvml_state["lib"], _nvml_state["handle"]
    _nvml_state["init"] = True
    try:
        import ctypes

        class _NvmlMemory(ctypes.Structure):
            _fields_ = [
                ("total", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong),
            ]

        lib = ctypes.WinDLL("nvml.dll") if os.name == "nt" else ctypes.CDLL("libnvidia-ml.so.1")
        if lib.nvmlInit_v2() != 0:
            return None, None
        handle = ctypes.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
            return None, None
        lib._NvmlMemory = _NvmlMemory
        _nvml_state["lib"] = lib
        _nvml_state["handle"] = handle
    except Exception:
        pass
    return _nvml_state["lib"], _nvml_state["handle"]


def _query_gpu_vram(interactive=False):
    if interactive and _vram_cache["used"] is not None:
        return _vram_cache["used"], _vram_cache["total"]
    now = time.time()
    if now - _vram_cache["ts"] < 2.5:
        return _vram_cache["used"], _vram_cache["total"]
    lib, handle = _init_nvml()
    if lib is not None and handle is not None:
        try:
            import ctypes
            mem = lib._NvmlMemory()
            if lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) == 0:
                _vram_cache["used"] = int(mem.used // (1024 * 1024))
                _vram_cache["total"] = int(mem.total // (1024 * 1024))
        except Exception:
            pass
    _vram_cache["ts"] = now
    return _vram_cache["used"], _vram_cache["total"]


class OvrtxViewport:
    def __init__(self):
        self.renderer = None
        self.stage = None
        self.ordinal = 0
        self.last_graph_hash = None
        self.last_static_scene_key = None
        self.render_graphs = {}
        self.render_graph_hashes = {}
        self.graph_stages = []
        self.graph_stats = None
        self.material_diagnostics = []
        self.material_fallbacks = []
        self.material_keepalive = []
        self.camera_fit_key = None
        self.last_camera_key = None
        self.last_width = 0
        self.last_height = 0
        self.temp_usd_path = None
        self.temp_usd_cache = {}
        self.last_base_key = None
        self.stage_pool = {}
        self.current_usd_stage = None
        self.last_outline_key = None
        self._outline_cache = None
        self.active_selection = []
        self.last_requested_selection = []
        self._material_users_cache = None
        # Incremental material updates: base_layer is what ovstage currently holds as its
        # stable population; the overlay is the single replaceable material delta reference.
        self.base_layer = None
        self.mx_rule_selections = {}
        self.last_suffix = None
        self.last_graph = None
        self.live_transforms = {}
        self._transform_overlay_layer = None
        self.pending_groom_graph = None
        self.pending_groom_frame = None
        self.last_brush_upload = None
        self.last_brush_frame = None
        self.prepared_hair_node_id = None
        self.live_groom_modified = False
        self.overlay_handle = None
        self._pose_overlay_handle = None
        self._pose_overlay_seq = 0
        self.overlay_path = None
        self._pin_mat_overlay_active = False
        self._pre_pin_overlay_path = None
        self._pin_overlay_path = None
        self._cache_mask_overlay_active = False
        self._pre_mask_overlay_path = None
        self._cache_mask_overlay_path = None
        self._last_orbit_pivot_seq = 0
        self._pending_camera_sync = None
        self._last_interactive = False
        self.camera_anchor_translation = None
        self.last_update_kind = "none"
        self._cuda_readback_cache = {}
        self.native_popup = None
        self._native_popup_active = False
        self._pt_stall_count = 0
        self._pt_stall_rebuilt_for = None
        self._ensure_initialized()

    def _ensure_initialized(self):
        if self.renderer is None:
            phase("registering NVIDIA schemas")
            ovrtx.register_schema_paths()
            phase("creating NVIDIA renderer")
            runtime = os.environ.get("ORCHFLOW_OVRTX_ROOT", "I:/AI/OrchFlow-renderers/ovrtx")
            ds_dir = (os.environ.get("ORCHFLOW_OVRTX_DATASTORE_CACHE") or os.path.join(runtime, "datastore_cache")).replace("\\", "/")
            os.makedirs(ds_dir, exist_ok=True)
            config = ovrtx.RendererConfig(
                selection_outline_enabled=True,
                selection_outline_width=2,
                datastore_cache=f"local://{ds_dir}",
            )
            self.renderer = ovrtx.Renderer(config)
            try:
                self.renderer.set_selection_group_styles({
                    1: ovrtx.SelectionGroupStyle(
                        outline_color=(1.0, 0.55, 0.05, 1.0),
                        fill_color=(1.0, 0.55, 0.05, 0.15),
                    ),
                    2: ovrtx.SelectionGroupStyle(
                        outline_color=(0.15, 0.85, 0.95, 1.0),
                        fill_color=(0.15, 0.85, 0.95, 0.18),
                    ),
                })
            except Exception as e:
                sys.stderr.write(f"Ovrtx selection group style init: {e}\n")
            phase("creating runtime stage")
            self.stage = ovstage.Stage("orchflow_ovrtx_" + uuid.uuid4().hex)
            phase("attaching runtime stage")
            self.renderer.attach_ovstage(self.stage)
            phase("renderer attached", report=False)

    def _flush_stage_pool(self):
        for entry in self.stage_pool.values():
            fp = entry.get("delta_path")
            if fp and fp != self.overlay_path and fp != self.temp_usd_path and os.path.isfile(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass
        self.stage_pool.clear()
        self.temp_usd_cache.clear()
        self.last_graph = None

    def clear_scene(self):
        self._flush_stage_pool()
        self.pending_groom_graph = None
        self.pending_groom_frame = None
        self.last_brush_upload = None
        self.last_brush_frame = None
        self.prepared_hair_node_id = None
        self.live_groom_modified = False
        if self.renderer is not None:
            try:
                self.renderer.detach_ovstage()
            except Exception:
                pass
        if self.stage is not None:
            try:
                self.stage.destroy()
            except Exception:
                pass
            self.stage = None
        if self.temp_usd_path and os.path.isfile(self.temp_usd_path):
            try:
                os.remove(self.temp_usd_path)
            except Exception:
                pass
        if self.overlay_path and os.path.isfile(self.overlay_path):
            try:
                os.remove(self.overlay_path)
            except Exception:
                pass
        self.temp_usd_path = None
        self.overlay_path = None
        self.overlay_handle = None
        self._pose_overlay_handle = None
        self._pose_overlay_seq = 0
        self.base_layer = None
        self.mx_rule_selections = {}
        self.current_usd_stage = None
        self.last_outline_key = None
        self._outline_cache = None
        self.active_selection = []
        self.last_requested_selection = []
        self._material_users_cache = None
        self.last_graph_hash = None
        self.last_static_scene_key = None
        self.last_base_key = None
        self.last_camera_key = None
        self.ordinal = 0
        if self.renderer is not None:
            self.stage = ovstage.Stage("orchflow_ovrtx_" + uuid.uuid4().hex)
            self.renderer.attach_ovstage(self.stage)
            try:
                self.renderer.reset(0.0)
            except Exception:
                pass

    def scene_children(self, request):
        graph = request.get("graph")
        graph = json.loads(graph) if isinstance(graph, str) else graph
        key = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest() if graph else request.get("path")
        if not key:
            raise ValueError("Choose a stage to inspect")
        reload_flag = bool(request.get("reload"))
        if not reload_flag and self.current_usd_stage is not None and self.last_outline_key == key:
            return usd_graph.query_scene_children(self.current_usd_stage, request)
        if not reload_flag and self.current_usd_stage is not None and self.pending_groom_graph and self.pending_groom_graph[0] == graph:
            return usd_graph.query_scene_children(self.current_usd_stage, request)
        if reload_flag or self._outline_cache is None or self._outline_cache[0] != key:
            if graph:
                prev_skip = getattr(usd_graph, "_SKIP_SIMULATION", False)
                usd_graph._SKIP_SIMULATION = True
                try:
                    stage, alive, _ = usd_graph.compile_graph(graph, reload_flag)
                finally:
                    usd_graph._SKIP_SIMULATION = prev_skip
            else:
                stage = Usd.Stage.Open(key)
                alive = []
                if stage and reload_flag:
                    stage.Reload()
            if not stage:
                raise ValueError("Unable to open scene hierarchy")
            self._outline_cache = (key, stage, alive)
        return usd_graph.query_scene_children(self._outline_cache[1], request)

    def close(self):
        self._flush_stage_pool()
        if self.stage is not None:
            try:
                self.stage.destroy()
            except Exception:
                pass
            self.stage = None
        if self.renderer is not None:
            try:
                self.renderer.destroy()
            except Exception:
                pass
            self.renderer = None
        if self.temp_usd_path and os.path.isfile(self.temp_usd_path):
            try:
                os.remove(self.temp_usd_path)
            except Exception:
                pass
            self.temp_usd_path = None
        self.temp_usd_cache = {}
        self.last_base_key = None
        self.stage_pool = {}
        if self.overlay_path and os.path.isfile(self.overlay_path):
            try:
                os.remove(self.overlay_path)
            except Exception:
                pass
            self.overlay_path = None
        self.overlay_handle = None
        self._pose_overlay_handle = None
        self._pose_overlay_seq = 0
        self.base_layer = None
        self.mx_rule_selections = {}
    def _compute_camera_params(self, request, width, height):
        transform_target, cur_trans = transform_gizmo_translation(
            self._current_render_graph, request.get("transformGizmoNodeId")
        )
        fit_key = (request.get("cameraSession"), request.get("frameFit", 0))
        target_prim = request.get("frameTarget")
        if self.camera_fit_key != fit_key or not hasattr(self, "camera_anchor"):
            self.camera_anchor_translation = cur_trans
            self.camera_anchor_target = transform_target
            anchor = request.get("cameraAnchor")
            if target_prim and self.current_usd_stage:
                sel_range = compute_selection_bounds(self.current_usd_stage, [target_prim], float(request.get("frame", 0)))
                if sel_range is not None and not sel_range.IsEmpty():
                    self.camera_anchor = (sel_range.GetMidpoint(), max(sel_range.GetSize().GetLength() * 0.5, 0.001))
                else:
                    self.camera_anchor = self.scene_camera_anchor
            else:
                self.camera_anchor = self.scene_camera_anchor
            if isinstance(anchor, dict) and fit_key[0] and (anchor.get("session"), anchor.get("fit")) == fit_key and not target_prim:
                center, radius = anchor.get("center"), anchor.get("radius")
                if (isinstance(center, list) and len(center) == 3 and
                    all(isinstance(v, (int, float)) and math.isfinite(v) for v in center) and
                    isinstance(radius, (int, float)) and math.isfinite(radius) and radius > 0):
                    self.camera_anchor = (Gf.Vec3d(*center), radius)
                    translation = anchor.get("translation")
                    if (
                        transform_target is not None
                        and anchor.get("transformTarget") == transform_target
                        and isinstance(translation, list)
                        and len(translation) == 3
                        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in translation)
                    ):
                        self.camera_anchor_translation = translation
            self.camera_fit_key = fit_key
        projection = str(request.get("projection") or "perspective")
        if projection not in ("perspective", "orthographic"):
            raise ValueError("Camera projection must be perspective or orthographic")
        is_ortho = projection == "orthographic"
        yaw = math.radians(float(request.get("yaw", 35)))
        pitch = math.radians(max(-90.0, min(90.0, float(request.get("pitch", 20)))))
        up_axis = str(request.get("upAxis", "Y")).upper()
        if up_axis == "Z":
            forward = Gf.Vec3d(math.sin(yaw)*math.cos(pitch), -math.cos(yaw)*math.cos(pitch), math.sin(pitch))
            right = Gf.Vec3d(math.cos(yaw), math.sin(yaw), 0.0)
        else:
            forward = Gf.Vec3d(math.sin(yaw)*math.cos(pitch), math.sin(pitch), math.cos(yaw)*math.cos(pitch))
            right = Gf.Vec3d(math.cos(yaw), 0.0, -math.sin(yaw))
        camera_up = Gf.Cross(forward, right).GetNormalized()
        req_pan_x = float(request.get("panX", 0))
        req_pan_y = float(request.get("panY", 0))
        req_zoom = max(0.001, float(request.get("zoom", 1)))
        aspect = float(width) / float(max(height, 1))
        fit_half_fov = math.radians(22.5)
        sin_k = math.sin(math.atan(math.tan(fit_half_fov) * min(aspect, 1.0)))
        orbit_seq = request.get("orbitPivotSeq")
        if self.current_usd_stage and hasattr(self, "camera_anchor"):
            old_center, old_radius = Gf.Vec3d(self.camera_anchor[0]), self.camera_anchor[1]
            new_center = None
            if orbit_seq and orbit_seq != self._last_orbit_pivot_seq:
                self._last_orbit_pivot_seq = orbit_seq
                orbit_target = request.get("orbitPivotTarget")
                orbit_point = request.get("orbitPivotPoint")
                if (
                    isinstance(orbit_point, (list, tuple))
                    and len(orbit_point) == 3
                    and all(isinstance(v, (int, float)) and math.isfinite(v) for v in orbit_point)
                ):
                    new_center = Gf.Vec3d(float(orbit_point[0]), float(orbit_point[1]), float(orbit_point[2]))
                elif orbit_target:
                    sel_range = compute_selection_bounds(self.current_usd_stage, [orbit_target], float(request.get("frame", 0)))
                    if sel_range is not None and not sel_range.IsEmpty():
                        new_center = sel_range.GetMidpoint()
                if new_center is not None:
                    self.camera_anchor_translation = cur_trans
                    self.camera_anchor_target = transform_target
            elif transform_target != self.camera_anchor_target:
                self.camera_anchor_translation = cur_trans
                self.camera_anchor_target = transform_target
            elif cur_trans is not None and cur_trans != self.camera_anchor_translation:
                new_center = old_center + (Gf.Vec3d(*cur_trans) - Gf.Vec3d(*self.camera_anchor_translation))
                self.camera_anchor_translation = cur_trans
            if new_center is not None:
                old_pivot = old_center + right * req_pan_x * old_radius + camera_up * req_pan_y * old_radius
                if is_ortho:
                    delta = old_pivot - new_center
                    req_pan_x = float(Gf.Dot(delta, right)) / old_radius
                    req_pan_y = float(Gf.Dot(delta, camera_up)) / old_radius
                else:
                    old_dist = old_radius / sin_k * 1.15 * req_zoom
                    old_eye = old_pivot + forward * old_dist
                    delta = old_eye - new_center
                    new_dist = max(1e-4, float(Gf.Dot(delta, forward)))
                    req_pan_x = float(Gf.Dot(delta, right)) / old_radius
                    req_pan_y = float(Gf.Dot(delta, camera_up)) / old_radius
                    req_zoom = new_dist / (old_radius / sin_k * 1.15)
                self.camera_anchor = (new_center, old_radius)
                self._pending_camera_sync = {"panX": req_pan_x, "panY": req_pan_y, "zoom": req_zoom}
        center, radius = Gf.Vec3d(self.camera_anchor[0]), self.camera_anchor[1]
        center += right * req_pan_x * radius + camera_up * req_pan_y * radius
        fov = max(5.0, min(170.0, float(request.get("fov") or 45.0)))
        half_fov = math.radians(fov * 0.5)
        dof_enabled = bool(request.get("dofEnabled", False)) and not is_ortho
        focus_distance = max(0.01, float(request.get("focusDistance") or 5.0))
        f_stop = max(0.1, float(request.get("fStop") or 5.6))
        distance = radius / sin_k * 1.15 * req_zoom
        ortho_half_height = max(1e-5, distance * math.tan(fit_half_fov))
        ortho_half_width = ortho_half_height * aspect
        scene_radius = self.scene_camera_anchor[1] if hasattr(self, "scene_camera_anchor") else radius
        clip_radius = max(radius, scene_radius)
        trans_reach = Gf.Vec3d(*cur_trans).GetLength() if cur_trans else 0.0
        eye_distance = max(distance, clip_radius * 4.0 + trans_reach * 2.0, 1.0) if is_ortho else distance
        eye = center + forward * eye_distance
        self.last_camera_basis = {
            "origin": (float(eye[0]), float(eye[1]), float(eye[2])),
            "forward": (float(-forward[0]), float(-forward[1]), float(-forward[2])),
            "right": (float(right[0]), float(right[1]), float(right[2])),
            "up": (float(camera_up[0]), float(camera_up[1]), float(camera_up[2])),
            "tan_half_fov": float(math.tan(half_fov)),
            "aspect": aspect,
            "orthographic": is_ortho,
            "ortho_half_height": float(ortho_half_height),
        }
        matrix = Gf.Matrix4d().SetLookAt(eye, center, camera_up).GetInverse()
        self._last_camera_transform = usd_graph.decompose_camera_transform(
            matrix, self.current_usd_stage, Usd.TimeCode(float(request.get("frame", 0)))
        )
        persp_h_aperture = 36.0 * aspect
        persp_v_aperture = 36.0
        focal_length = 36.0 / (2 * math.tan(half_fov))
        ortho_h_aperture = 20.0 * ortho_half_width
        ortho_v_aperture = 20.0 * ortho_half_height
        horizontal_aperture = ortho_h_aperture if is_ortho else persp_h_aperture
        vertical_aperture = ortho_v_aperture if is_ortho else persp_v_aperture
        effective_f_stop = f_stop if dof_enabled else 0.0
        near_clip = max(1e-4, min(clip_radius * 0.001, eye_distance * 0.001))
        far_clip = max(eye_distance * 100.0, clip_radius * 100.0, trans_reach * 10.0, near_clip * 1000.0)
        return {
            "projection": projection,
            "is_ortho": is_ortho,
            "matrix": matrix,
            "horizontal_aperture": horizontal_aperture,
            "vertical_aperture": vertical_aperture,
            "persp_horizontal_aperture": persp_h_aperture,
            "persp_vertical_aperture": persp_v_aperture,
            "ortho_horizontal_aperture": ortho_h_aperture,
            "ortho_vertical_aperture": ortho_v_aperture,
            "focal_length": focal_length,
            "focus_distance": focus_distance,
            "f_stop": effective_f_stop,
            "dof_enabled": dof_enabled,
            "clipping_range": (near_clip, far_clip),
        }

    def _update_camera(self, stage, request, width, height):
        cam_params = self._compute_camera_params(request, width, height)
        camera = UsdGeom.Camera.Define(stage, "/Render/Camera")
        camera.CreateProjectionAttr().Set(UsdGeom.Tokens.perspective)
        camera.MakeMatrixXform().Set(cam_params["matrix"])
        camera.CreateHorizontalApertureAttr().Set(cam_params["persp_horizontal_aperture"])
        camera.CreateVerticalApertureAttr().Set(cam_params["persp_vertical_aperture"])
        camera.CreateFocalLengthAttr().Set(cam_params["focal_length"])
        camera.CreateFocusDistanceAttr().Set(cam_params["focus_distance"])
        camera.CreateFStopAttr().Set(cam_params["f_stop"])
        camera.CreateClippingRangeAttr().Set(cam_params["clipping_range"])

        ortho_cam = UsdGeom.Camera.Define(stage, "/Render/OrthoCamera")
        ortho_cam.CreateProjectionAttr().Set(UsdGeom.Tokens.orthographic)
        ortho_cam.MakeMatrixXform().Set(cam_params["matrix"])
        ortho_cam.CreateHorizontalApertureAttr().Set(cam_params["ortho_horizontal_aperture"])
        ortho_cam.CreateVerticalApertureAttr().Set(cam_params["ortho_vertical_aperture"])
        ortho_cam.CreateFocalLengthAttr().Set(cam_params["focal_length"])
        ortho_cam.CreateFocusDistanceAttr().Set(cam_params["focus_distance"])
        ortho_cam.CreateFStopAttr().Set(0.0)
        ortho_cam.CreateClippingRangeAttr().Set(cam_params["clipping_range"])

        product_prim = stage.GetPrimAtPath("/Render/RenderProduct_Viewport")
        if product_prim and product_prim.IsValid():
            product_prim.CreateAttribute("omni:rtx:post:dof:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(cam_params["dof_enabled"])
            product_prim.CreateAttribute("omni:rtx:post:dof:overrideEnabled", Sdf.ValueTypeNames.Bool, custom=False).Set(cam_params["dof_enabled"])
        ortho_product = stage.GetPrimAtPath("/Render/RenderProduct_ViewportOrtho")
        if ortho_product and ortho_product.IsValid():
            ortho_product.CreateAttribute("omni:rtx:post:dof:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(False)
            ortho_product.CreateAttribute("omni:rtx:post:dof:overrideEnabled", Sdf.ValueTypeNames.Bool, custom=False).Set(False)

    def _read_pick_hit(self, frame):
        render_vars = frame.render_vars
        if "ovrtx_pick_hit" not in render_vars:
            sys.stderr.write(f"Ovrtx pick: no ovrtx_pick_hit render var (available: {list(render_vars.keys())})\n")
            return None
        try:
            with render_vars["ovrtx_pick_hit"].map(device=ovrtx.Device.CPU) as mapped:
                ids = np.array(np.from_dlpack(mapped["primPath"]), copy=True).ravel()
        except Exception as e:
            sys.stderr.write(f"Ovrtx read pick hit error: {e}\n")
            return None
        for hit_id in ids:
            if int(hit_id):
                try:
                    path = self.renderer.resolve_prim_path_id(int(hit_id))
                except Exception as e:
                    sys.stderr.write(f"Ovrtx resolve pick path error: {e}\n")
                    return None
                return self._scene_prim_for_pick(path)
        return None

    def _scene_prim_for_pick(self, path):
        # Map renderer-internal paths (e.g. prototype/proxy children) to the
        # nearest prim that exists on the composed preview stage.
        if not path or not self.current_usd_stage:
            return path or None
        sdf_path = Sdf.Path(str(path))
        while sdf_path and sdf_path != Sdf.Path.absoluteRootPath:
            if self.current_usd_stage.GetPrimAtPath(sdf_path):
                return str(sdf_path)
            sdf_path = sdf_path.GetParentPath()
        return str(path)

    def _apply_material_delta_path(self, new_path):
        """Swap the single material-delta reference on the live ovstage.

        Only the affected material/binding/transform opinions change; geometry already uploaded to the
        renderer is untouched and ovrtx restarts progressive accumulation on its own.
        """
        phase("applying material delta")
        if self.overlay_handle is not None:
            ovstage.population.remove_usd(self.stage, self.overlay_handle)
            self.overlay_handle = None
        self.ordinal += 1
        wrapper = Sdf.Layer.CreateAnonymous("viewport-composed-overlay.usda")
        wrapper.subLayerPaths = ([new_path] if new_path else []) + [self.temp_usd_path]
        ovstage.population.open_usd_from_string(self.stage, wrapper.ExportToString(), ordinal=self.ordinal,
            time=getattr(self, "_population_sample", 0.0) / max(1e-5, self.current_usd_stage.GetTimeCodesPerSecond()))
        self.stage.compute_hierarchy(self.ordinal, self.ordinal,
            model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL)
        self.stage.advance_write_floor(self.ordinal).wait()
        # apply_usd_changes re-syncs /Render/RenderProduct_Viewport from temp_usd_path
        # (which has omni:rtx:pt:samplesPerPixel = 1 and maxBounces = 8); mark both to be re-synced.
        self._pt_spp_at_one = True
        self._pt_max_bounces = 8
        self._pt_stall_count = 0
        self.overlay_path = new_path

    def _write_pt_samples_per_pixel(self, target_spp):
        self.ordinal += 1
        with ovstage.PathDictionary(self.stage) as paths:
            for prod_path in _VIEWPORT_PRODUCTS:
                prod_plist = paths.create_path_list_from_strings([prod_path])
                try:
                    prod_query = self.stage.query_from_path_list(prod_plist)
                    self.stage.write_attribute(
                        prod_query,
                        "omni:rtx:pt:samplesPerPixel",
                        self.ordinal,
                        np.asarray([target_spp], dtype=np.uint32),
                        is_array=False,
                        semantic=0,
                    ).wait()
                finally:
                    paths.destroy_path_list(prod_plist)
        self.stage.compute_hierarchy(
            self.ordinal,
            self.ordinal,
            model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
        )
        self.stage.advance_write_floor(self.ordinal).wait()
        self._pt_spp_at_one = False

    def _recover_stalled_ovstage(self, request, render_mode, target_spp):
        """Rebuild the live ovstage in place from the existing preview snapshot & overlays
        when ovrtx path-tracing accumulation stalls at sample 1 across multiple refinement steps."""
        if not self.temp_usd_path or not os.path.isfile(self.temp_usd_path) or self.renderer is None:
            return False
        try:
            self.renderer.detach_ovstage()
        except Exception:
            pass
        if self.stage is not None:
            try:
                self.stage.destroy()
            except Exception:
                pass
        self.stage = ovstage.Stage("orchflow_ovrtx_" + uuid.uuid4().hex)
        self.renderer.attach_ovstage(self.stage)
        self.overlay_handle = None
        self._pose_overlay_handle = None
        self.ordinal = 1
        tps = self.current_usd_stage.GetTimeCodesPerSecond() if self.current_usd_stage is not None else 24.0
        ovstage.population.open_usd(
            self.stage,
            self.temp_usd_path,
            ordinal=self.ordinal,
            time=float(request.get("frame", 0)) / max(1e-5, tps),
        )
        added_overlay = False
        if self.overlay_path and os.path.isfile(self.overlay_path):
            self.overlay_handle = ovstage.population.add_usd_reference(self.stage, self.overlay_path, "/")
            added_overlay = True
        pose_path = getattr(self, "_pose_overlay_path", None)
        if pose_path and os.path.isfile(pose_path):
            self._pose_overlay_handle = ovstage.population.add_usd_reference(self.stage, pose_path, "/")
            added_overlay = True
        if added_overlay:
            ovstage.population.apply_usd_changes(self.stage, ordinal=self.ordinal)
        self.stage.compute_hierarchy(
            self.ordinal,
            self.ordinal,
            model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
        )
        self.stage.advance_write_floor(self.ordinal).wait()
        self.last_camera_key = None
        self.last_width = 0
        self.last_height = 0
        self.last_requested_selection = []
        self.active_selection = []
        self._pt_spp_at_one = (render_mode == "pathTracing" and target_spp > 1)
        self._pt_max_bounces = 8
        self._pt_stall_count = 0
        return True

    def _write_hair_points_to_ovstage(self, updates):
        if not updates:
            return
        self.ordinal += 1
        dl_vec3 = ovstage.DLDataType(ovstage.DLDataTypeCode.kDLFloat, 32, 3)
        with ovstage.PathDictionary(self.stage) as paths:
            for prim_path, pts_buf in updates:
                pl = paths.create_path_list_from_strings([prim_path])
                try:
                    q = self.stage.query_from_path_list(pl)
                    dt = ovstage.make_dltensor(pts_buf, dtype=dl_vec3, shape=[len(pts_buf)])
                    placement = (
                        ovstage.Placement.from_dlpack_device(pts_buf)
                        if not isinstance(pts_buf, np.ndarray) and hasattr(pts_buf, "__dlpack_device__")
                        else None
                    )
                    self.stage.write_attribute(
                        q,
                        "points",
                        self.ordinal,
                        [dt],
                        is_array=True,
                        semantic=ovstage.AttributeSemantic.POINT,
                        placement=placement,
                    ).wait()
                finally:
                    paths.destroy_path_list(pl)
        self.stage.advance_write_floor(self.ordinal).wait()

    def _write_hair_display_colors_to_ovstage(self, color_updates):
        if not color_updates:
            return
        self.ordinal += 1
        dl_vec3 = ovstage.DLDataType(ovstage.DLDataTypeCode.kDLFloat, 32, 3)
        with ovstage.PathDictionary(self.stage) as paths:
            for prim_path, colors_np in color_updates:
                pl = paths.create_path_list_from_strings([prim_path])
                try:
                    q = self.stage.query_from_path_list(pl)
                    dt = ovstage.make_dltensor(colors_np, dtype=dl_vec3, shape=[len(colors_np)])
                    self.stage.write_attribute(
                        q,
                        "primvars:displayColor",
                        self.ordinal,
                        [dt],
                        is_array=True,
                        semantic=ovstage.AttributeSemantic.COLOR,
                    ).wait()
                finally:
                    paths.destroy_path_list(pl)
        self.stage.advance_write_floor(self.ordinal).wait()

    def _update_hair_pin_overlay(self, active, color_updates):
        """Show/hide per-strand pin colors on the live ovstage without mutating source USD."""
        if active and color_updates:
            if not self._pin_mat_overlay_active:
                from pxr import Sdf, Usd, UsdGeom, UsdShade, Vt
                self._pre_pin_overlay_path = self.overlay_path
                layer = Sdf.Layer.CreateAnonymous(".usda")
                if self._pre_pin_overlay_path and os.path.isfile(self._pre_pin_overlay_path):
                    layer.subLayerPaths.append(self._pre_pin_overlay_path)
                st = Usd.Stage.Open(layer)
                mat_path = Sdf.Path("/__OrchFlowPinPreviewMat")
                mat = UsdShade.Material.Define(st, mat_path)
                surf = UsdShade.Shader.Define(st, mat_path.AppendChild("PreviewSurface"))
                surf.CreateIdAttr("UsdPreviewSurface")
                surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.55)
                reader = UsdShade.Shader.Define(st, mat_path.AppendChild("DisplayColorReader"))
                reader.CreateIdAttr("UsdPrimvarReader_float3")
                reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
                reader.CreateInput("fallback", Sdf.ValueTypeNames.Float3).Set((0.14, 0.13, 0.13))
                out = reader.CreateOutput("result", Sdf.ValueTypeNames.Float3)
                surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(out)
                mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(), "surface")
                for prim_path, colors_np in color_updates:
                    over_prim = st.OverridePrim(prim_path)
                    UsdShade.MaterialBindingAPI.Apply(over_prim).Bind(
                        mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants
                    )
                    UsdGeom.PrimvarsAPI(over_prim).CreatePrimvar(
                        "displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex
                    ).Set(Vt.Vec3fArray.FromNumpy(colors_np))
                fd, pin_path = tempfile.mkstemp(prefix="ovrtx_pin_", suffix=".usda")
                os.close(fd)
                layer.Export(pin_path)
                old_pin = self._pin_overlay_path
                self._pin_overlay_path = pin_path
                self._apply_material_delta_path(pin_path)
                if old_pin and old_pin != pin_path and os.path.isfile(old_pin):
                    try:
                        os.remove(old_pin)
                    except OSError:
                        pass
                self._pin_mat_overlay_active = True
                return True
            else:
                self._write_hair_display_colors_to_ovstage(color_updates)
                return True
        elif not active and self._pin_mat_overlay_active:
            restore_path = self._pre_pin_overlay_path
            self._pin_mat_overlay_active = False
            self._pre_pin_overlay_path = None
            self._apply_material_delta_path(restore_path)
            if self._pin_overlay_path and os.path.isfile(self._pin_overlay_path):
                try:
                    os.remove(self._pin_overlay_path)
                except OSError:
                    pass
                self._pin_overlay_path = None
            return True
        return False

    def _sync_live_points_to_ovstage(self, sample_frame):
        """Re-push any time-sampled Mesh/BasisCurves points from current_usd_stage to ovstage.

        ovstage.population.apply_usd_changes re-syncs points from temp_usd_path (which is not
        re-exported during fast cache_edit / cloth sim steps), so call this after mask/material
        delta swaps to preserve the simulated/sculpted garment pose.
        """
        if self.stage is None or self.current_usd_stage is None:
            return
        tc = Usd.TimeCode(float(sample_frame))
        updates = []
        for p in self.current_usd_stage.Traverse():
            if p.IsInstanceProxy():
                continue
            if p.IsA(UsdGeom.Mesh) or p.IsA(UsdGeom.BasisCurves):
                pa = UsdGeom.PointBased(p).GetPointsAttr()
                if pa and pa.GetNumTimeSamples() >= 1:
                    pts_val = pa.Get(tc)
                    if pts_val is not None and len(pts_val) > 0:
                        updates.append((str(p.GetPath()), np.ascontiguousarray(pts_val, dtype=np.float32)))
        if updates:
            self._write_hair_points_to_ovstage(updates)

    def _composite_cache_mask_overlay(self, pixels, active_graph, request, width, height):
        """Composite translucent magenta vertex-mask overlay directly onto the rendered RGBA/RGB image.

        Avoids ovstage.population.apply_usd_changes (~13s freeze on heavy Path Tracing scenes)
        and keeps real garment materials/lighting visible underneath the painted pin mask.
        """
        basis = getattr(self, "last_camera_basis", None)
        if basis is None or self.current_usd_stage is None or not isinstance(active_graph, dict):
            return pixels
        cache_brush = request.get("cacheBrush")
        ctid_m = (
            (cache_brush.get("nodeId") if isinstance(cache_brush, dict) else None)
            or request.get("activeCacheEditNodeId")
            or active_graph.get("targetNodeId")
        )
        mask_cfg = None
        for n in active_graph.get("nodes") or []:
            if n.get("id") == ctid_m and n.get("operation") == "cache_edit":
                mask_cfg = n.get("cacheEdit")
                break
        if not isinstance(mask_cfg, dict):
            return pixels
        try:
            _wdir = os.path.dirname(os.path.abspath(__file__))
            if _wdir not in sys.path:
                sys.path.insert(0, _wdir)
            import cache_edit as _ce

            target_q = str(mask_cfg.get("targetQuery") or "")
            prims = _ce._collect_target_meshes(self.current_usd_stage, target_q, make_editable=False)
            if not prims:
                return pixels
            c_frame = float(request.get("frame", 0.0))
            tc = Usd.TimeCode(c_frame)
            xf_cache = UsdGeom.XformCache(tc)
            mask_map = mask_cfg.get("maskWeights") if isinstance(mask_cfg.get("maskWeights"), dict) else {}
            stage_id = id(self.current_usd_stage)
            frame_key = round(c_frame, 4)

            origin = np.asarray(basis["origin"], dtype=np.float32)
            fwd = np.asarray(basis["forward"], dtype=np.float32)
            right = np.asarray(basis["right"], dtype=np.float32)
            up = np.asarray(basis["up"], dtype=np.float32)
            tan_hf = max(1.0e-5, float(basis["tan_half_fov"]))
            aspect = max(1.0e-5, float(basis["aspect"]))
            is_ortho_mask = bool(basis.get("orthographic"))
            ortho_hh = max(1.0e-5, float(basis.get("ortho_half_height", 1.0)))

            mask_buf = None
            for prim in prims:
                pp = str(prim.GetPath())
                sess = _ce._CACHE_SESSIONS.get((stage_id, pp, frame_key))
                if sess is not None:
                    pts_w = sess.cur_world
                    weights = sess.mask
                else:
                    pa = UsdGeom.Mesh(prim).GetPointsAttr()
                    if not pa:
                        continue
                    raw_pts = pa.Get(tc if pa.GetNumTimeSamples() > 0 else Usd.TimeCode.Default())
                    if raw_pts is None or len(raw_pts) == 0:
                        continue
                    weights = _ce.decode_mesh_mask(mask_map.get(pp), len(raw_pts))
                    if float(weights.max()) <= 0.02:
                        continue
                    wm = np.asarray(xf_cache.GetLocalToWorldTransform(prim), dtype=np.float32)
                    pts_w = _ce._points_to_world_np(np.asarray(raw_pts, dtype=np.float32), wm, np)

                sel = weights > 0.02
                if not np.any(sel):
                    continue
                pw = pts_w[sel]
                sw = weights[sel]
                rel = pw - origin
                z = rel @ fwd
                in_front = z > 1.0e-4
                if not np.any(in_front):
                    continue
                rel = rel[in_front]
                z = z[in_front]
                sw = sw[in_front]
                if is_ortho_mask:
                    nx = (rel @ right) / (ortho_hh * aspect)
                    ny = (rel @ up) / ortho_hh
                else:
                    nx = (rel @ right) / (z * tan_hf * aspect)
                    ny = (rel @ up) / (z * tan_hf)
                in_view = (nx >= -1.02) & (nx <= 1.02) & (ny >= -1.02) & (ny <= 1.02)
                if not np.any(in_view):
                    continue
                px = np.clip(np.round((0.5 + 0.5 * nx[in_view]) * (width - 1)).astype(np.int32), 0, width - 1)
                py = np.clip(np.round((0.5 - 0.5 * ny[in_view]) * (height - 1)).astype(np.int32), 0, height - 1)
                sw = sw[in_view]
                if mask_buf is None:
                    mask_buf = np.zeros((height, width), dtype=np.float32)
                np.maximum.at(mask_buf, (py, px), sw)

            if mask_buf is None:
                return pixels
            # Fast 3x3 dilation so discrete mesh vertices form a smooth translucent overlay
            dil = mask_buf.copy()
            dil[1:, :] = np.maximum(dil[1:, :], mask_buf[:-1, :])
            dil[:-1, :] = np.maximum(dil[:-1, :], mask_buf[1:, :])
            dil2 = dil.copy()
            dil2[:, 1:] = np.maximum(dil2[:, 1:], dil[:, :-1])
            dil2[:, :-1] = np.maximum(dil2[:, :-1], dil[:, 1:])

            out = pixels.copy()
            alpha = (dil2 * 0.62)[:, :, None]
            tint = np.array([232.0, 44.0, 126.0], dtype=np.float32)
            rgb = out[:, :, :3].astype(np.float32)
            out[:, :, :3] = np.clip(rgb * (1.0 - alpha) + tint * alpha, 0.0, 255.0).astype(np.uint8)
            return out
        except Exception:
            return pixels

    def _try_fast_cache_edit_delta(self, prev_graph, next_graph, sample_frame):
        """Update Mesh.points in-place on live ovstage when only Cache Sculptor settings change.

        Avoids 100MB stage.Flatten(), .usdc disk export, and full ovstage repopulation (~15-20s -> ~1.2s).
        """
        if not isinstance(prev_graph, dict) or not isinstance(next_graph, dict):
            return False
        if {k: v for k, v in prev_graph.items() if k != "nodes"} != {
            k: v for k, v in next_graph.items() if k != "nodes"
        }:
            return False
        prev_nodes = prev_graph.get("nodes") or []
        next_nodes = next_graph.get("nodes") or []
        if len(prev_nodes) != len(next_nodes):
            return False
        diff_pair = None
        for pn, nn in zip(prev_nodes, next_nodes):
            if pn == nn:
                continue
            if diff_pair is not None:
                return False
            diff_pair = (pn, nn)
        if diff_pair is None:
            return False
        pn, nn = diff_pair
        if nn.get("operation") != "cache_edit":
            return False
        pn_copy = dict(pn)
        nn_copy = dict(nn)
        prev_ce = pn_copy.pop("cacheEdit", None) or {}
        next_ce = nn_copy.pop("cacheEdit", None) or {}
        if pn_copy != nn_copy:
            return False
        # If targetQuery changed, new meshes may need de-instancing in ovstage -> full rebuild
        if (prev_ce.get("targetQuery") or "") != (next_ce.get("targetQuery") or ""):
            return False
        try:
            _wdir = os.path.dirname(os.path.abspath(__file__))
            if _wdir not in sys.path:
                sys.path.insert(0, _wdir)
            import cache_edit as _ce

            # If simCacheVersion changed (e.g. user clicked Clear Cache), drop live sim cache
            cache_ver_changed = (prev_ce.get("simCacheVersion", 0) or 0) != (next_ce.get("simCacheVersion", 0) or 0)
            if cache_ver_changed:
                _ce.clear_cloth_sim_cache()

            # If ONLY simulation solver parameters or pin maskWeights changed,
            # skip recompiling the USD graph entirely (<1 ms). The new parameters take effect on next Simulate press.
            non_sim_keys = (
                "targetQuery",
                "layers",
                "temporalSmoothFrames",
                "temporalSmoothStrength",
                "simCacheVersion",
                "simEnabled",
            )
            if not cache_ver_changed and all(prev_ce.get(k) == next_ce.get(k) for k in non_sim_keys):
                return True

            stage, graph_stages, graph_stats = usd_graph.compile_graph(next_graph)

            target_q = str(next_ce.get("targetQuery") or "")
            tc = Usd.TimeCode(sample_frame)
            updates = []
            for prim in stage.Traverse():
                if prim.IsInstanceProxy() or not prim.IsA(UsdGeom.Mesh):
                    continue
                pp = str(prim.GetPath())
                if not _ce._mesh_query_matches(pp, target_q):
                    continue
                pa = UsdGeom.Mesh(prim).GetPointsAttr()
                if not pa:
                    continue
                pts = pa.Get(tc if pa.GetNumTimeSamples() > 0 else Usd.TimeCode.Default())
                if pts is not None and len(pts) > 0:
                    updates.append((pp, np.ascontiguousarray(pts, dtype=np.float32)))
            if not updates:
                return False
            self._write_hair_points_to_ovstage(updates)
            self.current_usd_stage = stage
            self.graph_stages = graph_stages
            self.graph_stats = graph_stats
            return True
        except Exception as err:
            sys.stderr.write(f"Ovrtx fast cache_edit delta failed; rebuilding scene: {err}\n")
            return False

    def _try_fast_hair_delta(self, prev_graph, next_graph, sample_frame, editable_hair_id):
        """Update BasisCurves.points in-place on the live ovstage when only Hair sim settings change."""
        if not isinstance(prev_graph, dict) or not isinstance(next_graph, dict):
            return False
        if {k: v for k, v in prev_graph.items() if k != "nodes"} != {
            k: v for k, v in next_graph.items() if k != "nodes"
        }:
            return False
        prev_nodes = prev_graph.get("nodes") or []
        next_nodes = next_graph.get("nodes") or []
        if len(prev_nodes) != len(next_nodes):
            return False
        diff_pair = None
        for pn, nn in zip(prev_nodes, next_nodes):
            if pn == nn:
                continue
            if diff_pair is not None:
                return False
            diff_pair = (pn, nn)
        if diff_pair is None:
            return False
        pn, nn = diff_pair
        if nn.get("operation") != "hair":
            return False
        pn_copy = dict(pn)
        nn_copy = dict(nn)
        prev_hair = pn_copy.pop("hair", None) or {}
        next_hair = nn_copy.pop("hair", None) or {}
        if pn_copy != nn_copy:
            return False
        for width_key in ("widthMode", "width", "taper", "rootScale"):
            if prev_hair.get(width_key) != next_hair.get(width_key):
                return False
        for gd in (prev_hair.get("groomDeltas"), next_hair.get("groomDeltas")):
            if isinstance(gd, dict) and any(isinstance(v, dict) and v.get("countsZB64") for v in gd.values()):
                return False
        try:
            stage, graph_stages, graph_stats = usd_graph.compile_graph(
                next_graph, interactive_hair=editable_hair_id is not None,
            )
            preview = usd_graph.open_hair_preview_stage(stage, next_graph)
            if preview != stage:
                graph_stages.append(stage)
                stage = preview
            tc = Usd.TimeCode(sample_frame)
            updates = []
            for prim in stage.Traverse():
                if prim.IsInstanceProxy() or not prim.IsA(UsdGeom.BasisCurves):
                    continue
                pts = UsdGeom.BasisCurves(prim).GetPointsAttr().Get(tc)
                if not pts or len(pts) == 0:
                    continue
                pts_np = np.ascontiguousarray(np.asarray(pts, dtype=np.float32))
                updates.append((str(prim.GetPath()), pts_np))
            if not updates:
                return False
            self._write_hair_points_to_ovstage(updates)
            self.live_groom_modified = True
            self.current_usd_stage = stage
            self.graph_stages = graph_stages
            self.graph_stats = graph_stats
            return True
        except Exception as error:
            sys.stderr.write(f"Ovrtx incremental hair update failed; rebuilding scene: {error}\n")
            return False

    @staticmethod
    def _extract_mesh_tris(mesh_prim, tc):
        mesh = UsdGeom.Mesh(mesh_prim)
        fvc_vt = mesh.GetFaceVertexCountsAttr().Get(tc)
        fvi_vt = mesh.GetFaceVertexIndicesAttr().Get(tc)
        if not fvc_vt or not fvi_vt:
            return None
        counts = np.asarray(fvc_vt, dtype=np.int64)
        indices = np.asarray(fvi_vt, dtype=np.int64)
        if np.all(counts == 3):
            tris = indices.reshape(-1, 3)
        elif np.all(counts == 4):
            quads = indices.reshape(-1, 4)
            tris = np.vstack((quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]))
        else:
            tri_list = []
            off = 0
            for c in counts:
                if c >= 3:
                    i0 = indices[off]
                    for k in range(1, c - 1):
                        tri_list.append((i0, indices[off + k], indices[off + k + 1]))
                off += c
            if not tri_list:
                return None
            tris = np.asarray(tri_list, dtype=np.int64)
        left_handed = mesh.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded
        return (tris, left_handed)

    @staticmethod
    def _compute_smooth_vertex_normals(pts_vt, topo):
        if topo is None:
            return None
        from pxr import Vt
        tris, left_handed = topo
        pts = np.asarray(pts_vt, dtype=np.float32)
        if pts.ndim != 2 or pts.shape[0] == 0:
            return None
        p0 = pts[tris[:, 0]]
        p1 = pts[tris[:, 1]]
        p2 = pts[tris[:, 2]]
        fn = np.cross(p1 - p0, p2 - p0)
        if left_handed:
            fn = -fn
        nrm = np.zeros_like(pts, dtype=np.float32)
        np.add.at(nrm, tris[:, 0], fn)
        np.add.at(nrm, tris[:, 1], fn)
        np.add.at(nrm, tris[:, 2], fn)
        lens = np.linalg.norm(nrm, axis=1, keepdims=True)
        safe = np.where(lens > 1e-8, lens, 1.0)
        nrm = np.where(lens > 1e-8, nrm / safe, np.array([0.0, 1.0, 0.0], dtype=np.float32))
        return Vt.Vec3fArray.FromNumpy(nrm)

    def _cache_stage_skel_bindings(self, stage, sample_frame):
        from pxr import UsdSkel
        tc = Usd.TimeCode(sample_frame)
        sc = UsdSkel.Cache()
        bindings_by_skel = {}
        for p in stage.Traverse():
            if p.IsA(UsdSkel.Root):
                sr = UsdSkel.Root(p)
                sc.Populate(sr, Usd.PrimDefaultPredicate)
                for b in sc.ComputeSkelBindings(sr, Usd.PrimDefaultPredicate):
                    skel = b.GetSkeleton()
                    sq = sc.GetSkelQuery(skel)
                    meshes = []
                    for skq in b.GetSkinningTargets():
                        mp = skq.GetPrim()
                        if mp and mp.IsValid() and mp.IsA(UsdGeom.Mesh):
                            rest_pts = UsdGeom.Mesh(mp).GetPointsAttr().Get(tc)
                            if rest_pts is not None and len(rest_pts) > 0:
                                topo = self._extract_mesh_tris(mp, tc)
                                meshes.append((str(mp.GetPath()), skq, rest_pts, topo))
                    if sq:
                        bindings_by_skel[str(skel.GetPath())] = (sq, meshes)
        self._ovrtx_skel_cache = sc
        self._ovrtx_skel_bindings = bindings_by_skel
        return bindings_by_skel

    def _bake_stage_skel_for_ovstage(self, stage, sample_frame):
        from pxr import UsdSkel, Vt
        bindings_by_skel = self._cache_stage_skel_bindings(stage, sample_frame)
        if not bindings_by_skel:
            return None
        tc = Usd.TimeCode(sample_frame)
        bake_layer = Sdf.Layer.CreateAnonymous("ovrtx_pose_flatten_bake.usda")
        sess = stage.GetSessionLayer()
        sess.subLayerPaths.insert(0, bake_layer.identifier)
        self._initial_pose_points = []
        with Usd.EditContext(stage, bake_layer):
            for _skel_path, (sq, meshes) in bindings_by_skel.items():
                if not meshes:
                    continue
                skin_xforms = sq.ComputeSkinningTransforms(tc)
                for mpath_str, skq, rest_pts, topo in meshes:
                    pts = Vt.Vec3fArray(rest_pts)
                    if skq.ComputeSkinnedPoints(skin_xforms, pts, tc):
                        nrm = self._compute_smooth_vertex_normals(pts, topo)
                        self._initial_pose_points.append((mpath_str, pts, nrm))
                        over_m = stage.OverridePrim(Sdf.Path(mpath_str))
                        mesh_api = UsdGeom.Mesh(over_m)
                        mesh_api.CreatePointsAttr().Set(pts)
                        if nrm is not None:
                            mesh_api.CreateNormalsAttr().Set(nrm)
                            mesh_api.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
                        UsdSkel.BindingAPI(over_m).CreateSkeletonRel().SetTargets([])
        return bake_layer

    def _strip_skel_from_flat(self, flat):
        bindings = getattr(self, "_ovrtx_skel_bindings", None) or {}
        for _skel_path, (_sq, meshes) in bindings.items():
            for mpath_str, _skq, _rest_pts, _topo in meshes:
                ps = flat.GetPrimAtPath(mpath_str)
                if not ps:
                    continue
                for prop_name in list(ps.properties.keys()):
                    if prop_name in ("points", "normals", "primvars:normals") or prop_name.startswith("skel:") or prop_name.startswith("primvars:skel:"):
                        del ps.properties[prop_name]
                api = ps.GetInfo("apiSchemas")
                if api:
                    items = [t for t in api.GetAddedOrExplicitItems() if t != "SkelBindingAPI"]
                    new_op = Sdf.TokenListOp()
                    new_op.explicitItems = items
                    ps.SetInfo("apiSchemas", new_op)
        def _clear_skel_roots(prim_spec):
            if prim_spec.typeName == "SkelRoot":
                prim_spec.typeName = "Xform"
            for child in prim_spec.nameChildren.values():
                _clear_skel_roots(child)
        for root_prim in flat.rootPrims.values():
            _clear_skel_roots(root_prim)

    def _swap_pose_overlay(self, updates, apply_changes=True):
        ov_layer = Sdf.Layer.CreateAnonymous("ovrtx_pose_overlay.usdc")
        for item in updates:
            mpath_str, pts_vt = item[0], item[1]
            nrm_vt = item[2] if len(item) > 2 else None
            spec = Sdf.CreatePrimInLayer(ov_layer, Sdf.Path(mpath_str))
            spec.specifier = Sdf.SpecifierOver
            attr = Sdf.AttributeSpec(spec, "points", Sdf.ValueTypeNames.Point3fArray)
            attr.default = pts_vt
            if nrm_vt is not None:
                nattr = Sdf.AttributeSpec(spec, "normals", Sdf.ValueTypeNames.Normal3fArray)
                nattr.default = nrm_vt
                nattr.SetInfo("interpolation", UsdGeom.Tokens.vertex)
        scratch_dir = os.path.dirname(self.temp_usd_path) if self.temp_usd_path else (
            os.environ.get("ORCHFLOW_OVRTX_SCRATCH") or os.path.join(tempfile.gettempdir(), "orchflow_ovrtx_scratch")
        )
        os.makedirs(scratch_dir, exist_ok=True)
        seq = getattr(self, "_pose_overlay_seq", 0) + 1
        self._pose_overlay_seq = seq
        new_path = os.path.join(scratch_dir, f"orchflow_ovrtx_pose_{seq}_{uuid.uuid4().hex[:8]}.usdc")
        ov_layer.Export(new_path)
        prev_handle = getattr(self, "_pose_overlay_handle", None)
        prev_path = getattr(self, "_pose_overlay_path", None)
        if prev_handle is not None:
            ovstage.population.remove_usd(self.stage, prev_handle)
            self._pose_overlay_handle = None
        self._pose_overlay_handle = ovstage.population.add_usd_reference(self.stage, new_path, "/")
        self._pose_overlay_path = new_path
        if prev_path and prev_path != new_path and os.path.isfile(prev_path):
            try:
                os.remove(prev_path)
            except OSError:
                pass
        if apply_changes:
            self.ordinal += 1
            ovstage.population.apply_usd_changes(self.stage, ordinal=self.ordinal)
            self.stage.compute_hierarchy(
                self.ordinal,
                self.ordinal,
                model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
            )
            self.stage.advance_write_floor(self.ordinal).wait()
            self._pt_spp_at_one = True
            self._pt_max_bounces = 8
            self._pt_stall_count = 0

    def _apply_live_pose_to_ovstage(self, skel_paths, sample_frame):
        from pxr import Vt
        bindings = getattr(self, "_ovrtx_skel_bindings", None)
        if not bindings and self.current_usd_stage is not None:
            bindings = self._cache_stage_skel_bindings(self.current_usd_stage, sample_frame)
        if not bindings or self.stage is None:
            return False
        tc = Usd.TimeCode(sample_frame)
        updates = []
        for sp in skel_paths:
            entry = bindings.get(sp)
            if not entry:
                continue
            sq, meshes = entry
            if not meshes:
                continue
            skin_xforms = sq.ComputeSkinningTransforms(tc)
            for mpath_str, skq, rest_pts, topo in meshes:
                pts = Vt.Vec3fArray(rest_pts)
                if skq.ComputeSkinnedPoints(skin_xforms, pts, tc):
                    nrm = self._compute_smooth_vertex_normals(pts, topo)
                    updates.append((mpath_str, pts, nrm))
        if updates:
            self._swap_pose_overlay(updates, apply_changes=True)
        return True

    def _try_fast_transform_overlay(self, previous, graph, frame):
        live = self.live_transforms
        changed = usd_graph.transform_only_node(previous, graph, live)
        if changed is None or not live.get(changed["id"]) or self.current_usd_stage is None:
            return False
        attrs = [self.current_usd_stage.GetAttributeAtPath(attr.GetPath()) for attr in live[changed["id"]]]
        if any(not attr for attr in attrs):
            return False
        matrix = usd_graph._build_transform_matrix(changed.get("transform"))
        # CPU opinions are private. Only these recorded node ops and their orders
        # change; no geometry, namespace, references or material bindings are copied.
        overlay = Sdf.Layer.CreateAnonymous("viewport-transform.usda")
        if self.overlay_path:
            overlay.TransferContent(Sdf.Layer.FindOrOpen(self.overlay_path))
        for attr in attrs:
            with Usd.EditContext(self.current_usd_stage, self.current_usd_stage.GetSessionLayer()):
                attr.Set(matrix)
            prim = attr.GetPrim()
            spec = Sdf.CreatePrimInLayer(overlay, prim.GetPath())
            spec.specifier = Sdf.SpecifierOver
            value = Sdf.AttributeSpec(spec, attr.GetName(), Sdf.ValueTypeNames.Matrix4d)
            value.default = matrix
            from pxr import Vt
            order = Sdf.AttributeSpec(spec, "xformOpOrder", Sdf.ValueTypeNames.TokenArray, Sdf.VariabilityUniform)
            order.default = Vt.TokenArray(UsdGeom.Xformable(prim).GetXformOpOrderAttr().Get())
        overlay.subLayerPaths = [self.temp_usd_path]
        self.ordinal += 1
        ovstage.population.open_usd_from_string(
            self.stage, overlay.ExportToString(), ordinal=self.ordinal,
            time=frame / max(1e-5, self.current_usd_stage.GetTimeCodesPerSecond()),
        )
        self.stage.compute_hierarchy(self.ordinal, self.ordinal,
            model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL)
        self.stage.advance_write_floor(self.ordinal).wait()
        self._flush_stage_pool()
        self._transform_overlay_layer = overlay
        self._pt_spp_at_one = True
        self._pt_max_bounces = 8
        self._pt_stall_count = 0
        return True

    def _try_fast_pose_delta(self, prev_graph, new_graph, sample_frame):
        try:
            live_poses = getattr(self, "live_poses", None)
            if not live_poses or self.current_usd_stage is None or self.stage is None:
                return False
            changed = usd_graph.pose_only_node(prev_graph, new_graph, live_poses)
            if changed is None:
                return False
            import pose_node
            targets = live_poses.get(changed["id"]) or []
            with Usd.EditContext(self.current_usd_stage, self.current_usd_stage.GetRootLayer()):
                if not pose_node.update_live_pose(changed, targets):
                    return False
            skel_paths = [t_skel_path for _r, _e, t_skel_path, _out in targets]
            self._apply_live_pose_to_ovstage(skel_paths, sample_frame)
            return True
        except Exception as error:
            sys.stderr.write(f"Ovrtx incremental pose update failed; rebuilding scene: {error}\n")
            return False

    def _write_postfx_to_ovstage(self, old_cfg, new_cfg):
        """Write changed PostFX attributes directly to /Render/RenderProduct_Viewport without recompiling the scene.
        Scalar/bool/color changes use direct ovstage.write_attribute; token changes use a 1-prim overlay delta.
        """
        if self.stage is None:
            return False
        old_specs = {name: (kind, val) for name, kind, val in build_postfx_attr_specs(old_cfg)}
        new_specs = build_postfx_attr_specs(new_cfg)
        changed_specs = []
        has_token_change = False
        for name, kind, val in new_specs:
            if old_specs.get(name) != (kind, val):
                if kind == "token":
                    has_token_change = True
                changed_specs.append((name, kind, val))
        if not changed_specs:
            return True
        if has_token_change:
            try:
                overlay = Sdf.Layer.CreateAnonymous("postfx_token_delta.usda")
                if self.overlay_path and os.path.isfile(self.overlay_path):
                    prev_overlay = Sdf.Layer.FindOrOpen(self.overlay_path)
                    if prev_overlay is not None:
                        overlay.TransferContent(prev_overlay)
                tmp_stage = Usd.Stage.Open(overlay)
                for prod_path in _VIEWPORT_PRODUCTS:
                    prod = UsdRender.Product.Define(tmp_stage, prod_path)
                    apply_postfx_to_render_product(prod.GetPrim(), new_cfg if new_cfg is not None else {})
                self._apply_material_delta(overlay)
                return True
            except Exception as err:
                sys.stderr.write(f"Ovrtx PostFX overlay update failed; rebuilding scene: {err}\n")
                return False
        try:
            self.ordinal += 1
            with ovstage.PathDictionary(self.stage) as paths:
                for prod_path in _VIEWPORT_PRODUCTS:
                    prod_plist = paths.create_path_list_from_strings([prod_path])
                    try:
                        prod_query = self.stage.query_from_path_list(prod_plist)
                        for name, kind, val in changed_specs:
                            if kind == "bool":
                                arr = np.asarray([bool(val)], dtype=np.bool_)
                                sem = 0
                            elif kind == "int":
                                arr = np.asarray([int(val)], dtype=np.int32)
                                sem = 0
                            elif kind == "float":
                                arr = np.asarray([float(val)], dtype=np.float32)
                                sem = 0
                            elif kind == "float3":
                                arr = np.asarray([[val[0], val[1], val[2]]], dtype=np.float32)
                                sem = 0
                            elif kind == "color3f":
                                arr = np.asarray([[val[0], val[1], val[2]]], dtype=np.float32)
                                sem = ovstage.AttributeSemantic.COLOR
                            else:
                                return False
                            self.stage.write_attribute(prod_query, name, self.ordinal, arr, is_array=False, semantic=sem).wait()
                    finally:
                        paths.destroy_path_list(prod_plist)
            self.stage.advance_write_floor(self.ordinal).wait()
            return True
        except Exception as err:
            sys.stderr.write(f"Ovrtx fast PostFX update failed; rebuilding scene: {err}\n")
            return False

    def _try_fast_groom_delta(self, prev_graph, new_graph, sample_frame):
        """Apply rigid Head Groom transform-only updates via a tiny USD overlay without recompiling."""
        if not prev_graph or not new_graph or self.stage is None:
            return None
        live_grooms = getattr(self, "live_grooms", None)
        if not live_grooms:
            return None
        changed = usd_graph.groom_xform_only_changes(prev_graph, new_graph, live_grooms)
        if not changed:
            return None
        prim_updates = usd_graph.apply_live_groom_xforms(changed, live_grooms)
        if not prim_updates:
            return None
        delta = Sdf.Layer.CreateAnonymous("ovrtx_groom_xform_delta.usda")
        from pxr import Vt
        for prim_path, tc_mats in prim_updates:
            spec = Sdf.CreatePrimInLayer(delta, Sdf.Path(prim_path))
            spec.specifier = Sdf.SpecifierOver
            order_attr = Sdf.AttributeSpec(spec, "xformOpOrder", Sdf.ValueTypeNames.TokenArray, Sdf.VariabilityUniform)
            order_attr.default = Vt.TokenArray(["xformOp:transform"])
            mat_attr = Sdf.AttributeSpec(spec, "xformOp:transform", Sdf.ValueTypeNames.Matrix4d)
            if len(tc_mats) == 1 and tc_mats[0][0] == Usd.TimeCode.Default():
                mat_attr.default = tc_mats[0][1]
            else:
                for tc, mat in tc_mats:
                    if tc == Usd.TimeCode.Default():
                        mat_attr.default = mat
                    else:
                        delta.SetTimeSample(mat_attr.path, float(tc.GetValue()), mat)
        delta_path = self._apply_material_delta(delta)
        if getattr(self, "_initial_pose_points", None):
            self._swap_pose_overlay(self._initial_pose_points, apply_changes=True)
        return delta_path

    def _try_fast_postfx_delta(self, prev_graph, new_graph):
        """Write PostFX-only scalar/bool/color updates directly to /Render/RenderProduct_Viewport in ovstage."""
        if not prev_graph or not new_graph or self.stage is None:
            return False
        if (
            prev_graph.get("targetNodeId") != new_graph.get("targetNodeId")
            or prev_graph.get("edges") != new_graph.get("edges")
            or prev_graph.get("dependencies") != new_graph.get("dependencies")
            or prev_graph.get("proceduralGraph") != new_graph.get("proceduralGraph")
        ):
            return False
        prev_nodes = prev_graph.get("nodes") or []
        new_nodes = new_graph.get("nodes") or []
        if len(prev_nodes) != len(new_nodes):
            return False
        changed_postfx = False
        for pn, nn in zip(prev_nodes, new_nodes):
            if pn.get("id") != nn.get("id") or pn.get("operation") != nn.get("operation"):
                return False
            if pn.get("operation") != "postfx":
                if pn != nn:
                    return False
                continue
            if pn != nn:
                p_clean = {k: v for k, v in pn.items() if k != "postFx"}
                n_clean = {k: v for k, v in nn.items() if k != "postFx"}
                if p_clean != n_clean:
                    return False
                changed_postfx = True
        if not changed_postfx:
            return False
        old_cfg = resolve_graph_postfx(prev_graph)
        new_cfg = resolve_graph_postfx(new_graph)
        if self._write_postfx_to_ovstage(old_cfg, new_cfg):
            self._last_postfx_cfg = new_cfg
            return True
        return False

    def _try_fast_materialx_delta(self, prev_graph, new_graph):
        """Author MaterialX-only parameter updates directly into a sparse delta layer.

        Skips full-stage compile, shader asset validation, and stage.Flatten() when
        only scalar/color MaterialX parameters changed (same rules/bindings/textures).
        Returns None to fall back to the full path whenever anything structural differs.
        """
        if not prev_graph or not new_graph or self.base_layer is None:
            return None
        # Instancer prototypes reference private upstream material snapshots. Recompile
        # them before planning the ordinary full-stage material delta.
        if any(n.get("operation") == "instancer" for n in new_graph.get("nodes", [])):
            return None
        if prev_graph.get("edges") != new_graph.get("edges"):
            return None
        if prev_graph.get("dependencies") != new_graph.get("dependencies"):
            return None
        prev_pg = prev_graph.get("proceduralGraph")
        new_pg = new_graph.get("proceduralGraph")
        changed_pg = False
        if prev_pg != new_pg:
            import procedural_mtlx
            if procedural_mtlx.graph_topology_signature(prev_pg) != procedural_mtlx.graph_topology_signature(new_pg):
                return None
            changed_pg = True
        prev_nodes = prev_graph.get("nodes") or []
        new_nodes = new_graph.get("nodes") or []
        if len(prev_nodes) != len(new_nodes):
            return None

        def _strip_mx_params(mat):
            if not isinstance(mat, dict):
                return mat
            keep_keys = (
                "name",
                "bindingTargets",
                "baseColorTexture",
                "baseColorTextureDependencyId",
                "roughnessTexture",
                "roughnessTextureDependencyId",
                "metallicTexture",
                "metallicTextureDependencyId",
                "normalTexture",
                "normalTextureDependencyId",
                "bumpTexture",
                "bumpTextureDependencyId",
                "opacityTexture",
                "opacityTextureDependencyId",
                "emissionTexture",
                "emissionTextureDependencyId",
                "subsurfaceColorTexture",
                "subsurfaceColorTextureDependencyId",
                "noise",
            )
            return {k: mat.get(k) for k in keep_keys if k in mat}

        def _mx_structure(node):
            rules = node.get("materialXRules")
            if rules is not None:
                return [
                    {
                        "id": r.get("id"),
                        "enabled": r.get("enabled"),
                        "mode": r.get("mode"),
                        "overrideParams": sorted(r.get("overrideParams") or []),
                        "searchType": r.get("searchType"),
                        "query": r.get("query"),
                        "material": _strip_mx_params(r.get("material")),
                    }
                    for r in rules
                ]
            return _strip_mx_params(node.get("materialX"))

        changed_mx = changed_pg
        mx_nodes = []
        for pn, nn in zip(prev_nodes, new_nodes):
            p_op = pn.get("operation") or (pn.get("data") or {}).get("operation")
            n_op = nn.get("operation") or (nn.get("data") or {}).get("operation")
            if pn.get("id") != nn.get("id") or p_op != n_op:
                return None
            if n_op != "materialx":
                if pn != nn:
                    return None
                continue
            mx_nodes.append(nn)
            if pn != nn:
                if _mx_structure(pn) != _mx_structure(nn):
                    return None
                p_clean = {k: v for k, v in pn.items() if k not in ("materialX", "materialXRules")}
                n_clean = {k: v for k, v in nn.items() if k not in ("materialX", "materialXRules")}
                if p_clean != n_clean:
                    return None
                changed_mx = True

        if not changed_mx or not mx_nodes:
            return None

        try:
            overlay = Sdf.Layer.CreateAnonymous("mx_fast_delta.usda")
            # Preserve any active non-MaterialX delta opinions (e.g. Light, HDRI, Transform)
            # currently applied on top of self.base_layer so fast MaterialX edits never reset them.
            if self.overlay_path and os.path.isfile(self.overlay_path):
                prev_overlay = Sdf.Layer.FindOrOpen(self.overlay_path)
                if prev_overlay is not None:
                    overlay.TransferContent(prev_overlay)
            overlay.subLayerPaths = [self.base_layer.identifier]
            tmp_stage = Usd.Stage.Open(overlay)
            default_prim = tmp_stage.GetPrimAtPath("/Scene")
            if default_prim and default_prim.IsValid():
                tmp_stage.SetDefaultPrim(default_prim)
            files = {
                d["id"]: d["path"]
                for d in (new_graph.get("dependencies") or [])
                if d.get("id") and d.get("path")
            }
            for mx_node in mx_nodes:
                node_id = mx_node.get("id")
                selected_override = (
                    self.mx_rule_selections.get(node_id)
                    if mx_node.get("materialXRules") is not None
                    else None
                )
                usd_graph.author_materialx_rules(
                    tmp_stage,
                    mx_node,
                    files,
                    procedural_graph=new_graph.get("proceduralGraph"),
                    selected_override=selected_override,
                )
            overlay.subLayerPaths.clear()
            overlay.ClearDefaultPrim()
            def _promote_overs(prim_spec):
                if prim_spec.specifier == Sdf.SpecifierOver:
                    prim_spec.specifier = Sdf.SpecifierDef
                for child in prim_spec.nameChildren:
                    _promote_overs(child)
            for root_prim in overlay.rootPrims:
                _promote_overs(root_prim)
            return overlay
        except Exception as err:
            sys.stderr.write(f"Fast MaterialX delta fallback to full compile: {err}\n")
            return None

    def _apply_material_delta(self, delta):
        new_path = export_preview_snapshot(delta) if len(delta.rootPrims) else None
        try:
            self._apply_material_delta_path(new_path)
        except Exception:
            if new_path and os.path.isfile(new_path):
                try:
                    os.remove(new_path)
                except OSError:
                    pass
            raise
        return new_path

    def _register_render_graph(self, key, graph):
        if len(self.render_graphs) >= 32 and key not in self.render_graphs:
            evicted = next(iter(self.render_graphs))
            self.render_graphs.pop(evicted)
            self.render_graph_hashes.pop(evicted, None)
        self.render_graphs[key] = graph
        self.render_graph_hashes[key] = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()

    def render(self, request):
        self._ensure_initialized()
        t_total_start = time.time()

        width = max(16, int(request.get("width", 800)))
        height = max(16, int(request.get("height", 600)))
        if request.get("nativePopup") and self.native_popup is not None and self.native_popup.is_open():
            cs = getattr(self.native_popup, "_canvas_size", None)
            if isinstance(cs, (tuple, list)) and len(cs) == 2:
                pw, ph = int(cs[0] or 0), int(cs[1] or 0)
                if pw >= 64 and ph >= 64:
                    width, height = pw, ph
        interactive = bool(request.get("interactive", False))
        render_mode = request.get("ovrtxMode", "realtime")
        if not isinstance(render_mode, str) or render_mode not in _RENDER_MODES:
            raise ValueError("NVIDIA render mode must be Real-Time Path Tracing or Path Tracing")

        if (
            request.get("popupHoverOnly")
            and request.get("nativePopup")
            and not request.get("hairBrush")
            and not request.get("cacheBrush")
            and self.native_popup is not None
            and self.native_popup.is_open()
            and getattr(self, "_popup_clean_u8", None) is not None
            and getattr(self, "_popup_clean_meta", None) is not None
            and self._popup_clean_meta[1] == width
            and self._popup_clean_meta[0] == height
        ):
            try:
                import warp as wp
                from hair_kernels import draw_brush_circle_cuda, draw_polyline_cuda
                clean_u8 = self._popup_clean_u8
                ah, aw, sp = self._popup_clean_meta
                if getattr(self, "_popup_draw_u8", None) is None or self._popup_draw_u8.shape != clean_u8.shape:
                    self._popup_draw_u8 = wp.clone(clean_u8)
                else:
                    wp.copy(self._popup_draw_u8, clean_u8)
                popup_status = request.get("popupStatus")
                if isinstance(popup_status, str) and popup_status:
                    self.native_popup.set_status(popup_status)
                popup_draw_curve = request.get("popupDrawCurve")
                if isinstance(popup_draw_curve, list) and len(popup_draw_curve) >= 2:
                    draw_polyline_cuda(self._popup_draw_u8, ah, aw, popup_draw_curve)
                r_px = float(request.get("popupBrushRadiusPx", 0.0) or 0.0)
                if r_px > 1.0:
                    hnx, hny = self.native_popup.last_hover_norm()
                    draw_brush_circle_cuda(self._popup_draw_u8, ah, aw, hnx, hny, r_px)
                self.native_popup.present_cuda(self._popup_draw_u8.ptr, aw, ah, cuda_stream=sp)
                return {
                    "width": aw,
                    "height": ah,
                    "renderer": "OmniverseRTX",
                    "renderMode": render_mode,
                    "samples": 1,
                    "targetSamples": 1,
                    "converged": True,
                    "dataUrl": "",
                    "nativePopupOpen": self.native_popup.is_open(),
                    "reused": True,
                    "elapsedMs": round((time.time() - t_total_start) * 1000, 1),
                }
            except Exception as hover_err:
                sys.stderr.write(f"Native CUDA pop-up hover reblit failed: {hover_err}\n")

        self._population_sample = float(request.get("frame", 0))
        t_scene_start = time.perf_counter()
        # 1. Compile or obtain the USD stage
        graph = request.get("graph")
        stage_path = request.get("path")
        graph_key = request.get("graphKey")
        if isinstance(graph, str):
            graph = json.loads(graph)
        if graph_key:
            if not isinstance(graph_key, str) or len(graph_key) > 100:
                raise ValueError("Invalid render graph handle")
            if graph:
                self._register_render_graph(graph_key, graph)
            else:
                graph = self.render_graphs.get(graph_key)
                if graph is None:
                    raise ValueError("Unknown render graph handle; register graph again")
        if graph_key:
            graph_hash = self.render_graph_hashes[graph_key]
        else:
            graph_json = json.dumps(graph, sort_keys=True) if graph else stage_path or ""
            graph_hash = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()
        self._current_render_graph = graph
        outline_key = graph_hash if graph else stage_path

        # Frame, material mode and render mode are fixed per populated scene; a change
        # to any of them always needs a full repopulation (never an incremental delta).
        isolated_prim = request.get("isolatedPrim")
        hidden_prims = sorted(set(request.get("hiddenPrims") or []))
        up_axis = str(request.get("upAxis", "Y")).upper()
        static_scene_key = str(outline_key or "") + ":materials:" + str(request.get("materials", "auto")) + ":mode:" + render_mode + ":isolated:" + str(isolated_prim or "") + ":hidden:" + ",".join(hidden_prims) + ":up:" + up_axis
        scene_suffix = ":frame:" + str(request.get("frame", 0)) + ":materials:" + str(request.get("materials", "auto")) + ":mode:" + render_mode + ":isolated:" + str(isolated_prim or "") + ":hidden:" + ",".join(hidden_prims) + ":up:" + up_axis
        graph_hash += scene_suffix
        target_spp = max(1, int(request.get("maxSamples", 512))) if render_mode == "pathTracing" else 16
        switched_scene = (graph_hash != self.last_graph_hash) or bool(request.get("reload"))
        editable_hair_id = usd_graph.editable_hair_node_id(graph, request.get("activeHairNodeId"))
        needs_hair_session = editable_hair_id is not None and editable_hair_id != self.prepared_hair_node_id
        scene_changed = switched_scene or self.temp_usd_path is None or needs_hair_session
        projection = str(request.get("projection") or "perspective")
        if projection not in ("perspective", "orthographic"):
            raise ValueError("Camera projection must be perspective or orthographic")
        is_ortho = projection == "orthographic"
        active_product = "/Render/RenderProduct_ViewportOrtho" if is_ortho else "/Render/RenderProduct_Viewport"
        active_cam_path = "/Render/OrthoCamera" if is_ortho else "/Render/Camera"
        camera_key = tuple(request.get(key) for key in ("yaw", "pitch", "zoom", "panX", "panY", "projection", "fov", "dofEnabled", "focusDistance", "fStop", "cameraSession", "frameFit", "frame", "frameTarget", "orbitPivotSeq", "upAxis", "transformGizmoNodeId"))
        camera_changed = camera_key != self.last_camera_key
        settled_from_interactive = (not interactive) and bool(getattr(self, "_last_interactive", False))
        size_changed = (width != self.last_width) or (height != self.last_height)

        live_hair_preview = bool(graph and any(usd_graph.is_live_hair_node(n) for n in graph.get("nodes", [])))
        groom_committed = (
            scene_changed and self.current_usd_stage is not None and self.temp_usd_path
            and not needs_hair_session and not request.get("reload") and not request.get("clearScene")
            and self.pending_groom_graph == (graph, request.get("cameraSession"), scene_suffix)
        )
        groom_frame = self.pending_groom_frame
        if scene_changed:
            self.pending_groom_graph = None
            self.pending_groom_frame = None
        if request.get("reload") or request.get("clearScene"):
            self._flush_stage_pool()
        elif groom_committed:
            self.last_outline_key = outline_key
            self.last_graph_hash = graph_hash
            self.last_static_scene_key = static_scene_key
            self.last_graph = graph
            self.last_update_kind = "groom-commit"
            scene_changed = switched_scene = False
            if (
                groom_frame is not None and groom_frame[0] == usd_graph.groom_frame_view(request)
                and not request.get("pick") and not request.get("hairBrush")
                and not (groom_frame[1].get("encoding") == "popup" and not request.get("popupSkipImage"))
            ):
                self._last_interactive = interactive
                return reuse_presented_image({
                    **groom_frame[1], "updateKind": "groom-commit", "interactive": interactive,
                    "renderMs": 0.0, "captureMs": 0.0, "sceneMs": 0.0, "stepMs": 0.0, "steps": 0,
                }, request)
        elif (
            needs_hair_session and not switched_scene and self.temp_usd_path
            and self.current_usd_stage is not None and not self.live_groom_modified
            and not request.get("reload") and not request.get("clearScene")
        ):
            # An unchanged graph already has the correct rendered geometry.
            # Evaluate its interactive Hair nodes to initialize their Warp
            # sessions without flattening or repopulating the live RTX scene.
            phase("preparing hair editing session", timeout=300)
            usd_graph.compile_graph(graph, interactive_hair=True)
            self.prepared_hair_node_id = editable_hair_id
            scene_changed = False
        elif (
            scene_changed
            and not request.get("reload")
            and not request.get("clearScene")
            and not needs_hair_session
            and not self.live_groom_modified
            and self._transform_overlay_layer is None
            and self.current_usd_stage is not None
            and self.stage is not None
            and self.temp_usd_path
            and getattr(self, "last_static_scene_key", None) == static_scene_key
        ):
            # Fast frame-only playback / scrubbing path: same graph & settings, only `frame` changed.
            # Avoids recompiling, flattening, and exporting a new .usdc on every timeline frame.
            from pxr import UsdSkel
            req_frame = float(request.get("frame", 0))
            tc = Usd.TimeCode(req_frame)
            has_xform_or_skel_anim = False
            point_updates = []
            for p in self.current_usd_stage.Traverse():
                if p.IsInstanceProxy():
                    continue
                if not has_xform_or_skel_anim:
                    if p.IsA(UsdSkel.Animation):
                        anim = UsdSkel.Animation(p)
                        if (
                            (anim.GetTranslationsAttr() and anim.GetTranslationsAttr().GetNumTimeSamples() > 1)
                            or (anim.GetRotationsAttr() and anim.GetRotationsAttr().GetNumTimeSamples() > 1)
                            or (anim.GetScalesAttr() and anim.GetScalesAttr().GetNumTimeSamples() > 1)
                        ):
                            has_xform_or_skel_anim = True
                    if not has_xform_or_skel_anim:
                        xf = UsdGeom.Xformable(p)
                        if xf and xf.TransformMightBeTimeVarying():
                            has_xform_or_skel_anim = True
                if p.IsA(UsdGeom.Mesh) or p.IsA(UsdGeom.BasisCurves):
                    pa = UsdGeom.PointBased(p).GetPointsAttr()
                    if pa and pa.GetNumTimeSamples() >= 1:
                        pts_val = pa.Get(tc)
                        if pts_val is not None and len(pts_val) > 0:
                            point_updates.append((str(p.GetPath()), np.ascontiguousarray(pts_val, dtype=np.float32)))
            if not has_xform_or_skel_anim and point_updates:
                self._write_hair_points_to_ovstage(point_updates)
                self.last_graph_hash = graph_hash
                self.last_suffix = scene_suffix
                self.last_update_kind = "frame-points"
                scene_changed = switched_scene = False
                camera_changed = True
            elif not has_xform_or_skel_anim:
                # Static scene with no animated points/xforms/skeletons
                self.last_graph_hash = graph_hash
                self.last_suffix = scene_suffix
                self.last_update_kind = "frame-static"
                scene_changed = switched_scene = False
            else:
                # Truly animated transforms or skeleton: reset and re-open at the new frame
                # (calling open_usd on an already-loaded path without reset is a no-op in ovstage).
                try:
                    self.ordinal += 1
                    ovstage.population.reset_usd(self.stage, self.temp_usd_path, ordinal=self.ordinal)
                except Exception:
                    pass
                self.ordinal += 1
                ovstage.population.open_usd(
                    self.stage,
                    self.temp_usd_path,
                    ordinal=self.ordinal,
                    time=req_frame / max(1e-5, self.current_usd_stage.GetTimeCodesPerSecond()),
                )
                self.stage.compute_hierarchy(
                    self.ordinal,
                    self.ordinal,
                    model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
                )
                self.stage.advance_write_floor(self.ordinal).wait()
                # Overlay any in-memory simulated/sculpted Mesh or BasisCurves points from current_usd_stage
                # (temp_usd_path is not re-exported during fast cache_edit / cloth sim steps).
                if point_updates:
                    self._write_hair_points_to_ovstage(point_updates)
                skel_bindings = getattr(self, "_ovrtx_skel_bindings", None)
                if skel_bindings:
                    self._apply_live_pose_to_ovstage(list(skel_bindings.keys()), req_frame)
                self.last_graph_hash = graph_hash
                self.last_suffix = scene_suffix
                self.last_update_kind = "frame-reopen"
                self._pt_spp_at_one = (render_mode == "pathTracing" and target_spp > 1)
                self._pt_max_bounces = 8
                self._pt_stall_count = 0
                scene_changed = switched_scene = False
                camera_changed = True
        elif (
            scene_changed and not needs_hair_session and not request.get("reload")
            and not request.get("clearScene") and self.last_suffix == scene_suffix
            and self._try_fast_transform_overlay(self.last_graph, graph, float(request.get("frame", 0)))
        ):
            self.last_outline_key = outline_key
            self.last_graph_hash = graph_hash
            self.last_static_scene_key = static_scene_key
            self.last_graph = copy.deepcopy(graph)
            self.last_update_kind = "transform-overlay"
            scene_changed = False
            camera_changed = True
        elif scene_changed and not self.live_groom_modified and editable_hair_id is None and graph_hash in self.stage_pool:
            cached = self.stage_pool[graph_hash]
            target_postfx = resolve_graph_postfx(graph)
            prev_postfx = getattr(self, "_last_postfx_cfg", None)
            if prev_postfx == target_postfx or self._write_postfx_to_ovstage(prev_postfx, target_postfx):
                if cached["delta_path"] != self.overlay_path:
                    self._apply_material_delta_path(cached["delta_path"])
                cached_pose_pts = cached.get("initial_pose_points")
                if cached_pose_pts:
                    self._initial_pose_points = cached_pose_pts
                    self._swap_pose_overlay(cached_pose_pts, apply_changes=True)
                self._last_postfx_cfg = target_postfx
                self.current_usd_stage = cached["current_usd_stage"]
                self.last_outline_key = outline_key
                self.material_diagnostics = cached["material_diagnostics"]
                self.material_fallbacks = cached["material_fallbacks"]
                self.material_keepalive = cached["material_keepalive"]
                self.graph_stages = cached["graph_stages"]
                self.graph_stats = cached["graph_stats"]
                self.mx_rule_selections = cached.get("mx_rule_selections", {})
                self.live_poses = cached.get("live_poses", {})
                self.live_grooms = cached.get("live_grooms", {})
                self._ovrtx_skel_bindings = cached.get("ovrtx_skel_bindings")
                self._ovrtx_skel_cache = cached.get("ovrtx_skel_cache")
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.prepared_hair_node_id = None
                self.live_transforms = {}
                self.last_update_kind = "cache-hit"
                scene_changed = False
        elif (
            scene_changed
            and not needs_hair_session
            and self.base_layer is not None
            and self._transform_overlay_layer is None
            and self.last_suffix == scene_suffix
            and self.temp_usd_path
            and self.current_usd_stage is not None
            and self.last_graph is not None
            and graph is not None
        ):
            if self._try_fast_postfx_delta(self.last_graph, graph):
                self.last_outline_key = outline_key
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.last_update_kind = "postfx-delta"
                scene_changed = False
            elif (fast_delta := (None if self.live_groom_modified else self._try_fast_materialx_delta(self.last_graph, graph))) is not None:
                delta_path = self._apply_material_delta(fast_delta)
                self.last_outline_key = outline_key
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.last_update_kind = "material-delta-fast"
                self.stage_pool[graph_hash] = {
                    "delta_path": delta_path,
                    "current_usd_stage": self.current_usd_stage,
                    "material_diagnostics": self.material_diagnostics,
                    "material_fallbacks": self.material_fallbacks,
                    "material_keepalive": self.material_keepalive,
                    "graph_stages": self.graph_stages,
                    "graph_stats": self.graph_stats,
                    "mx_rule_selections": self.mx_rule_selections,
                    "live_poses": getattr(self, "live_poses", {}),
                    "live_grooms": getattr(self, "live_grooms", {}),
                    "ovrtx_skel_bindings": getattr(self, "_ovrtx_skel_bindings", None),
                    "ovrtx_skel_cache": getattr(self, "_ovrtx_skel_cache", None),
                    "initial_pose_points": getattr(self, "_initial_pose_points", None),
                }
                scene_changed = False
            elif self._try_fast_hair_delta(self.last_graph, graph, float(request.get("frame", 0)), editable_hair_id):
                self.last_outline_key = outline_key
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.prepared_hair_node_id = editable_hair_id
                self.last_update_kind = "hair-delta"
                scene_changed = False
            elif self._try_fast_cache_edit_delta(self.last_graph, graph, float(request.get("frame", 0))):
                self.last_outline_key = outline_key
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.last_update_kind = "cache-delta"
                scene_changed = False
                camera_changed = True
            elif self._try_fast_pose_delta(self.last_graph, graph, float(request.get("frame", 0))):
                self.last_outline_key = outline_key
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.last_update_kind = "pose-delta"
                scene_changed = False
                camera_changed = True
            elif (groom_delta_path := self._try_fast_groom_delta(self.last_graph, graph, float(request.get("frame", 0)))) is not None:
                self.last_outline_key = outline_key
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.last_update_kind = "groom-xform-delta"
                self.stage_pool[graph_hash] = {
                    "delta_path": groom_delta_path,
                    "current_usd_stage": self.current_usd_stage,
                    "material_diagnostics": self.material_diagnostics,
                    "material_fallbacks": self.material_fallbacks,
                    "material_keepalive": self.material_keepalive,
                    "graph_stages": self.graph_stages,
                    "graph_stats": self.graph_stats,
                    "mx_rule_selections": self.mx_rule_selections,
                    "live_poses": getattr(self, "live_poses", {}),
                    "live_grooms": getattr(self, "live_grooms", {}),
                    "ovrtx_skel_bindings": getattr(self, "_ovrtx_skel_bindings", None),
                    "ovrtx_skel_cache": getattr(self, "_ovrtx_skel_cache", None),
                    "initial_pose_points": getattr(self, "_initial_pose_points", None),
                }
                scene_changed = False
        if size_changed and not scene_changed and self.last_width is not None and (
            self._transform_overlay_layer is not None or self.overlay_path or not self.temp_usd_path or not os.path.isfile(self.temp_usd_path)
        ):
            scene_changed = True
        if scene_changed or not self.temp_usd_path:
            phase("compiling USD graph", timeout=300)
            compiled_mx_selections = {}
            self.live_poses = {}
            self.live_grooms = {}
            self.live_transforms = {}
            self._transform_overlay_layer = None
            self._ovrtx_skel_bindings = None
            if graph:
                stage, self.graph_stages, self.graph_stats = usd_graph.compile_graph(
                    graph, request.get("reload", False), live_poses=self.live_poses, live_grooms=self.live_grooms,
                    live_transforms=self.live_transforms,
                    mx_selections_out=compiled_mx_selections,
                    interactive_hair=editable_hair_id is not None,
                )
                preview = usd_graph.open_hair_preview_stage(stage, graph)
                if preview != stage:
                    self.graph_stages.append(stage)
                    stage = preview
            elif stage_path and os.path.isfile(stage_path):
                source = Usd.Stage.Open(stage_path)
                if source is None: raise ValueError("Unable to open USD stage")
                wrap_layer = Sdf.Layer.CreateAnonymous("ovrtx_source_wrap.usda")
                wrap_layer.subLayerPaths.append(os.path.abspath(stage_path))
                stage = Usd.Stage.Open(wrap_layer)
                display_mat = Gf.Matrix4d(1)
                comp = request.get("comparison")
                if isinstance(comp, str):
                    try:
                        comp = json.loads(comp)
                    except Exception:
                        comp = None
                used_comparison = False
                if isinstance(comp, dict) and comp.get("handoff"):
                    mat = comp.get("inverse")
                    if (
                        isinstance(mat, list)
                        and len(mat) == 4
                        and all(isinstance(r, list) and len(r) == 4 for r in mat)
                    ):
                        cand = Gf.Matrix4d(*[float(v) for r in mat for v in r])
                        if abs(cand.GetDeterminant()) >= 1e-100:
                            display_mat = cand
                            used_comparison = True
                if not used_comparison:
                    rot = Gf.Matrix4d(1)
                    if UsdGeom.GetStageUpAxis(source) == UsdGeom.Tokens.z:
                        rot.SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), -90))
                    mpu = float(UsdGeom.GetStageMetersPerUnit(source) or 1.0)
                    if mpu <= 0.0:
                        mpu = 1.0
                    display_mat = Gf.Matrix4d().SetScale(mpu) * rot
                if display_mat != Gf.Matrix4d(1):
                    stage.SetEditTarget(wrap_layer)
                    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
                    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
                    for root_child in stage.GetPseudoRoot().GetChildren():
                        if root_child.IsA(UsdGeom.Xformable):
                            xf = UsdGeom.Xformable(root_child)
                            existing_ops = list(xf.GetOrderedXformOps())
                            conv_op = xf.AddTransformOp(opSuffix="displayConvert")
                            conv_op.Set(display_mat)
                            xf.SetXformOpOrder([conv_op] + existing_ops)
                self.graph_stages = [source, wrap_layer]
                self.graph_stats = None
            else:
                raise ValueError("A valid USD graph or stage path is required for Omniverse RTX rendering")

            self.prepared_hair_node_id = editable_hair_id
            phase("checking shader assets")
            sample = float(request.get("frame", 0))
            mdl_bin = Path(ovrtx.__file__).resolve().parent / "bin"
            mdl_library = mdl_bin / "library" / "mdl"
            scratch_root = Path(os.environ.get("ORCHFLOW_OVRTX_SCRATCH") or (tempfile.gettempdir() + "/orchflow_ovrtx_scratch"))
            self.material_diagnostics = missing_shader_assets(
                stage, sample, (mdl_bin / "mdl", mdl_library, mdl_library / "Base", scratch_root))
            issues = invalid_shader_inputs(stage, sample)
            self.material_fallbacks = []
            mode = request.get("materials", "auto")
            if mode == "auto" and (self.material_diagnostics or issues):
                phase("applying selective material fallback")
                preserve_mdl_preview(stage, self.material_diagnostics, issues)
                stage, self.material_keepalive, self.material_fallbacks = selective_material_preview(
                    stage, self.material_diagnostics, sample, issues)
                if live_hair_preview:
                    stage.SetEditTarget(stage.GetSessionLayer())
            elif mode == "scene" and (self.material_diagnostics or issues):
                raise ValueError("NVIDIA source materials contain unresolved shader assets or invalid inputs; use Auto or repair their links")

            # Ensure default studio lights only when no scene lights exist and no Light/HDRI node is in the active subgraph
            def _graph_has_lighting_node(g):
                if not isinstance(g, dict):
                    return False
                tgt = g.get("targetNodeId")
                nmap = {n.get("id"): n for n in (g.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
                if not tgt or tgt not in nmap:
                    return False
                inc = {}
                for e in (g.get("edges") or []):
                    if isinstance(e, dict) and e.get("target") and e.get("source"):
                        inc.setdefault(e["target"], []).append(e["source"])
                seen, stack = set(), [tgt]
                while stack:
                    nid = stack.pop()
                    if nid in seen:
                        continue
                    seen.add(nid)
                    n = nmap.get(nid)
                    if not n:
                        continue
                    op = n.get("operation") or (n.get("data") or {}).get("operation")
                    if op in ("light", "hdri"):
                        return True
                    stack.extend(inc.get(nid, ()))
                return False

            has_lighting_node = _graph_has_lighting_node(graph)
            has_scene_lights = any(
                ('Light' in prim.GetTypeName() or (hasattr(UsdLux, 'LightAPI') and prim.HasAPI(UsdLux.LightAPI)))
                and not prim.GetPath().HasPrefix(Sdf.Path('/Render/DefaultLights'))
                for prim in stage.Traverse(Usd.TraverseInstanceProxies())
            )
            if not has_lighting_node and not has_scene_lights:
                def_scope = UsdGeom.Scope.Define(stage, '/Render/DefaultLights')
                def_scope.GetPrim().SetActive(True)
                if not stage.GetPrimAtPath('/Render/DefaultLights/DomeLight'):
                    dome = UsdLux.DomeLight.Define(stage, "/Render/DefaultLights/DomeLight")
                    if hasattr(UsdLux, 'ShadowAPI'):
                        UsdLux.ShadowAPI.Apply(dome.GetPrim()).CreateShadowEnableAttr().Set(True)
                    dome.CreateIntensityAttr().Set(3000.0)
                    dome.CreateColorAttr().Set(Gf.Vec3f(0.95, 0.97, 1.0))
                if not stage.GetPrimAtPath('/Render/DefaultLights/KeyLight'):
                    key = UsdLux.DistantLight.Define(stage, "/Render/DefaultLights/KeyLight")
                    if hasattr(UsdLux, 'ShadowAPI'):
                        UsdLux.ShadowAPI.Apply(key.GetPrim()).CreateShadowEnableAttr().Set(True)
                    key.CreateIntensityAttr().Set(3000.0)
                    key.CreateColorAttr().Set(Gf.Vec3f(1.0, 0.98, 0.95))
                    key.AddRotateXYZOp().Set(Gf.Vec3f(-45.0, 45.0, 0.0))
            else:
                def_prim = stage.GetPrimAtPath('/Render/DefaultLights')
                if def_prim and def_prim.IsValid():
                    stage.OverridePrim(Sdf.Path('/Render/DefaultLights')).SetActive(False)

            # Define / update Cameras (perspective + orthographic)
            cam = UsdGeom.Camera.Define(stage, "/Render/Camera")
            cam.CreateProjectionAttr().Set(UsdGeom.Tokens.perspective)
            cam.CreateClippingRangeAttr().Set((0.1, 100000.0))
            ortho_cam = UsdGeom.Camera.Define(stage, "/Render/OrthoCamera")
            ortho_cam.CreateProjectionAttr().Set(UsdGeom.Tokens.orthographic)
            ortho_cam.CreateClippingRangeAttr().Set((0.1, 100000.0))

            var = UsdRender.Var.Define(stage, "/Render/RenderVar_LdrColor")
            var.CreateSourceNameAttr().Set("LdrColor")
            var.CreateDataTypeAttr().Set("color4f")

            postfx_cfg = resolve_graph_postfx(graph)
            dof_enabled = bool(request.get("dofEnabled", False))
            if render_mode == "realtime":
                self._pt_spp_at_one = False
            else:
                spp_per_iter = max(1, int(request.get("samples", 1)))
                self._pt_spp_at_one = target_spp > 1
                self._pt_max_bounces = 8

            # Define / update RenderProducts for both projections so toggling Persp/Ortho is instant
            for prod_path, cam_target, prod_dof in (
                ("/Render/RenderProduct_Viewport", "/Render/Camera", dof_enabled),
                ("/Render/RenderProduct_ViewportOrtho", "/Render/OrthoCamera", False),
            ):
                product = UsdRender.Product.Define(stage, prod_path)
                product.CreateResolutionAttr().Set(Gf.Vec2i(width, height))
                product.CreatePixelAspectRatioAttr().Set(1.0)
                product.CreateAspectRatioConformPolicyAttr().Set("adjustApertureHeight")
                product.CreateCameraRel().SetTargets([cam_target])
                product_prim = product.GetPrim()
                product_prim.CreateAttribute("omni:rtx:rendermode", Sdf.ValueTypeNames.Token, custom=False).Set(_RENDER_MODES[render_mode])
                product_prim.CreateAttribute("omni:rtx:post:dof:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(prod_dof)
                product_prim.CreateAttribute("omni:rtx:post:dof:overrideEnabled", Sdf.ValueTypeNames.Bool, custom=False).Set(prod_dof)
                if render_mode == "realtime":
                    product_prim.CreateAttribute("omni:rtx:post:aa:op", Sdf.ValueTypeNames.Token, custom=False).Set("dlss")
                    product_prim.CreateAttribute("omni:rtx:post:dlss:execMode", Sdf.ValueTypeNames.Token, custom=False).Set("quality")
                    product_prim.CreateAttribute("omni:rtx:rt:sss:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(True)
                    product_prim.CreateAttribute("omni:rtx:translucency:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(True)
                    product_prim.CreateAttribute("omni:rtx:translucency:maxRefractionBounces", Sdf.ValueTypeNames.Int, custom=False).Set(8)
                    product_prim.CreateAttribute("omni:rtx:raytracing:fractionalCutoutOpacity", Sdf.ValueTypeNames.Bool, custom=False).Set(True)
                else:
                    product_prim.CreateAttribute("omni:rtx:post:aa:op", Sdf.ValueTypeNames.Token, custom=False).Set("none")
                    product_prim.CreateAttribute("omni:rtx:pt:dlss:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(False)
                    product_prim.CreateAttribute("omni:rtx:pt:denoising:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(True)
                    product_prim.CreateAttribute("omni:rtx:pt:denoising:optix:temporal", Sdf.ValueTypeNames.Bool, custom=False).Set(False)
                    product_prim.CreateAttribute("omni:rtx:pt:limits:maxBounces", Sdf.ValueTypeNames.UInt, custom=False).Set(8)
                    product_prim.CreateAttribute("omni:rtx:pt:samplesPerIteration", Sdf.ValueTypeNames.Int, custom=False).Set(spp_per_iter)
                    product_prim.CreateAttribute("omni:rtx:pt:samplesPerPixel", Sdf.ValueTypeNames.UInt, custom=False).Set(1)
                apply_postfx_to_render_product(product_prim, postfx_cfg if postfx_cfg is not None else {})
                product.CreateOrderedVarsRel().SetTargets(["/Render/RenderVar_LdrColor"])

            previous_path = self.temp_usd_path
            # Incremental path: when only material networks/bindings differ from what ovstage
            # already holds, apply them as a small delta reference. The renderer, ovstage,
            # uploaded geometry and camera all stay alive; ovrtx restarts accumulation itself.
            if isolated_prim or hidden_prims:
                apply_viewport_visibility(stage, isolated_prim, hidden_prims)
            hdri_prim = stage.GetPrimAtPath("/OrchFlowHDRI")
            if hdri_prim and hdri_prim.IsValid():
                xf = UsdGeom.Xformable(hdri_prim)
                spin = 0.0
                for op in xf.GetOrderedXformOps():
                    if op.GetOpType() == UsdGeom.XformOp.TypeRotateY:
                        val = op.Get()
                        if val is not None:
                            spin = float(val)
                xf.ClearXformOpOrder()
                if up_axis == "Y":
                    xf.AddRotateXOp(opSuffix="ovrtxUp").Set(-90.0)
                if abs(spin) > 1e-6:
                    xf.AddRotateZOp(opSuffix="ovrtxSpin").Set(spin)
            # Prevent transmissive shells from casting opaque shadows inward in Realtime RTX (and thick dielectrics in PT).
            # Note: thin_walled surfaces in PathTracing already pass light physically without doNotCastShadows (which would double-light them).
            from pxr import UsdShade
            mat_trans_cache = {}
            for prim in stage.Traverse():
                if not prim.IsA(UsdGeom.Mesh):
                    continue
                mat, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
                if not mat:
                    continue
                mat_path = mat.GetPath()
                cached_flags = mat_trans_cache.get(mat_path)
                if cached_flags is None:
                    is_transmissive = False
                    is_thin_walled = False
                    for sp in Usd.PrimRange(mat.GetPrim()):
                        if not sp.IsA(UsdShade.Shader):
                            continue
                        sh = UsdShade.Shader(sp)
                        tr_inp = sh.GetInput("transmission")
                        if tr_inp and (tr_inp.Get() or 0) > 0.5:
                            is_transmissive = True
                        tw_inp = sh.GetInput("thin_walled")
                        if tw_inp and bool(tw_inp.Get()):
                            is_thin_walled = True
                    cached_flags = (is_transmissive, is_thin_walled)
                    mat_trans_cache[mat_path] = cached_flags
                is_transmissive, is_thin_walled = cached_flags
                if is_transmissive and (render_mode == "realtime" or not is_thin_walled):
                    UsdGeom.PrimvarsAPI(prim).CreatePrimvar("doNotCastShadows", Sdf.ValueTypeNames.Bool, UsdGeom.Tokens.constant).Set(True)
            phase("flattening private USD preview")
            bake_layer = self._bake_stage_skel_for_ovstage(stage, sample)
            try:
                flat = stage.Flatten()
                if bake_layer is not None:
                    self._strip_skel_from_flat(flat)
            finally:
                if bake_layer is not None:
                    bake_layer.Clear()
                    try:
                        stage.GetSessionLayer().subLayerPaths.remove(bake_layer.identifier)
                    except Exception:
                        pass
            incremental = False
            delta_path = None
            prev_postfx_cfg = getattr(self, "_last_postfx_cfg", None)
            postfx_tokens_same = (
                dict(_POSTFX_DEFAULTS, **(prev_postfx_cfg or {})).get("tonemapOperator") == dict(_POSTFX_DEFAULTS, **(postfx_cfg or {})).get("tonemapOperator")
                and dict(_POSTFX_DEFAULTS, **(prev_postfx_cfg or {})).get("tonemapColorMode") == dict(_POSTFX_DEFAULTS, **(postfx_cfg or {})).get("tonemapColorMode")
                and dict(_POSTFX_DEFAULTS, **(prev_postfx_cfg or {})).get("autoExposureHistogramFilter") == dict(_POSTFX_DEFAULTS, **(postfx_cfg or {})).get("autoExposureHistogramFilter")
            )
            if (not self.live_groom_modified and self.base_layer is not None and self.last_suffix == scene_suffix and previous_path
                    and self.current_usd_stage is not None and not request.get("reload") and postfx_tokens_same):
                phase("diffing material changes")
                delta = ovrtx_material_delta.plan(self.base_layer, flat)
                if delta is not None:
                    try:
                        delta_path = self._apply_material_delta(delta)
                        if getattr(self, "_initial_pose_points", None):
                            self._swap_pose_overlay(self._initial_pose_points, apply_changes=True)
                        if prev_postfx_cfg != postfx_cfg:
                            if not self._write_postfx_to_ovstage(prev_postfx_cfg, postfx_cfg):
                                raise RuntimeError("PostFX live attribute sync failed")
                        self._last_postfx_cfg = postfx_cfg
                        incremental = True
                    except Exception as error:
                        sys.stderr.write(f"Ovrtx incremental material update failed; rebuilding scene: {error}\n")
            if incremental:
                self.current_usd_stage = stage
                self.mx_rule_selections = compiled_mx_selections
                self.last_outline_key = outline_key
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.last_update_kind = "material-delta"
                self.stage_pool[graph_hash] = {
                    "delta_path": delta_path,
                    "current_usd_stage": stage,
                    "material_diagnostics": self.material_diagnostics,
                    "material_fallbacks": self.material_fallbacks,
                    "material_keepalive": self.material_keepalive,
                    "graph_stages": self.graph_stages,
                    "graph_stats": self.graph_stats,
                    "mx_rule_selections": self.mx_rule_selections,
                    "live_poses": getattr(self, "live_poses", {}),
                    "live_grooms": getattr(self, "live_grooms", {}),
                    "ovrtx_skel_bindings": getattr(self, "_ovrtx_skel_bindings", None),
                    "ovrtx_skel_cache": getattr(self, "_ovrtx_skel_cache", None),
                    "initial_pose_points": getattr(self, "_initial_pose_points", None),
                }
            else:
                base_key = None
                if graph:
                    base_nodes = [
                        n for n in graph.get("nodes", [])
                        if (n.get("operation") or (n.get("data") or {}).get("operation"))
                        in ("source", "blender", "application", "metahuman")
                    ]
                    if base_nodes:
                        base_key = json.dumps(base_nodes, sort_keys=True) + scene_suffix
                preserve_camera = (
                    not request.get("reload")
                    and base_key is not None
                    and base_key == self.last_base_key
                    and hasattr(self, "scene_camera_anchor")
                )
                self.last_base_key = base_key
                self.last_update_kind = "full-rebuild"
                self.live_groom_modified = False
                if not preserve_camera:
                    # Compute bounds of visible renderable geometry, excluding synthetic /Render prims
                    bounds = compute_visible_geometry_bounds(stage, Usd.TimeCode(sample))
                    self.scene_camera_anchor = ((Gf.Vec3d(0), 1.0) if bounds.IsEmpty() else
                        (bounds.GetMidpoint(), max(bounds.GetSize().GetLength() * .5, .001)))
                flat_stage = Usd.Stage.Open(flat)
                self.current_usd_stage = stage
                self._update_camera(flat_stage, request, width, height)
                phase("writing temporary preview snapshot")
                new_usd_path = export_preview_snapshot(flat)
                old_pose_overlay = getattr(self, "_pose_overlay_path", None)
                self._pose_overlay_path = None

                old_overlay_path = self.overlay_path
                old_temp_path = self.temp_usd_path
                self._flush_stage_pool()
                if self.stage is not None and (
                    self.last_graph_hash is not None
                    or self.ordinal > 0
                    or self.overlay_handle is not None
                    or getattr(self, "_pose_overlay_handle", None) is not None
                ):
                    try:
                        self.renderer.detach_ovstage()
                    except Exception:
                        pass
                    try:
                        self.stage.destroy()
                    except Exception:
                        pass
                    self.stage = ovstage.Stage("orchflow_ovrtx_" + uuid.uuid4().hex)
                    self.renderer.attach_ovstage(self.stage)
                    self.overlay_handle = None
                    self._pose_overlay_handle = None
                    self._pose_overlay_seq = 0
                    self.ordinal = 0
                for old_fp in (old_overlay_path, old_temp_path, old_pose_overlay):
                    if old_fp and os.path.isfile(old_fp):
                        try:
                            os.remove(old_fp)
                        except OSError:
                            pass
                self.temp_usd_path = new_usd_path
                self.ordinal += 1
                phase("populating NVIDIA scene")
                ovstage.population.open_usd(self.stage, self.temp_usd_path, ordinal=self.ordinal,
                    time=float(request.get("frame", 0)) / stage.GetTimeCodesPerSecond())
                if getattr(self, "_initial_pose_points", None):
                    self._swap_pose_overlay(self._initial_pose_points, apply_changes=False)
                    ovstage.population.apply_usd_changes(self.stage, ordinal=self.ordinal)
                phase("computing transform hierarchy")
                self.stage.compute_hierarchy(self.ordinal, self.ordinal,
                    model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL)
                self.stage.advance_write_floor(self.ordinal).wait()

                self.overlay_path = None
                self._pin_mat_overlay_active = False
                self._pre_pin_overlay_path = None
                self._cache_mask_overlay_active = False
                self._pre_mask_overlay_path = None
                self.base_layer = flat
                self._last_postfx_cfg = postfx_cfg
                self.mx_rule_selections = compiled_mx_selections
                self.last_suffix = scene_suffix
                self.current_usd_stage = stage
                self.last_outline_key = outline_key
                self.last_graph_hash = graph_hash
                self.last_static_scene_key = static_scene_key
                self.last_graph = graph
                self.last_width = width
                self.last_height = height
                self.stage_pool[graph_hash] = {
                    "delta_path": None,
                    "current_usd_stage": stage,
                    "material_diagnostics": self.material_diagnostics,
                    "material_fallbacks": self.material_fallbacks,
                    "material_keepalive": self.material_keepalive,
                    "graph_stages": self.graph_stages,
                    "graph_stats": self.graph_stats,
                    "mx_rule_selections": self.mx_rule_selections,
                    "live_poses": getattr(self, "live_poses", {}),
                    "live_grooms": getattr(self, "live_grooms", {}),
                    "ovrtx_skel_bindings": getattr(self, "_ovrtx_skel_bindings", None),
                    "ovrtx_skel_cache": getattr(self, "_ovrtx_skel_cache", None),
                    "initial_pose_points": getattr(self, "_initial_pose_points", None),
                }

        live_pose = request.get("livePose")
        live_pose_bones = None
        if isinstance(live_pose, dict) and self.current_usd_stage is not None and self.stage is not None:
            from pxr import UsdSkel, Vt
            lp_node = live_pose.get("nodeId")
            lp_entry = live_pose.get("entryId")
            lp_skel = live_pose.get("skeletonPath")
            lp_idx = live_pose.get("jointIndex")
            lp_base = live_pose.get("baseRotation")
            lp_off = live_pose.get("rotationOffset")
            targets = getattr(self, "live_poses", {}).get(lp_node) if lp_node else None
            req_frame = float(request.get("frame", 0))
            if (
                targets
                and isinstance(lp_idx, int)
                and isinstance(lp_base, list) and len(lp_base) == 4
                and isinstance(lp_off, list) and len(lp_off) == 3
            ):
                for _row_id, t_entry_id, t_skel_path, output in targets:
                    if t_entry_id == lp_entry and t_skel_path == lp_skel:
                        rot_attr = output.GetRotationsAttr()
                        rots = list(rot_attr.Get() or [])
                        if 0 <= lp_idx < len(rots):
                            base = Gf.Rotation(Gf.Quatd(float(lp_base[0]), Gf.Vec3d(float(lp_base[1]), float(lp_base[2]), float(lp_base[3]))))
                            offset = (
                                Gf.Rotation(Gf.Vec3d(1, 0, 0), float(lp_off[0]))
                                * Gf.Rotation(Gf.Vec3d(0, 1, 0), float(lp_off[1]))
                                * Gf.Rotation(Gf.Vec3d(0, 0, 1), float(lp_off[2]))
                            )
                            qd = (offset * base).GetQuat()
                            im = qd.GetImaginary()
                            rots[lp_idx] = Gf.Quatf(float(qd.GetReal()), float(im[0]), float(im[1]), float(im[2]))
                            rot_arr = Vt.QuatfArray(rots)
                            out_stage = output.GetPrim().GetStage()
                            with Usd.EditContext(out_stage, out_stage.GetRootLayer()):
                                rot_attr.Clear()
                                rot_attr.Set(rot_arr)
                                rot_attr.Set(rot_arr, Usd.TimeCode(req_frame))
                                rot_attr.Set(rot_arr, Usd.TimeCode(req_frame + 1e-4))
                                import pose_node as _pn
                                _pn.apply_body_riglogic_to_anim(output, self.current_usd_stage.GetPrimAtPath(lp_skel), req_frame)
                            self._apply_live_pose_to_ovstage([lp_skel], req_frame)
                            camera_changed = True
                            self.last_update_kind = "live-pose"
                            bindings = getattr(self, "_ovrtx_skel_bindings", None) or {}
                            entry = bindings.get(lp_skel)
                            skel_prim = self.current_usd_stage.GetPrimAtPath(lp_skel)
                            if entry and skel_prim and skel_prim.IsValid():
                                sq, _meshes = entry
                                w = UsdGeom.XformCache(req_frame).GetLocalToWorldTransform(skel_prim)
                                wmats = [m * w for m in sq.ComputeJointSkelTransforms(req_frame)]
                                live_pose_bones = {
                                    "worldPoints": [list(wm.ExtractTranslation()) for wm in wmats],
                                    "worldAxes": [
                                        [
                                            list(wm.TransformDir(Gf.Vec3d(1, 0, 0)).GetNormalized()),
                                            list(wm.TransformDir(Gf.Vec3d(0, 1, 0)).GetNormalized()),
                                            list(wm.TransformDir(Gf.Vec3d(0, 0, 1)).GetNormalized()),
                                        ]
                                        for wm in wmats
                                    ],
                                }
                        break

        # Explicit frame-by-frame cloth simulation step (triggered only by the Simulate button, never Play)
        cloth_sim_step = request.get("clothSimStep")
        if (
            isinstance(cloth_sim_step, dict)
            and cloth_sim_step.get("active")
            and self.current_usd_stage is not None
            and self.stage is not None
        ):
            active_graph = graph or self.last_graph
            if isinstance(active_graph, dict):
                import cache_edit as _ce

                sim_frame = int(round(float(request.get("frame", 0))))
                origin_frame = cloth_sim_step.get("originFrame")
                reset_sim = bool(cloth_sim_step.get("reset", False))
                all_sim_updates = []
                for n in active_graph.get("nodes") or []:
                    if n.get("operation") == "cache_edit":
                        ce_cfg = n.get("cacheEdit")
                        if isinstance(ce_cfg, dict):
                            ups = _ce.step_cloth_simulation(
                                self.current_usd_stage,
                                ce_cfg,
                                sim_frame,
                                origin_frame=origin_frame,
                                reset=reset_sim,
                                session_id=cloth_sim_step.get("session"),
                            )
                            if ups:
                                all_sim_updates.extend(ups)
                if all_sim_updates:
                    self._write_hair_points_to_ovstage(all_sim_updates)
                    self.last_update_kind = "cloth-sim-step"
                    camera_changed = True

        release_brush = request.get("hairBrush")
        unchanged_brush_release = (
            isinstance(release_brush, dict) and release_brush.get("commitUsd") and release_brush.get("settleOnly")
            and self.last_brush_upload is not None
            and self.last_brush_upload[0] == (self.stage, self.current_usd_stage, self.ordinal)
            and self.last_brush_frame is not None
            and self.last_brush_frame[0] == usd_graph.groom_frame_view(request)
        )
        if size_changed and not scene_changed and self.last_width is not None and self.temp_usd_path and os.path.isfile(self.temp_usd_path):
            phase("resizing NVIDIA viewport", timeout=120)
            # Recreating the ovrtx renderer on resize avoids omni.rtx GPU pagefaults
            # triggered by mutating RenderProduct.resolution on a live ovstage.
            # Export to a fresh temp path so ovstage's internal SdfLayer cache never
            # reuses the stale resolution from the previous filename.
            old_resized_path = self.temp_usd_path
            temp_layer = Sdf.Layer.FindOrOpen(self.temp_usd_path)
            if temp_layer is not None:
                for prod_path in _VIEWPORT_PRODUCTS:
                    rp = temp_layer.GetPrimAtPath(prod_path)
                    if rp and "resolution" in rp.properties:
                        rp.properties["resolution"].default = Gf.Vec2i(width, height)
                    if rp and render_mode == "pathTracing" and target_spp > 1 and "omni:rtx:pt:samplesPerPixel" in rp.properties:
                        rp.properties["omni:rtx:pt:samplesPerPixel"].default = 1
                new_resized_path = export_preview_snapshot(temp_layer)
                self.temp_usd_path = new_resized_path
                if self.base_layer is not None:
                    for prod_path in _VIEWPORT_PRODUCTS:
                        rp_b = self.base_layer.GetPrimAtPath(prod_path)
                        if rp_b and "resolution" in rp_b.properties:
                            rp_b.properties["resolution"].default = Gf.Vec2i(width, height)
                del temp_layer
                if old_resized_path and old_resized_path != new_resized_path and os.path.isfile(old_resized_path):
                    try:
                        os.remove(old_resized_path)
                    except OSError:
                        pass
            if self.stage is not None:
                try:
                    self.stage.destroy()
                except Exception:
                    pass
                self.stage = None
            if self.renderer is not None:
                try:
                    self.renderer.destroy()
                except Exception:
                    pass
                self.renderer = None
            self._ensure_initialized()
            self.overlay_handle = None
            self._pose_overlay_handle = None
            self.ordinal = 1
            ovstage.population.open_usd(
                self.stage,
                self.temp_usd_path,
                ordinal=self.ordinal,
                time=float(request.get("frame", 0)) / (self.current_usd_stage.GetTimeCodesPerSecond() if self.current_usd_stage else 24.0),
            )
            reattached_overlay = False
            if self.overlay_path and os.path.isfile(self.overlay_path):
                self.overlay_handle = ovstage.population.add_usd_reference(self.stage, self.overlay_path, "/")
                reattached_overlay = True
            pose_path = getattr(self, "_pose_overlay_path", None)
            if pose_path and os.path.isfile(pose_path):
                self._pose_overlay_handle = ovstage.population.add_usd_reference(self.stage, pose_path, "/")
                reattached_overlay = True
            if reattached_overlay:
                ovstage.population.apply_usd_changes(self.stage, ordinal=self.ordinal)
            self.stage.compute_hierarchy(
                self.ordinal,
                self.ordinal,
                model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
            )
            self.stage.advance_write_floor(self.ordinal).wait()
            if self.current_usd_stage is not None and "hair_sim" in sys.modules:
                _hs = sys.modules["hair_sim"]
                _active_sessions = getattr(_hs, "_ACTIVE_SIM_SESSIONS", None) or {}
                if _active_sessions:
                    _live_hair_updates = []
                    for _p_str, _sess in _active_sessions.items():
                        _prim = self.current_usd_stage.GetPrimAtPath(_p_str)
                        if _prim and _prim.IsValid() and _prim.IsA(UsdGeom.BasisCurves):
                            _pts_np = _hs._session_local_points(_sess, np)
                            if _pts_np is not None and len(_pts_np) > 0:
                                _live_hair_updates.append((_p_str, _pts_np))
                    if _live_hair_updates:
                        self._write_hair_points_to_ovstage(_live_hair_updates)
            self._pt_spp_at_one = (render_mode == "pathTracing" and target_spp > 1)
            self._pt_max_bounces = 8
            self._pt_stall_count = 0
            camera_changed = True
            # Keep size_changed=False for RenderProduct, but update Camera aperture below.
            size_changed = False
            update_cam_aperture = True
        else:
            update_cam_aperture = size_changed or is_ortho or (projection != getattr(self, "_last_projection", None))

        if (
            not scene_changed
            or self.last_update_kind in ("material-delta", "material-delta-fast", "cache-hit")
        ) and (
            size_changed
            or camera_changed
            or (settled_from_interactive and not unchanged_brush_release)
            or (switched_scene and self.last_update_kind in ("material-delta", "material-delta-fast", "cache-hit"))
        ):
            cam_params = self._compute_camera_params(request, width, height)
            self.ordinal += 1
            matrix = cam_params["matrix"]
            # Never replace the retained USD population source with a camera-only
            # document: future hierarchy updates must still see the full scene.
            with ovstage.PathDictionary(self.stage) as paths:
                cam_plist = paths.create_path_list_from_strings([active_cam_path])
                try:
                    cam_query = self.stage.query_from_path_list(cam_plist)
                    self.stage.write_attribute(cam_query, "omni:xform", self.ordinal,
                        np.asarray(matrix, dtype=np.float64).reshape(1, 4, 4), is_array=False,
                        semantic=ovstage.AttributeSemantic.MATRIX).wait()
                    self.stage.write_attribute(cam_query, "omni:resetXformStack", self.ordinal,
                        np.asarray([True], dtype=np.bool_), is_array=False, semantic=0).wait()
                    self.stage.write_attribute(cam_query, "clippingRange", self.ordinal,
                        np.asarray([cam_params["clipping_range"]], dtype=np.float32),
                        is_array=False, semantic=0).wait()
                    self.stage.write_attribute(cam_query, "focalLength", self.ordinal,
                        np.asarray([cam_params["focal_length"]], dtype=np.float32),
                        is_array=False, semantic=0).wait()
                    self.stage.write_attribute(cam_query, "focusDistance", self.ordinal,
                        np.asarray([cam_params["focus_distance"]], dtype=np.float32),
                        is_array=False, semantic=0).wait()
                    self.stage.write_attribute(cam_query, "fStop", self.ordinal,
                        np.asarray([cam_params["f_stop"]], dtype=np.float32),
                        is_array=False, semantic=0).wait()
                    if update_cam_aperture:
                        self.stage.write_attribute(cam_query, "horizontalAperture", self.ordinal,
                            np.asarray([cam_params["horizontal_aperture"]], dtype=np.float32),
                            is_array=False, semantic=0).wait()
                        self.stage.write_attribute(cam_query, "verticalAperture", self.ordinal,
                            np.asarray([cam_params["vertical_aperture"]], dtype=np.float32),
                            is_array=False, semantic=0).wait()
                finally:
                    paths.destroy_path_list(cam_plist)
                for prod_path in _VIEWPORT_PRODUCTS:
                    prod_plist = paths.create_path_list_from_strings([prod_path])
                    try:
                        prod_query = self.stage.query_from_path_list(prod_plist)
                        if size_changed:
                            self.stage.write_attribute(prod_query, "resolution", self.ordinal,
                                np.asarray([[width, height]], dtype=np.int32), is_array=False, semantic=0).wait()
                            if render_mode == "pathTracing" and target_spp > 1 and not getattr(self, "_pt_spp_at_one", False):
                                self.stage.write_attribute(prod_query, "omni:rtx:pt:samplesPerPixel", self.ordinal,
                                    np.asarray([1], dtype=np.uint32), is_array=False, semantic=0).wait()
                        prod_dof = cam_params["dof_enabled"] if prod_path == "/Render/RenderProduct_Viewport" else False
                        self.stage.write_attribute(prod_query, "omni:rtx:post:dof:enabled", self.ordinal,
                            np.asarray([prod_dof], dtype=np.bool_), is_array=False, semantic=0).wait()
                        self.stage.write_attribute(prod_query, "omni:rtx:post:dof:overrideEnabled", self.ordinal,
                            np.asarray([prod_dof], dtype=np.bool_), is_array=False, semantic=0).wait()
                    finally:
                        paths.destroy_path_list(prod_plist)
                if size_changed and render_mode == "pathTracing" and target_spp > 1:
                    self._pt_spp_at_one = True
            phase("computing transform hierarchy", timeout=60, report=False)
            self.stage.compute_hierarchy(
                self.ordinal,
                self.ordinal,
                model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
            )
            self.stage.advance_write_floor(self.ordinal).wait()
        self.last_camera_key = camera_key
        self._last_projection = projection
        self.last_width, self.last_height = width, height

        # 2. Update selection outline if requested
        requested_selection = request.get("selection", [])
        if not isinstance(requested_selection, list):
            requested_selection = [requested_selection] if requested_selection else []
        requested_selection = [str(p) for p in requested_selection if p]
        if switched_scene or scene_changed:
            self._material_users_cache = None

        if requested_selection != self.last_requested_selection or switched_scene or scene_changed:
            object_outline = []
            material_outline = []
            if requested_selection and self.current_usd_stage:
                from pxr import UsdShade
                for sel_path in requested_selection:
                    p = self.current_usd_stage.GetPrimAtPath(Sdf.Path(sel_path))
                    if not p:
                        continue
                    if p.IsA(UsdShade.Material):
                        if self._material_users_cache is None:
                            self._material_users_cache = usd_graph.compute_material_users(self.current_usd_stage)
                        material_outline.extend(sorted(self._material_users_cache.get(str(p.GetPath()), ())))
                    elif p.IsA(UsdGeom.Mesh):
                        object_outline.append(str(p.GetPath()))
                    else:
                        for child in self.current_usd_stage.Traverse(Usd.TraverseInstanceProxies()):
                            if child.GetPath().HasPrefix(p.GetPath()):
                                if child.IsA(UsdGeom.Mesh):
                                    object_outline.append(str(child.GetPath()))
                                elif child.IsA(UsdShade.Material):
                                    if self._material_users_cache is None:
                                        self._material_users_cache = usd_graph.compute_material_users(self.current_usd_stage)
                                    material_outline.extend(sorted(self._material_users_cache.get(str(child.GetPath()), ())))
            object_outline = list(dict.fromkeys(object_outline))
            obj_set = set(object_outline)
            material_outline = [m for m in dict.fromkeys(material_outline) if m not in obj_set]
            to_outline = object_outline + material_outline
            # Remove outline on previous selection meshes
            if self.active_selection:
                try:
                    self.renderer.set_selection_outline_group_strings(self.active_selection, 0)
                except Exception:
                    pass
            # Set outline on new selection meshes (group 1 = object, group 2 = material)
            if object_outline:
                try:
                    self.renderer.set_selection_outline_group_strings(object_outline, 1)
                except Exception:
                    pass
            if material_outline:
                try:
                    self.renderer.set_selection_outline_group_strings(material_outline, 2)
                except Exception:
                    pass
            self.last_requested_selection = requested_selection
            self.active_selection = to_outline

        scene_ms = (time.perf_counter() - t_scene_start) * 1000
        t_step_start = time.perf_counter()
        # 3. Step the Omniverse RTX renderer
        products = {active_product}

        # Enqueue pick query if requested (e.g. Ctrl+click on viewport)
        pick_coord = request.get("pick")
        if isinstance(pick_coord, (list, tuple)) and len(pick_coord) == 2:
            try:
                u, v = float(pick_coord[0]), float(pick_coord[1])
                # NDC rectangle for pixel query clamped to [0, 1]
                half_w, half_h = 0.5 / max(1, width), 0.5 / max(1, height)
                l = max(0.0, min(0.999, u - half_w))
                t = max(0.0, min(0.999, v - half_h))
                r = max(0.001, min(1.0, u + half_w))
                b = max(0.001, min(1.0, v + half_h))
                self.renderer.enqueue_pick_query(active_product, l, t, r, b)
            except Exception as e:
                sys.stderr.write(f"Ovrtx enqueue pick query error: {e}\n")

        has_procedural = bool(
            graph
            and any(
                (n.get("data") or {}).get("kind") == "output"
                and (n.get("data") or {}).get("enabled", True) is not False
                for n in ((graph.get("proceduralGraph") or {}).get("nodes") or [])
            )
        )
        compiling_label = (
            "compiling procedural shaders & rendering frame"
            if has_procedural
            else "compiling shaders & rendering frame"
        )
        step_timeout = 180 if (scene_changed or getattr(self, "_first_refinement_pending", False)) else 60
        if scene_changed:
            self._first_refinement_pending = True
            if self.last_update_kind == "material-delta-fast":
                phase_name = "updating procedural shaders & rendering frame" if has_procedural else "rendering NVIDIA frame"
            else:
                phase_name = compiling_label
            should_report = True
        elif getattr(self, "_first_refinement_pending", False):
            phase_name = compiling_label
            should_report = True
        else:
            phase_name = "rendering NVIDIA frame"
            should_report = bool(switched_scene)
        phase(
            phase_name,
            timeout=step_timeout,
            report=should_report,
        )
        need_pt_spp_raise = (
            render_mode == "pathTracing"
            and bool(getattr(self, "_pt_spp_at_one", False))
            and target_spp > 1
        )
        hair_brush = request.get("hairBrush")
        upload_context = (self.stage, self.current_usd_stage, self.ordinal)
        if self.last_brush_upload is not None and self.last_brush_upload[0] != upload_context:
            self.last_brush_upload = None
            self.last_brush_frame = None
        previous_brush_frame = self.last_brush_frame
        hair_brushed = False
        hair_topology_changed = False
        reused_all_brush_points = False
        committed_groom_deltas = None
        committed_groom_handle = None
        committed_groom_node_id = None
        hair_sim_ms = None
        hair_upload_ms = None
        groom_encode_ms = None
        hair_stats = None
        hair_pinned_count = None
        active_graph = graph if isinstance(graph, dict) else getattr(self, "last_graph", None)
        hair_cfg = None
        tid = None
        if isinstance(active_graph, dict):
            tid = (
                (hair_brush.get("nodeId") if isinstance(hair_brush, dict) else None)
                or request.get("activeHairNodeId")
                or active_graph.get("targetNodeId")
            )
            for n in active_graph.get("nodes") or []:
                if n.get("id") == tid and n.get("operation") == "hair":
                    hair_cfg = n.get("hair")
                    break
        brush_seq = hair_brush.get("seq") if isinstance(hair_brush, dict) else None
        is_new_brush_seq = (brush_seq is None) or (brush_seq != getattr(self, "last_hair_brush_seq", None))
        if (
            isinstance(hair_brush, dict)
            and is_new_brush_seq
            and self.stage is not None
            and self.current_usd_stage is not None
            and hasattr(self, "last_camera_basis")
        ):
            self.last_hair_brush_seq = brush_seq
            if isinstance(hair_cfg, dict) and hair_cfg.get("simEnabled"):
                _wdir = os.path.dirname(os.path.abspath(__file__))
                if _wdir not in sys.path:
                    sys.path.insert(0, _wdir)
                import hair_sim
                commit_usd = bool(hair_brush.get("commitUsd", False))
                uploaded_points = self.last_brush_upload[1] if self.last_brush_upload else None
                self.last_brush_upload = None
                self.last_brush_frame = None
                unchanged_uploads = set()
                hair_stats = {"totalStrands": 0, "guideStrands": 0, "fullSolveStrands": 0}
                hair_sim_started = time.perf_counter()
                with Usd.EditContext(self.current_usd_stage, self.current_usd_stage.GetSessionLayer()):
                    updates = hair_sim.step_hair_brush(
                        self.current_usd_stage,
                        hair_cfg,
                        usd_graph._hair_query_matches,
                        hair_brush,
                        self.last_camera_basis,
                        write_usd=commit_usd,
                        return_gpu=(not commit_usd),
                        diagnostics=hair_stats,
                        uploaded_points=uploaded_points,
                        unchanged_uploads=unchanged_uploads,
                    )
                hair_sim_ms = (time.perf_counter() - hair_sim_started) * 1000
                hair_topology_changed = hair_sim.pop_topology_changed(
                    self.current_usd_stage,
                    hair_cfg,
                    usd_graph._hair_query_matches,
                )
                if updates or hair_topology_changed:
                    self.pending_groom_graph = None
                    self.pending_groom_frame = None
                    self.live_groom_modified = True
                if hair_topology_changed:
                    from pxr import Gf as _Gf_t, Sdf as _Sdf_t, UsdShade as _UsdShade_t, Vt as _Vt_t
                    base_stage = Usd.Stage.Open(self.base_layer) if self.base_layer is not None else self.current_usd_stage
                    if self.base_layer is not None and base_stage is not None:
                        for src_prim in self.current_usd_stage.Traverse():
                            if src_prim.IsInstanceProxy() or not src_prim.IsA(UsdGeom.BasisCurves):
                                continue
                            dst_prim = base_stage.GetPrimAtPath(src_prim.GetPath())
                            if not dst_prim or not dst_prim.IsValid():
                                dst_prim = UsdGeom.BasisCurves.Define(base_stage, src_prim.GetPath()).GetPrim()
                                UsdGeom.Xformable(dst_prim).SetResetXformStack(True)
                                _dst_bc = UsdGeom.BasisCurves(dst_prim)
                                _dst_bc.CreateTypeAttr().Set(UsdGeom.Tokens.cubic)
                                _dst_bc.CreateBasisAttr().Set(UsdGeom.Tokens.bspline)
                                _dst_bc.CreateWrapAttr().Set(UsdGeom.Tokens.pinned)
                                _dst_bc.SetWidthsInterpolation(
                                    UsdGeom.BasisCurves(src_prim).GetWidthsInterpolation() or UsdGeom.Tokens.vertex
                                )
                                dc_primvar = UsdGeom.PrimvarsAPI(dst_prim).CreatePrimvar(
                                    "displayColor", _Sdf_t.ValueTypeNames.Color3fArray, UsdGeom.Tokens.constant
                                )
                                dc_primvar.Set(_Vt_t.Vec3fArray([_Gf_t.Vec3f(0.14, 0.08, 0.05)]))
                                _mat = _UsdShade_t.Material.Define(base_stage, "/Scene/_OrchFlowCreatedHairMat")
                                _sh = _UsdShade_t.Shader.Define(base_stage, "/Scene/_OrchFlowCreatedHairMat/Surface")
                                _sh.CreateIdAttr("UsdPreviewSurface")
                                _sh.CreateInput("diffuseColor", _Sdf_t.ValueTypeNames.Color3f).Set(_Gf_t.Vec3f(0.14, 0.08, 0.05))
                                _sh.CreateInput("roughness", _Sdf_t.ValueTypeNames.Float).Set(0.42)
                                _mat.CreateSurfaceOutput().ConnectToSource(_sh.ConnectableAPI(), "surface")
                                _UsdShade_t.MaterialBindingAPI.Apply(dst_prim).Bind(_mat)
                            sc = UsdGeom.BasisCurves(src_prim)
                            dc = UsdGeom.BasisCurves(dst_prim)
                            c_val = sc.GetCurveVertexCountsAttr().Get()
                            p_val = sc.GetPointsAttr().Get()
                            w_val = sc.GetWidthsAttr().Get()
                            if c_val is not None:
                                dc.CreateCurveVertexCountsAttr().Set(c_val)
                            if p_val is not None:
                                dc.CreatePointsAttr().Set(p_val)
                                try:
                                    import numpy as _np_ext
                                    _pa = _np_ext.asarray(p_val, dtype=_np_ext.float32)
                                    if len(_pa) > 0:
                                        dc.CreateExtentAttr().Set(_Vt_t.Vec3fArray([
                                            _Gf_t.Vec3f(*_pa.min(axis=0).tolist()),
                                            _Gf_t.Vec3f(*_pa.max(axis=0).tolist()),
                                        ]))
                                except Exception:
                                    pass
                            if w_val is not None:
                                dc.CreateWidthsAttr().Set(w_val)
                    if self.base_layer is not None:
                        for prod_path in _VIEWPORT_PRODUCTS:
                            rp_b = self.base_layer.GetPrimAtPath(prod_path)
                            if rp_b and "resolution" in rp_b.properties:
                                rp_b.properties["resolution"].default = _Gf_t.Vec2i(width, height)
                    target_stage = base_stage if base_stage is not None else self.current_usd_stage
                    self._update_camera(target_stage, request, width, height)
                    flat = self.base_layer if self.base_layer is not None else target_stage.Flatten()
                    new_usd_path = export_preview_snapshot(flat)
                    old_temp_path = self.temp_usd_path
                    self.temp_usd_path = new_usd_path
                    self.base_layer = flat
                    self._flush_stage_pool()
                    try:
                        self.renderer.detach_ovstage()
                    except Exception:
                        pass
                    try:
                        self.stage.destroy()
                    except Exception:
                        pass
                    self.stage = ovstage.Stage("orchflow_ovrtx_" + uuid.uuid4().hex)
                    self.renderer.attach_ovstage(self.stage)
                    self.overlay_handle = None
                    self._pose_overlay_handle = None
                    self._pose_overlay_seq = 0
                    self.ordinal = 1
                    ovstage.population.open_usd(
                        self.stage,
                        self.temp_usd_path,
                        ordinal=self.ordinal,
                        time=float(request.get("frame", 0)) / self.current_usd_stage.GetTimeCodesPerSecond(),
                    )
                    pose_path = getattr(self, "_pose_overlay_path", None)
                    if pose_path and os.path.isfile(pose_path):
                        self._pose_overlay_handle = ovstage.population.add_usd_reference(self.stage, pose_path, "/")
                        ovstage.population.apply_usd_changes(self.stage, ordinal=self.ordinal)
                    self.stage.compute_hierarchy(
                        self.ordinal,
                        self.ordinal,
                        model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
                    )
                    self.stage.advance_write_floor(self.ordinal).wait()
                    self.overlay_path = None
                    self._pt_spp_at_one = (render_mode == "pathTracing" and target_spp > 1)
                    self._pt_max_bounces = 8
                    self._pt_stall_count = 0
                    self._pin_mat_overlay_active = False
                    self._pre_pin_overlay_path = None
                    self._cache_mask_overlay_active = False
                    self._pre_mask_overlay_path = None
                    if old_temp_path and os.path.isfile(old_temp_path):
                        try:
                            os.remove(old_temp_path)
                        except OSError:
                            pass
                    self.last_update_kind = "hair-brush"
                    hair_brushed = True
                elif updates:
                    render_updates = [(path, points) for path, points in updates if path not in unchanged_uploads]
                    reused_all_brush_points = not render_updates
                    if render_updates:
                        upload_started = time.perf_counter()
                        self._write_hair_points_to_ovstage(render_updates)
                        hair_upload_ms = (time.perf_counter() - upload_started) * 1000
                        self.last_update_kind = "hair-brush"
                        hair_brushed = True
                        if not commit_usd:
                            self.last_brush_upload = (
                                (self.stage, self.current_usd_stage, self.ordinal), dict(render_updates),
                            )
                if commit_usd and (updates or hair_brush.get("undoStroke")):
                    phase("saving hair groom")
                    groom_encode_started = time.perf_counter()
                    committed_groom_deltas = hair_sim.export_session_groom_deltas(
                        self.current_usd_stage,
                        hair_cfg,
                        usd_graph._hair_query_matches,
                        local_updates=updates,
                        acknowledge=True,
                    )
                    groom_encode_ms = (time.perf_counter() - groom_encode_started) * 1000
                    committed_groom_node_id = tid
                    expected_graph = usd_graph.graph_after_groom_commit(active_graph, tid, committed_groom_deltas)
                    if expected_graph is not None:
                        self.pending_groom_graph = (expected_graph, request.get("cameraSession"), scene_suffix)
                        if expected_graph != active_graph:
                            committed_groom_handle = f"groom-{uuid.uuid4().hex}"
                            self._register_render_graph(committed_groom_handle, expected_graph)
        pin_tool = isinstance(hair_brush, dict) and str(hair_brush.get("tool") or "").lower() in ("pin", "unpin")
        cache_brush = request.get("cacheBrush")
        committed_cache_deltas = None
        committed_cache_masks = None
        committed_cache_layer_id = None
        committed_cache_node_id = None
        cache_sim_ms = None
        c_brush_seq = cache_brush.get("seq") if isinstance(cache_brush, dict) else None
        is_new_cache_seq = (c_brush_seq is None) or (c_brush_seq != getattr(self, "last_cache_brush_seq", None))
        if (
            isinstance(cache_brush, dict)
            and is_new_cache_seq
            and self.stage is not None
            and self.current_usd_stage is not None
            and hasattr(self, "last_camera_basis")
            and isinstance(active_graph, dict)
        ):
            self.last_cache_brush_seq = c_brush_seq
            ctid = (
                cache_brush.get("nodeId")
                or request.get("activeCacheEditNodeId")
                or active_graph.get("targetNodeId")
            )
            cache_cfg = None
            for n in active_graph.get("nodes") or []:
                if n.get("id") == ctid and n.get("operation") == "cache_edit":
                    cache_cfg = n.get("cacheEdit")
                    break
            if isinstance(cache_cfg, dict):
                _wdir = os.path.dirname(os.path.abspath(__file__))
                if _wdir not in sys.path:
                    sys.path.insert(0, _wdir)
                import cache_edit as _cache_edit
                c_frame = float(request.get("frame", 0.0))
                commit_c = bool(cache_brush.get("commitUsd", False))
                c_started = time.perf_counter()
                with Usd.EditContext(self.current_usd_stage, self.current_usd_stage.GetSessionLayer()):
                    c_updates = _cache_edit.step_cache_brush(
                        self.current_usd_stage,
                        cache_cfg,
                        cache_brush,
                        self.last_camera_basis,
                        frame=c_frame,
                        write_usd=commit_c,
                        return_gpu=(not commit_c),
                    )
                cache_sim_ms = (time.perf_counter() - c_started) * 1000
                if c_updates:
                    self._write_hair_points_to_ovstage(c_updates)
                    self.last_update_kind = "cache-brush"
                    hair_brushed = True
                if commit_c and (
                    c_updates
                    or cache_brush.get("undoStroke")
                    or cache_brush.get("clearMask")
                    or cache_brush.get("invertMask")
                    or str(cache_brush.get("tool", "")).endswith("mask")
                ):
                    committed_cache_layer_id, committed_cache_deltas, committed_cache_masks = (
                        _cache_edit.export_session_cache_deltas(self.current_usd_stage, cache_cfg, frame=c_frame)
                    )
                    committed_cache_node_id = ctid
        cache_mask_tool = isinstance(cache_brush, dict) and str(cache_brush.get("tool") or "").lower() in ("mask", "unmask")
        show_cache_mask = bool(request.get("showCacheMask")) or cache_mask_tool
        self._cache_mask_overlay_active = show_cache_mask
        show_hair_pins = bool(request.get("showHairPins")) or pin_tool
        pin_data_changed = pin_tool or hair_topology_changed or (
            isinstance(hair_brush, dict) and bool(hair_brush.get("clearPins") or hair_brush.get("undoStroke"))
        )
        pin_overlay_changed = show_hair_pins != self._pin_mat_overlay_active
        if (
            isinstance(hair_cfg, dict)
            and hair_cfg.get("simEnabled")
            and self.stage is not None
            and self.current_usd_stage is not None
            and (pin_data_changed or pin_overlay_changed or (show_hair_pins and scene_changed))
        ):
            _wdir = os.path.dirname(os.path.abspath(__file__))
            if _wdir not in sys.path:
                sys.path.insert(0, _wdir)
            import hair_sim
            hair_pinned_count, color_updates = hair_sim.get_pinned_strand_vertex_colors(
                self.current_usd_stage,
                hair_cfg,
                usd_graph._hair_query_matches,
                include_colors=show_hair_pins,
            )
            if self._update_hair_pin_overlay(show_hair_pins, color_updates):
                # Only changing the USD overlay can restore USD-authored points.
                # Updating display colors on the live stage leaves points intact.
                if pin_overlay_changed:
                    pts_updates = hair_sim.get_active_hair_points(
                        self.current_usd_stage,
                        hair_cfg,
                        usd_graph._hair_query_matches,
                    )
                    if pts_updates:
                        self._write_hair_points_to_ovstage(pts_updates)
                hair_brushed = True
        elif self._pin_mat_overlay_active and self.stage is not None and (
            not isinstance(hair_cfg, dict) or not hair_cfg.get("simEnabled") or self.current_usd_stage is None
        ):
            if self._update_hair_pin_overlay(False, []):
                if isinstance(hair_cfg, dict) and self.current_usd_stage is not None:
                    _wdir = os.path.dirname(os.path.abspath(__file__))
                    if _wdir not in sys.path:
                        sys.path.insert(0, _wdir)
                    import hair_sim
                    pts_updates = hair_sim.get_active_hair_points(
                        self.current_usd_stage,
                        hair_cfg,
                        usd_graph._hair_query_matches,
                    )
                    if pts_updates:
                        self._write_hair_points_to_ovstage(pts_updates)
                hair_brushed = True

        if (
            reused_all_brush_points and committed_groom_deltas is not None
            and not (switched_scene or scene_changed or camera_changed or size_changed or hair_brushed
                     or need_pt_spp_raise or request.get("pick") or self._pending_camera_sync)
            and previous_brush_frame is not None
            and previous_brush_frame[0] == usd_graph.groom_frame_view(request)
            and not (previous_brush_frame[1].get("encoding") == "popup" and not request.get("popupSkipImage"))
        ):
            self._last_interactive = interactive
            total_ms = (time.perf_counter() - t_scene_start) * 1000
            frame = {
                **previous_brush_frame[1], "updateKind": "groom-commit", "interactive": interactive,
                "hairSimMs": hair_sim_ms, "hairSolveStats": hair_stats, "groomEncodeMs": groom_encode_ms,
                "hairUploadMs": hair_upload_ms,
                "sceneMs": scene_ms, "renderMs": 0.0, "captureMs": 0.0, "stepMs": 0.0,
                "totalMs": round(total_ms, 2), "timingMs": round(total_ms, 2), "steps": 0,
            }
            if self.pending_groom_graph is not None:
                self.pending_groom_frame = (previous_brush_frame[0], frame)
            return reuse_presented_image({
                **frame, "hairGroomDeltas": committed_groom_deltas, "hairGroomNodeId": committed_groom_node_id,
                "hairGroomGraphHandle": committed_groom_handle,
            }, request)

        if committed_groom_deltas is not None:
            phase("rendering NVIDIA frame", timeout=step_timeout)
        first_frame = switched_scene or scene_changed or camera_changed or size_changed or hair_brushed
        # Keep at least 2 bounces during interaction: maxBounces=1 in ovrtx PathTracing drops direct lighting.
        desired_bounces = 2 if (interactive or hair_brushed) else 4
        if render_mode == "pathTracing" and self.stage is not None and getattr(self, "_pt_max_bounces", 4) != desired_bounces:
            self.ordinal += 1
            with ovstage.PathDictionary(self.stage) as paths:
                for prod_path in _VIEWPORT_PRODUCTS:
                    prod_plist = paths.create_path_list_from_strings([prod_path])
                    try:
                        prod_query = self.stage.query_from_path_list(prod_plist)
                        self.stage.write_attribute(
                            prod_query,
                            "omni:rtx:pt:limits:maxBounces",
                            self.ordinal,
                            np.asarray([desired_bounces], dtype=np.uint32),
                            is_array=False,
                            semantic=0,
                        ).wait()
                    finally:
                        paths.destroy_path_list(prod_plist)
            self.stage.compute_hierarchy(
                self.ordinal,
                self.ordinal,
                model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
            )
            self.stage.advance_write_floor(self.ordinal).wait()
            self._pt_max_bounces = desired_bounces
        step_count = 1 if (interactive or need_pt_spp_raise or hair_brushed or groom_committed) else (
            8 if (switched_scene and self.last_update_kind in ("material-delta", "material-delta-fast"))
            else (4 if (switched_scene and self.last_update_kind == "cache-hit")
                  else (2 if (first_frame or request.get("hairEditing")) else (4 if render_mode == "pathTracing" else 2)))
        )
        result = None
        picked_prim = None
        picked_material = None
        picked_point = None
        pick_pending = bool(pick_coord)
        render_started = time.perf_counter()
        for step_idx in range(step_count):
            if step_idx > 0:
                phase("rendering NVIDIA frame", timeout=step_timeout, report=False)
            result = self.renderer.step(render_products=products, delta_time=1 / 60, ordinal=self.ordinal)
            if pick_pending:
                # Pick hits are attached only to the step immediately after the
                # query was enqueued; read them before further steps replace it.
                picked_prim = self._read_pick_hit(result[active_product].frames[0])
                hit_mesh_path = None
                hit_face_index = None
                if picked_prim and self.current_usd_stage and hasattr(self, "last_camera_basis") and isinstance(pick_coord, (list, tuple)) and len(pick_coord) == 2:
                    cb = self.last_camera_basis
                    u_p = float(pick_coord[0])
                    v_p = float(pick_coord[1])
                    fwd = cb["forward"]
                    rgt = cb["right"]
                    upv = cb["up"]
                    if cb.get("orthographic"):
                        ortho_hh = float(cb.get("ortho_half_height", 1.0))
                        ox = (u_p * 2.0 - 1.0) * ortho_hh * float(cb["aspect"])
                        oy = (1.0 - v_p * 2.0) * ortho_hh
                        orig = cb["origin"]
                        rorig = (orig[0] + rgt[0] * ox + upv[0] * oy, orig[1] + rgt[1] * ox + upv[1] * oy, orig[2] + rgt[2] * ox + upv[2] * oy)
                        rdir = fwd
                    else:
                        sx = (u_p * 2.0 - 1.0) * float(cb["tan_half_fov"]) * float(cb["aspect"])
                        sy = (1.0 - v_p * 2.0) * float(cb["tan_half_fov"])
                        rorig = cb["origin"]
                        rdir = (fwd[0] + rgt[0] * sx + upv[0] * sy, fwd[1] + rgt[1] * sx + upv[1] * sy, fwd[2] + rgt[2] * sx + upv[2] * sy)
                    hit = raycast_prim_hit(
                        self.current_usd_stage,
                        picked_prim,
                        rorig,
                        rdir,
                        float(request.get("frame", 0)),
                    )
                    if hit:
                        picked_point = hit["point"]
                        hit_mesh_path = hit.get("meshPath")
                        hit_face_index = hit.get("faceIndex")
                if picked_prim and self.current_usd_stage:
                    picked_material = usd_graph.resolve_prim_material(
                        self.current_usd_stage,
                        picked_prim,
                        hit_mesh_path=hit_mesh_path,
                        face_index=hit_face_index,
                    )
                pick_pending = False
            if render_mode == "pathTracing" and bool(result[active_product].frames[0].converged):
                break
            if not (switched_scene and self.last_update_kind in ("material-delta", "material-delta-fast")) and (time.perf_counter() - t_step_start) >= 1.5:
                break

        render_ms = (time.perf_counter() - render_started) * 1000
        if not scene_changed and getattr(self, "_first_refinement_pending", False):
            self._first_refinement_pending = False
        if need_pt_spp_raise:
            self._write_pt_samples_per_pixel(target_spp)

        step_ms = (time.perf_counter() - t_step_start) * 1000
        t_capture_start = time.perf_counter()
        # 4. Extract pixel tensor and read back to CPU
        frame = result[active_product].frames[0]

        render_var = frame.render_vars["/Render/RenderVar_LdrColor"]
        current_samples = int(frame.progression)
        # In standalone ovrtx PathTracing, frame.progression is the completed sample count (offset by +1)
        # and frame.converged is True once target_spp is reached.
        # In RealTimePathTracing, progression is temporal history frames (starts at 50) and reaches target_spp (or 16).
        if render_mode == "pathTracing":
            display_samples = min(target_spp, max(1, current_samples - 1 if current_samples > 1 else current_samples))
            converged = (not need_pt_spp_raise) and (bool(frame.converged) or display_samples >= target_spp)
            if (
                target_spp > 1
                and not interactive
                and not first_frame
                and not need_pt_spp_raise
                and not hair_brushed
                and not groom_committed
                and not converged
                and display_samples <= 1
            ):
                self._pt_stall_count = getattr(self, "_pt_stall_count", 0) + 1
                if self._pt_stall_count == 2:
                    sys.stderr.write(
                        f"OVRTX PT progression stalled at {display_samples}/{target_spp} "
                        f"(raw={current_samples}, converged={bool(frame.converged)}, ordinal={self.ordinal}, "
                        f"steps={step_count}, updateKind={self.last_update_kind}); re-writing samplesPerPixel.\n"
                    )
                    try:
                        self._write_pt_samples_per_pixel(target_spp)
                    except Exception as err:
                        sys.stderr.write(f"OVRTX samplesPerPixel nudge failed: {err}\n")
                elif self._pt_stall_count >= 4:
                    stall_key = (self.last_graph_hash, self.overlay_path)
                    if getattr(self, "_pt_stall_rebuilt_for", None) != stall_key:
                        sys.stderr.write(
                            f"OVRTX PT progression still stalled at {display_samples}/{target_spp} after {self._pt_stall_count} "
                            f"refinement ticks (raw={current_samples}, ordinal={self.ordinal}, updateKind={self.last_update_kind}); "
                            "rebuilding ovstage in place.\n"
                        )
                        self._pt_stall_rebuilt_for = stall_key
                        self._recover_stalled_ovstage(request, render_mode, target_spp)
            else:
                self._pt_stall_count = 0
                if display_samples > 1 or converged:
                    self._pt_stall_rebuilt_for = None
        else:
            self._pt_stall_count = 0
            rt_samples = max(1, current_samples - 49) if current_samples >= 50 else max(1, current_samples)
            display_samples = min(target_spp, rt_samples)
            converged = bool(frame.converged) or rt_samples >= target_spp

        phase("reading NVIDIA pixels", timeout=20, report=False)
        use_native_popup = bool(request.get("nativePopup"))
        close_native_popup = bool(request.get("closeNativePopup")) or (request.get("nativePopup") is False)
        popup_skip_image = bool(request.get("popupSkipImage"))
        native_popup_open = False
        presented_popup = False
        if close_native_popup and not use_native_popup and self.native_popup is not None:
            try:
                self.native_popup.close()
            except Exception:
                pass
            self.native_popup = None
            self._native_popup_active = False

        pixels = None
        _wdir = os.path.dirname(os.path.abspath(__file__))
        if _wdir not in sys.path:
            sys.path.insert(0, _wdir)
        if use_native_popup:
            try:
                from hair_kernels import draw_brush_circle_cuda, draw_polyline_cuda, render_var_to_cuda_u8
                from native_cuda_viewport import NativeCudaViewport
                parent_hwnd = int(request.get("popupParentHwnd") or 0)
                prect = request.get("popupRect") if isinstance(request.get("popupRect"), dict) else {}
                rx = int(prect.get("x", 0))
                ry = int(prect.get("y", 0))
                rw = max(64, int(prect.get("width", width)))
                rh = max(64, int(prect.get("height", height)))
                if self.native_popup is None:
                    self.native_popup = NativeCudaViewport(
                        "OrchFlow Hair Grooming & Live Physics", on_input=_emit_popup_input
                    )
                if not self.native_popup.is_open():
                    if not self._native_popup_active:
                        self.native_popup.open(rw, rh, parent_hwnd=parent_hwnd, x=rx, y=ry)
                        self._native_popup_active = True
                    else:
                        # User closed the pop-up window manually
                        self.native_popup = None
                        self._native_popup_active = False
                if self.native_popup is not None and self.native_popup.is_open():
                    popup_status = request.get("popupStatus")
                    if isinstance(popup_status, str) and popup_status:
                        self.native_popup.set_status(popup_status)
                    cuda_res = render_var_to_cuda_u8(render_var, ovrtx, self._cuda_readback_cache)
                    if cuda_res is not None:
                        dev_u8, actual_height, actual_width, stream_ptr = cuda_res
                        import warp as wp
                        if getattr(self, "_popup_clean_u8", None) is None or self._popup_clean_u8.shape != dev_u8.shape:
                            self._popup_clean_u8 = wp.clone(dev_u8)
                        else:
                            wp.copy(self._popup_clean_u8, dev_u8)
                        self._popup_clean_meta = (actual_height, actual_width, stream_ptr)
                        popup_draw_curve = request.get("popupDrawCurve")
                        if isinstance(popup_draw_curve, list) and len(popup_draw_curve) >= 2:
                            draw_polyline_cuda(dev_u8, actual_height, actual_width, popup_draw_curve)
                        r_px = float(request.get("popupBrushRadiusPx", 0.0) or 0.0)
                        if r_px > 1.0:
                            hnx, hny = self.native_popup.last_hover_norm()
                            draw_brush_circle_cuda(dev_u8, actual_height, actual_width, hnx, hny, r_px)
                        presented_popup = self.native_popup.present_cuda(
                            dev_u8.ptr, actual_width, actual_height, cuda_stream=stream_ptr
                        )
                        native_popup_open = self.native_popup.is_open()
                        if not native_popup_open:
                            self._native_popup_active = False
            except Exception as popup_err:
                sys.stderr.write(f"Native CUDA pop-up present failed: {popup_err}\n")
                presented_popup = False
                native_popup_open = False

        if not (presented_popup and popup_skip_image):
            try:
                from hair_kernels import read_render_var_rgba_cuda
                pixels = read_render_var_rgba_cuda(render_var, ovrtx, self._cuda_readback_cache)
            except Exception:
                pixels = None
            if pixels is None:
                with render_var.map(device=ovrtx.Device.CPU) as rv:
                    arr = np.from_dlpack(rv)
                    if arr.dtype in (np.float32, np.float64):
                        pixels = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
                    else:
                        pixels = arr.copy()
                    del arr

        del render_var, frame, result

        capture_ms = (time.perf_counter() - t_capture_start) * 1000
        binary_frame = bool(request.get("binaryFrame"))
        is_fast_frame = interactive or bool(request.get("hairBrush")) or bool(request.get("cacheBrush")) or bool(request.get("hairEditing")) or not converged
        if presented_popup and popup_skip_image:
            encoded_bytes = b""
            encoding = "popup"
            mime = "application/octet-stream"
        else:
            # 5. Compress image to data URI (JPEG for interactive gestures, PNG for idle)
            if pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
                raise RuntimeError(f"Unexpected NVIDIA pixel tensor shape: {pixels.shape}")
            actual_height, actual_width = pixels.shape[:2]
            if (actual_width, actual_height) != (width, height):
                raise RuntimeError(f"NVIDIA returned {actual_width}x{actual_height} for requested {width}x{height}; refusing to stretch a stale frame")
            if show_cache_mask:
                pixels = self._composite_cache_mask_overlay(pixels, active_graph, request, actual_width, actual_height)
            phase("encoding viewport image", timeout=15, report=False)
            if binary_frame and is_fast_frame:
                if pixels.shape[2] == 3:
                    rgba = np.empty((actual_height, actual_width, 4), dtype=np.uint8)
                    rgba[:, :, :3] = pixels
                    rgba[:, :, 3] = 255
                elif pixels.flags["C_CONTIGUOUS"] and pixels.dtype == np.uint8:
                    rgba = pixels
                else:
                    rgba = np.ascontiguousarray(pixels, dtype=np.uint8)
                encoded_bytes = memoryview(rgba).cast("B")
                encoding = "rgba"
                mime = "application/octet-stream"
            else:
                img = Image.fromarray(pixels)
                buf = io.BytesIO()
                if interactive:
                    # Drop alpha for JPEG
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        img_rgb = img.convert("RGB")
                    else:
                        img_rgb = img
                    img_rgb.save(buf, format="JPEG", quality=70)
                    encoding = "jpeg"
                    mime = "image/jpeg"
                else:
                    img.save(buf, format="PNG", compress_level=1)
                    encoding = "png"
                    mime = "image/png"
                encoded_bytes = buf.getvalue()
        total_ms = (time.time() - t_total_start) * 1000.0

        vram_used, vram_total = _query_gpu_vram(interactive=is_fast_frame)
        if vram_used and vram_total and (vram_used / vram_total) > 0.70 and len(self.stage_pool) > 1:
            self._flush_stage_pool()
        pose_camera_params = self._compute_camera_params(request, width, height)
        pose_camera = Gf.Camera()
        pose_camera.projection = Gf.Camera.Orthographic if pose_camera_params["is_ortho"] else Gf.Camera.Perspective
        pose_camera.transform = pose_camera_params["matrix"]
        pose_camera.horizontalAperture = pose_camera_params["horizontal_aperture"]
        pose_camera.verticalAperture = pose_camera_params["vertical_aperture"]
        pose_camera.focalLength = pose_camera_params["focal_length"]
        pose_camera.clippingRange = Gf.Range1f(0.001, 1000000.0)
        pose_vp = pose_camera.frustum.ComputeViewMatrix() * pose_camera.frustum.ComputeProjectionMatrix()
        response = {
            "width": width,
            "projection": projection,
            "viewProjection": [list(row) for row in pose_vp],
            "poseBones": live_pose_bones,
            "height": height,
            "renderer": "OmniverseRTX",
            "interactive": interactive,
            "encoding": encoding,
            "nativePopupOpen": native_popup_open,
            "updateKind": self.last_update_kind,
            "gpu": "NVIDIA RTX",
            "converged": converged,
            "instancerHandles": usd_graph.instancer_handles(self.current_usd_stage, request.get("frame", 0)) if graph and any(n.get('operation') == 'instancer' for n in graph.get('nodes', [])) else [],
            "pickedObject": usd_graph.instancer_pick_target(self.current_usd_stage, picked_prim, request.get("frame", 0)) if picked_prim and graph and any(n.get('operation') == 'instancer' for n in graph.get('nodes', [])) else None,
            "pickedPrim": picked_prim,
            "pickedMaterial": picked_material,
            "pickedPoint": picked_point,
            "cameraAnchor": {
                "session": self.camera_fit_key[0], "fit": self.camera_fit_key[1],
                "center": list(self.camera_anchor[0]), "radius": self.camera_anchor[1],
                "translation": list(self.camera_anchor_translation or [0.0, 0.0, 0.0]),
                "transformTarget": self.camera_anchor_target,
                "gizmoOffset": (
                    [
                        float(g_entry["head_world_pos"][0] - self.camera_anchor[0][0]),
                        float(g_entry["head_world_pos"][1] - self.camera_anchor[0][1]),
                        float(g_entry["head_world_pos"][2] - self.camera_anchor[0][2]),
                    ]
                    if isinstance(self.camera_anchor_target, str)
                    and self.camera_anchor_target.startswith("groom:")
                    and len(self.camera_anchor_target.split(":", 2)) == 3
                    and (g_entry := getattr(self, "live_grooms", {}).get(tuple(self.camera_anchor_target.split(":", 2)[1:])))
                    and g_entry.get("head_world_pos")
                    else None
                ),
                "unitScale": (1.0 / max(1e-6, float(UsdGeom.GetStageMetersPerUnit(self.current_usd_stage) or 1.0))) if self.current_usd_stage else 1.0,
            } if self.camera_fit_key[0] else None,
            "cameraSync": self._pending_camera_sync,
            "cameraTransform": getattr(self, "_last_camera_transform", None),
            "raySamples": {"current": display_samples, "target": target_spp},
            "frame": float(request.get("frame", 0)),
            "renderMs": render_ms,
            "hairSimMs": hair_sim_ms,
            "hairUploadMs": hair_upload_ms,
            "hairSolveStats": hair_stats,
            "groomEncodeMs": groom_encode_ms,
            "captureMs": capture_ms,
            "sceneMs": scene_ms,
            "stepMs": step_ms,
            "totalMs": round(total_ms, 2),
            "pngBytes": len(encoded_bytes),
            "upAxis": str(request.get("upAxis", "Y")).upper(),
            "graphStats": self.graph_stats,
            "missingAssets": self.material_diagnostics,
            "materialMode": "mixed" if self.material_fallbacks else "scene",
            "warnings": [f"Selective fallback applied to {len(self.material_fallbacks)} material(s) with missing shader assets or invalid inputs"] if self.material_fallbacks else [],
            "steps": step_count,
            "vramUsedMb": vram_used,
            "vramTotalMb": vram_total,
            "cachedScenes": len(self.stage_pool),
            "timingMs": round(total_ms, 2),
            "stats": {
                "activeRenderer": "Omniverse RTX (NVIDIA ovrtx)",
                "device": "NVIDIA GeForce RTX 3090",
                "width": width,
                "height": height
            }
        }
        if committed_groom_deltas is not None:
            response["hairGroomDeltas"] = committed_groom_deltas
            if committed_groom_node_id:
                response["hairGroomNodeId"] = committed_groom_node_id
            if committed_groom_handle:
                response["hairGroomGraphHandle"] = committed_groom_handle
        if committed_cache_deltas is not None:
            response["cacheEditDeltas"] = committed_cache_deltas
            response["cacheEditMasks"] = committed_cache_masks
            if committed_cache_layer_id:
                response["cacheEditLayerId"] = committed_cache_layer_id
            if committed_cache_node_id:
                response["cacheEditNodeId"] = committed_cache_node_id
        if cache_sim_ms is not None:
            response["cacheSimMs"] = cache_sim_ms
        self._pending_camera_sync = None
        self._last_interactive = interactive
        if hair_pinned_count is not None:
            response["hairPinnedCount"] = hair_pinned_count
        if binary_frame:
            response["_binary_bytes"] = encoded_bytes
        else:
            img_b64 = base64.b64encode(encoded_bytes).decode("ascii")
            response["image"] = f"data:{mime};base64,{img_b64}"
        if request.get("viewportImageId"):
            response["viewportImageId"] = request["viewportImageId"]
        if hair_brushed and self.last_brush_upload is not None and not request.get("pick") and not response["cameraSync"]:
            self.last_brush_frame = (usd_graph.groom_frame_view(request), response)
        if committed_groom_deltas is not None and self.pending_groom_graph is not None and not request.get("pick") and not response["cameraSync"]:
            self.pending_groom_frame = (
                usd_graph.groom_frame_view(request),
                {key: value for key, value in response.items()
                 if key not in ("hairGroomDeltas", "hairGroomNodeId", "hairGroomGraphHandle")},
            )
        return response


def viewport():
    global _viewport
    if _viewport is None:
        _viewport = OvrtxViewport()
    return _viewport


def close_viewport():
    global _viewport
    if _viewport is not None:
        _viewport.close()
        _viewport = None

import atexit
atexit.register(close_viewport)


def dispatch(request):
    operation = request.get("operation")

    if operation == "ping":
        return {"status": "ok", "worker": "ovrtx"}

    if operation == "capabilities":
        runtime = discover_ovrtx_runtime()
        return {
            "version": runtime.get("ovrtxVersion", "0.6.0"),
            "inspection": False,
            "viewport": bool(runtime.get("available", False)),
            "renderers": [
                {
                    "id": "OmniverseRTX",
                    "name": "Omniverse RTX (NVIDIA ovrtx)",
                    "support": "Standalone RTX Path Tracing / Real-Time"
                }
            ],
            "gpu": runtime.get("device", "NVIDIA RTX GPU"),
            "ovrtxRuntime": runtime
        }

    if operation == "clear_scene":
        viewport().clear_scene()
        usd_graph.clear_graph_caches()
        try:
            import hair_sim
            hair_sim.reset_hair_sessions()
        except Exception:
            pass
        try:
            import cache_edit as _ce
            _ce.reset_cache_sessions()
        except Exception:
            pass
        used_mb, total_mb = _query_gpu_vram()
        return {"cleared": True, "vramUsedMb": used_mb, "vramTotalMb": total_mb, "cachedScenes": 0}

    if operation == "reset_hair":
        try:
            _wdir = os.path.dirname(os.path.abspath(__file__))
            if _wdir not in sys.path:
                sys.path.insert(0, _wdir)
            import hair_sim
            hair_sim.reset_hair_sessions()
        except Exception:
            pass
        try:
            import cache_edit as _ce
            _ce.reset_cache_sessions()
        except Exception:
            pass
        vp = viewport()
        vp.last_graph_hash = None
        vp.pending_groom_graph = None
        vp.pending_groom_frame = None
        vp.last_brush_upload = None
        vp.last_brush_frame = None
        vp.prepared_hair_node_id = None
        vp.live_groom_modified = False
        vp._flush_stage_pool()
        return {"reset": True}

    if operation in ("render", "update_nvidia_materials"):
        return viewport().render(request)

    if operation == "scene_children":
        return viewport().scene_children(request)

    raise ValueError(f"Unsupported ovrtx worker operation: {operation}")


def respond(request):
    done = threading.Event()
    phase("starting request", report=False)
    watchdog = threading.Thread(target=request_watchdog, args=(done,), daemon=True)
    watchdog.start()
    try:
        result = dispatch(request)
        phase("request complete", report=False)
        return result
    except Exception as exc:
        import datetime, traceback
        traceback.print_exc(file=sys.stderr)
        try:
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            log_dir = os.path.join(base, "OrchFlow")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "worker-errors.log")
            if os.path.isfile(log_path) and os.path.getsize(log_path) > 2 * 1024 * 1024:
                backup = log_path + ".1"
                if os.path.isfile(backup):
                    os.remove(backup)
                os.replace(log_path, backup)
            op = request.get("operation", "render") if isinstance(request, dict) else "unknown"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] ovrtx_worker op={op}: {exc}\n")
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        return {"error": str(exc)}
    finally:
        done.set()


if __name__ == "__main__":
    try:
        if "--serve" in sys.argv:
            sys.stdout = _LockedStdout(_RAW_STDOUT)
            # Configure standard IO line buffering
            if hasattr(sys.stdin, "reconfigure"):
                sys.stdin.reconfigure(line_buffering=True)
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                    res = respond(req)
                except Exception as exc:
                    res = {"error": str(exc)}
                binary_bytes = res.get("_binary_bytes") if isinstance(res, dict) else None
                if binary_bytes is not None:
                    res = {key: value for key, value in res.items() if key != "_binary_bytes"}
                    res["binaryBytes"] = len(binary_bytes)
                    header = (_PROTOCOL + json.dumps(res) + "\n").encode("utf-8")
                    with _STDOUT_LOCK:
                        _RAW_STDOUT.flush()
                        _RAW_STDOUT.buffer.write(header)
                        _RAW_STDOUT.buffer.write(binary_bytes)
                        _RAW_STDOUT.buffer.flush()
                else:
                    _write_stdout_bytes((_PROTOCOL + json.dumps(res) + "\n").encode("utf-8"))
        else:
            req = json.load(sys.stdin)
            print(json.dumps(respond(req)))
    finally:
        close_viewport()
