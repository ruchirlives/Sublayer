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
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
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
    from material_preview import missing_shader_assets, invalid_shader_inputs, preserve_mdl_preview, selective_material_preview
    from ovrtx_runtime import discover_ovrtx_runtime
    import ovrtx_material_delta
    from viewport_bounds import compute_visible_geometry_bounds, compute_selection_bounds, isolate_prim_in_stage, apply_viewport_visibility
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


def report_status(phase_name, details=None):
    try:
        payload = {"phase": phase_name}
        if details:
            payload["details"] = details
        sys.stdout.write("ORCHFLOW_STATUS:" + json.dumps(payload) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


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
            print(_PROTOCOL + json.dumps({"error": message}), flush=True)
            os._exit(124)


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


def _query_gpu_vram(interactive=False):
    if interactive and _vram_cache["used"] is not None:
        return _vram_cache["used"], _vram_cache["total"]
    now = time.time()
    if now - _vram_cache["ts"] < 2.5:
        return _vram_cache["used"], _vram_cache["total"]
    try:
        smi = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if os.name == "nt" else "nvidia-smi")
        kwargs = {"stderr": subprocess.DEVNULL, "text": True, "timeout": 1.5}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000
        out = subprocess.check_output(
            [smi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            **kwargs,
        ).strip().splitlines()
        if out:
            used_s, total_s = out[0].split(",")[:2]
            _vram_cache["used"] = int(float(used_s.strip()))
            _vram_cache["total"] = int(float(total_s.strip()))
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
        self.last_suffix = None
        self.last_graph = None
        self.overlay_handle = None
        self.overlay_path = None
        self.last_update_kind = "none"
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
                    )
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
        self.base_layer = None
        self.current_usd_stage = None
        self.last_outline_key = None
        self._outline_cache = None
        self.active_selection = []
        self.last_requested_selection = []
        self._material_users_cache = None
        self.last_graph_hash = None
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
        key = json.dumps(graph, sort_keys=True) if graph else request.get("path")
        if not key:
            raise ValueError("Choose a stage to inspect")
        reload_flag = bool(request.get("reload"))
        if not reload_flag and self.current_usd_stage is not None and self.last_outline_key == key:
            return usd_graph.query_scene_children(self.current_usd_stage, request)
        if reload_flag or self._outline_cache is None or self._outline_cache[0] != key:
            if graph:
                stage, alive, _ = usd_graph.compile_graph(graph, reload_flag)
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
        self.base_layer = None
    def _update_camera(self, stage, request, width, height):
        fit_key = (request.get("cameraSession"), request.get("frameFit", 0))
        target_prim = request.get("frameTarget")
        if self.camera_fit_key != fit_key or not hasattr(self, "camera_anchor"):
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
            self.camera_fit_key = fit_key
        center, radius = Gf.Vec3d(self.camera_anchor[0]), self.camera_anchor[1]
        yaw, pitch = math.radians(float(request.get("yaw", 35))), math.radians(float(request.get("pitch", 20)))
        up_axis = str(request.get("upAxis", "Y")).upper()
        if up_axis == "Z":
            up = Gf.Vec3d(0, 0, 1)
            forward = Gf.Vec3d(math.sin(yaw)*math.cos(pitch), -math.cos(yaw)*math.cos(pitch), math.sin(pitch))
        else:
            up = Gf.Vec3d(0, 1, 0)
            forward = Gf.Vec3d(math.sin(yaw)*math.cos(pitch), math.sin(pitch), math.cos(yaw)*math.cos(pitch))
        right = Gf.Cross(up, forward).GetNormalized()
        camera_up = Gf.Cross(forward, right).GetNormalized()
        center += right * float(request.get("panX", 0)) * radius + camera_up * float(request.get("panY", 0)) * radius
        fov = max(5.0, min(170.0, float(request.get("fov") or 45.0)))
        half_fov = math.radians(fov * 0.5)
        dof_enabled = bool(request.get("dofEnabled", False))
        focus_distance = max(0.01, float(request.get("focusDistance") or 5.0))
        f_stop = max(0.1, float(request.get("fStop") or 5.6))
        distance = radius / math.sin(math.atan(math.tan(math.radians(22.5))*min(width/height, 1))) * 1.15 * float(request.get("zoom", 1))
        eye = center + forward * distance
        self.last_camera_basis = {
            "origin": (float(eye[0]), float(eye[1]), float(eye[2])),
            "forward": (float(-forward[0]), float(-forward[1]), float(-forward[2])),
            "right": (float(right[0]), float(right[1]), float(right[2])),
            "up": (float(camera_up[0]), float(camera_up[1]), float(camera_up[2])),
            "tan_half_fov": float(math.tan(half_fov)),
            "aspect": float(width) / float(max(height, 1)),
        }
        camera = UsdGeom.Camera.Define(stage, "/Render/Camera")
        camera.MakeMatrixXform().Set(Gf.Matrix4d().SetLookAt(eye, center, up).GetInverse())
        camera.CreateHorizontalApertureAttr().Set(36.0 * width / height)
        camera.CreateVerticalApertureAttr().Set(36.0)
        camera.CreateFocalLengthAttr().Set(36.0 / (2 * math.tan(half_fov)))
        camera.CreateFocusDistanceAttr().Set(focus_distance)
        camera.CreateFStopAttr().Set(f_stop if dof_enabled else 0.0)
        product_prim = stage.GetPrimAtPath("/Render/RenderProduct_Viewport")
        if product_prim and product_prim.IsValid():
            product_prim.CreateAttribute("omni:rtx:post:dof:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(dof_enabled)
            product_prim.CreateAttribute("omni:rtx:post:dof:overrideEnabled", Sdf.ValueTypeNames.Bool, custom=False).Set(dof_enabled)
        scene_radius = self.scene_camera_anchor[1] if hasattr(self, "scene_camera_anchor") else radius
        clip_radius = max(radius, scene_radius)
        near_clip = max(1e-5, min(clip_radius * 0.0001, distance * 0.01), distance - clip_radius * 2)
        far_clip = max(distance + clip_radius * 4, clip_radius * 8)
        camera.CreateClippingRangeAttr().Set((near_clip, far_clip))

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
        if new_path:
            self.overlay_handle = ovstage.population.add_usd_reference(self.stage, new_path, "/")
        self.ordinal += 1
        ovstage.population.apply_usd_changes(self.stage, ordinal=self.ordinal)
        self.stage.compute_hierarchy(self.ordinal, self.ordinal,
            model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL)
        self.stage.advance_write_floor(self.ordinal).wait()
        # apply_usd_changes re-syncs /Render/RenderProduct_Viewport from temp_usd_path
        # (which has omni:rtx:pt:samplesPerPixel = 1); mark it to be raised to target_spp.
        self._pt_spp_at_one = True
        self.overlay_path = new_path

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

    def _try_fast_hair_delta(self, prev_graph, next_graph, sample_frame):
        """Update BasisCurves.points in-place on the live ovstage when only Hair sim settings change."""
        if not isinstance(prev_graph, dict) or not isinstance(next_graph, dict):
            return False
        if (
            prev_graph.get("targetNodeId") != next_graph.get("targetNodeId")
            or prev_graph.get("edges") != next_graph.get("edges")
            or prev_graph.get("dependencies") != next_graph.get("dependencies")
        ):
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
        if nn.get("operation") != "hair" or nn.get("id") != next_graph.get("targetNodeId"):
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
        try:
            stage, graph_stages, graph_stats = usd_graph.compile_graph(next_graph, False)
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
            self.current_usd_stage = stage
            self.graph_stages = graph_stages
            self.graph_stats = graph_stats
            return True
        except Exception as error:
            sys.stderr.write(f"Ovrtx incremental hair update failed; rebuilding scene: {error}\n")
            return False

    def _try_fast_materialx_delta(self, prev_graph, new_graph):
        """Author MaterialX-only parameter updates directly into a sparse delta layer.

        Skips full-stage compile, shader asset validation, and stage.Flatten() when
        only scalar/color MaterialX parameters changed (same rules/bindings/textures).
        Returns None to fall back to the full path whenever anything structural differs.
        """
        if not prev_graph or not new_graph or self.base_layer is None:
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
                usd_graph.author_materialx_rules(tmp_stage, mx_node, files, procedural_graph=new_graph.get("proceduralGraph"))
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

    def render(self, request):
        self._ensure_initialized()
        t_total_start = time.time()

        width = max(16, int(request.get("width", 800)))
        height = max(16, int(request.get("height", 600)))
        interactive = bool(request.get("interactive", False))
        render_mode = request.get("ovrtxMode", "realtime")
        if not isinstance(render_mode, str) or render_mode not in _RENDER_MODES:
            raise ValueError("NVIDIA render mode must be Real-Time Path Tracing or Path Tracing")

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
                if len(self.render_graphs) >= 32 and graph_key not in self.render_graphs:
                    evicted = next(iter(self.render_graphs))
                    self.render_graphs.pop(evicted)
                    self.render_graph_hashes.pop(evicted, None)
                self.render_graphs[graph_key] = graph
                self.render_graph_hashes[graph_key] = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
            else:
                graph = self.render_graphs.get(graph_key)
                if graph is None:
                    raise ValueError("Unknown render graph handle; register graph again")
        if graph_key:
            graph_hash = self.render_graph_hashes[graph_key]
        else:
            graph_json = json.dumps(graph, sort_keys=True) if graph else stage_path or ""
            graph_hash = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()

        # Frame, material mode and render mode are fixed per populated scene; a change
        # to any of them always needs a full repopulation (never an incremental delta).
        isolated_prim = request.get("isolatedPrim")
        hidden_prims = sorted(set(request.get("hiddenPrims") or []))
        up_axis = str(request.get("upAxis", "Y")).upper()
        scene_suffix = ":frame:" + str(request.get("frame", 0)) + ":materials:" + str(request.get("materials", "auto")) + ":mode:" + render_mode + ":isolated:" + str(isolated_prim or "") + ":hidden:" + ",".join(hidden_prims) + ":up:" + up_axis
        graph_hash += scene_suffix
        base_key = None
        if graph:
            base_nodes = [
                n for n in graph.get("nodes", [])
                if (n.get("operation") or (n.get("data") or {}).get("operation")) in ("source", "blender", "application")
            ]
            base_key = json.dumps(base_nodes, sort_keys=True) + scene_suffix
        target_spp = max(1, int(request.get("maxSamples", 512))) if render_mode == "pathTracing" else 16
        switched_scene = (graph_hash != self.last_graph_hash) or bool(request.get("reload"))
        scene_changed = (graph_hash != self.last_graph_hash) or (self.temp_usd_path is None) or bool(request.get("reload"))
        camera_key = tuple(request.get(key) for key in ("yaw", "pitch", "zoom", "panX", "panY", "fov", "dofEnabled", "focusDistance", "fStop", "cameraSession", "frameFit", "frame", "frameTarget", "upAxis"))
        camera_changed = camera_key != self.last_camera_key
        size_changed = (width != self.last_width) or (height != self.last_height)

        raw_outline_key = json.dumps(graph, sort_keys=True) if graph else (stage_path or "")
        if request.get("reload") or request.get("clearScene"):
            self._flush_stage_pool()
        elif scene_changed and graph_hash in self.stage_pool:
            cached = self.stage_pool[graph_hash]
            if cached["delta_path"] != self.overlay_path:
                self._apply_material_delta_path(cached["delta_path"])
            self.current_usd_stage = cached["current_usd_stage"]
            self.last_outline_key = raw_outline_key
            self.material_diagnostics = cached["material_diagnostics"]
            self.material_fallbacks = cached["material_fallbacks"]
            self.material_keepalive = cached["material_keepalive"]
            self.graph_stages = cached["graph_stages"]
            self.graph_stats = cached["graph_stats"]
            self.last_graph_hash = graph_hash
            self.last_graph = graph
            self.last_update_kind = "cache-hit"
            scene_changed = False
        elif (
            scene_changed
            and self.base_layer is not None
            and self.last_suffix == scene_suffix
            and self.temp_usd_path
            and self.current_usd_stage is not None
            and self.last_graph is not None
            and graph is not None
        ):
            fast_delta = self._try_fast_materialx_delta(self.last_graph, graph)
            if fast_delta is not None:
                delta_path = self._apply_material_delta(fast_delta)
                self.last_outline_key = raw_outline_key
                self.last_graph_hash = graph_hash
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
                }
                scene_changed = False
            elif self._try_fast_hair_delta(self.last_graph, graph, float(request.get("frame", 0))):
                self.last_outline_key = raw_outline_key
                self.last_graph_hash = graph_hash
                self.last_graph = graph
                self.last_update_kind = "hair-delta"
                scene_changed = False
        if scene_changed or not self.temp_usd_path:
            phase("compiling USD graph", timeout=300)
            if graph:
                stage, self.graph_stages, self.graph_stats = usd_graph.compile_graph(graph, request.get("reload", False))
            elif stage_path and os.path.isfile(stage_path):
                source = Usd.Stage.Open(stage_path)
                if source is None: raise ValueError("Unable to open USD stage")
                wrap_layer = Sdf.Layer.CreateAnonymous("ovrtx_source_wrap.usda")
                wrap_layer.subLayerPaths.append(os.path.abspath(stage_path))
                stage = Usd.Stage.Open(wrap_layer)
                self.graph_stages = [source, wrap_layer]
                self.graph_stats = None
            else:
                raise ValueError("A valid USD graph or stage path is required for Omniverse RTX rendering")

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
            elif mode == "scene" and (self.material_diagnostics or issues):
                raise ValueError("NVIDIA source materials contain unresolved shader assets or invalid inputs; use Auto or repair their links")

            # Ensure studio ambient dome if no DomeLight exists, and key light if no scene lights exist
            has_dome = any(
                'DomeLight' in prim.GetTypeName()
                and not prim.GetPath().HasPrefix(Sdf.Path('/Render/DefaultLights'))
                for prim in stage.Traverse(Usd.TraverseInstanceProxies())
            )
            has_scene_lights = any(
                ('Light' in prim.GetTypeName() or (hasattr(UsdLux, 'LightAPI') and prim.HasAPI(UsdLux.LightAPI)))
                and not prim.GetPath().HasPrefix(Sdf.Path('/Render/DefaultLights'))
                for prim in stage.Traverse(Usd.TraverseInstanceProxies())
            )
            if not has_dome or not has_scene_lights:
                if not stage.GetPrimAtPath('/Render/DefaultLights'):
                    UsdGeom.Scope.Define(stage, '/Render/DefaultLights')
                if not has_dome and not stage.GetPrimAtPath('/Render/DefaultLights/DomeLight'):
                    dome = UsdLux.DomeLight.Define(stage, "/Render/DefaultLights/DomeLight")
                    dome.CreateIntensityAttr().Set(3000.0)
                    dome.CreateColorAttr().Set(Gf.Vec3f(0.95, 0.97, 1.0))
                if not has_scene_lights and not stage.GetPrimAtPath('/Render/DefaultLights/KeyLight'):
                    key = UsdLux.DistantLight.Define(stage, "/Render/DefaultLights/KeyLight")
                    key.CreateIntensityAttr().Set(3000.0)
                    key.CreateColorAttr().Set(Gf.Vec3f(1.0, 0.98, 0.95))
                    key.AddRotateXYZOp().Set(Gf.Vec3f(-45.0, 45.0, 0.0))

            # Define / update Camera
            cam = UsdGeom.Camera.Define(stage, "/Render/Camera")
            cam.CreateClippingRangeAttr().Set((0.1, 100000.0))

            # Define / update RenderProduct & RenderVar
            product = UsdRender.Product.Define(stage, "/Render/RenderProduct_Viewport")
            product.CreateResolutionAttr().Set(Gf.Vec2i(width, height))
            product.CreatePixelAspectRatioAttr().Set(1.0)
            product.CreateAspectRatioConformPolicyAttr().Set("adjustApertureHeight")
            product.CreateCameraRel().SetTargets(["/Render/Camera"])
            product_prim = product.GetPrim()
            product_prim.CreateAttribute("omni:rtx:rendermode", Sdf.ValueTypeNames.Token, custom=False).Set(_RENDER_MODES[render_mode])
            dof_enabled = bool(request.get("dofEnabled", False))
            product_prim.CreateAttribute("omni:rtx:post:dof:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(dof_enabled)
            product_prim.CreateAttribute("omni:rtx:post:dof:overrideEnabled", Sdf.ValueTypeNames.Bool, custom=False).Set(dof_enabled)
            if render_mode == "realtime":
                self._pt_spp_at_one = False
                product_prim.CreateAttribute("omni:rtx:rt:sss:enabled", Sdf.ValueTypeNames.Bool, custom=False).Set(True)
            else:
                spp_per_iter = max(1, int(request.get("samples", 1)))
                self._pt_spp_at_one = target_spp > 1
                product_prim.CreateAttribute("omni:rtx:pt:samplesPerIteration", Sdf.ValueTypeNames.Int, custom=False).Set(spp_per_iter)
                product_prim.CreateAttribute("omni:rtx:pt:samplesPerPixel", Sdf.ValueTypeNames.UInt, custom=False).Set(1)

            var = UsdRender.Var.Define(stage, "/Render/RenderVar_LdrColor")
            var.CreateSourceNameAttr().Set("LdrColor")
            var.CreateDataTypeAttr().Set("color4f")
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
            phase("flattening private USD preview")
            flat = stage.Flatten()
            incremental = False
            delta_path = None
            if (self.base_layer is not None and self.last_suffix == scene_suffix and previous_path
                    and self.current_usd_stage is not None and not request.get("reload")):
                phase("diffing material changes")
                delta = ovrtx_material_delta.plan(self.base_layer, flat)
                if delta is not None:
                    try:
                        delta_path = self._apply_material_delta(delta)
                        incremental = True
                    except Exception as error:
                        sys.stderr.write(f"Ovrtx incremental material update failed; rebuilding scene: {error}\n")
            if incremental:
                self.current_usd_stage = stage
                self.last_outline_key = raw_outline_key
                self.last_graph_hash = graph_hash
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
                }
            else:
                preserve_camera = (
                    not request.get("reload")
                    and base_key is not None
                    and base_key == self.last_base_key
                    and hasattr(self, "scene_camera_anchor")
                )
                self.last_base_key = base_key
                self.last_update_kind = "full-rebuild"
                if not preserve_camera:
                    # Compute bounds of visible renderable geometry, excluding synthetic /Render prims
                    bounds = compute_visible_geometry_bounds(stage, Usd.TimeCode(sample))
                    self.scene_camera_anchor = ((Gf.Vec3d(0), 1.0) if bounds.IsEmpty() else
                        (bounds.GetMidpoint(), max(bounds.GetSize().GetLength() * .5, .001)))
                flat_stage = Usd.Stage.Open(flat)
                self._update_camera(flat_stage, request, width, height)
                phase("writing temporary preview snapshot")
                new_usd_path = export_preview_snapshot(flat)

                old_overlay_path = self.overlay_path
                old_temp_path = self.temp_usd_path
                self._flush_stage_pool()
                if self.stage is not None and self.last_graph_hash is not None:
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
                    self.ordinal = 0
                for old_fp in (old_overlay_path, old_temp_path):
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
                phase("computing transform hierarchy")
                self.stage.compute_hierarchy(self.ordinal, self.ordinal,
                    model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL)
                self.stage.advance_write_floor(self.ordinal).wait()

                self.overlay_path = None
                self.base_layer = flat
                self.last_suffix = scene_suffix
                self.current_usd_stage = stage
                self.last_outline_key = raw_outline_key
                self.last_graph_hash = graph_hash
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
                }

        if (
            not scene_changed
            or self.last_update_kind in ("material-delta", "material-delta-fast", "cache-hit")
        ) and (
            size_changed
            or camera_changed
            or (switched_scene and self.last_update_kind in ("material-delta", "material-delta-fast", "cache-hit"))
        ):
            # Populate only camera opinions from fresh inline USD. Reopening the
            # same filename can reuse the population runtime's cached USD stage.
            camera_stage = Usd.Stage.CreateInMemory()
            self._update_camera(camera_stage, request, width, height)
            if size_changed:
                product = UsdRender.Product.Define(camera_stage, "/Render/RenderProduct_Viewport")
                product.CreateResolutionAttr().Set(Gf.Vec2i(width, height))
                product.CreatePixelAspectRatioAttr().Set(1.0)
                product.CreateCameraRel().SetTargets(["/Render/Camera"])
                product.CreateOrderedVarsRel().SetTargets(["/Render/RenderVar_LdrColor"])
                var = UsdRender.Var.Define(camera_stage, "/Render/RenderVar_LdrColor")
                var.CreateSourceNameAttr().Set("LdrColor")
                var.CreateDataTypeAttr().Set("color4f")
            self.ordinal += 1
            matrix = UsdGeom.Camera.Get(camera_stage, "/Render/Camera").GetLocalTransformation()
            # Never replace the retained USD population source with a camera-only
            # document: future hierarchy updates must still see the full scene.
            with ovstage.PathDictionary(self.stage) as paths:
                cam_plist = paths.create_path_list_from_strings(["/Render/Camera"])
                try:
                    cam_query = self.stage.query_from_path_list(cam_plist)
                    self.stage.write_attribute(cam_query, "omni:xform", self.ordinal,
                        np.asarray(matrix, dtype=np.float64).reshape(1, 4, 4), is_array=False,
                        semantic=ovstage.AttributeSemantic.MATRIX).wait()
                    self.stage.write_attribute(cam_query, "omni:resetXformStack", self.ordinal,
                        np.asarray([True], dtype=np.bool_), is_array=False, semantic=0).wait()
                    camera = UsdGeom.Camera.Get(camera_stage, "/Render/Camera")
                    self.stage.write_attribute(cam_query, "clippingRange", self.ordinal,
                        np.asarray([camera.GetClippingRangeAttr().Get()], dtype=np.float32),
                        is_array=False, semantic=0).wait()
                    self.stage.write_attribute(cam_query, "focalLength", self.ordinal,
                        np.asarray([camera.GetFocalLengthAttr().Get()], dtype=np.float32),
                        is_array=False, semantic=0).wait()
                    self.stage.write_attribute(cam_query, "focusDistance", self.ordinal,
                        np.asarray([camera.GetFocusDistanceAttr().Get()], dtype=np.float32),
                        is_array=False, semantic=0).wait()
                    self.stage.write_attribute(cam_query, "fStop", self.ordinal,
                        np.asarray([camera.GetFStopAttr().Get()], dtype=np.float32),
                        is_array=False, semantic=0).wait()
                    if size_changed:
                        self.stage.write_attribute(cam_query, "horizontalAperture", self.ordinal,
                            np.asarray([camera.GetHorizontalApertureAttr().Get()], dtype=np.float32),
                            is_array=False, semantic=0).wait()
                        self.stage.write_attribute(cam_query, "verticalAperture", self.ordinal,
                            np.asarray([camera.GetVerticalApertureAttr().Get()], dtype=np.float32),
                            is_array=False, semantic=0).wait()
                finally:
                    paths.destroy_path_list(cam_plist)
                prod_plist = paths.create_path_list_from_strings(["/Render/RenderProduct_Viewport"])
                try:
                    prod_query = self.stage.query_from_path_list(prod_plist)
                    if size_changed:
                        self.stage.write_attribute(prod_query, "resolution", self.ordinal,
                            np.asarray([[width, height]], dtype=np.int32), is_array=False, semantic=0).wait()
                    dof_enabled = bool(request.get("dofEnabled", False))
                    self.stage.write_attribute(prod_query, "omni:rtx:post:dof:enabled", self.ordinal,
                        np.asarray([dof_enabled], dtype=np.bool_), is_array=False, semantic=0).wait()
                    self.stage.write_attribute(prod_query, "omni:rtx:post:dof:overrideEnabled", self.ordinal,
                        np.asarray([dof_enabled], dtype=np.bool_), is_array=False, semantic=0).wait()
                finally:
                    paths.destroy_path_list(prod_plist)
            phase("computing transform hierarchy", timeout=60, report=False)
            self.stage.compute_hierarchy(
                self.ordinal,
                self.ordinal,
                model=ovstage.HierarchyComputationModel.CPU_INCREMENTAL,
            )
            self.stage.advance_write_floor(self.ordinal).wait()
        self.last_camera_key = camera_key
        self.last_width, self.last_height = width, height

        # 2. Update selection outline if requested
        requested_selection = request.get("selection", [])
        if not isinstance(requested_selection, list):
            requested_selection = [requested_selection] if requested_selection else []
        requested_selection = [str(p) for p in requested_selection if p]
        if switched_scene or scene_changed:
            self._material_users_cache = None

        if requested_selection != self.last_requested_selection or switched_scene or scene_changed:
            to_outline = []
            if requested_selection and self.current_usd_stage:
                from pxr import UsdShade
                for sel_path in requested_selection:
                    p = self.current_usd_stage.GetPrimAtPath(Sdf.Path(sel_path))
                    if not p:
                        continue
                    if p.IsA(UsdShade.Material):
                        if self._material_users_cache is None:
                            self._material_users_cache = usd_graph.compute_material_users(self.current_usd_stage)
                        to_outline.extend(sorted(self._material_users_cache.get(str(p.GetPath()), ())))
                    elif p.IsA(UsdGeom.Mesh):
                        to_outline.append(str(p.GetPath()))
                    else:
                        for child in self.current_usd_stage.Traverse(Usd.TraverseInstanceProxies()):
                            if child.GetPath().HasPrefix(p.GetPath()):
                                if child.IsA(UsdGeom.Mesh):
                                    to_outline.append(str(child.GetPath()))
                                elif child.IsA(UsdShade.Material):
                                    if self._material_users_cache is None:
                                        self._material_users_cache = usd_graph.compute_material_users(self.current_usd_stage)
                                    to_outline.extend(sorted(self._material_users_cache.get(str(child.GetPath()), ())))
            to_outline = list(dict.fromkeys(to_outline))
            # Remove outline on previous selection meshes
            if self.active_selection:
                try:
                    self.renderer.set_selection_outline_group_strings(self.active_selection, 0)
                except Exception:
                    pass
            # Set outline on new selection meshes
            if to_outline:
                try:
                    self.renderer.set_selection_outline_group_strings(to_outline, 1)
                except Exception:
                    pass
            self.last_requested_selection = requested_selection
            self.active_selection = to_outline

        scene_ms = (time.perf_counter() - t_scene_start) * 1000
        t_step_start = time.perf_counter()
        # 3. Step the Omniverse RTX renderer
        products = {"/Render/RenderProduct_Viewport"}

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
                self.renderer.enqueue_pick_query("/Render/RenderProduct_Viewport", l, t, r, b)
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
        hair_brushed = False
        if (
            isinstance(hair_brush, dict)
            and self.stage is not None
            and self.current_usd_stage is not None
            and hasattr(self, "last_camera_basis")
        ):
            active_graph = graph if isinstance(graph, dict) else self.last_graph
            hair_cfg = None
            if isinstance(active_graph, dict):
                tid = active_graph.get("targetNodeId")
                for n in active_graph.get("nodes") or []:
                    if n.get("id") == tid and n.get("operation") == "hair":
                        hair_cfg = n.get("hair")
                        break
            if isinstance(hair_cfg, dict) and hair_cfg.get("simEnabled"):
                _wdir = os.path.dirname(os.path.abspath(__file__))
                if _wdir not in sys.path:
                    sys.path.insert(0, _wdir)
                import hair_sim
                commit_usd = bool(hair_brush.get("commitUsd", False))
                updates = hair_sim.step_hair_brush(
                    self.current_usd_stage,
                    hair_cfg,
                    usd_graph._hair_query_matches,
                    hair_brush,
                    self.last_camera_basis,
                    write_usd=commit_usd,
                    return_gpu=(not commit_usd),
                )
                if updates:
                    self._write_hair_points_to_ovstage(updates)
                    self.last_update_kind = "hair-brush"
                    hair_brushed = True

        first_frame = switched_scene or scene_changed or camera_changed or size_changed or hair_brushed
        step_count = 1 if (interactive or need_pt_spp_raise) else (
            8 if (switched_scene and self.last_update_kind in ("material-delta", "material-delta-fast"))
            else (4 if (switched_scene and self.last_update_kind == "cache-hit")
                  else (2 if first_frame else (8 if render_mode == "pathTracing" else 4)))
        )
        result = None
        picked_prim = None
        pick_pending = bool(pick_coord)
        for step_idx in range(step_count):
            if step_idx > 0:
                phase("rendering NVIDIA frame", timeout=step_timeout, report=False)
            result = self.renderer.step(render_products=products, delta_time=1 / 60, ordinal=self.ordinal)
            if pick_pending:
                # Pick hits are attached only to the step immediately after the
                # query was enqueued; read them before further steps replace it.
                picked_prim = self._read_pick_hit(result["/Render/RenderProduct_Viewport"].frames[0])
                pick_pending = False
            if render_mode == "pathTracing" and bool(result["/Render/RenderProduct_Viewport"].frames[0].converged):
                break
            if not (switched_scene and self.last_update_kind in ("material-delta", "material-delta-fast")) and (time.perf_counter() - t_step_start) >= 1.5:
                break

        if not scene_changed and getattr(self, "_first_refinement_pending", False):
            self._first_refinement_pending = False
        if need_pt_spp_raise:
            self.ordinal += 1
            with ovstage.PathDictionary(self.stage) as paths:
                prod_plist = paths.create_path_list_from_strings(["/Render/RenderProduct_Viewport"])
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

        step_ms = (time.perf_counter() - t_step_start) * 1000
        t_capture_start = time.perf_counter()
        # 4. Extract pixel tensor and read back to CPU
        frame = result["/Render/RenderProduct_Viewport"].frames[0]

        render_var = frame.render_vars["/Render/RenderVar_LdrColor"]
        current_samples = int(frame.progression)
        # In standalone ovrtx PathTracing, frame.progression is the completed sample count (offset by +1)
        # and frame.converged is True once target_spp is reached.
        # In RealTimePathTracing, progression is temporal history frames (starts at 50) and reaches target_spp (or 16).
        if render_mode == "pathTracing":
            display_samples = min(target_spp, max(1, current_samples - 1 if current_samples > 1 else current_samples))
            converged = (not need_pt_spp_raise) and (bool(frame.converged) or display_samples >= target_spp)
        else:
            rt_samples = max(1, current_samples - 49) if current_samples >= 50 else max(1, current_samples)
            display_samples = min(target_spp, rt_samples)
            converged = bool(frame.converged) or rt_samples >= target_spp

        phase("reading NVIDIA pixels", timeout=20, report=False)
        with render_var.map(device=ovrtx.Device.CPU) as rv:
            arr = np.from_dlpack(rv)
            if arr.dtype in (np.float32, np.float64):
                pixels = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
            else:
                pixels = arr.copy()
            del arr

        del render_var, frame, result

        capture_ms = (time.perf_counter() - t_capture_start) * 1000
        # 5. Compress image to data URI (JPEG for interactive gestures, PNG for idle)
        if pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
            raise RuntimeError(f"Unexpected NVIDIA pixel tensor shape: {pixels.shape}")
        actual_height, actual_width = pixels.shape[:2]
        if (actual_width, actual_height) != (width, height):
            raise RuntimeError(f"NVIDIA returned {actual_width}x{actual_height} for requested {width}x{height}; refusing to stretch a stale frame")
        phase("encoding viewport image", timeout=15, report=False)
        img = Image.fromarray(pixels)
        buf = io.BytesIO()
        if interactive:
            # Drop alpha for JPEG
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img_rgb = img.convert("RGB")
            else:
                img_rgb = img
            img_rgb.save(buf, format="JPEG", quality=70)
            mime = "image/jpeg"
        else:
            img.save(buf, format="PNG")
            mime = "image/png"

        img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        data_uri = f"data:{mime};base64,{img_b64}"

        total_ms = (time.time() - t_total_start) * 1000.0

        return {
            "image": data_uri,
            "width": width,
            "height": height,
            "renderer": "OmniverseRTX",
            "interactive": interactive,
            "updateKind": self.last_update_kind,
            "gpu": "NVIDIA RTX",
            "converged": converged,
            "pickedPrim": picked_prim,
            "cameraAnchor": {
                "session": self.camera_fit_key[0], "fit": self.camera_fit_key[1],
                "center": list(self.camera_anchor[0]), "radius": self.camera_anchor[1],
            } if self.camera_fit_key[0] else None,
            "raySamples": {"current": display_samples, "target": target_spp},
            "frame": float(request.get("frame", 0)),
            "renderMs": step_ms,
            "captureMs": capture_ms,
            "sceneMs": scene_ms,
            "stepMs": step_ms,
            "totalMs": round(total_ms, 2),
            "pngBytes": len(buf.getvalue()),
            "upAxis": str(request.get("upAxis", "Y")).upper(),
            "graphStats": self.graph_stats,
            "missingAssets": self.material_diagnostics,
            "materialMode": "mixed" if self.material_fallbacks else "scene",
            "warnings": [f"Selective fallback applied to {len(self.material_fallbacks)} material(s) with missing shader assets or invalid inputs"] if self.material_fallbacks else [],
            "steps": step_count,
            "updateKind": self.last_update_kind,
            "vramUsedMb": _query_gpu_vram(interactive=interactive)[0],
            "vramTotalMb": _query_gpu_vram(interactive=interactive)[1],
            "cachedScenes": len(self.stage_pool),
            "timingMs": round(total_ms, 2),
            "stats": {
                "activeRenderer": "Omniverse RTX (NVIDIA ovrtx)",
                "device": "NVIDIA GeForce RTX 3090",
                "width": width,
                "height": height
            }
        }


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
        vp = viewport()
        vp.last_graph_hash = None
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
        return {"error": str(exc)}
    finally:
        done.set()


if __name__ == "__main__":
    try:
        if "--serve" in sys.argv:
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
                print(_PROTOCOL + json.dumps(res), flush=True)
        else:
            req = json.load(sys.stdin)
            print(json.dumps(respond(req)))
    finally:
        close_viewport()
