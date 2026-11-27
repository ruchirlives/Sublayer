"""Cache Sculptor USD animation cache editor & GPU Cloth Physics Brush engine.

Provides:
- Non-destructive multi-layer USD mesh cache editing (`apply_cache_edit`) using
  an anonymous `cache_edit.usda` overlay layer (never modifies source files).
- Temporal influence curves (`smoothstep`, `gaussian`, `linear`, `constant`) across
  `frameRange`, `singleFrame`, and `allFrames` modes, plus temporal smoothing filter.
- Compact sparse delta and vertex mask zlib+base64 serialization for single-JSON storage.
- Interactive GPU-accelerated (NVIDIA Warp CUDA, with NumPy fallback) sculpt & cloth
  physics brush sessions (`step_cache_brush`, `export_session_cache_deltas`) supporting
  both Hydra Storm (session-layer live updates) and Omniverse RTX (zero-copy DLPack).
"""

from __future__ import annotations

import base64
import hashlib
import heapq
import math
import sys
import zlib
from collections import OrderedDict

import numpy as np

from hair_colliders import (
    _build_merged_collider_mesh,
    _build_skel_context,
    _points_to_world_np,
    _triangulate_mesh,
)


# ---------------------------------------------------------------------------
# Query matching & temporal weighting
# ---------------------------------------------------------------------------


def _mesh_query_matches(prim_path: str, query: str) -> bool:
    q = (query or "").strip()
    if not q or q == "*":
        return True
    if q == "__none__":
        return False
    tokens = [t.strip() for t in q.replace(";", ",").split(",") if t.strip()]
    if not tokens:
        return True
    pl = prim_path.lower()
    for tok in tokens:
        tl = tok.lower()
        if "*" in tl:
            parts = [p for p in tl.split("*") if p]
            pos = 0
            ok = True
            for part in parts:
                idx = pl.find(part, pos)
                if idx < 0:
                    ok = False
                    break
                pos = idx + len(part)
            if ok:
                return True
        elif pl == tl or pl.startswith(tl + "/") or tl in pl:
            return True
    return False


def compute_temporal_weight(frame: float, layer: dict) -> float:
    """Compute the [0, 1] temporal influence weight of `layer` at `frame`."""
    mode = str(layer.get("mode") or "frameRange")
    if mode == "allFrames":
        return 1.0
    center = float(layer.get("centerFrame", 0.0))
    if mode == "singleFrame":
        return 1.0 if abs(frame - center) <= 1.0e-4 else 0.0

    # mode == "frameRange"
    fade_in = max(0.0, float(layer.get("fadeIn", 8.0)))
    fade_out = max(0.0, float(layer.get("fadeOut", 8.0)))
    if abs(frame - center) <= 1.0e-5:
        t = 1.0
    elif frame < center:
        if fade_in <= 1.0e-5:
            return 0.0
        t = 1.0 - (center - frame) / fade_in
    else:
        if fade_out <= 1.0e-5:
            return 0.0
        t = 1.0 - (frame - center) / fade_out

    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0

    falloff = str(layer.get("falloff") or "smoothstep")
    if falloff == "constant":
        return 1.0
    if falloff == "linear":
        return float(t)
    if falloff == "gaussian":
        # Smooth bell curve normalized to 0 at t=0 and 1 at t=1
        sigma = 0.42
        g0 = math.exp(-0.5 / (sigma * sigma))
        g = math.exp(-0.5 * ((1.0 - t) / sigma) ** 2)
        return float(max(0.0, min(1.0, (g - g0) / (1.0 - g0))))
    # Default: smoothstep
    return float(t * t * (3.0 - 2.0 * t))


# ---------------------------------------------------------------------------
# Sparse delta & vertex mask codec
# ---------------------------------------------------------------------------


def encode_mesh_delta(delta_np: np.ndarray, eps: float = 1.0e-6) -> dict | None:
    """Encode an `(N, 3)` float32 local-space vertex delta into compact JSON dict."""
    arr = np.ascontiguousarray(delta_np, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != 3 or arr.shape[0] == 0:
        return None
    mag = np.max(np.abs(arr), axis=1)
    changed = np.flatnonzero(mag > eps).astype(np.uint32)
    if changed.size == 0:
        return None
    n = int(arr.shape[0])
    if changed.size * 4 < n:
        idx_bytes = zlib.compress(changed.tobytes(), level=6)
        val_bytes = zlib.compress(np.ascontiguousarray(arr[changed], dtype=np.float32).tobytes(), level=6)
        return {
            "count": n,
            "indicesZB64": base64.b64encode(idx_bytes).decode("ascii"),
            "deltasZB64": base64.b64encode(val_bytes).decode("ascii"),
        }
    val_bytes = zlib.compress(arr.tobytes(), level=6)
    return {
        "count": n,
        "deltasZB64": base64.b64encode(val_bytes).decode("ascii"),
    }


def decode_mesh_delta(entry: dict | None, expected_count: int) -> np.ndarray | None:
    """Decode a stored mesh delta entry into an `(expected_count, 3)` float32 array."""
    if not isinstance(entry, dict) or expected_count <= 0:
        return None
    if int(entry.get("count", -1)) != expected_count:
        return None
    deltas_b64 = entry.get("deltasZB64")
    if not isinstance(deltas_b64, str) or not deltas_b64:
        return None
    try:
        raw_vals = zlib.decompress(base64.b64decode(deltas_b64))
        vals = np.frombuffer(raw_vals, dtype=np.float32)
    except Exception:
        return None

    idx_b64 = entry.get("indicesZB64")
    if isinstance(idx_b64, str) and idx_b64:
        try:
            raw_idx = zlib.decompress(base64.b64decode(idx_b64))
            idx = np.frombuffer(raw_idx, dtype=np.uint32)
        except Exception:
            return None
        if vals.size != idx.size * 3:
            return None
        if idx.size > 0 and int(idx.max()) >= expected_count:
            return None
        out = np.zeros((expected_count, 3), dtype=np.float32)
        out[idx] = vals.reshape(-1, 3)
        return out

    if vals.size != expected_count * 3:
        return None
    return np.ascontiguousarray(vals.reshape(expected_count, 3), dtype=np.float32)


def encode_mesh_mask(mask_np: np.ndarray, eps: float = 1.0e-3) -> dict | None:
    """Encode an `(N,)` float32 vertex mask `[0, 1]` as compressed uint8."""
    arr = np.clip(np.asarray(mask_np, dtype=np.float32), 0.0, 1.0)
    if arr.ndim != 1 or arr.size == 0 or float(arr.max()) <= eps:
        return None
    q = np.rint(arr * 255.0).astype(np.uint8)
    raw = zlib.compress(q.tobytes(), level=6)
    return {
        "count": int(arr.size),
        "weightsZB64": base64.b64encode(raw).decode("ascii"),
    }


def decode_mesh_mask(entry: dict | None, expected_count: int) -> np.ndarray:
    """Decode a vertex mask entry into an `(expected_count,)` float32 array in `[0, 1]`."""
    zeros = np.zeros((expected_count,), dtype=np.float32)
    if not isinstance(entry, dict) or expected_count <= 0:
        return zeros
    if int(entry.get("count", -1)) != expected_count:
        return zeros
    b64 = entry.get("weightsZB64")
    if not isinstance(b64, str) or not b64:
        return zeros
    try:
        raw = zlib.decompress(base64.b64decode(b64))
        q = np.frombuffer(raw, dtype=np.uint8)
        if q.size != expected_count:
            return zeros
        return (q.astype(np.float32) / 255.0).copy()
    except Exception:
        return zeros


# ---------------------------------------------------------------------------
# Topology extraction (structural edges, interior bend hinges, 1-ring CSR)
# ---------------------------------------------------------------------------


def _extract_mesh_topology(verts_world: np.ndarray, tris: np.ndarray):
    """Build structural edges, opposite-vertex bending pairs, and 1-ring CSR adjacency."""
    n_verts = int(verts_world.shape[0])
    tris_2d = np.ascontiguousarray(tris, dtype=np.int32).reshape(-1, 3)

    # Structural edges (unique undirected pairs) and edge -> adjacent opposite vertices
    edge_to_opp: dict[tuple[int, int], list[int]] = {}
    adj_sets: list[set[int]] = [set() for _ in range(n_verts)]

    for t0, t1, t2 in tris_2d.tolist():
        if t0 == t1 or t1 == t2 or t2 == t0:
            continue
        adj_sets[t0].add(t1)
        adj_sets[t0].add(t2)
        adj_sets[t1].add(t0)
        adj_sets[t1].add(t2)
        adj_sets[t2].add(t0)
        adj_sets[t2].add(t1)
        for a, b, opp in ((t0, t1, t2), (t1, t2, t0), (t2, t0, t1)):
            key = (a, b) if a < b else (b, a)
            lst = edge_to_opp.get(key)
            if lst is None:
                edge_to_opp[key] = [opp]
            elif len(lst) < 2 and opp != lst[0]:
                lst.append(opp)

    edges_v0 = []
    edges_v1 = []
    bend_v0 = []
    bend_v1 = []
    for (a, b), opps in edge_to_opp.items():
        edges_v0.append(a)
        edges_v1.append(b)
        if len(opps) == 2:
            bend_v0.append(opps[0])
            bend_v1.append(opps[1])

    e0 = np.asarray(edges_v0, dtype=np.int32)
    e1 = np.asarray(edges_v1, dtype=np.int32)
    if e0.size > 0:
        e_rest = np.linalg.norm(verts_world[e1] - verts_world[e0], axis=1).astype(np.float32)
        e_rest = np.maximum(e_rest, 1.0e-6)
    else:
        e_rest = np.zeros((0,), dtype=np.float32)

    b0 = np.asarray(bend_v0, dtype=np.int32)
    b1 = np.asarray(bend_v1, dtype=np.int32)
    if b0.size > 0:
        b_rest = np.linalg.norm(verts_world[b1] - verts_world[b0], axis=1).astype(np.float32)
        b_rest = np.maximum(b_rest, 1.0e-6)
    else:
        b_rest = np.zeros((0,), dtype=np.float32)

    offsets = np.zeros((n_verts + 1,), dtype=np.int32)
    neighbors_list: list[int] = []
    for i, s in enumerate(adj_sets):
        neighbors_list.extend(sorted(s))
        offsets[i + 1] = len(neighbors_list)
    neighbors = np.asarray(neighbors_list, dtype=np.int32)

    return {
        "tris": np.ascontiguousarray(tris_2d.reshape(-1), dtype=np.int32),
        "edge_v0": e0,
        "edge_v1": e1,
        "edge_rest": e_rest,
        "bend_v0": b0,
        "bend_v1": b1,
        "bend_rest": b_rest,
        "adj_offsets": offsets,
        "adj_neighbors": neighbors,
    }


def _recompute_normals_cpu(pos: np.ndarray, tris: np.ndarray) -> np.ndarray:
    normals = np.zeros_like(pos, dtype=np.float32)
    t = tris.reshape(-1, 3)
    if t.shape[0] == 0:
        normals[:, 1] = 1.0
        return normals
    v0 = pos[t[:, 0]]
    v1 = pos[t[:, 1]]
    v2 = pos[t[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    np.add.at(normals, t[:, 0], fn)
    np.add.at(normals, t[:, 1], fn)
    np.add.at(normals, t[:, 2], fn)
    lens = np.linalg.norm(normals, axis=1, keepdims=True)
    valid = lens[:, 0] > 1.0e-8
    normals[valid] /= lens[valid]
    normals[~valid] = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    return normals


# ---------------------------------------------------------------------------
# Full-Garment Temporal Cloth Simulation ("Simulate in Sublayer")
# ---------------------------------------------------------------------------

_CLOTH_SIM_CACHE: OrderedDict[str, tuple[list[float], list[np.ndarray]]] = OrderedDict()
_MAX_CLOTH_SIM_CACHE = 64
_CLOTH_PROXY_CACHE: OrderedDict[str, dict] = OrderedDict()
_CLOTH_LIVE_CACHE: dict[str, dict] = {}
_CLOTH_LIVE_RUNNERS: dict[str, dict] = {}


def clear_cloth_sim_cache(prim_paths: list[str] | None = None):
    """Clear interactive cloth simulation frame caches and live Warp solver runners."""
    if prim_paths is None:
        _CLOTH_LIVE_CACHE.clear()
        _CLOTH_LIVE_RUNNERS.clear()
    else:
        for pp in prim_paths:
            _CLOTH_LIVE_CACHE.pop(pp, None)
            _CLOTH_LIVE_RUNNERS.pop(pp, None)


def _emit_cloth_status(phase: str):
    try:
        main_mod = sys.modules.get("__main__")
        fn = getattr(main_mod, "report_status", None)
        if callable(fn):
            fn(phase)
            return
        import usd_graph

        usd_graph.report_status(phase)
    except Exception:
        pass


def _collect_collider_prims(stage, collider_query: str, exclude_path: str):
    from pxr import Usd, UsdGeom

    q = (collider_query or "").strip()
    if not q or q == "__none__":
        return []
    out = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        pp = str(prim.GetPath())
        if pp == exclude_path:
            continue
        if _mesh_query_matches(pp, q):
            out.append(prim)
    return out


def _sample_collider_at_time(collider_prims, tc, skel_ctx):
    from pxr import UsdGeom

    if not collider_prims:
        return None, None
    xform_cache = UsdGeom.XformCache(tc)
    unbounded_min = np.array([-1.0e9, -1.0e9, -1.0e9], dtype=np.float32)
    unbounded_max = np.array([1.0e9, 1.0e9, 1.0e9], dtype=np.float32)
    c_pts, c_tris, _ = _build_merged_collider_mesh(
        collider_prims, tc, xform_cache, unbounded_min, unbounded_max, np, skel_ctx=skel_ctx
    )
    if c_pts is None or c_tris is None or c_pts.shape[0] == 0 or c_tris.size == 0:
        return None, None
    return np.ascontiguousarray(c_pts, dtype=np.float32), np.ascontiguousarray(c_tris, dtype=np.int32)


def _compute_lra_tethers(pos_world: np.ndarray, topo: dict, mask_np: np.ndarray):
    """Multi-source Dijkstra from pinned vertices (`mask >= 0.5`) for Long-Range Attachment tethers.

    Returns `(anchor_idx, tether_dist)` arrays so free cloth vertices can never stretch
    beyond their geodesic rest distance from the nearest pinned vertex.
    """
    n = int(pos_world.shape[0])
    anchors = np.full((n,), -1, dtype=np.int32)
    dists = np.full((n,), np.inf, dtype=np.float64)
    pinned = np.flatnonzero(mask_np >= 0.5)
    if pinned.size == 0 or n == 0:
        return anchors, np.zeros((n,), dtype=np.float32)

    offsets = topo["adj_offsets"]
    neighbors = topo["adj_neighbors"]
    heap: list[tuple[float, int, int]] = []
    for p_idx in pinned.tolist():
        dists[p_idx] = 0.0
        anchors[p_idx] = p_idx
        heapq.heappush(heap, (0.0, p_idx, p_idx))

    while heap:
        d, u, root = heapq.heappop(heap)
        if d > dists[u] + 1.0e-9:
            continue
        pu = pos_world[u]
        s0 = int(offsets[u])
        s1 = int(offsets[u + 1])
        for k in range(s0, s1):
            v = int(neighbors[k])
            pv = pos_world[v]
            dx = float(pv[0] - pu[0])
            dy = float(pv[1] - pu[1])
            dz = float(pv[2] - pu[2])
            nd = d + math.sqrt(dx * dx + dy * dy + dz * dz)
            if nd < dists[v]:
                dists[v] = nd
                anchors[v] = root
                heapq.heappush(heap, (nd, v, root))

    out_dists = np.where(np.isfinite(dists), dists, 0.0).astype(np.float32)
    return anchors, out_dists


def _effective_stage_meters_per_unit(stage) -> float:
    """Return authored metersPerUnit from stage or its sublayers, defaulting to 1.0 (meters)."""
    from pxr import UsdGeom

    if stage is None:
        return 1.0
    if UsdGeom.StageHasAuthoredMetersPerUnit(stage):
        v = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
        return v if v > 1.0e-6 else 1.0
    for layer in stage.GetLayerStack(includeSessionLayers=False):
        pr = layer.pseudoRoot
        if pr and pr.HasInfo("metersPerUnit"):
            v = float(pr.GetInfo("metersPerUnit") or 1.0)
            return v if v > 1.0e-6 else 1.0
    return 1.0


def _collider_is_animated(collider_prims, skel_ctx) -> bool:
    from pxr import UsdGeom
    from hair_colliders import _transform_might_be_time_varying

    if not collider_prims:
        return False
    if skel_ctx:
        return True
    for prim in collider_prims:
        mesh = UsdGeom.Mesh(prim)
        pts_attr = mesh.GetPointsAttr()
        if pts_attr and pts_attr.GetNumTimeSamples() > 1:
            return True
        if _transform_might_be_time_varying(prim):
            return True
    return False


def _simulate_cloth_mesh(stage, prim, cfg: dict, status_label: str = ""):
    """Run GPU XPBD (with CPU fallback) full-garment cloth simulation for one USD Mesh prim.

    Returns `(sample_times, local_frame_pts)` where `sample_times` is a list of float
    frame numbers (or `[None]` for static mode) and `local_frame_pts` are `(N, 3)` float32
    arrays in the mesh prim's local USD space.
    """
    from pxr import Usd, UsdGeom

    prim_path = str(prim.GetPath())
    mesh = UsdGeom.Mesh(prim)
    pts_attr = mesh.GetPointsAttr()
    if not pts_attr:
        return [], []

    sim_mode = str(cfg.get("simMode") or "animated")
    start_f = int(round(float(cfg.get("simStartFrame", 0.0))))
    end_f = int(round(float(cfg.get("simEndFrame", 48.0))))
    if end_f < start_f:
        end_f = start_f
    fps = max(1.0, float(cfg.get("simFps", 24.0)))
    settle_steps = max(0, min(500, int(cfg.get("simSettleSteps", 12))))

    substeps = max(1, min(32, int(cfg.get("clothSubsteps", 4))))
    iterations = max(1, min(64, int(cfg.get("clothIterations", 8))))
    stretch_stiff = max(0.0, min(1.0, float(cfg.get("clothStretchStiffness", 0.95))))
    bend_stiff = max(0.0, min(1.0, float(cfg.get("clothBendStiffness", 0.35))))
    damping = max(0.0, min(0.95, float(cfg.get("clothDamping", 0.08))))
    mpu = _effective_stage_meters_per_unit(stage)
    sim_scale = max(1.0e-3, min(10000.0, float(cfg.get("clothSimScale", 1.0))))
    unit_scale = (1.0 / max(1.0e-5, mpu)) * sim_scale
    gravity_mag = float(cfg.get("clothGravity", 9.81)) * unit_scale
    margin = max(0.0, float(cfg.get("clothCollisionMargin", 0.004))) * unit_scale
    friction = max(0.0, min(1.0, float(cfg.get("clothFriction", 0.35))))
    air_drag = max(0.0, min(5.0, float(cfg.get("simAirDrag", 0.05))))
    wind_raw = cfg.get("simWind") or [0.0, 0.0, 0.0]
    wind_vec = (
        float(wind_raw[0]) * unit_scale,
        float(wind_raw[1]) * unit_scale,
        float(wind_raw[2]) * unit_scale,
    )
    self_col = bool(cfg.get("simSelfCollision", True))
    self_thick = max(0.0, float(cfg.get("simSelfThickness", 0.004))) * unit_scale
    pin_to_collider = bool(cfg.get("simPinToCollider", True))
    collider_query = str(cfg.get("colliderQuery") or "__none__")
    cache_ver = int(cfg.get("simCacheVersion", 0) or 0)

    up_axis = str(UsdGeom.GetStageUpAxis(stage) or "Y").upper()
    gravity_vec = (0.0, 0.0, -gravity_mag) if up_axis == "Z" else (0.0, -gravity_mag, 0.0)

    # Read start-frame mesh points & triangulation
    sample_times_in = list(pts_attr.GetTimeSamples())
    start_tc = Usd.TimeCode(float(start_f)) if sample_times_in else Usd.TimeCode.Default()
    raw_start_pts = pts_attr.Get(start_tc)
    if not raw_start_pts:
        return [], []
    start_local_np = np.ascontiguousarray(np.asarray(raw_start_pts, dtype=np.float32))
    n_verts = int(start_local_np.shape[0])
    if n_verts == 0:
        return [], []

    xform_start = UsdGeom.XformCache(start_tc)
    _, tris = _triangulate_mesh(mesh, start_tc, xform_start, np)
    if tris is None or tris.size == 0:
        return [], []
    world_mat_start = np.asarray(xform_start.GetLocalToWorldTransform(prim), dtype=np.float32)
    inv_world_start = np.asarray(xform_start.GetLocalToWorldTransform(prim).GetInverse(), dtype=np.float32)
    pos_world_start = _points_to_world_np(start_local_np, world_mat_start, np)

    # Vertex pin mask (from Cache Sculptor maskWeights)
    mask_map = cfg.get("maskWeights") if isinstance(cfg.get("maskWeights"), dict) else {}
    mask_np = decode_mesh_mask(mask_map.get(prim_path), n_verts)

    # Discover colliders
    collider_prims = _collect_collider_prims(stage, collider_query, prim_path)
    skel_ctx = _build_skel_context(stage) if collider_prims else None

    # Build deterministic LRU cache key
    h = hashlib.sha256()
    h.update(prim_path.encode("utf-8"))
    h.update(start_local_np.tobytes())
    h.update(world_mat_start.tobytes())
    h.update(np.ascontiguousarray(tris, dtype=np.int32).tobytes())
    h.update(mask_np.tobytes())
    params_sig = (
        sim_mode,
        start_f,
        end_f,
        round(fps, 4),
        settle_steps,
        substeps,
        iterations,
        round(stretch_stiff, 5),
        round(bend_stiff, 5),
        round(damping, 5),
        round(sim_scale, 5),
        round(gravity_mag, 5),
        round(margin, 6),
        round(friction, 5),
        round(air_drag, 5),
        tuple(round(v, 5) for v in wind_vec),
        self_col,
        round(self_thick, 6),
        pin_to_collider,
        collider_query,
        up_axis,
        cache_ver,
    )
    h.update(repr(params_sig).encode("utf-8"))

    # Include start/end collider samples in cache key so animated collider changes invalidate cache
    c_pts_start, c_tris_start = _sample_collider_at_time(
        collider_prims, Usd.TimeCode(float(start_f)), skel_ctx
    )
    if c_pts_start is not None:
        h.update(c_pts_start.tobytes())
        h.update(c_tris_start.tobytes())
    if sim_mode == "animated" and end_f > start_f and collider_prims:
        c_pts_end, _ = _sample_collider_at_time(
            collider_prims, Usd.TimeCode(float(end_f)), skel_ctx
        )
        if c_pts_end is not None:
            h.update(c_pts_end.tobytes())

    cache_key = h.hexdigest()
    cached = _CLOTH_SIM_CACHE.get(cache_key)
    if cached is not None:
        _CLOTH_SIM_CACHE.move_to_end(cache_key)
        times_c, frames_c = cached
        return list(times_c), [arr.copy() for arr in frames_c]

    import cloth_proxy as cp

    use_proxy = n_verts > cp.PROXY_VERTEX_THRESHOLD
    proxy = None
    if use_proxy:
        p_key_h = hashlib.sha256()
        p_key_h.update(prim_path.encode("utf-8"))
        p_key_h.update(pos_world_start.tobytes())
        p_key_h.update(np.ascontiguousarray(tris, dtype=np.int32).tobytes())
        p_key = p_key_h.hexdigest()
        proxy = _CLOTH_PROXY_CACHE.get(p_key)
        if proxy is not None:
            _CLOTH_PROXY_CACHE.move_to_end(p_key)
        else:
            _emit_cloth_status(f"Cloth sim {status_label}: building coarse proxy ({n_verts:,} verts)...")
            proxy = cp.build_cloth_proxy(pos_world_start, tris)
            _CLOTH_PROXY_CACHE[p_key] = proxy
            while len(_CLOTH_PROXY_CACHE) > 4:
                _CLOTH_PROXY_CACHE.popitem(last=False)
        n_sim = proxy["n_proxy"]
        sim_pos_start = proxy["proxy_pos"]
        sim_mask = cp.aggregate_proxy_mask(mask_np, proxy["vert_to_cluster"], n_sim)
        topo = proxy
    else:
        n_sim = n_verts
        sim_pos_start = pos_world_start
        sim_mask = mask_np
        topo = _extract_mesh_topology(pos_world_start, tris)

    inv_mass_np = np.clip(1.0 - sim_mask, 0.0, 1.0).astype(np.float32)
    inv_mass_np[sim_mask >= 0.999] = 0.0

    import cloth_kernels as ck

    wp, dev = ck.get_warp()
    frame_list = [start_f] if sim_mode == "static" else list(range(start_f, end_f + 1))
    dt_sub = (1.0 / fps) / float(substeps)
    inv_dt_sub = 1.0 / max(1.0e-6, dt_sub)
    median_edge = float(np.median(topo["edge_rest"])) if topo["edge_rest"].size > 0 else 0.01 * unit_scale
    max_step = min(0.05 * unit_scale, max(median_edge * 1.0, 0.005 * unit_scale))
    max_speed = 6.0 * unit_scale
    settle_max_speed = 2.5 * unit_scale
    max_query_dist = max(0.25 * unit_scale, margin * 12.0)
    # Give coarse proxy extra clearance so chords between ~1.5cm proxy nodes don't cut into curved limbs
    proxy_margin = (margin + median_edge * 0.35) if use_proxy else margin

    out_times: list[float] = []
    out_local_frames: list[np.ndarray] = []

    if wp is not None and dev is not None:
        wp_pos = wp.array(sim_pos_start, dtype=wp.vec3, device=dev)
        wp_rest_pos = wp.array(sim_pos_start, dtype=wp.vec3, device=dev)
        wp_prev = wp.zeros(n_sim, dtype=wp.vec3, device=dev)
        wp_vel = wp.zeros(n_sim, dtype=wp.vec3, device=dev)
        wp_normals = wp.zeros(n_sim, dtype=wp.vec3, device=dev)
        wp_pin_w = wp.array(sim_mask, dtype=float, device=dev)
        wp_pin_target = wp.array(sim_pos_start, dtype=wp.vec3, device=dev)
        wp_inv_mass = wp.array(inv_mass_np, dtype=float, device=dev)
        wp_local_out = wp.zeros(n_verts, dtype=wp.vec3, device=dev)
        wp_fine_start = wp.array(pos_world_start, dtype=wp.vec3, device=dev) if use_proxy else None
        wp_fine_out = wp.zeros(n_verts, dtype=wp.vec3, device=dev) if use_proxy else None
        wp_fine_mask = wp.array(mask_np, dtype=float, device=dev) if use_proxy else None
        wp_bind_idx = wp.array(proxy["bind_idx"], dtype=int, device=dev) if use_proxy else None
        wp_bind_w = wp.array(proxy["bind_w"], dtype=float, device=dev) if use_proxy else None
        wp_tris = wp.array(topo["tris"], dtype=int, device=dev)
        wp_e0 = wp.array(topo["edge_v0"], dtype=int, device=dev)
        wp_e1 = wp.array(topo["edge_v1"], dtype=int, device=dev)
        wp_erest = wp.array(topo["edge_rest"], dtype=float, device=dev)
        wp_b0 = wp.array(topo["bend_v0"], dtype=int, device=dev)
        wp_b1 = wp.array(topo["bend_v1"], dtype=int, device=dev)
        wp_brest = wp.array(topo["bend_rest"], dtype=float, device=dev)
        wp_adj_off = wp.array(topo["adj_offsets"], dtype=int, device=dev)
        wp_adj_nbr = wp.array(topo["adj_neighbors"], dtype=int, device=dev)
        wp_corr_sum = wp.zeros(n_sim, dtype=wp.vec3, device=dev)
        wp_corr_cnt = wp.zeros(n_sim, dtype=int, device=dev)

        n_tris = int(topo["tris"].size // 3)
        n_edges = int(topo["edge_v0"].size)
        n_bends = int(topo["bend_v0"].size)
        g_vec = wp.vec3(*gravity_vec)
        w_vec = wp.vec3(*wind_vec)

        tether_anchors_np, tether_dists_np = _compute_lra_tethers(sim_pos_start, topo, sim_mask)
        has_lra = bool(np.any(tether_anchors_np >= 0))
        wp_tether_anchor = wp.array(tether_anchors_np, dtype=int, device=dev) if has_lra else None
        wp_tether_dist = wp.array(tether_dists_np, dtype=float, device=dev) if has_lra else None
        lra_allowance = max(0.005, (1.0 - stretch_stiff) * 0.15 + 0.01)
        # Keep total stretch work per frame bounded as substeps increases (smaller dt already
        # increases effective XPBD stiffness quadratically).
        sim_stretch_iters = (
            max(20, min(64, int(round((iterations * 8) * (4.0 / max(1, substeps))))))
            if stretch_stiff > 0.0
            else 0
        )
        sim_bend_iters = max(1, min(4, iterations // 3)) if bend_stiff > 0.0 else 0

        hash_grid = None
        if self_col and self_thick > 1.0e-6:
            hash_grid = wp.HashGrid(dim_x=64, dim_y=64, dim_z=64, device=dev)

        # Initialize start-frame collider mesh & collider-bound pins
        wp_col_pts = None
        wp_col_tris = None
        wp_col_mesh = None
        wp_col_pts_a = None
        wp_col_pts_b = None
        wp_pin_face = wp.full(n_sim, -1, dtype=int, device=dev)
        wp_pin_uv = wp.zeros(n_sim, dtype=wp.vec2, device=dev)
        wp_pin_offset = wp.zeros(n_sim, dtype=float, device=dev)
        has_bound_pins = False

        if c_pts_start is not None and c_tris_start is not None:
            wp_col_pts = wp.array(c_pts_start, dtype=wp.vec3, device=dev)
            wp_col_tris = wp.array(c_tris_start, dtype=int, device=dev)
            wp_col_mesh = wp.Mesh(points=wp_col_pts, indices=wp_col_tris)
            wp_col_pts_a = wp.array(c_pts_start, dtype=wp.vec3, device=dev)
            wp_col_pts_b = wp.array(c_tris_start, dtype=wp.vec3, device=dev) if False else wp.array(c_pts_start, dtype=wp.vec3, device=dev)
            if pin_to_collider and float(sim_mask.max()) > 1.0e-4:
                wp.launch(
                    ck.cloth_bind_collider_pins_kernel,
                    dim=n_sim,
                    inputs=[
                        wp_pos,
                        wp_pin_w,
                        wp_col_mesh.id,
                        margin,
                        max_query_dist,
                        wp_pin_face,
                        wp_pin_uv,
                        wp_pin_offset,
                    ],
                    device=dev,
                )
                has_bound_pins = True

        def _run_substep(col_mesh_ref, step_dt, step_inv_dt, step_damping=damping, step_max_speed=max_speed, rebuild_hash=True):
            if col_mesh_ref is not None and has_bound_pins:
                wp.launch(
                    ck.cloth_eval_collider_pins_kernel,
                    dim=n_sim,
                    inputs=[
                        wp_pin_w,
                        wp_pin_face,
                        wp_pin_uv,
                        wp_pin_offset,
                        col_mesh_ref.id,
                        wp_pin_target,
                    ],
                    device=dev,
                )
            wp_normals.zero_()
            if n_tris > 0:
                wp.launch(
                    ck.accum_face_normals_kernel,
                    dim=n_tris,
                    inputs=[wp_pos, wp_tris, wp_normals],
                    device=dev,
                )
            wp.launch(
                ck.normalize_normals_kernel,
                dim=n_sim,
                inputs=[wp_normals],
                device=dev,
            )
            wp.launch(
                ck.cloth_sim_predict_kernel,
                dim=n_sim,
                inputs=[
                    wp_pos,
                    wp_prev,
                    wp_vel,
                    wp_normals,
                    wp_pin_w,
                    wp_pin_target,
                    g_vec,
                    w_vec,
                    air_drag,
                    step_dt,
                    step_damping,
                    step_max_speed,
                ],
                device=dev,
            )
            # Note: cloth_apply_jacobi_kernel clears corr_sum & corr_count per vertex,
            # so we only zero once before the constraint loop instead of every iteration.
            wp_corr_sum.zero_()
            wp_corr_cnt.zero_()
            # 1. Dedicated inextensible structural stretch passes (with midpoint collider projection
            #    so stretch wraps cloth around curved limbs instead of pulling through the body)
            if n_edges > 0 and sim_stretch_iters > 0:
                mid_it = sim_stretch_iters // 2
                for s_it in range(sim_stretch_iters):
                    wp.launch(
                        ck.cloth_stretch_jacobi_kernel,
                        dim=n_edges,
                        inputs=[
                            wp_pos,
                            wp_inv_mass,
                            wp_e0,
                            wp_e1,
                            wp_erest,
                            wp_corr_sum,
                            wp_corr_cnt,
                            stretch_stiff,
                        ],
                        device=dev,
                    )
                    wp.launch(
                        ck.cloth_apply_jacobi_kernel,
                        dim=n_sim,
                        inputs=[wp_pos, wp_corr_sum, wp_corr_cnt, 1.65, max_step],
                        device=dev,
                    )
                    if col_mesh_ref is not None and s_it == mid_it:
                        wp.launch(
                            ck.cloth_collider_kernel,
                            dim=n_sim,
                            inputs=[
                                wp_pos,
                                wp_prev,
                                wp_inv_mass,
                                col_mesh_ref.id,
                                proxy_margin,
                                friction,
                                max_query_dist,
                            ],
                            device=dev,
                        )
            # 2. Separate bending hinge passes for crisp cloth folds
            if n_bends > 0 and sim_bend_iters > 0:
                for _ in range(sim_bend_iters):
                    wp.launch(
                        ck.cloth_bend_jacobi_kernel,
                        dim=n_bends,
                        inputs=[
                            wp_pos,
                            wp_inv_mass,
                            wp_b0,
                            wp_b1,
                            wp_brest,
                            wp_corr_sum,
                            wp_corr_cnt,
                            bend_stiff,
                        ],
                        device=dev,
                    )
                    wp.launch(
                        ck.cloth_apply_jacobi_kernel,
                        dim=n_sim,
                        inputs=[wp_pos, wp_corr_sum, wp_corr_cnt, 0.65, max_step],
                        device=dev,
                    )
            # 3. Quick stretch cleanup + Long-Range Attachment (LRA) inextensibility tether
            if n_edges > 0 and stretch_stiff > 0.0:
                for _ in range(8):
                    wp.launch(
                        ck.cloth_stretch_jacobi_kernel,
                        dim=n_edges,
                        inputs=[
                            wp_pos,
                            wp_inv_mass,
                            wp_e0,
                            wp_e1,
                            wp_erest,
                            wp_corr_sum,
                            wp_corr_cnt,
                            stretch_stiff,
                        ],
                        device=dev,
                    )
                    wp.launch(
                        ck.cloth_apply_jacobi_kernel,
                        dim=n_sim,
                        inputs=[wp_pos, wp_corr_sum, wp_corr_cnt, 1.65, max_step],
                        device=dev,
                    )
            if has_lra and wp_tether_anchor is not None and wp_tether_dist is not None:
                wp.launch(
                    ck.cloth_lra_tether_kernel,
                    dim=n_sim,
                    inputs=[wp_pos, wp_inv_mass, wp_tether_anchor, wp_tether_dist, lra_allowance],
                    device=dev,
                )
            if hash_grid is not None:
                if rebuild_hash:
                    hash_grid.build(points=wp_pos, radius=self_thick)
                wp.launch(
                    ck.cloth_self_collision_kernel,
                    dim=n_sim,
                    inputs=[
                        hash_grid.id,
                        wp_pos,
                        wp_rest_pos,
                        wp_inv_mass,
                        wp_adj_off,
                        wp_adj_nbr,
                        wp_corr_sum,
                        wp_corr_cnt,
                        self_thick,
                    ],
                    device=dev,
                )
                wp.launch(
                    ck.cloth_apply_jacobi_kernel,
                    dim=n_sim,
                    inputs=[wp_pos, wp_corr_sum, wp_corr_cnt, 0.5, min(max_step, self_thick * 0.5)],
                    device=dev,
                )
            if col_mesh_ref is not None:
                wp.launch(
                    ck.cloth_collider_kernel,
                    dim=n_sim,
                    inputs=[
                        wp_pos,
                        wp_prev,
                        wp_inv_mass,
                        col_mesh_ref.id,
                        proxy_margin,
                        friction,
                        max_query_dist,
                    ],
                    device=dev,
                )
            wp.launch(
                ck.cloth_sim_update_vel_kernel,
                dim=n_sim,
                inputs=[wp_pos, wp_prev, wp_vel, wp_inv_mass, step_inv_dt, step_damping * 0.25, step_max_speed],
                device=dev,
            )

        wp_fine_inv_mass = wp.array(np.clip(1.0 - mask_np, 0.0, 1.0).astype(np.float32), dtype=float, device=dev) if use_proxy else None

        def _read_local_frame(inv_mat_np):
            src_world = wp_pos
            if use_proxy:
                wp.launch(
                    ck.cloth_reconstruct_fine_kernel,
                    dim=n_verts,
                    inputs=[
                        wp_fine_start,
                        wp_fine_out,
                        wp_fine_mask,
                        wp_rest_pos,
                        wp_pos,
                        wp_bind_idx,
                        wp_bind_w,
                    ],
                    device=dev,
                )
                # Final fine-vertex collider push-out so reconstructed 95k fine verts never poke through curved limbs
                if wp_col_mesh is not None and wp_fine_inv_mass is not None:
                    wp.launch(
                        ck.cloth_collider_kernel,
                        dim=n_verts,
                        inputs=[
                            wp_fine_out,
                            wp_fine_out,
                            wp_fine_inv_mass,
                            wp_col_mesh.id,
                            margin,
                            0.0,
                            max_query_dist,
                        ],
                        device=dev,
                    )
                src_world = wp_fine_out
            m0 = wp.vec3(float(inv_mat_np[0, 0]), float(inv_mat_np[0, 1]), float(inv_mat_np[0, 2]))
            m1 = wp.vec3(float(inv_mat_np[1, 0]), float(inv_mat_np[1, 1]), float(inv_mat_np[1, 2]))
            m2 = wp.vec3(float(inv_mat_np[2, 0]), float(inv_mat_np[2, 1]), float(inv_mat_np[2, 2]))
            t = wp.vec3(float(inv_mat_np[3, 0]), float(inv_mat_np[3, 1]), float(inv_mat_np[3, 2]))
            wp.launch(
                ck.world_to_local_kernel,
                dim=n_verts,
                inputs=[src_world, wp_local_out, m0, m1, m2, t],
                device=dev,
            )
            return np.ascontiguousarray(wp_local_out.numpy(), dtype=np.float32)

        # Preroll / static settle at start frame
        prefix = f"Cloth sim {status_label}" if status_label else "Cloth sim"
        preroll = max(1, settle_steps) if sim_mode == "static" else settle_steps
        if preroll > 0:
            _emit_cloth_status(f"{prefix}: settling ({preroll} steps)...")
            settle_damp = max(damping, 0.35)
            for p_i in range(preroll):
                _run_substep(wp_col_mesh, dt_sub, inv_dt_sub, settle_damp, settle_max_speed, rebuild_hash=(p_i % 2 == 0))
            wp_vel.zero_()

        if sim_mode == "static":
            out_times.append(float(start_f))
            out_local_frames.append(_read_local_frame(inv_world_start))
        else:
            out_times.append(float(start_f))
            out_local_frames.append(_read_local_frame(inv_world_start))
            col_animated = _collider_is_animated(collider_prims, skel_ctx)
            prev_col_pts = c_pts_start
            prev_col_tris = c_tris_start
            total_frames = max(1, len(frame_list) - 1)
            for idx_f, f in enumerate(frame_list[1:], start=1):
                if idx_f == 1 or idx_f % 8 == 0 or idx_f == total_frames:
                    _emit_cloth_status(f"{prefix}: frame {f}/{end_f}...")
                tc_f = Usd.TimeCode(float(f))
                # Update non-collider pin targets if cloth itself is animated upstream
                if not has_bound_pins and sample_times_in and float(sim_mask.max()) > 1.0e-4:
                    raw_f = pts_attr.Get(tc_f)
                    if raw_f is not None and len(raw_f) == n_verts:
                        xf_f = UsdGeom.XformCache(tc_f)
                        wm_f = np.asarray(xf_f.GetLocalToWorldTransform(prim), dtype=np.float32)
                        pin_w_f = _points_to_world_np(np.asarray(raw_f, dtype=np.float32), wm_f, np)
                        if use_proxy and proxy is not None:
                            pin_w_f = cp.recompute_proxy_positions(
                                pin_w_f, proxy["vert_to_cluster"], proxy["cluster_counts"], n_sim
                            )
                        wp_pin_target = wp.array(pin_w_f, dtype=wp.vec3, device=dev)

                same_col_topo = False
                cur_col_pts, cur_col_tris = prev_col_pts, prev_col_tris
                if col_animated:
                    cur_col_pts, cur_col_tris = _sample_collider_at_time(collider_prims, tc_f, skel_ctx)
                    same_col_topo = (
                        wp_col_mesh is not None
                        and cur_col_pts is not None
                        and prev_col_pts is not None
                        and cur_col_pts.shape == prev_col_pts.shape
                        and cur_col_tris is not None
                        and prev_col_tris is not None
                        and cur_col_tris.size == prev_col_tris.size
                    )
                    if same_col_topo:
                        wp_col_pts_a = wp.array(prev_col_pts, dtype=wp.vec3, device=dev)
                        wp_col_pts_b = wp.array(cur_col_pts, dtype=wp.vec3, device=dev)
                    elif cur_col_pts is not None and cur_col_tris is not None:
                        wp_col_pts = wp.array(cur_col_pts, dtype=wp.vec3, device=dev)
                        wp_col_tris = wp.array(cur_col_tris, dtype=int, device=dev)
                        wp_col_mesh = wp.Mesh(points=wp_col_pts, indices=wp_col_tris)
                        has_bound_pins = False

                for s_i in range(substeps):
                    if same_col_topo:
                        alpha = float(s_i + 1) / float(substeps)
                        wp.launch(
                            ck.cloth_lerp_collider_pts_kernel,
                            dim=int(cur_col_pts.shape[0]),
                            inputs=[wp_col_pts_a, wp_col_pts_b, wp_col_pts, alpha],
                            device=dev,
                        )
                        wp_col_mesh.refit()
                    _run_substep(wp_col_mesh, dt_sub, inv_dt_sub, rebuild_hash=(s_i == 0))

                prev_col_pts = cur_col_pts
                prev_col_tris = cur_col_tris

                xf_out = UsdGeom.XformCache(tc_f)
                inv_wm_f = np.asarray(xf_out.GetLocalToWorldTransform(prim).GetInverse(), dtype=np.float32)
                out_times.append(float(f))
                out_local_frames.append(_read_local_frame(inv_wm_f))
    else:
        # Lightweight CPU fallback when Warp is unavailable
        pos = sim_pos_start.copy()
        vel = np.zeros_like(pos)
        g_np = np.array(gravity_vec, dtype=np.float32)
        w_np = np.array(wind_vec, dtype=np.float32)
        e0, e1, erest = topo["edge_v0"], topo["edge_v1"], topo["edge_rest"]
        b0, b1, brest = topo["bend_v0"], topo["bend_v1"], topo["bend_rest"]
        pinned = sim_mask >= 0.999

        def _cpu_substep():
            nonlocal pos, vel
            prev = pos.copy()
            free = ~pinned
            vel[free] = vel[free] * max(0.0, 1.0 - damping) + (g_np + (w_np - vel[free]) * (air_drag * 0.25)) * dt_sub
            pos[free] = pos[free] + vel[free] * dt_sub
            for _ in range(iterations):
                for idx_a, idx_b, rest_l, st in ((e0, e1, erest, stretch_stiff), (b0, b1, brest, bend_stiff)):
                    if idx_a.size == 0 or st <= 0.0:
                        continue
                    diff = pos[idx_b] - pos[idx_a]
                    dist = np.linalg.norm(diff, axis=1)
                    valid = dist > 1.0e-7
                    if not np.any(valid):
                        continue
                    wa = inv_mass_np[idx_a[valid]]
                    wb = inv_mass_np[idx_b[valid]]
                    ws = wa + wb
                    ok = ws > 1.0e-8
                    if not np.any(ok):
                        continue
                    ia = idx_a[valid][ok]
                    ib = idx_b[valid][ok]
                    err = dist[valid][ok] - rest_l[valid][ok]
                    n = diff[valid][ok] / dist[valid][ok][:, None]
                    sc = (st * err / ws[ok])[:, None]
                    np.add.at(pos, ia, n * (sc * wa[ok][:, None] * 0.5))
                    np.add.at(pos, ib, -n * (sc * wb[ok][:, None] * 0.5))
            pos[pinned] = sim_pos_start[pinned]
            vel[free] = (pos[free] - prev[free]) * inv_dt_sub
            vel[pinned] = 0.0

        preroll = max(1, settle_steps) if sim_mode == "static" else settle_steps
        for _ in range(preroll):
            _cpu_substep()
        vel.fill(0.0)
        for idx_f, f in enumerate(frame_list):
            if idx_f > 0:
                for _ in range(substeps):
                    _cpu_substep()
            out_times.append(float(f))
            if use_proxy and proxy is not None:
                b_idx = proxy["bind_idx"].reshape(n_verts, 4)
                b_w = proxy["bind_w"].reshape(n_verts, 4)
                p_disp = pos - sim_pos_start
                fine_disp = np.sum(p_disp[b_idx] * b_w[:, :, None], axis=1)
                fine_p = pos_world_start + fine_disp * np.clip(1.0 - mask_np, 0.0, 1.0)[:, None]
                out_local_frames.append(_points_to_world_np(fine_p, inv_world_start, np))
            else:
                out_local_frames.append(_points_to_world_np(pos, inv_world_start, np))

    _CLOTH_SIM_CACHE[cache_key] = (list(out_times), [arr.copy() for arr in out_local_frames])
    while len(_CLOTH_SIM_CACHE) > _MAX_CLOTH_SIM_CACHE:
        _CLOTH_SIM_CACHE.popitem(last=False)

    return out_times, out_local_frames


def _cloth_live_params_sig(cfg: dict, prim_path: str, n_verts: int, mask_np: np.ndarray, up_axis: str):
    return (
        prim_path,
        n_verts,
        hashlib.sha256(mask_np.tobytes()).hexdigest()[:16],
        str(cfg.get("simMode") or "animated"),
        int(cfg.get("clothSubsteps", 4) or 4),
        int(cfg.get("clothIterations", 8) or 8),
        round(float(cfg.get("clothStretchStiffness", 0.95)), 5),
        round(float(cfg.get("clothBendStiffness", 0.35)), 5),
        round(float(cfg.get("clothDamping", 0.08)), 5),
        round(float(cfg.get("clothSimScale", 1.0) or 1.0), 5),
        round(float(cfg.get("clothGravity", 9.81)), 5),
        round(float(cfg.get("clothCollisionMargin", 0.004)), 6),
        round(float(cfg.get("clothFriction", 0.35)), 5),
        round(float(cfg.get("simAirDrag", 0.05)), 5),
        tuple(round(float(v), 5) for v in (cfg.get("simWind") or (0.0, 0.0, 0.0))[:3]),
        bool(cfg.get("simSelfCollision", True)),
        round(float(cfg.get("simSelfThickness", 0.004)), 6),
        bool(cfg.get("simPinToCollider", True)),
        str(cfg.get("colliderQuery") or "__none__").strip(),
        int(cfg.get("simCacheVersion", 0) or 0),
        up_axis,
    )


def _build_cloth_step_runner(
    stage,
    prim,
    cfg: dict,
    start_f: int,
    initial_local_pts: np.ndarray | None = None,
    do_preroll: bool = False,
    status_label: str = "",
):
    """Initialize a persistent GPU (or CPU) cloth solver starting from `start_f`."""
    from pxr import Usd, UsdGeom

    mesh = UsdGeom.Mesh(prim)
    pts_attr = mesh.GetPointsAttr()
    if not pts_attr:
        return None
    prim_path = str(prim.GetPath())
    mpu = _effective_stage_meters_per_unit(stage)
    sim_scale = max(1.0e-3, min(10000.0, float(cfg.get("clothSimScale", 1.0) or 1.0)))
    unit_scale = (1.0 / max(1.0e-5, mpu)) * sim_scale
    up_axis = str(UsdGeom.GetStageUpAxis(stage) or "Y").upper()
    fps = max(1.0, float(cfg.get("simFps", stage.GetFramesPerSecond() or stage.GetTimeCodesPerSecond() or 24.0)))

    settle_steps = max(0, min(500, int(cfg.get("simSettleSteps", 12) or 0)))
    substeps = max(1, min(32, int(cfg.get("clothSubsteps", 4) or 4)))
    iterations = max(1, min(64, int(cfg.get("clothIterations", 8) or 8)))
    stretch_stiff = max(0.0, min(1.0, float(cfg.get("clothStretchStiffness", 0.95))))
    bend_stiff = max(0.0, min(1.0, float(cfg.get("clothBendStiffness", 0.35))))
    damping = max(0.0, min(0.95, float(cfg.get("clothDamping", 0.08))))
    gravity_mag = float(cfg.get("clothGravity", 9.81)) * unit_scale
    margin = max(0.0, float(cfg.get("clothCollisionMargin", 0.004))) * unit_scale
    friction = max(0.0, min(1.0, float(cfg.get("clothFriction", 0.35))))
    air_drag = max(0.0, min(5.0, float(cfg.get("simAirDrag", 0.05))))
    raw_wind = cfg.get("simWind")
    if isinstance(raw_wind, (list, tuple)) and len(raw_wind) >= 3:
        wind_vec = (float(raw_wind[0]) * unit_scale, float(raw_wind[1]) * unit_scale, float(raw_wind[2]) * unit_scale)
    else:
        wind_vec = (0.0, 0.0, 0.0)
    self_col = bool(cfg.get("simSelfCollision", True))
    self_thick = max(0.0, float(cfg.get("simSelfThickness", 0.004))) * unit_scale
    pin_to_collider = bool(cfg.get("simPinToCollider", True))
    collider_query = str(cfg.get("colliderQuery") or "__none__").strip()
    gravity_vec = (0.0, 0.0, -gravity_mag) if up_axis == "Z" else (0.0, -gravity_mag, 0.0)

    sample_times_in = list(pts_attr.GetTimeSamples())
    start_tc = Usd.TimeCode(float(start_f))
    if initial_local_pts is not None:
        start_local_np = np.ascontiguousarray(initial_local_pts, dtype=np.float32)
    else:
        raw_pts = pts_attr.Get(start_tc if sample_times_in else Usd.TimeCode.Default())
        if raw_pts is None or len(raw_pts) == 0:
            return None
        start_local_np = np.ascontiguousarray(raw_pts, dtype=np.float32)
    n_verts = int(start_local_np.shape[0])
    if n_verts == 0:
        return None

    xform_start = UsdGeom.XformCache(start_tc)
    _, tris = _triangulate_mesh(mesh, start_tc, xform_start, np)
    if tris is None or tris.size == 0:
        return None
    world_mat_start = np.asarray(xform_start.GetLocalToWorldTransform(prim), dtype=np.float32)
    inv_world_start = np.asarray(xform_start.GetLocalToWorldTransform(prim).GetInverse(), dtype=np.float32)
    pos_world_start = _points_to_world_np(start_local_np, world_mat_start, np)

    mask_map = cfg.get("maskWeights") if isinstance(cfg.get("maskWeights"), dict) else {}
    mask_np = decode_mesh_mask(mask_map.get(prim_path), n_verts)
    params_sig = _cloth_live_params_sig(cfg, prim_path, n_verts, mask_np, up_axis)

    collider_prims = _collect_collider_prims(stage, collider_query, prim_path)
    skel_ctx = _build_skel_context(stage) if collider_prims else None
    col_animated = _collider_is_animated(collider_prims, skel_ctx)
    c_pts_start, c_tris_start = _sample_collider_at_time(collider_prims, start_tc, skel_ctx)

    import cloth_proxy as cp

    use_proxy = n_verts > cp.PROXY_VERTEX_THRESHOLD
    proxy = None
    if use_proxy:
        p_key_h = hashlib.sha256()
        p_key_h.update(prim_path.encode("utf-8"))
        p_key_h.update(pos_world_start.tobytes())
        p_key_h.update(np.ascontiguousarray(tris, dtype=np.int32).tobytes())
        p_key = p_key_h.hexdigest()
        proxy = _CLOTH_PROXY_CACHE.get(p_key)
        if proxy is not None:
            _CLOTH_PROXY_CACHE.move_to_end(p_key)
        else:
            _emit_cloth_status(f"Cloth sim {status_label}: building coarse proxy ({n_verts:,} verts)...")
            proxy = cp.build_cloth_proxy(pos_world_start, tris)
            _CLOTH_PROXY_CACHE[p_key] = proxy
            while len(_CLOTH_PROXY_CACHE) > 4:
                _CLOTH_PROXY_CACHE.popitem(last=False)
        n_sim = proxy["n_proxy"]
        sim_pos_start = proxy["proxy_pos"]
        sim_mask = cp.aggregate_proxy_mask(mask_np, proxy["vert_to_cluster"], n_sim)
        topo = proxy
    else:
        n_sim = n_verts
        sim_pos_start = pos_world_start
        sim_mask = mask_np
        topo = _extract_mesh_topology(pos_world_start, tris)

    inv_mass_np = np.clip(1.0 - sim_mask, 0.0, 1.0).astype(np.float32)
    inv_mass_np[sim_mask >= 0.999] = 0.0

    import cloth_kernels as ck

    wp, dev = ck.get_warp()
    dt_sub = (1.0 / fps) / float(substeps)
    inv_dt_sub = 1.0 / max(1.0e-6, dt_sub)
    median_edge = float(np.median(topo["edge_rest"])) if topo["edge_rest"].size > 0 else 0.01 * unit_scale
    max_step = min(0.05 * unit_scale, max(median_edge * 1.0, 0.005 * unit_scale))
    max_speed = 6.0 * unit_scale
    settle_max_speed = 2.5 * unit_scale
    max_query_dist = max(0.25 * unit_scale, margin * 12.0)
    proxy_margin = (margin + median_edge * 0.35) if use_proxy else margin

    if wp is not None and dev is not None:
        wp_pos = wp.array(sim_pos_start, dtype=wp.vec3, device=dev)
        wp_rest_pos = wp.array(sim_pos_start, dtype=wp.vec3, device=dev)
        wp_prev = wp.zeros(n_sim, dtype=wp.vec3, device=dev)
        wp_vel = wp.zeros(n_sim, dtype=wp.vec3, device=dev)
        wp_normals = wp.zeros(n_sim, dtype=wp.vec3, device=dev)
        wp_pin_w = wp.array(sim_mask, dtype=float, device=dev)
        wp_pin_target = wp.array(sim_pos_start, dtype=wp.vec3, device=dev)
        wp_inv_mass = wp.array(inv_mass_np, dtype=float, device=dev)
        wp_local_out = wp.zeros(n_verts, dtype=wp.vec3, device=dev)
        wp_fine_start = wp.array(pos_world_start, dtype=wp.vec3, device=dev) if use_proxy else None
        wp_fine_out = wp.zeros(n_verts, dtype=wp.vec3, device=dev) if use_proxy else None
        wp_fine_mask = wp.array(mask_np, dtype=float, device=dev) if use_proxy else None
        wp_bind_idx = wp.array(proxy["bind_idx"], dtype=int, device=dev) if use_proxy else None
        wp_bind_w = wp.array(proxy["bind_w"], dtype=float, device=dev) if use_proxy else None
        wp_tris = wp.array(topo["tris"], dtype=int, device=dev)
        wp_e0 = wp.array(topo["edge_v0"], dtype=int, device=dev)
        wp_e1 = wp.array(topo["edge_v1"], dtype=int, device=dev)
        wp_erest = wp.array(topo["edge_rest"], dtype=float, device=dev)
        wp_b0 = wp.array(topo["bend_v0"], dtype=int, device=dev)
        wp_b1 = wp.array(topo["bend_v1"], dtype=int, device=dev)
        wp_brest = wp.array(topo["bend_rest"], dtype=float, device=dev)
        wp_adj_off = wp.array(topo["adj_offsets"], dtype=int, device=dev)
        wp_adj_nbr = wp.array(topo["adj_neighbors"], dtype=int, device=dev)
        wp_corr_sum = wp.zeros(n_sim, dtype=wp.vec3, device=dev)
        wp_corr_cnt = wp.zeros(n_sim, dtype=int, device=dev)

        n_tris = int(topo["tris"].size // 3)
        n_edges = int(topo["edge_v0"].size)
        n_bends = int(topo["bend_v0"].size)
        g_vec = wp.vec3(*gravity_vec)
        w_vec = wp.vec3(*wind_vec)

        tether_anchors_np, tether_dists_np = _compute_lra_tethers(sim_pos_start, topo, sim_mask)
        has_lra = bool(np.any(tether_anchors_np >= 0))
        wp_tether_anchor = wp.array(tether_anchors_np, dtype=int, device=dev) if has_lra else None
        wp_tether_dist = wp.array(tether_dists_np, dtype=float, device=dev) if has_lra else None
        lra_allowance = max(0.005, (1.0 - stretch_stiff) * 0.15 + 0.01)
        sim_stretch_iters = (
            max(20, min(64, int(round((iterations * 8) * (4.0 / max(1, substeps))))))
            if stretch_stiff > 0.0
            else 0
        )
        sim_bend_iters = max(1, min(4, iterations // 3)) if bend_stiff > 0.0 else 0

        hash_grid = wp.HashGrid(dim_x=64, dim_y=64, dim_z=64, device=dev) if (self_col and self_thick > 1.0e-6) else None

        state = {
            "wp_col_pts": None,
            "wp_col_tris": None,
            "wp_col_mesh": None,
            "wp_col_pts_a": None,
            "wp_col_pts_b": None,
            "has_bound_pins": False,
            "prev_col_pts": c_pts_start,
            "prev_col_tris": c_tris_start,
            "wp_pin_target": wp_pin_target,
        }
        wp_pin_face = wp.full(n_sim, -1, dtype=int, device=dev)
        wp_pin_uv = wp.zeros(n_sim, dtype=wp.vec2, device=dev)
        wp_pin_offset = wp.zeros(n_sim, dtype=float, device=dev)

        if c_pts_start is not None and c_tris_start is not None:
            state["wp_col_pts"] = wp.array(c_pts_start, dtype=wp.vec3, device=dev)
            state["wp_col_tris"] = wp.array(c_tris_start, dtype=int, device=dev)
            state["wp_col_mesh"] = wp.Mesh(points=state["wp_col_pts"], indices=state["wp_col_tris"])
            state["wp_col_pts_a"] = wp.array(c_pts_start, dtype=wp.vec3, device=dev)
            state["wp_col_pts_b"] = wp.array(c_pts_start, dtype=wp.vec3, device=dev)
            if pin_to_collider and float(sim_mask.max()) > 1.0e-4:
                wp.launch(
                    ck.cloth_bind_collider_pins_kernel,
                    dim=n_sim,
                    inputs=[
                        wp_pos,
                        wp_pin_w,
                        state["wp_col_mesh"].id,
                        margin,
                        max_query_dist,
                        wp_pin_face,
                        wp_pin_uv,
                        wp_pin_offset,
                    ],
                    device=dev,
                )
                state["has_bound_pins"] = True

        fine_inv_mass_np = np.clip(1.0 - mask_np, 0.0, 1.0).astype(np.float32) if use_proxy else None
        if fine_inv_mass_np is not None:
            fine_inv_mass_np[mask_np >= 0.999] = 0.0
        wp_fine_inv_mass = wp.array(fine_inv_mass_np, dtype=float, device=dev) if fine_inv_mass_np is not None else None

        def _run_substep(col_mesh_ref, step_dt, step_inv_dt, step_damping=damping, step_max_speed=max_speed, rebuild_hash=True):
            if col_mesh_ref is not None and state["has_bound_pins"]:
                wp.launch(
                    ck.cloth_eval_collider_pins_kernel,
                    dim=n_sim,
                    inputs=[wp_pin_w, wp_pin_face, wp_pin_uv, wp_pin_offset, col_mesh_ref.id, state["wp_pin_target"]],
                    device=dev,
                )
            wp_normals.zero_()
            if n_tris > 0:
                wp.launch(ck.accum_face_normals_kernel, dim=n_tris, inputs=[wp_pos, wp_tris, wp_normals], device=dev)
            wp.launch(ck.normalize_normals_kernel, dim=n_sim, inputs=[wp_normals], device=dev)
            wp.launch(
                ck.cloth_sim_predict_kernel,
                dim=n_sim,
                inputs=[
                    wp_pos, wp_prev, wp_vel, wp_normals, wp_pin_w, state["wp_pin_target"],
                    g_vec, w_vec, air_drag, step_dt, step_damping, step_max_speed,
                ],
                device=dev,
            )
            wp_corr_sum.zero_()
            wp_corr_cnt.zero_()
            if sim_stretch_iters > 0 and n_edges > 0:
                mid_col_it = sim_stretch_iters // 2
                for it_s in range(sim_stretch_iters):
                    wp.launch(
                        ck.cloth_stretch_jacobi_kernel,
                        dim=n_edges,
                        inputs=[wp_pos, wp_inv_mass, wp_e0, wp_e1, wp_erest, wp_corr_sum, wp_corr_cnt, stretch_stiff],
                        device=dev,
                    )
                    wp.launch(
                        ck.cloth_apply_jacobi_kernel,
                        dim=n_sim,
                        inputs=[wp_pos, wp_corr_sum, wp_corr_cnt, 1.65, max_step],
                        device=dev,
                    )
                    if it_s == mid_col_it and col_mesh_ref is not None:
                        wp.launch(
                            ck.cloth_collider_kernel,
                            dim=n_sim,
                            inputs=[wp_pos, wp_prev, wp_inv_mass, col_mesh_ref.id, proxy_margin, 0.0, max_query_dist],
                            device=dev,
                        )
            if sim_bend_iters > 0 and n_bends > 0:
                for _ in range(sim_bend_iters):
                    wp.launch(
                        ck.cloth_bend_jacobi_kernel,
                        dim=n_bends,
                        inputs=[wp_pos, wp_inv_mass, wp_b0, wp_b1, wp_brest, wp_corr_sum, wp_corr_cnt, bend_stiff],
                        device=dev,
                    )
                    wp.launch(
                        ck.cloth_apply_jacobi_kernel,
                        dim=n_sim,
                        inputs=[wp_pos, wp_corr_sum, wp_corr_cnt, 0.85, max_step],
                        device=dev,
                    )
            if has_lra and wp_tether_anchor is not None and wp_tether_dist is not None:
                wp.launch(
                    ck.cloth_lra_tether_kernel,
                    dim=n_sim,
                    inputs=[wp_pos, wp_inv_mass, wp_tether_anchor, wp_tether_dist, lra_allowance],
                    device=dev,
                )
            if col_mesh_ref is not None:
                wp.launch(
                    ck.cloth_collider_kernel,
                    dim=n_sim,
                    inputs=[wp_pos, wp_prev, wp_inv_mass, col_mesh_ref.id, proxy_margin, friction, max_query_dist],
                    device=dev,
                )
            if hash_grid is not None and self_thick > 1.0e-6:
                if rebuild_hash:
                    hash_grid.build(points=wp_pos, radius=max(self_thick * 1.5, 1.0e-4))
                wp.launch(
                    ck.cloth_self_collision_kernel,
                    dim=n_sim,
                    inputs=[
                        hash_grid.id,
                        wp_pos,
                        wp_rest_pos,
                        wp_inv_mass,
                        wp_adj_off,
                        wp_adj_nbr,
                        wp_corr_sum,
                        wp_corr_cnt,
                        self_thick,
                    ],
                    device=dev,
                )
                wp.launch(
                    ck.cloth_apply_jacobi_kernel,
                    dim=n_sim,
                    inputs=[wp_pos, wp_corr_sum, wp_corr_cnt, 0.9, max_step],
                    device=dev,
                )
                if col_mesh_ref is not None:
                    wp.launch(
                        ck.cloth_collider_kernel,
                        dim=n_sim,
                        inputs=[wp_pos, wp_prev, wp_inv_mass, col_mesh_ref.id, proxy_margin, 0.0, max_query_dist],
                        device=dev,
                    )
            wp.launch(
                ck.cloth_sim_update_vel_kernel,
                dim=n_sim,
                inputs=[wp_pos, wp_prev, wp_vel, wp_inv_mass, step_inv_dt, step_damping * 0.25, step_max_speed],
                device=dev,
            )

        def _read_local_frame(inv_mat_np):
            src_world = wp_pos
            if use_proxy and wp_fine_start is not None and wp_fine_out is not None:
                wp.launch(
                    ck.cloth_reconstruct_fine_kernel,
                    dim=n_verts,
                    inputs=[
                        wp_fine_start,
                        wp_fine_out,
                        wp_fine_mask,
                        wp_rest_pos,
                        wp_pos,
                        wp_bind_idx,
                        wp_bind_w,
                    ],
                    device=dev,
                )
                if state["wp_col_mesh"] is not None and wp_fine_inv_mass is not None:
                    wp.launch(
                        ck.cloth_collider_kernel,
                        dim=n_verts,
                        inputs=[wp_fine_out, wp_fine_out, wp_fine_inv_mass, state["wp_col_mesh"].id, margin, 0.0, max_query_dist],
                        device=dev,
                    )
                src_world = wp_fine_out
            m0 = wp.vec3(float(inv_mat_np[0, 0]), float(inv_mat_np[0, 1]), float(inv_mat_np[0, 2]))
            m1 = wp.vec3(float(inv_mat_np[1, 0]), float(inv_mat_np[1, 1]), float(inv_mat_np[1, 2]))
            m2 = wp.vec3(float(inv_mat_np[2, 0]), float(inv_mat_np[2, 1]), float(inv_mat_np[2, 2]))
            t = wp.vec3(float(inv_mat_np[3, 0]), float(inv_mat_np[3, 1]), float(inv_mat_np[3, 2]))
            wp.launch(ck.world_to_local_kernel, dim=n_verts, inputs=[src_world, wp_local_out, m0, m1, m2, t], device=dev)
            return np.ascontiguousarray(wp_local_out.numpy(), dtype=np.float32)

        if do_preroll and settle_steps > 0:
            settle_damp = max(damping, 0.35)
            for p_i in range(settle_steps):
                _run_substep(state["wp_col_mesh"], dt_sub, inv_dt_sub, settle_damp, settle_max_speed, rebuild_hash=(p_i % 2 == 0))
            wp_vel.zero_()

        initial_out = _read_local_frame(inv_world_start)

        def _step_to(next_f: int, cur_stage) -> np.ndarray:
            tc_f = Usd.TimeCode(float(next_f))
            cur_prim = cur_stage.GetPrimAtPath(prim_path)
            if cur_prim and cur_prim.IsValid():
                cur_pts_attr = UsdGeom.Mesh(cur_prim).GetPointsAttr()
                if not state["has_bound_pins"] and sample_times_in and float(sim_mask.max()) > 1.0e-4 and cur_pts_attr:
                    raw_f = cur_pts_attr.Get(tc_f)
                    if raw_f is not None and len(raw_f) == n_verts:
                        xf_f = UsdGeom.XformCache(tc_f)
                        wm_f = np.asarray(xf_f.GetLocalToWorldTransform(cur_prim), dtype=np.float32)
                        pin_w_f = _points_to_world_np(np.asarray(raw_f, dtype=np.float32), wm_f, np)
                        if use_proxy and proxy is not None:
                            pin_w_f = cp.recompute_proxy_positions(
                                pin_w_f, proxy["vert_to_cluster"], proxy["cluster_counts"], n_sim
                            )
                        state["wp_pin_target"] = wp.array(pin_w_f, dtype=wp.vec3, device=dev)

            cur_colliders = _collect_collider_prims(cur_stage, collider_query, prim_path) if collider_query and collider_query != "__none__" else []
            cur_skel = _build_skel_context(cur_stage) if cur_colliders else None
            same_col_topo = False
            cur_col_pts, cur_col_tris = state["prev_col_pts"], state["prev_col_tris"]
            if col_animated and cur_colliders:
                cur_col_pts, cur_col_tris = _sample_collider_at_time(cur_colliders, tc_f, cur_skel)
                same_col_topo = (
                    state["wp_col_mesh"] is not None
                    and cur_col_pts is not None
                    and state["prev_col_pts"] is not None
                    and cur_col_pts.shape == state["prev_col_pts"].shape
                    and cur_col_tris is not None
                    and state["prev_col_tris"] is not None
                    and cur_col_tris.size == state["prev_col_tris"].size
                )
                if same_col_topo:
                    state["wp_col_pts_a"] = wp.array(state["prev_col_pts"], dtype=wp.vec3, device=dev)
                    state["wp_col_pts_b"] = wp.array(cur_col_pts, dtype=wp.vec3, device=dev)
                elif cur_col_pts is not None and cur_col_tris is not None:
                    state["wp_col_pts"] = wp.array(cur_col_pts, dtype=wp.vec3, device=dev)
                    state["wp_col_tris"] = wp.array(cur_col_tris, dtype=int, device=dev)
                    state["wp_col_mesh"] = wp.Mesh(points=state["wp_col_pts"], indices=state["wp_col_tris"])
                    state["has_bound_pins"] = False

            for s_i in range(substeps):
                if same_col_topo:
                    alpha = float(s_i + 1) / float(substeps)
                    wp.launch(
                        ck.cloth_lerp_collider_pts_kernel,
                        dim=int(cur_col_pts.shape[0]),
                        inputs=[state["wp_col_pts_a"], state["wp_col_pts_b"], state["wp_col_pts"], alpha],
                        device=dev,
                    )
                    state["wp_col_mesh"].refit()
                _run_substep(state["wp_col_mesh"], dt_sub, inv_dt_sub, rebuild_hash=(s_i == 0))

            state["prev_col_pts"] = cur_col_pts
            state["prev_col_tris"] = cur_col_tris
            xf_out = UsdGeom.XformCache(tc_f)
            inv_wm_f = np.asarray(
                xf_out.GetLocalToWorldTransform(cur_prim if (cur_prim and cur_prim.IsValid()) else prim).GetInverse(),
                dtype=np.float32,
            )
            return _read_local_frame(inv_wm_f)

        return {
            "last_frame": int(start_f),
            "params_sig": params_sig,
            "step_to": _step_to,
            "initial_points": initial_out,
        }

    # Lightweight CPU fallback runner
    pos = sim_pos_start.copy()
    vel = np.zeros_like(pos)
    g_np = np.array(gravity_vec, dtype=np.float32)
    w_np = np.array(wind_vec, dtype=np.float32)
    e0, e1, erest = topo["edge_v0"], topo["edge_v1"], topo["edge_rest"]
    b0, b1, brest = topo["bend_v0"], topo["bend_v1"], topo["bend_rest"]
    pinned = sim_mask >= 0.999

    def _cpu_substep():
        prev = pos.copy()
        free = ~pinned
        vel[free] = vel[free] * max(0.0, 1.0 - damping) + (g_np + (w_np - vel[free]) * (air_drag * 0.25)) * dt_sub
        pos[free] = pos[free] + vel[free] * dt_sub
        for _ in range(iterations):
            for idx_a, idx_b, rest_l, st in ((e0, e1, erest, stretch_stiff), (b0, b1, brest, bend_stiff)):
                if idx_a.size == 0 or st <= 0.0:
                    continue
                diff = pos[idx_b] - pos[idx_a]
                dist = np.linalg.norm(diff, axis=1)
                valid = dist > 1.0e-7
                if not np.any(valid):
                    continue
                wa = inv_mass_np[idx_a[valid]]
                wb = inv_mass_np[idx_b[valid]]
                ws = wa + wb
                ok = ws > 1.0e-8
                if not np.any(ok):
                    continue
                ia = idx_a[valid][ok]
                ib = idx_b[valid][ok]
                err = dist[valid][ok] - rest_l[valid][ok]
                n = diff[valid][ok] / dist[valid][ok][:, None]
                sc = (st * err / ws[ok])[:, None]
                np.add.at(pos, ia, n * (sc * wa[ok][:, None] * 0.5))
                np.add.at(pos, ib, -n * (sc * wb[ok][:, None] * 0.5))
        pos[pinned] = sim_pos_start[pinned]
        vel[free] = (pos[free] - prev[free]) * inv_dt_sub
        vel[pinned] = 0.0

    def _cpu_read_local(inv_mat_np):
        if use_proxy and proxy is not None:
            b_idx = proxy["bind_idx"].reshape(n_verts, 4)
            b_w = proxy["bind_w"].reshape(n_verts, 4)
            p_disp = pos - sim_pos_start
            fine_disp = np.sum(p_disp[b_idx] * b_w[:, :, None], axis=1)
            fine_p = pos_world_start + fine_disp * np.clip(1.0 - mask_np, 0.0, 1.0)[:, None]
            return _points_to_world_np(fine_p, inv_mat_np, np)
        return _points_to_world_np(pos, inv_mat_np, np)

    if do_preroll and settle_steps > 0:
        for _ in range(settle_steps):
            _cpu_substep()
        vel.fill(0.0)
    initial_out = _cpu_read_local(inv_world_start)

    def _cpu_step_to(next_f: int, _cur_stage) -> np.ndarray:
        for _ in range(substeps):
            _cpu_substep()
        return _cpu_read_local(inv_world_start)

    return {
        "last_frame": int(start_f),
        "params_sig": params_sig,
        "step_to": _cpu_step_to,
        "initial_points": initial_out,
    }


def _find_or_create_cache_overlay(stage):
    return stage.GetSessionLayer()


def step_cloth_simulation(
    stage,
    cfg: dict | None,
    target_frame: int,
    origin_frame: int | None = None,
    reset: bool = False,
    session_id: int | None = None,
) -> list[tuple[str, np.ndarray]]:
    """Advance (or initialize) interactive GPU cloth simulation to `target_frame`.

    Caches each simulated frame in `_CLOTH_LIVE_CACHE` and authors `points` at `target_frame`
    into the stage's SessionLayer so playback (`Play`) reads cached frames without re-simulating.
    Starting a new simulation session at `origin_frame` replaces the cache from `origin_frame`
    onwards (clearing any stale cached frames > `origin_frame` while preserving frames < `origin_frame`).
    """
    from pxr import Usd, UsdGeom, Vt

    if not isinstance(cfg, dict) or stage is None:
        return []
    target_query = str(cfg.get("targetQuery") or "")
    col_q = str(cfg.get("colliderQuery") or "__none__").strip()
    cache_ver = int(cfg.get("simCacheVersion", 0) or 0)
    up_axis = str(UsdGeom.GetStageUpAxis(stage) or "Y").upper()
    sim_start_cfg = int(round(float(cfg.get("simStartFrame", 0) or 0)))

    overlay = _find_or_create_cache_overlay(stage)
    root_layer = stage.GetRootLayer()
    updates: list[tuple[str, np.ndarray]] = []
    f_int = int(round(float(target_frame)))
    orig_int = int(round(float(origin_frame))) if origin_frame is not None else f_int

    with Usd.EditContext(stage, overlay):
        target_prims = _collect_target_meshes(stage, target_query, make_editable=True)
        sim_prims = [
            p
            for p in target_prims
            if not (col_q and col_q != "__none__" and _mesh_query_matches(str(p.GetPath()), col_q))
        ]
        if not sim_prims:
            return []

        for prim in sim_prims:
            prim_path = str(prim.GetPath())
            mesh = UsdGeom.Mesh(prim)
            pts_attr = mesh.GetPointsAttr()
            if not pts_attr:
                continue

            # Determine vertex count & current mask for parameter signature check
            sample_times_in = list(pts_attr.GetTimeSamples())
            raw_cur = pts_attr.Get(Usd.TimeCode(float(f_int)) if sample_times_in else Usd.TimeCode.Default())
            if raw_cur is None or len(raw_cur) == 0:
                continue
            n_verts = len(raw_cur)
            mask_map = cfg.get("maskWeights") if isinstance(cfg.get("maskWeights"), dict) else {}
            mask_np = decode_mesh_mask(mask_map.get(prim_path), n_verts)
            params_sig = _cloth_live_params_sig(cfg, prim_path, n_verts, mask_np, up_axis)

            runner = _CLOTH_LIVE_RUNNERS.get(prim_path)
            cache_entry = _CLOTH_LIVE_CACHE.get(prim_path)
            if cache_entry is None or cache_entry.get("version") != cache_ver:
                cache_entry = {"version": cache_ver, "frames": {}}
                _CLOTH_LIVE_CACHE[prim_path] = cache_entry
                runner = None

            is_new_session_start = (
                session_id is not None
                and f_int == orig_int
                and (runner is None or runner.get("session_id") != session_id)
            )
            can_continue = (
                not reset
                and not is_new_session_start
                and runner is not None
                and runner["params_sig"] == params_sig
                and runner["last_frame"] == f_int - 1
            )

            if can_continue:
                local_pts = runner["step_to"](f_int, stage)
                runner["last_frame"] = f_int
            else:
                # Replace cache from f_int onwards: drop any stale frames > f_int in both memory and USD overlay
                for stale_f in [k for k in list(cache_entry["frames"].keys()) if k > f_int]:
                    del cache_entry["frames"][stale_f]
                for stale_t in list(pts_attr.GetTimeSamples()):
                    if float(stale_t) > float(f_int) + 1.0e-4:
                        pts_attr.ClearAtTime(float(stale_t))

                # Initialize solver at f_int directly from the live stage points at f_int
                # (which already include any sculpt brush edits and/or prior cached simulation at f_int).
                do_preroll = (f_int == orig_int) and (f_int <= sim_start_cfg) and (f_int not in cache_entry["frames"])
                runner = _build_cloth_step_runner(
                    stage,
                    prim,
                    cfg,
                    start_f=f_int,
                    initial_local_pts=None,
                    do_preroll=do_preroll,
                    status_label=f"[{prim.GetName()}]",
                )
                if runner is None:
                    continue
                runner["session_id"] = session_id
                _CLOTH_LIVE_RUNNERS[prim_path] = runner
                local_pts = runner["initial_points"]

            cache_entry["frames"][f_int] = local_pts.copy()
            pts_attr.Set(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(local_pts, dtype=np.float32)), float(f_int))
            if not stage.HasAuthoredTimeCodeRange():
                root_layer.startTimeCode = float(f_int)
                root_layer.endTimeCode = float(f_int)
            else:
                if float(f_int) < stage.GetStartTimeCode():
                    root_layer.startTimeCode = float(f_int)
                if float(f_int) > stage.GetEndTimeCode():
                    root_layer.endTimeCode = float(f_int)
            updates.append((prim_path, local_pts))

    return updates


# ---------------------------------------------------------------------------
# USD Graph Evaluation: apply_cache_edit
# ---------------------------------------------------------------------------


def _deinstance_prim_if_needed(stage, prim):
    """Ensure `prim` is editable in the current anonymous EditTarget by clearing ancestor instanceable flags top-down."""
    from pxr import Sdf

    if not prim or not prim.IsValid():
        return None
    path = prim.GetPath()
    while prim and prim.IsValid() and prim.IsInstanceProxy():
        cleared = False
        for prefix in path.GetPrefixes():
            anc = stage.GetPrimAtPath(prefix)
            if anc and anc.IsValid() and anc.IsInstance() and not anc.IsInstanceProxy():
                anc.SetInstanceable(False)
                cleared = True
                break
        if not cleared:
            break
        prim = stage.GetPrimAtPath(Sdf.Path(str(path)))
    return prim if (prim and prim.IsValid() and not prim.IsInstanceProxy()) else None


def _collect_target_meshes(stage, target_query: str, make_editable: bool = False):
    from pxr import Usd, UsdGeom

    matched_paths = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        p = str(prim.GetPath())
        if _mesh_query_matches(p, target_query):
            matched_paths.append(p)
    result = []
    for p in matched_paths:
        prim = stage.GetPrimAtPath(p)
        if not prim or not prim.IsValid():
            continue
        if prim.IsInstanceProxy():
            if make_editable:
                prim = _deinstance_prim_if_needed(stage, prim)
                if prim is None:
                    continue
            else:
                continue
        if prim.IsA(UsdGeom.Mesh):
            result.append(prim)
    return result


def apply_cache_edit(stage, cfg: dict | None):
    """Apply all enabled sculpt layers and temporal smoothing into an anonymous USD overlay."""
    from pxr import Sdf, Usd, UsdGeom, Vt

    if not isinstance(cfg, dict):
        return
    layers = cfg.get("layers")
    if not isinstance(layers, list):
        layers = []
    enabled_layers = [l for l in layers if isinstance(l, dict) and l.get("enabled", True)]
    t_smooth_win = max(0, int(cfg.get("temporalSmoothFrames", 0) or 0))
    t_smooth_str = max(0.0, min(1.0, float(cfg.get("temporalSmoothStrength", 0.0) or 0.0)))

    target_query = str(cfg.get("targetQuery") or "")
    sim_enabled = bool(cfg.get("simEnabled", False))
    sim_mode = str(cfg.get("simMode") or "animated")
    overlay = Sdf.Layer.CreateAnonymous("cache_edit.usda")
    stage.GetRootLayer().subLayerPaths.insert(0, overlay.identifier)

    with Usd.EditContext(stage, overlay):
        # Always de-instance matching target meshes in the private overlay so live
        # Storm & Omniverse RTX sculpting can edit `points` directly on them.
        target_prims = _collect_target_meshes(stage, target_query, make_editable=True)
        if not target_prims:
            return

        # Step 1: Full-garment GPU cloth simulation ("Simulate in Sublayer")
        skip_sim = False
        manual_sim = False
        try:
            import usd_graph as _ug

            skip_sim = bool(getattr(_ug, "_SKIP_SIMULATION", False))
            manual_sim = bool(getattr(_ug, "_INTERACTIVE_MANUAL_SIM", False))
        except Exception:
            pass
        if sim_enabled and not skip_sim:
            root_layer = stage.GetRootLayer()
            col_q = str(cfg.get("colliderQuery") or "__none__").strip()
            cache_ver = int(cfg.get("simCacheVersion", 0) or 0)
            sim_prims = [
                p
                for p in target_prims
                if not (col_q and col_q != "__none__" and _mesh_query_matches(str(p.GetPath()), col_q))
            ]
            total_sim = len(sim_prims)
            for s_idx, prim in enumerate(sim_prims, start=1):
                prim_path = str(prim.GetPath())
                pts_attr = UsdGeom.Mesh(prim).GetPointsAttr()
                if not pts_attr:
                    continue
                live_entry = _CLOTH_LIVE_CACHE.get(prim_path)
                if live_entry is not None and live_entry.get("version") == cache_ver and live_entry.get("frames"):
                    # Replay interactively simulated frames from cache without re-simulating
                    sorted_items = sorted(live_entry["frames"].items())
                    min_f = float(sorted_items[0][0])
                    max_f = float(sorted_items[-1][0])
                    if not stage.HasAuthoredTimeCodeRange():
                        root_layer.startTimeCode = min_f
                        root_layer.endTimeCode = max_f
                    else:
                        if min_f < stage.GetStartTimeCode():
                            root_layer.startTimeCode = min_f
                        if max_f > stage.GetEndTimeCode():
                            root_layer.endTimeCode = max_f
                    for t_i, arr in sorted_items:
                        pts_attr.Set(
                            Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(arr, dtype=np.float32)),
                            float(t_i),
                        )
                    continue
                if manual_sim:
                    # In interactive viewport mode, never auto-run cloth simulation on graph compile
                    # or parameter tweaks; simulation only advances when the user clicks Simulate.
                    continue
                label = f"[{s_idx}/{total_sim} {prim.GetName()}]" if total_sim > 1 else f"[{prim.GetName()}]"
                sim_times, sim_frames = _simulate_cloth_mesh(stage, prim, cfg, status_label=label)
                if not sim_frames:
                    continue
                if sim_mode == "static" or len(sim_frames) == 1:
                    pts_attr.Set(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(sim_frames[0], dtype=np.float32)))
                else:
                    min_f = float(sim_times[0])
                    max_f = float(sim_times[-1])
                    if not stage.HasAuthoredTimeCodeRange():
                        root_layer.startTimeCode = min_f
                        root_layer.endTimeCode = max_f
                    else:
                        if min_f < stage.GetStartTimeCode():
                            root_layer.startTimeCode = min_f
                        if max_f > stage.GetEndTimeCode():
                            root_layer.endTimeCode = max_f
                    for t, arr in zip(sim_times, sim_frames):
                        pts_attr.Set(
                            Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(arr, dtype=np.float32)),
                            float(t),
                        )

        has_any_delta = any(
            isinstance(l.get("deltas"), dict) and len(l["deltas"]) > 0
            for l in enabled_layers
        )
        if not has_any_delta and (t_smooth_win <= 0 or t_smooth_str <= 0.0):
            return
        for prim in target_prims:
            prim_path = str(prim.GetPath())
            mesh = UsdGeom.Mesh(prim)
            pts_attr = mesh.GetPointsAttr()
            if not pts_attr:
                continue

            # Gather decoded deltas for this prim from enabled layers
            prim_layers = []
            sample_times = list(pts_attr.GetTimeSamples())
            ref_pts = pts_attr.Get(sample_times[0] if sample_times else Usd.TimeCode.Default())
            if not ref_pts:
                continue
            n_verts = len(ref_pts)

            for layer in enabled_layers:
                deltas_map = layer.get("deltas")
                if not isinstance(deltas_map, dict):
                    continue
                d_entry = deltas_map.get(prim_path)
                d_arr = decode_mesh_delta(d_entry, n_verts)
                if d_arr is None:
                    continue
                weight = float(layer.get("weight", 1.0))
                if abs(weight) <= 1.0e-7:
                    continue
                prim_layers.append((layer, weight, d_arr))

            if not prim_layers and (t_smooth_win <= 0 or t_smooth_str <= 0.0 or len(sample_times) < 2):
                continue

            if not sample_times:
                # Check if any enabled layer uses temporal animation (frameRange / singleFrame)
                has_temporal = any(
                    str(layer.get("mode") or "frameRange") in ("frameRange", "singleFrame")
                    for layer, _w, _d in prim_layers
                )
                if not has_temporal:
                    # Purely static sculpt (allFrames): write a single default points value
                    base_np = np.asarray(ref_pts, dtype=np.float32).copy()
                    acc = np.zeros_like(base_np)
                    for _layer, weight, d_arr in prim_layers:
                        acc += weight * d_arr
                    out_np = np.ascontiguousarray(base_np + acc, dtype=np.float32)
                    pts_attr.Set(Vt.Vec3fArray.FromNumpy(out_np))
                    continue

                # Animate static mesh over the union of all temporal layer envelopes
                min_f = None
                max_f = None
                for layer, _w, _d in prim_layers:
                    mode = str(layer.get("mode") or "frameRange")
                    if mode == "allFrames":
                        continue
                    c = float(layer.get("centerFrame", 0.0))
                    if mode == "singleFrame":
                        lo = math.floor(c) - 1
                        hi = math.ceil(c) + 1
                    else:
                        fi = max(0.0, float(layer.get("fadeIn", 8.0)))
                        fo = max(0.0, float(layer.get("fadeOut", 8.0)))
                        lo = math.floor(c - fi) - 1
                        hi = math.ceil(c + fo) + 1
                    min_f = lo if min_f is None else min(min_f, lo)
                    max_f = hi if max_f is None else max(max_f, hi)

                if min_f is None or max_f is None:
                    min_f, max_f = 0, 0
                if min_f < 0 and all(float(l.get("centerFrame", 0.0)) >= 0.0 for l, _w, _d in prim_layers):
                    min_f = 0
                if max_f - min_f > 2000:
                    max_f = min_f + 2000

                sample_times = [float(f) for f in range(int(min_f), int(max_f) + 1)]
                # Ensure root layer time range covers the generated animation
                root_layer = stage.GetRootLayer()
                if not stage.HasAuthoredTimeCodeRange():
                    root_layer.startTimeCode = float(min_f)
                    root_layer.endTimeCode = float(max_f)
                else:
                    if float(min_f) < stage.GetStartTimeCode():
                        root_layer.startTimeCode = float(min_f)
                    if float(max_f) > stage.GetEndTimeCode():
                        root_layer.endTimeCode = float(max_f)

                base_static_np = np.asarray(ref_pts, dtype=np.float32)
                frame_pts = []
                for t in sample_times:
                    p_np = base_static_np.copy()
                    for layer, weight, d_arr in prim_layers:
                        tw = compute_temporal_weight(t, layer)
                        if tw > 0.0:
                            p_np += (weight * tw) * d_arr
                    frame_pts.append(p_np)
            else:
                # Animated mesh: evaluate across all existing time samples
                frame_pts = []
                for t in sample_times:
                    raw = pts_attr.Get(t)
                    if raw is None or len(raw) != n_verts:
                        frame_pts.append(None)
                        continue
                    p_np = np.asarray(raw, dtype=np.float32).copy()
                    for layer, weight, d_arr in prim_layers:
                        tw = compute_temporal_weight(float(t), layer)
                        if tw > 0.0:
                            p_np += (weight * tw) * d_arr
                    frame_pts.append(p_np)

            # Optional temporal smoothing across neighboring frames
            if t_smooth_win > 0 and t_smooth_str > 0.0 and len(sample_times) >= 3:
                smoothed = []
                n_frames = len(frame_pts)
                for idx in range(n_frames):
                    cur = frame_pts[idx]
                    if cur is None:
                        smoothed.append(None)
                        continue
                    w_sum = 0.0
                    acc = np.zeros_like(cur)
                    lo = max(0, idx - t_smooth_win)
                    hi = min(n_frames - 1, idx + t_smooth_win)
                    for j in range(lo, hi + 1):
                        pj = frame_pts[j]
                        if pj is None:
                            continue
                        w = float(t_smooth_win + 1 - abs(j - idx))
                        acc += w * pj
                        w_sum += w
                    if w_sum > 0.0:
                        avg = acc / w_sum
                        smoothed.append(cur + (avg - cur) * t_smooth_str)
                    else:
                        smoothed.append(cur)
                frame_pts = smoothed

            for t, p_np in zip(sample_times, frame_pts):
                if p_np is None:
                    continue
                pts_attr.Set(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(p_np, dtype=np.float32)), t)


# ---------------------------------------------------------------------------
# Interactive Viewport Sculpt & Cloth Physics Session
# ---------------------------------------------------------------------------


_CACHE_SESSIONS: OrderedDict[tuple, "CacheMeshSession"] = OrderedDict()
_MAX_CACHE_SESSIONS = 12


def reset_cache_sessions():
    """Clear all live cache-edit GPU/CPU brush sessions and cloth simulation cache."""
    _CACHE_SESSIONS.clear()
    _CLOTH_SIM_CACHE.clear()


def _ray_from_screen(nx: float, ny: float, cam_basis: dict):
    origin = np.asarray(cam_basis["origin"], dtype=np.float32)
    fwd = np.asarray(cam_basis["forward"], dtype=np.float32)
    right = np.asarray(cam_basis["right"], dtype=np.float32)
    up = np.asarray(cam_basis["up"], dtype=np.float32)
    aspect = float(cam_basis["aspect"])
    if cam_basis.get("orthographic"):
        ortho_hh = float(cam_basis.get("ortho_half_height", 1.0))
        ox = (nx * 2.0 - 1.0) * aspect * ortho_hh
        oy = (1.0 - ny * 2.0) * ortho_hh
        d = fwd
        dl = float(np.linalg.norm(d))
        if dl > 1.0e-8:
            d = d / dl
        return origin + right * ox + up * oy, d
    tan_hf = float(cam_basis["tan_half_fov"])
    sx = (nx * 2.0 - 1.0) * aspect * tan_hf
    sy = (1.0 - ny * 2.0) * tan_hf
    d = fwd + right * sx + up * sy
    dl = float(np.linalg.norm(d))
    if dl > 1.0e-8:
        d = d / dl
    return origin, d


def _raycast_mesh_triangles(origin: np.ndarray, ray_dir: np.ndarray, pos: np.ndarray, tris: np.ndarray):
    """Vectorized Moller-Trumbore ray-mesh intersection. Returns `(hit_pos, hit_dist, hit_normal)` or `(None, inf, None)`."""
    t = tris.reshape(-1, 3)
    if t.shape[0] == 0:
        return None, float("inf"), None
    v0 = pos[t[:, 0]]
    v1 = pos[t[:, 1]]
    v2 = pos[t[:, 2]]
    e1 = v1 - v0
    e2 = v2 - v0
    h = np.cross(ray_dir[None, :], e2)
    a = np.einsum("ij,ij->i", e1, h)
    valid = np.abs(a) > 1.0e-7
    if not np.any(valid):
        return None, float("inf"), None
    idx = np.flatnonzero(valid)
    f = 1.0 / a[idx]
    s = origin[None, :] - v0[idx]
    u = f * np.einsum("ij,ij->i", s, h[idx])
    m1 = (u >= -1.0e-5) & (u <= 1.0 + 1.0e-5)
    if not np.any(m1):
        return None, float("inf"), None
    idx = idx[m1]
    f = f[m1]
    s = s[m1]
    u = u[m1]
    q = np.cross(s, e1[idx])
    v = f * np.einsum("ij,j->i", q, ray_dir)
    m2 = (v >= -1.0e-5) & (u + v <= 1.0 + 1.0e-5)
    if not np.any(m2):
        return None, float("inf"), None
    idx = idx[m2]
    f = f[m2]
    q = q[m2]
    dist = f * np.einsum("ij,ij->i", e2[idx], q)
    m3 = dist > 1.0e-5
    if not np.any(m3):
        return None, float("inf"), None
    idx = idx[m3]
    dist = dist[m3]
    best_i = int(np.argmin(dist))
    best_dist = float(dist[best_i])
    tri_i = int(idx[best_i])
    hit_pos = origin + ray_dir * best_dist
    fn = np.cross(e1[tri_i], e2[tri_i])
    fn_l = float(np.linalg.norm(fn))
    hit_normal = (fn / fn_l) if fn_l > 1.0e-8 else np.array([0.0, 1.0, 0.0], dtype=np.float32)
    if float(np.dot(hit_normal, ray_dir)) > 0.0:
        hit_normal = -hit_normal
    return hit_pos.astype(np.float32), best_dist, hit_normal.astype(np.float32)


class CacheMeshSession:
    """Live GPU/CPU sculpting and cloth physics session for one USD Mesh prim at one frame."""

    def __init__(
        self,
        prim_path: str,
        base_local_pts: np.ndarray,
        cur_local_pts: np.ndarray,
        tris: np.ndarray,
        world_mat: np.ndarray,
        inv_world_mat: np.ndarray,
        mask: np.ndarray,
        frame: float,
        is_animated: bool,
        active_layer_id: str,
        effective_layer_scale: float,
    ):
        self.prim_path = prim_path
        self.base_local_pts = np.ascontiguousarray(base_local_pts, dtype=np.float32)
        self.world_mat = np.ascontiguousarray(world_mat, dtype=np.float32)
        self.inv_world_mat = np.ascontiguousarray(inv_world_mat, dtype=np.float32)
        self.frame = float(frame)
        self.is_animated = bool(is_animated)
        self.active_layer_id = active_layer_id
        self.effective_layer_scale = float(effective_layer_scale)

        self.pos_world = _points_to_world_np(cur_local_pts, self.world_mat, np)
        self.n_verts = int(self.pos_world.shape[0])
        self.mask = np.ascontiguousarray(mask, dtype=np.float32)
        self.vel_world = np.zeros_like(self.pos_world, dtype=np.float32)

        import cloth_proxy as cp

        self.tris = np.ascontiguousarray(tris, dtype=np.int32).reshape(-1)
        self.use_proxy = self.n_verts > cp.PROXY_VERTEX_THRESHOLD
        self._proxy = None
        self.n_sim = self.n_verts
        self.proxy_pos: np.ndarray | None = None
        self.proxy_start_pos: np.ndarray | None = None
        self.proxy_vel: np.ndarray | None = None
        self.proxy_mask: np.ndarray | None = None
        self.adj_offsets = None
        self.adj_neighbors = None

        if self.use_proxy:
            self._proxy = cp.build_cloth_proxy(self.pos_world, self.tris)
            self.n_sim = int(self._proxy["n_proxy"])
            self.proxy_pos = self._proxy["proxy_pos"].copy()
            self.proxy_start_pos = self.proxy_pos.copy()
            self.proxy_vel = np.zeros_like(self.proxy_pos, dtype=np.float32)
            self.proxy_mask = cp.aggregate_proxy_mask(self.mask, self._proxy["vert_to_cluster"], self.n_sim)
            self.edge_v0 = self._proxy["edge_v0"]
            self.edge_v1 = self._proxy["edge_v1"]
            self.edge_rest = self._proxy["edge_rest"]
            self.bend_v0 = self._proxy["bend_v0"]
            self.bend_v1 = self._proxy["bend_v1"]
            self.bend_rest = self._proxy["bend_rest"]
        else:
            topo = _extract_mesh_topology(self.pos_world, self.tris)
            self.tris = topo["tris"]
            self.edge_v0 = topo["edge_v0"]
            self.edge_v1 = topo["edge_v1"]
            self.edge_rest = topo["edge_rest"]
            self.bend_v0 = topo["bend_v0"]
            self.bend_v1 = topo["bend_v1"]
            self.bend_rest = topo["bend_rest"]
            self.adj_offsets = topo["adj_offsets"]
            self.adj_neighbors = topo["adj_neighbors"]

        self.stroke_id = -1
        self.stroke_hit_pos: np.ndarray | None = None
        self.stroke_hit_depth: float = 0.0
        self.stroke_start_world: np.ndarray | None = None
        self.stroke_brush_weights: np.ndarray | None = None
        self.undo_stack: list[tuple[np.ndarray, np.ndarray]] = []
        self.dirty = False
        self.mask_dirty = False

        # Warp GPU buffers (lazily allocated)
        self._wp = None
        self._dev = None
        self._gpu_ready = False
        self._wp_pos = None
        self._wp_prev_pos = None
        self._wp_stroke_start = None
        self._wp_local_out = None
        self._wp_vel = None
        self._wp_normals = None
        self._wp_mask = None
        self._wp_inv_mass = None
        self._wp_brush_w = None
        self._wp_tris = None
        self._wp_e0 = None
        self._wp_e1 = None
        self._wp_erest = None
        self._wp_b0 = None
        self._wp_b1 = None
        self._wp_brest = None
        self._wp_adj_off = None
        self._wp_adj_nbr = None
        self._wp_corr_sum = None
        self._wp_corr_cnt = None
        self._wp_tmp_pos = None
        self._collider_key = None
        self._wp_collider_mesh = None
        self._wp_collider_pts = None
        self._wp_collider_tris = None

        self._init_gpu()

    def _init_gpu(self):
        import cloth_kernels as ck

        wp, dev = ck.get_warp()
        if wp is None or dev is None:
            return
        self._wp = wp
        self._dev = dev
        self._wp_pos = wp.array(self.pos_world, dtype=wp.vec3, device=dev)
        self._wp_prev_pos = wp.zeros(self.n_verts, dtype=wp.vec3, device=dev)
        self._wp_stroke_start = wp.array(self.pos_world, dtype=wp.vec3, device=dev)
        self._wp_local_out = wp.zeros(self.n_verts, dtype=wp.vec3, device=dev)
        self._wp_tmp_pos = wp.zeros(self.n_verts, dtype=wp.vec3, device=dev)
        self._wp_vel = wp.zeros(self.n_verts, dtype=wp.vec3, device=dev)
        self._wp_normals = wp.zeros(self.n_verts, dtype=wp.vec3, device=dev)
        self._wp_mask = wp.array(self.mask, dtype=float, device=dev)
        self._wp_inv_mass = wp.zeros(self.n_verts, dtype=float, device=dev)
        self._wp_brush_w = wp.zeros(self.n_verts, dtype=float, device=dev)
        self._wp_tris = wp.array(self.tris, dtype=int, device=dev)
        self._wp_e0 = wp.array(self.edge_v0, dtype=int, device=dev)
        self._wp_e1 = wp.array(self.edge_v1, dtype=int, device=dev)
        self._wp_erest = wp.array(self.edge_rest, dtype=float, device=dev)
        self._wp_b0 = wp.array(self.bend_v0, dtype=int, device=dev)
        self._wp_b1 = wp.array(self.bend_v1, dtype=int, device=dev)
        self._wp_brest = wp.array(self.bend_rest, dtype=float, device=dev)
        if self.adj_offsets is not None and self.adj_neighbors is not None:
            self._wp_adj_off = wp.array(self.adj_offsets, dtype=int, device=dev)
            self._wp_adj_nbr = wp.array(self.adj_neighbors, dtype=int, device=dev)
        self._wp_corr_sum = wp.zeros(self.n_sim, dtype=wp.vec3, device=dev)
        self._wp_corr_cnt = wp.zeros(self.n_sim, dtype=int, device=dev)

        # Compact proxy GPU buffers when solving dense meshes via coarse cloth proxy
        self._wp_p_pos = None
        self._wp_p_prev_pos = None
        self._wp_p_start = None
        self._wp_p_vel = None
        self._wp_p_normals = None
        self._wp_p_mask = None
        self._wp_p_inv_mass = None
        self._wp_p_brush_w = None
        self._wp_p_tris = None
        self._wp_bind_idx = None
        self._wp_bind_w = None
        if self.use_proxy and self._proxy is not None:
            self._wp_p_pos = wp.array(self.proxy_pos, dtype=wp.vec3, device=dev)
            self._wp_p_prev_pos = wp.zeros(self.n_sim, dtype=wp.vec3, device=dev)
            self._wp_p_start = wp.array(self.proxy_pos, dtype=wp.vec3, device=dev)
            self._wp_p_vel = wp.zeros(self.n_sim, dtype=wp.vec3, device=dev)
            self._wp_p_normals = wp.array(self._proxy["proxy_normals"], dtype=wp.vec3, device=dev)
            self._wp_p_mask = wp.array(self.proxy_mask, dtype=float, device=dev)
            self._wp_p_inv_mass = wp.zeros(self.n_sim, dtype=float, device=dev)
            self._wp_p_brush_w = wp.zeros(self.n_sim, dtype=float, device=dev)
            self._wp_p_tris = wp.array(self._proxy["tris"], dtype=int, device=dev)
            self._wp_bind_idx = wp.array(self._proxy["bind_idx"], dtype=int, device=dev)
            self._wp_bind_w = wp.array(self._proxy["bind_w"], dtype=float, device=dev)

        self._gpu_ready = True
        self._recompute_normals_gpu()

    def _ensure_fine_adj(self):
        """Lazily build fine-mesh 1-ring CSR adjacency only when Smooth brush is used on a dense mesh."""
        if self.adj_offsets is not None and self.adj_neighbors is not None:
            return
        topo = _extract_mesh_topology(self.pos_world, self.tris)
        self.adj_offsets = topo["adj_offsets"]
        self.adj_neighbors = topo["adj_neighbors"]
        if self._gpu_ready:
            wp = self._wp
            self._wp_adj_off = wp.array(self.adj_offsets, dtype=int, device=self._dev)
            self._wp_adj_nbr = wp.array(self.adj_neighbors, dtype=int, device=self._dev)

    def _sync_proxy_from_fine(self):
        """Refresh coarse proxy positions & mask from current fine mesh state at cloth stroke start."""
        if not self.use_proxy or self._proxy is None:
            return
        import cloth_proxy as cp

        self.sync_pos_from_gpu()
        self.sync_mask_from_gpu()
        self.proxy_pos = cp.recompute_proxy_positions(
            self.pos_world, self._proxy["vert_to_cluster"], self._proxy["cluster_counts"], self.n_sim
        )
        self.proxy_start_pos = self.proxy_pos.copy()
        self.proxy_vel.fill(0.0)
        self.proxy_mask = cp.aggregate_proxy_mask(self.mask, self._proxy["vert_to_cluster"], self.n_sim)
        if self._gpu_ready:
            self._wp_p_pos.assign(self.proxy_pos)
            self._wp_p_start.assign(self.proxy_start_pos)
            self._wp_p_vel.zero_()
            self._wp_p_mask.assign(self.proxy_mask)

    def _recompute_normals_gpu(self):
        import cloth_kernels as ck

        if not self._gpu_ready:
            return
        wp = self._wp
        self._wp_normals.zero_()
        n_tris = int(self.tris.size // 3)
        if n_tris > 0:
            wp.launch(
                ck.accum_face_normals_kernel,
                dim=n_tris,
                inputs=[self._wp_pos, self._wp_tris, self._wp_normals],
                device=self._dev,
            )
        wp.launch(
            ck.normalize_normals_kernel,
            dim=self.n_verts,
            inputs=[self._wp_normals],
            device=self._dev,
        )

    def ensure_collider(self, stage, collider_query: str):
        if not self._gpu_ready:
            return
        q = (collider_query or "").strip()
        if not q or q == "__none__":
            self._wp_collider_mesh = None
            self._collider_key = None
            return
        key = (id(stage), q, round(self.frame, 4))
        if self._collider_key == key and self._wp_collider_mesh is not None:
            return
        from pxr import Usd, UsdGeom

        collider_prims = []
        for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
            if not prim.IsA(UsdGeom.Mesh):
                continue
            pp = str(prim.GetPath())
            if pp == self.prim_path:
                continue
            if _mesh_query_matches(pp, q):
                collider_prims.append(prim)
        if not collider_prims:
            self._wp_collider_mesh = None
            self._collider_key = key
            return
        p_min = self.pos_world.min(axis=0) - 0.5
        p_max = self.pos_world.max(axis=0) + 0.5
        tc = Usd.TimeCode(self.frame) if self.is_animated else Usd.TimeCode.Default()
        xform_cache = UsdGeom.XformCache(tc)
        skel_ctx = _build_skel_context(stage)
        c_pts, c_tris, _ = _build_merged_collider_mesh(
            collider_prims, tc, xform_cache, p_min, p_max, np, skel_ctx=skel_ctx
        )
        if c_pts is None or c_tris is None or c_pts.shape[0] == 0 or c_tris.size == 0:
            self._wp_collider_mesh = None
            self._collider_key = key
            return
        wp = self._wp
        self._wp_collider_pts = wp.array(np.ascontiguousarray(c_pts, dtype=np.float32), dtype=wp.vec3, device=self._dev)
        self._wp_collider_tris = wp.array(np.ascontiguousarray(c_tris, dtype=np.int32), dtype=int, device=self._dev)
        self._wp_collider_mesh = wp.Mesh(points=self._wp_collider_pts, indices=self._wp_collider_tris)
        self._collider_key = key

    def sync_pos_from_gpu(self):
        if self._gpu_ready:
            self.pos_world = self._wp_pos.numpy()

    def sync_mask_from_gpu(self):
        if self._gpu_ready:
            self.mask = self._wp_mask.numpy()

    def push_undo(self):
        self.sync_pos_from_gpu()
        self.sync_mask_from_gpu()
        self.undo_stack.append((self.pos_world.copy(), self.mask.copy()))
        if len(self.undo_stack) > 24:
            self.undo_stack.pop(0)

    def undo_stroke(self) -> bool:
        if not self.undo_stack:
            return False
        prev_pos, prev_mask = self.undo_stack.pop()
        self.pos_world = prev_pos
        self.mask = prev_mask
        self.vel_world.fill(0.0)
        if self._gpu_ready:
            self._wp_pos.assign(self.pos_world)
            self._wp_mask.assign(self.mask)
            self._wp_vel.zero_()
            self._recompute_normals_gpu()
        self.dirty = True
        return True

    def clear_mask(self):
        self.push_undo()
        self.mask.fill(0.0)
        if self._gpu_ready:
            self._wp_mask.zero_()
        self.mask_dirty = True

    def invert_mask(self):
        self.push_undo()
        self.mask = np.ascontiguousarray(1.0 - self.mask, dtype=np.float32)
        if self._gpu_ready:
            self._wp_mask.assign(self.mask)
        self.mask_dirty = True

    def get_local_points(self, return_gpu: bool = False):
        """Return USD-local points as Warp GPU array (for RTX DLPack) or NumPy float32 array."""
        import cloth_kernels as ck

        if return_gpu and self._gpu_ready:
            wp = self._wp
            m = self.inv_world_mat
            wp.launch(
                ck.world_to_local_kernel,
                dim=self.n_verts,
                inputs=[
                    self._wp_pos,
                    self._wp_local_out,
                    wp.vec3(float(m[0, 0]), float(m[0, 1]), float(m[0, 2])),
                    wp.vec3(float(m[1, 0]), float(m[1, 1]), float(m[1, 2])),
                    wp.vec3(float(m[2, 0]), float(m[2, 1]), float(m[2, 2])),
                    wp.vec3(float(m[3, 0]), float(m[3, 1]), float(m[3, 2])),
                ],
                device=self._dev,
            )
            wp.synchronize_device(self._dev)
            return self._wp_local_out
        self.sync_pos_from_gpu()
        m = self.inv_world_mat
        return np.ascontiguousarray(self.pos_world @ m[:3, :3] + m[3, :3], dtype=np.float32)

    def apply_brush(self, brush: dict, cam_basis: dict, cfg: dict, stage) -> bool:
        """Apply one interactive sculpt or cloth physics brush sample."""
        import cloth_kernels as ck
        from pxr import UsdGeom

        tool = str(brush.get("tool") or "grab")
        mpu = _effective_stage_meters_per_unit(stage)
        sim_scale = max(1.0e-4, min(10000.0, float(cfg.get("clothSimScale", 1.0) or 1.0)))
        unit_scale = (1.0 / max(1.0e-5, mpu)) * sim_scale
        fallback_radius = max(1.0e-4, float(brush.get("radius", 0.08)) * unit_scale)
        screen_rad_frac = brush.get("screenRadiusFrac")
        screen_rad_frac = float(screen_rad_frac) if isinstance(screen_rad_frac, (int, float)) and screen_rad_frac > 0.0 else None
        tan_hf = float(cam_basis.get("tan_half_fov", 0.41421356))
        strength = max(0.0, min(4.0, float(brush.get("strength", 0.6))))
        invert = bool(brush.get("invert", False))
        backface_mask = 1 if brush.get("backfaceMask", True) else 0
        new_stroke = bool(brush.get("newStroke", False))
        stroke_id = int(brush.get("strokeId", 0))
        nx = float(brush.get("nx", 0.5))
        ny = float(brush.get("ny", 0.5))
        prev_nx = float(brush.get("prevNx", nx))
        prev_ny = float(brush.get("prevNy", ny))

        origin, ray_dir = _ray_from_screen(nx, ny, cam_basis)
        prev_origin, prev_ray_dir = _ray_from_screen(prev_nx, prev_ny, cam_basis)
        cam_fwd = np.asarray(cam_basis["forward"], dtype=np.float32)

        def _world_radius_at_depth(depth: float) -> float:
            if screen_rad_frac is not None:
                if cam_basis.get("orthographic"):
                    return max(1.0e-4, float(cam_basis.get("ortho_half_height", 1.0)) * screen_rad_frac)
                if depth > 1.0e-5:
                    return max(1.0e-4, depth * tan_hf * screen_rad_frac)
            return fallback_radius

        def _pick_surface(ray_o: np.ndarray, ray_d: np.ndarray):
            hp, hd, hn = _raycast_mesh_triangles(ray_o, ray_d, self.pos_world, self.tris)
            if hp is not None:
                return hp, hd, hn
            # Fallback: closest vertex to ray within brush radius (catches edges / thin strips)
            rel = self.pos_world - ray_o[None, :]
            proj = np.einsum("ij,j->i", rel, ray_d)
            in_front = proj > 1.0e-4
            if not np.any(in_front):
                return None, float("inf"), None
            idx = np.flatnonzero(in_front)
            perp = rel[idx] - proj[idx, None] * ray_d[None, :]
            perp_d = np.linalg.norm(perp, axis=1)
            best = int(np.argmin(perp_d))
            depth_b = float(proj[idx[best]])
            tol_r = _world_radius_at_depth(depth_b)
            if float(perp_d[best]) <= tol_r * 1.15:
                vi = int(idx[best])
                return self.pos_world[vi].copy(), depth_b, (-ray_d).astype(np.float32)
            return None, float("inf"), None

        def _backface_ref_dir(hit_n: np.ndarray, ref_pts: np.ndarray, hp: np.ndarray) -> np.ndarray:
            """Return a direction pointing away from the clicked surface side so
            `dot(vertex_normal, ref_dir) > 0.25` masks only the opposite side,
            regardless of mesh winding or double-sided open cloth orientation."""
            # Check how vertex normals near hit_pos align with camera-facing hit_n
            d2 = np.sum((ref_pts - hp[None, :]) ** 2, axis=1)
            nearest_v = int(np.argmin(d2))
            v_norms = _recompute_normals_cpu(ref_pts, self.tris)
            vn = v_norms[nearest_v]
            # If the mesh's own vertex normal at the hit point opposes hit_n (e.g.
            # sculpting the inside of an open cloth sheet or flipped winding),
            # flip the reference direction so the clicked side is never masked out.
            if float(np.dot(vn, hit_n)) < 0.0:
                return hit_n.astype(np.float32)
            return (-hit_n).astype(np.float32)

        radius = fallback_radius
        if new_stroke or stroke_id != self.stroke_id:
            self.sync_pos_from_gpu()
            hit_pos, hit_dist, hit_normal = _pick_surface(origin, ray_dir)
            if hit_pos is None and (abs(nx - prev_nx) > 1.0e-5 or abs(ny - prev_ny) > 1.0e-5):
                hit_pos, hit_dist, hit_normal = _pick_surface(prev_origin, prev_ray_dir)
            if hit_pos is None:
                self.stroke_id = stroke_id
                self.stroke_hit_pos = None
                return False
            self.push_undo()
            self.stroke_id = stroke_id
            self.stroke_hit_pos = hit_pos
            self.stroke_hit_depth = float(np.dot(hit_pos - origin, cam_fwd))
            radius = _world_radius_at_depth(self.stroke_hit_depth)
            self.stroke_start_world = self.pos_world.copy()
            self.vel_world.fill(0.0)
            if self._gpu_ready:
                self._wp_stroke_start.assign(self.stroke_start_world)
                self._wp_vel.zero_()

            # For Grab and Cloth Drag, capture initial stroke weights around hit_pos
            if tool == "grab":
                dists = np.linalg.norm(self.stroke_start_world - hit_pos[None, :], axis=1)
                t = np.clip(dists / radius, 0.0, 1.0)
                w = ((1.0 - t * t) ** 2 * strength).astype(np.float32)
                w[dists >= radius] = 0.0
                if backface_mask:
                    normals = _recompute_normals_cpu(self.stroke_start_world, self.tris)
                    bf_dir = _backface_ref_dir(hit_normal, self.stroke_start_world, hit_pos)
                    w[np.einsum("ij,j->i", normals, bf_dir) > 0.25] = 0.0
                self.stroke_brush_weights = w
                if self._gpu_ready:
                    self._wp_brush_w.assign(w)
            elif tool in ("clothDrag", "clothRelax", "clothInflate"):
                self.ensure_collider(stage, str(cfg.get("colliderQuery") or "__none__"))
                if self.use_proxy:
                    self._sync_proxy_from_fine()
                patch_mult = max(1.05, float(cfg.get("clothDragPinFalloff", 1.4)))
                patch_radius = radius * patch_mult
                if self._gpu_ready:
                    wp = self._wp
                    wp.launch(
                        ck.cloth_prepare_patch_kernel,
                        dim=self.n_sim,
                        inputs=[
                            self._wp_p_pos if self.use_proxy else self._wp_pos,
                            self._wp_p_mask if self.use_proxy else self._wp_mask,
                            self._wp_p_inv_mass if self.use_proxy else self._wp_inv_mass,
                            self._wp_p_brush_w if self.use_proxy else self._wp_brush_w,
                            wp.vec3(float(hit_pos[0]), float(hit_pos[1]), float(hit_pos[2])),
                            float(radius),
                            float(patch_radius),
                        ],
                        device=self._dev,
                    )
                else:
                    self._prepare_cloth_patch_cpu(hit_pos, radius, patch_radius)

        if tool in ("grab", "clothDrag"):
            if self.stroke_hit_pos is None:
                return False
            # Intersect current & previous screen rays with the camera-aligned plane at stroke_hit_depth
            denom_cur = float(np.dot(ray_dir, cam_fwd))
            denom_prev = float(np.dot(prev_ray_dir, cam_fwd))
            if abs(denom_cur) < 1.0e-5 or abs(denom_prev) < 1.0e-5:
                return False
            cur_plane_pt = origin + ray_dir * (self.stroke_hit_depth / denom_cur)
            prev_plane_pt = prev_origin + prev_ray_dir * (self.stroke_hit_depth / denom_prev)
            step_delta = (cur_plane_pt - prev_plane_pt).astype(np.float32)
            total_delta = (cur_plane_pt - self.stroke_hit_pos).astype(np.float32)

            if tool == "grab":
                if self._gpu_ready:
                    wp = self._wp
                    wp.launch(
                        ck.brush_grab_kernel,
                        dim=self.n_verts,
                        inputs=[
                            self._wp_pos,
                            self._wp_stroke_start,
                            self._wp_mask,
                            self._wp_brush_w,
                            wp.vec3(float(total_delta[0]), float(total_delta[1]), float(total_delta[2])),
                        ],
                        device=self._dev,
                    )
                else:
                    m = np.clip(1.0 - self.mask, 0.0, 1.0)
                    w = self.stroke_brush_weights * m
                    self.pos_world = self.stroke_start_world + w[:, None] * total_delta[None, :]
                self.dirty = True
                return True

            # tool == "clothDrag"
            self._run_cloth_substeps(
                mode=0,
                total_delta=total_delta,
                drag_spring=min(0.65, max(0.1, 0.35 * strength)),
                inflate_amount=0.0,
                cfg=cfg,
                stage=stage,
            )
            self.dirty = True
            return True

        # Surface-following brushes: raycast current cursor position on live mesh
        self.sync_pos_from_gpu()
        hit_pos, _, hit_normal = _pick_surface(origin, ray_dir)
        if hit_pos is None:
            return False
        radius = _world_radius_at_depth(float(np.dot(hit_pos - origin, cam_fwd)))
        bf_dir = _backface_ref_dir(hit_normal, self.pos_world, hit_pos)

        if tool in ("clothRelax", "clothInflate"):
            self.ensure_collider(stage, str(cfg.get("colliderQuery") or "__none__"))
            if self.use_proxy and new_stroke:
                self._sync_proxy_from_fine()
            patch_mult = max(1.05, float(cfg.get("clothDragPinFalloff", 1.4)))
            patch_radius = radius * patch_mult
            if self._gpu_ready:
                wp = self._wp
                wp.launch(
                    ck.cloth_prepare_patch_kernel,
                    dim=self.n_sim,
                    inputs=[
                        self._wp_p_pos if self.use_proxy else self._wp_pos,
                        self._wp_p_mask if self.use_proxy else self._wp_mask,
                        self._wp_p_inv_mass if self.use_proxy else self._wp_inv_mass,
                        self._wp_p_brush_w if self.use_proxy else self._wp_brush_w,
                        wp.vec3(float(hit_pos[0]), float(hit_pos[1]), float(hit_pos[2])),
                        float(radius),
                        float(patch_radius),
                    ],
                    device=self._dev,
                )
            else:
                self._prepare_cloth_patch_cpu(hit_pos, radius, patch_radius)

            sign = -1.0 if invert else 1.0
            mode = 1 if tool == "clothRelax" else 2
            inflate_amt = sign * strength * radius * 0.14 if mode == 2 else 0.0
            self._run_cloth_substeps(
                mode=mode,
                total_delta=np.zeros(3, dtype=np.float32),
                drag_spring=0.0,
                inflate_amount=inflate_amt,
                cfg=cfg,
                stage=stage,
            )
            self.dirty = True
            return True

        if tool in ("mask", "unmask"):
            target_val = 0.0 if (tool == "unmask" or invert) else 1.0
            if self._gpu_ready:
                wp = self._wp
                self._recompute_normals_gpu()
                wp.launch(
                    ck.brush_mask_kernel,
                    dim=self.n_verts,
                    inputs=[
                        self._wp_pos,
                        self._wp_normals,
                        self._wp_mask,
                        wp.vec3(float(hit_pos[0]), float(hit_pos[1]), float(hit_pos[2])),
                        wp.vec3(float(bf_dir[0]), float(bf_dir[1]), float(bf_dir[2])),
                        float(radius),
                        float(target_val),
                        float(strength),
                        int(backface_mask),
                    ],
                    device=self._dev,
                )
            else:
                dists = np.linalg.norm(self.pos_world - hit_pos[None, :], axis=1)
                t = np.clip(dists / radius, 0.0, 1.0)
                w = ((1.0 - t * t) ** 2).astype(np.float32)
                w[dists >= radius] = 0.0
                blend = np.clip(strength * w * 0.5, 0.0, 1.0)
                self.mask = np.clip(self.mask + (target_val - self.mask) * blend, 0.0, 1.0)
            self.mask_dirty = True
            return True

        if tool == "inflate":
            sign = -1.0 if invert else 1.0
            amount = sign * strength * radius * 0.12
            if self._gpu_ready:
                wp = self._wp
                self._recompute_normals_gpu()
                wp.launch(
                    ck.brush_inflate_kernel,
                    dim=self.n_verts,
                    inputs=[
                        self._wp_pos,
                        self._wp_normals,
                        self._wp_mask,
                        wp.vec3(float(hit_pos[0]), float(hit_pos[1]), float(hit_pos[2])),
                        wp.vec3(float(bf_dir[0]), float(bf_dir[1]), float(bf_dir[2])),
                        float(radius),
                        float(amount),
                        int(backface_mask),
                    ],
                    device=self._dev,
                )
            else:
                normals = _recompute_normals_cpu(self.pos_world, self.tris)
                dists = np.linalg.norm(self.pos_world - hit_pos[None, :], axis=1)
                t = np.clip(dists / radius, 0.0, 1.0)
                w = ((1.0 - t * t) ** 2 * np.clip(1.0 - self.mask, 0.0, 1.0)).astype(np.float32)
                w[dists >= radius] = 0.0
                self.pos_world = self.pos_world + normals * (amount * w[:, None])
            self.dirty = True
            return True

        if tool == "pinch":
            eff_str = -strength if invert else strength
            if self._gpu_ready:
                wp = self._wp
                self._recompute_normals_gpu()
                wp.launch(
                    ck.brush_pinch_kernel,
                    dim=self.n_verts,
                    inputs=[
                        self._wp_pos,
                        self._wp_normals,
                        self._wp_mask,
                        wp.vec3(float(hit_pos[0]), float(hit_pos[1]), float(hit_pos[2])),
                        wp.vec3(float(bf_dir[0]), float(bf_dir[1]), float(bf_dir[2])),
                        float(radius),
                        float(eff_str),
                        int(backface_mask),
                    ],
                    device=self._dev,
                )
            else:
                diff = hit_pos[None, :] - self.pos_world
                dists = np.linalg.norm(diff, axis=1)
                t = np.clip(dists / radius, 0.0, 1.0)
                w = ((1.0 - t * t) ** 2 * np.clip(1.0 - self.mask, 0.0, 1.0)).astype(np.float32)
                w[dists >= radius] = 0.0
                step = np.clip(eff_str * w * 0.22, -0.45, 0.45)
                self.pos_world = self.pos_world + diff * step[:, None]
            self.dirty = True
            return True

        if tool == "flatten":
            # Compute local average plane origin & normal under brush
            dists = np.linalg.norm(self.pos_world - hit_pos[None, :], axis=1)
            inside = dists < radius
            if not np.any(inside):
                return False
            normals = _recompute_normals_cpu(self.pos_world, self.tris)
            plane_origin = self.pos_world[inside].mean(axis=0).astype(np.float32)
            pn = normals[inside].mean(axis=0).astype(np.float32)
            pnl = float(np.linalg.norm(pn))
            plane_normal = (pn / pnl) if pnl > 1.0e-7 else hit_normal
            if self._gpu_ready:
                wp = self._wp
                self._recompute_normals_gpu()
                wp.launch(
                    ck.brush_flatten_kernel,
                    dim=self.n_verts,
                    inputs=[
                        self._wp_pos,
                        self._wp_normals,
                        self._wp_mask,
                        wp.vec3(float(hit_pos[0]), float(hit_pos[1]), float(hit_pos[2])),
                        wp.vec3(float(plane_origin[0]), float(plane_origin[1]), float(plane_origin[2])),
                        wp.vec3(float(plane_normal[0]), float(plane_normal[1]), float(plane_normal[2])),
                        wp.vec3(float(bf_dir[0]), float(bf_dir[1]), float(bf_dir[2])),
                        float(radius),
                        float(strength),
                        int(backface_mask),
                    ],
                    device=self._dev,
                )
            else:
                t = np.clip(dists / radius, 0.0, 1.0)
                w = ((1.0 - t * t) ** 2 * np.clip(1.0 - self.mask, 0.0, 1.0)).astype(np.float32)
                w[dists >= radius] = 0.0
                signed_d = np.einsum("ij,j->i", self.pos_world - plane_origin[None, :], plane_normal)
                blend = np.clip(strength * w * 0.45, 0.0, 0.95)
                self.pos_world = self.pos_world - (signed_d * blend)[:, None] * plane_normal[None, :]
            self.dirty = True
            return True

        if tool == "smooth":
            self._ensure_fine_adj()
            # Volume-preserving Taubin smooth (+lambda, -mu)
            lam = min(0.65, 0.45 * strength)
            mu = -min(0.68, 0.47 * strength)
            if self._gpu_ready:
                wp = self._wp
                self._recompute_normals_gpu()
                for _ in range(2):
                    wp.launch(
                        ck.brush_smooth_step_kernel,
                        dim=self.n_verts,
                        inputs=[
                            self._wp_pos,
                            self._wp_tmp_pos,
                            self._wp_normals,
                            self._wp_mask,
                            self._wp_adj_off,
                            self._wp_adj_nbr,
                            wp.vec3(float(hit_pos[0]), float(hit_pos[1]), float(hit_pos[2])),
                            wp.vec3(float(bf_dir[0]), float(bf_dir[1]), float(bf_dir[2])),
                            float(radius),
                            float(lam),
                            int(backface_mask),
                        ],
                        device=self._dev,
                    )
                    wp.launch(
                        ck.brush_smooth_step_kernel,
                        dim=self.n_verts,
                        inputs=[
                            self._wp_tmp_pos,
                            self._wp_pos,
                            self._wp_normals,
                            self._wp_mask,
                            self._wp_adj_off,
                            self._wp_adj_nbr,
                            wp.vec3(float(hit_pos[0]), float(hit_pos[1]), float(hit_pos[2])),
                            wp.vec3(float(bf_dir[0]), float(bf_dir[1]), float(bf_dir[2])),
                            float(radius),
                            float(mu),
                            int(backface_mask),
                        ],
                        device=self._dev,
                    )
            else:
                dists = np.linalg.norm(self.pos_world - hit_pos[None, :], axis=1)
                t = np.clip(dists / radius, 0.0, 1.0)
                w = ((1.0 - t * t) ** 2 * np.clip(1.0 - self.mask, 0.0, 1.0)).astype(np.float32)
                w[dists >= radius] = 0.0
                active = np.flatnonzero(w > 0.0)
                for factor in (lam, mu, lam, mu):
                    nxt = self.pos_world.copy()
                    for vi in active.tolist():
                        s0 = int(self.adj_offsets[vi])
                        s1 = int(self.adj_offsets[vi + 1])
                        if s1 > s0:
                            avg = self.pos_world[self.adj_neighbors[s0:s1]].mean(axis=0)
                            nxt[vi] = self.pos_world[vi] + (avg - self.pos_world[vi]) * (factor * w[vi])
                    self.pos_world = nxt
            self.dirty = True
            return True

        return False

    def _prepare_cloth_patch_cpu(self, hit_pos: np.ndarray, brush_radius: float, patch_radius: float):
        pts = self.proxy_pos if self.use_proxy else self.pos_world
        msk = self.proxy_mask if self.use_proxy else self.mask
        dists = np.linalg.norm(pts - hit_pos[None, :], axis=1)
        unmasked = np.clip(1.0 - msk, 0.0, 1.0)
        tp = np.clip(dists / max(1.0e-6, patch_radius), 0.0, 1.0)
        pw = (1.0 - tp * tp) ** 2
        mob = np.where(dists <= patch_radius * 0.65, unmasked, unmasked * np.clip(pw * 4.0, 0.0, 1.0))
        mob[dists >= patch_radius] = 0.0
        tb = np.clip(dists / max(1.0e-6, brush_radius), 0.0, 1.0)
        bw = ((1.0 - tb * tb) ** 2) * unmasked
        bw[dists >= brush_radius] = 0.0
        self._cpu_inv_mass = mob.astype(np.float32)
        self._cpu_brush_w = bw.astype(np.float32)

    def _run_cloth_substeps(
        self,
        mode: int,
        total_delta: np.ndarray,
        drag_spring: float,
        inflate_amount: float,
        cfg: dict,
        stage,
    ):
        import cloth_kernels as ck
        from pxr import UsdGeom

        substeps = max(1, min(16, int(cfg.get("clothSubsteps", 4))))
        iterations = max(1, min(32, int(cfg.get("clothIterations", 8))))
        stretch_k = max(0.0, min(1.0, float(cfg.get("clothStretchStiffness", 0.95))))
        bend_k = max(0.0, min(1.0, float(cfg.get("clothBendStiffness", 0.35))))
        damping = max(0.0, min(0.95, float(cfg.get("clothDamping", 0.08))))
        gravity = float(cfg.get("clothGravity", 9.81))
        margin = max(0.0, float(cfg.get("clothCollisionMargin", 0.004)))
        friction = max(0.0, min(1.0, float(cfg.get("clothFriction", 0.35))))

        up_axis = UsdGeom.GetStageUpAxis(stage) if stage is not None else UsdGeom.Tokens.y
        mpu = _effective_stage_meters_per_unit(stage)
        sim_scale = max(1.0e-4, min(10000.0, float(cfg.get("clothSimScale", 1.0) or 1.0)))
        unit_scale = (1.0 / max(1.0e-5, mpu)) * sim_scale
        g_mag = gravity * unit_scale
        if up_axis == UsdGeom.Tokens.z:
            g_vec = (0.0, 0.0, -g_mag)
        else:
            g_vec = (0.0, -g_mag, 0.0)

        dt = (1.0 / 60.0) / float(substeps)
        sub_inflate = float(inflate_amount) / float(substeps)
        # Separate stretch & bend passes with strong SOR over-relaxation so woven cloth
        # resists rubbery stretching even on dense meshes.
        stretch_iters = max(72, iterations * 8) if stretch_k > 0.0 else 0
        bend_iters = max(2, iterations // 2) if bend_k > 0.0 else 0
        median_edge = float(np.median(self.edge_rest)) if self.edge_rest.size > 0 else 0.01 * unit_scale
        max_step = min(0.025 * unit_scale, max(median_edge * 0.65, 0.002 * unit_scale))
        brush_max_speed = 6.0 * unit_scale

        if self._gpu_ready:
            wp = self._wp
            s_pos = self._wp_p_pos if self.use_proxy else self._wp_pos
            s_prev = self._wp_p_prev_pos if self.use_proxy else self._wp_prev_pos
            s_start = self._wp_p_start if self.use_proxy else self._wp_stroke_start
            s_vel = self._wp_p_vel if self.use_proxy else self._wp_vel
            s_norm = self._wp_p_normals if self.use_proxy else self._wp_normals
            s_im = self._wp_p_inv_mass if self.use_proxy else self._wp_inv_mass
            s_bw = self._wp_p_brush_w if self.use_proxy else self._wp_brush_w
            n_s = self.n_sim
            if mode == 2:
                if self.use_proxy and self._wp_p_tris is not None and self._wp_p_tris.shape[0] >= 3:
                    s_norm.zero_()
                    wp.launch(
                        ck.accum_face_normals_kernel,
                        dim=int(self._wp_p_tris.shape[0] // 3),
                        inputs=[s_pos, self._wp_p_tris, s_norm],
                        device=self._dev,
                    )
                    wp.launch(ck.normalize_normals_kernel, dim=n_s, inputs=[s_norm], device=self._dev)
                elif not self.use_proxy:
                    self._recompute_normals_gpu()
            n_edges = int(self.edge_v0.size)
            n_bends = int(self.bend_v0.size)
            for _ in range(substeps):
                wp.launch(
                    ck.cloth_predict_kernel,
                    dim=n_s,
                    inputs=[
                        s_pos,
                        s_prev,
                        s_start,
                        s_vel,
                        s_norm,
                        s_im,
                        s_bw,
                        wp.vec3(float(total_delta[0]), float(total_delta[1]), float(total_delta[2])),
                        wp.vec3(g_vec[0], g_vec[1], g_vec[2]),
                        float(sub_inflate),
                        float(drag_spring),
                        float(dt),
                        float(damping),
                        int(mode),
                    ],
                    device=self._dev,
                )
                # 1. Dedicated inextensible structural stretch passes (no bend dilution)
                for _it in range(stretch_iters):
                    if n_edges > 0:
                        wp.launch(
                            ck.cloth_stretch_jacobi_kernel,
                            dim=n_edges,
                            inputs=[
                                s_pos,
                                s_im,
                                self._wp_e0,
                                self._wp_e1,
                                self._wp_erest,
                                self._wp_corr_sum,
                                self._wp_corr_cnt,
                                float(stretch_k),
                            ],
                            device=self._dev,
                        )
                        wp.launch(
                            ck.cloth_apply_jacobi_kernel,
                            dim=n_s,
                            inputs=[
                                s_pos,
                                self._wp_corr_sum,
                                self._wp_corr_cnt,
                                float(0.95),
                                float(max_step),
                            ],
                            device=self._dev,
                        )
                # 2. Separate bending hinge passes for crisp cloth folds
                for _it in range(bend_iters):
                    if n_bends > 0:
                        wp.launch(
                            ck.cloth_bend_jacobi_kernel,
                            dim=n_bends,
                            inputs=[
                                s_pos,
                                s_im,
                                self._wp_b0,
                                self._wp_b1,
                                self._wp_brest,
                                self._wp_corr_sum,
                                self._wp_corr_cnt,
                                float(bend_k),
                            ],
                            device=self._dev,
                        )
                        wp.launch(
                            ck.cloth_apply_jacobi_kernel,
                            dim=n_s,
                            inputs=[
                                s_pos,
                                self._wp_corr_sum,
                                self._wp_corr_cnt,
                                float(0.65),
                                float(max_step),
                            ],
                            device=self._dev,
                        )
                # 3. Final stretch cleanup + collider push-out
                for _it in range(6 if (n_edges > 0 and stretch_k > 0.0) else 0):
                    wp.launch(
                        ck.cloth_stretch_jacobi_kernel,
                        dim=n_edges,
                        inputs=[
                            s_pos,
                            s_im,
                            self._wp_e0,
                            self._wp_e1,
                            self._wp_erest,
                            self._wp_corr_sum,
                            self._wp_corr_cnt,
                            float(stretch_k),
                        ],
                        device=self._dev,
                    )
                    wp.launch(
                        ck.cloth_apply_jacobi_kernel,
                        dim=n_s,
                        inputs=[
                            s_pos,
                            self._wp_corr_sum,
                            self._wp_corr_cnt,
                            float(0.85),
                            float(max_step),
                        ],
                        device=self._dev,
                    )
                if self._wp_collider_mesh is not None:
                    wp.launch(
                        ck.cloth_collider_kernel,
                        dim=n_s,
                        inputs=[
                            s_pos,
                            s_prev,
                            s_im,
                            self._wp_collider_mesh.id,
                            float(margin * unit_scale),
                            float(friction),
                            float(max(0.15 * unit_scale, margin * unit_scale * 8.0)),
                        ],
                        device=self._dev,
                    )
                wp.launch(
                    ck.cloth_sim_update_vel_kernel,
                    dim=n_s,
                    inputs=[
                        s_pos,
                        s_prev,
                        s_vel,
                        s_im,
                        float(1.0 / max(1.0e-6, dt)),
                        float(damping),
                        float(brush_max_speed),
                    ],
                    device=self._dev,
                )
            # Reconstruct full-resolution fine vertices smoothly from proxy deformation
            if self.use_proxy:
                wp.launch(
                    ck.cloth_reconstruct_fine_kernel,
                    dim=self.n_verts,
                    inputs=[
                        self._wp_stroke_start,
                        self._wp_pos,
                        self._wp_mask,
                        self._wp_p_start,
                        self._wp_p_pos,
                        self._wp_bind_idx,
                        self._wp_bind_w,
                    ],
                    device=self._dev,
                )
            return

        # CPU fallback XPBD cloth solver (separate stretch & bend passes)
        inv_m = self._cpu_inv_mass
        bw = self._cpu_brush_w
        g_arr = np.asarray(g_vec, dtype=np.float32)
        normals = _recompute_normals_cpu(self.pos_world, self.tris) if mode == 2 else None
        start_w = self.stroke_start_world if self.stroke_start_world is not None else self.pos_world
        for _ in range(substeps):
            prev_p = self.pos_world.copy()
            mobile = inv_m > 0.0
            self.vel_world[~mobile] = 0.0
            if mode == 0:
                self.vel_world[mobile] *= 0.2
                target = start_w[mobile] + total_delta[None, :]
                pull = (target - self.pos_world[mobile]) * np.clip(bw[mobile] * drag_spring, 0.0, 0.65)[:, None]
                self.pos_world[mobile] += self.vel_world[mobile] * dt + pull
            elif mode == 1:
                self.vel_world[mobile] *= max(0.0, 1.0 - damping)
                self.vel_world[mobile] += (dt * bw[mobile, None] * inv_m[mobile, None]) * g_arr[None, :]
                self.pos_world[mobile] += self.vel_world[mobile] * dt
            else:
                self.vel_world[mobile] *= 0.25
                self.pos_world[mobile] += self.vel_world[mobile] * dt + normals[mobile] * (sub_inflate * bw[mobile, None])

            def _cpu_pass(v0, v1, rest, k_val, omega):
                if v0.size == 0 or k_val <= 0.0:
                    return
                w0 = inv_m[v0]
                w1 = inv_m[v1]
                ws = w0 + w1
                act = ws > 1.0e-8
                if not np.any(act):
                    return
                i0 = v0[act]
                i1 = v1[act]
                diff = self.pos_world[i1] - self.pos_world[i0]
                dist = np.linalg.norm(diff, axis=1)
                ok = dist > 1.0e-8
                if not np.any(ok):
                    return
                i0, i1, diff, dist = i0[ok], i1[ok], diff[ok], dist[ok]
                err = dist - rest[act][ok]
                scale = k_val * err / ws[act][ok]
                n = diff / dist[:, None]
                corr = np.zeros_like(self.pos_world)
                cnt = np.zeros((self.n_verts,), dtype=np.int32)
                np.add.at(corr, i0, n * (scale * w0[act][ok])[:, None])
                np.add.at(corr, i1, -n * (scale * w1[act][ok])[:, None])
                np.add.at(cnt, i0, 1)
                np.add.at(cnt, i1, 1)
                has_c = cnt > 0
                if np.any(has_c):
                    self.pos_world[has_c] += (corr[has_c] / cnt[has_c, None]) * omega

            for _it in range(stretch_iters):
                _cpu_pass(self.edge_v0, self.edge_v1, self.edge_rest, stretch_k, 1.75)
            for _it in range(bend_iters):
                _cpu_pass(self.bend_v0, self.bend_v1, self.bend_rest, bend_k, 1.1)
            for _it in range(6 if stretch_k > 0.0 else 0):
                _cpu_pass(self.edge_v0, self.edge_v1, self.edge_rest, stretch_k, 1.75)

            self.vel_world[mobile] = ((self.pos_world[mobile] - prev_p[mobile]) / max(1.0e-6, dt)) * max(
                0.0, 1.0 - damping
            )


def _get_or_create_sessions(stage, cfg: dict, frame: float):
    """Get or initialize `CacheMeshSession`s for all target meshes on `stage` at `frame`."""
    from pxr import Usd, UsdGeom

    target_query = str(cfg.get("targetQuery") or "")
    target_prims = _collect_target_meshes(stage, target_query, make_editable=True)
    if not target_prims:
        return []

    layers = cfg.get("layers") if isinstance(cfg.get("layers"), list) else []
    active_layer_id = str(cfg.get("activeLayerId") or "")
    active_layer = next((l for l in layers if isinstance(l, dict) and l.get("id") == active_layer_id), None)
    if active_layer is None and layers:
        active_layer = layers[0]
        active_layer_id = str(active_layer.get("id") or "")

    eff_scale = 1.0
    if isinstance(active_layer, dict):
        w = float(active_layer.get("weight", 1.0))
        tw = compute_temporal_weight(frame, active_layer)
        if abs(w * tw) > 1.0e-4:
            eff_scale = w * tw
        elif abs(w) > 1.0e-4:
            eff_scale = w

    masks_map = cfg.get("maskWeights") if isinstance(cfg.get("maskWeights"), dict) else {}
    sessions = []

    for prim in target_prims:
        pp = str(prim.GetPath())
        mesh = UsdGeom.Mesh(prim)
        pts_attr = mesh.GetPointsAttr()
        if not pts_attr:
            continue
        is_anim = len(pts_attr.GetTimeSamples()) > 0
        tc = Usd.TimeCode(frame) if is_anim else Usd.TimeCode.Default()
        sess_key = (id(stage), pp, round(frame, 4) if is_anim else None, active_layer_id)
        sess = _CACHE_SESSIONS.get(sess_key)
        if sess is not None:
            _CACHE_SESSIONS.move_to_end(sess_key)
            sessions.append((prim, sess))
            continue

        cur_pts = pts_attr.Get(tc)
        if not cur_pts or len(cur_pts) == 0:
            continue
        cur_local_np = np.asarray(cur_pts, dtype=np.float32).copy()
        n_verts = int(cur_local_np.shape[0])

        # Compute base_local_pts by subtracting the active layer's current contribution
        base_local_np = cur_local_np.copy()
        if isinstance(active_layer, dict) and active_layer.get("enabled", True):
            d_map = active_layer.get("deltas")
            if isinstance(d_map, dict) and pp in d_map:
                existing_d = decode_mesh_delta(d_map.get(pp), n_verts)
                if existing_d is not None:
                    w = float(active_layer.get("weight", 1.0))
                    tw = compute_temporal_weight(frame, active_layer) if is_anim else (
                        compute_temporal_weight(0.0, active_layer) or 1.0
                    )
                    base_local_np = np.ascontiguousarray(cur_local_np - (w * tw) * existing_d, dtype=np.float32)

        xform_cache = UsdGeom.XformCache(tc)
        world_mat = np.asarray(xform_cache.GetLocalToWorldTransform(prim), dtype=np.float32)
        inv_world_mat = np.asarray(xform_cache.GetLocalToWorldTransform(prim).GetInverse(), dtype=np.float32)

        _, tris_np = _triangulate_mesh(mesh, tc, xform_cache, np)
        if tris_np is None or tris_np.size == 0:
            continue
        # Respect leftHanded USD mesh orientation so face/vertex normals always point outward
        orient = mesh.GetOrientationAttr().Get(tc)
        if orient == UsdGeom.Tokens.leftHanded:
            t2d = tris_np.reshape(-1, 3)[:, [0, 2, 1]]
            tris_np = np.ascontiguousarray(t2d.reshape(-1), dtype=np.int32)

        mask_np = decode_mesh_mask(masks_map.get(pp), n_verts)
        sess = CacheMeshSession(
            prim_path=pp,
            base_local_pts=base_local_np,
            cur_local_pts=cur_local_np,
            tris=tris_np,
            world_mat=world_mat,
            inv_world_mat=inv_world_mat,
            mask=mask_np,
            frame=frame,
            is_animated=is_anim,
            active_layer_id=active_layer_id,
            effective_layer_scale=eff_scale,
        )
        _CACHE_SESSIONS[sess_key] = sess
        while len(_CACHE_SESSIONS) > _MAX_CACHE_SESSIONS:
            _CACHE_SESSIONS.popitem(last=False)
        sessions.append((prim, sess))

    return sessions


def step_cache_brush(
    stage,
    cfg: dict,
    brush: dict,
    cam_basis: dict,
    frame: float = 0.0,
    write_usd: bool = True,
    return_gpu: bool = False,
):
    """Execute one interactive sculpt/cloth brush step on `stage` and return `[(prim_path, pts)]`."""
    from pxr import Usd, UsdGeom, Vt

    sessions = _get_or_create_sessions(stage, cfg, frame)
    if not sessions:
        return []

    updates = []
    if brush.get("undoStroke"):
        for prim, sess in sessions:
            if sess.undo_stroke():
                pts_out = sess.get_local_points(return_gpu=return_gpu)
                if write_usd:
                    local_np = pts_out.numpy() if hasattr(pts_out, "numpy") else pts_out
                    tc = Usd.TimeCode(sess.frame) if sess.is_animated else Usd.TimeCode.Default()
                    UsdGeom.Mesh(prim).GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(local_np), tc)
                updates.append((sess.prim_path, pts_out))
        return updates

    if brush.get("clearMask"):
        for _prim, sess in sessions:
            sess.clear_mask()
        return updates

    if brush.get("invertMask"):
        for _prim, sess in sessions:
            sess.invert_mask()
        return updates

    if brush.get("commitOnly"):
        for prim, sess in sessions:
            if sess.dirty:
                pts_out = sess.get_local_points(return_gpu=return_gpu)
                if write_usd:
                    local_np = pts_out.numpy() if hasattr(pts_out, "numpy") else pts_out
                    tc = Usd.TimeCode(sess.frame) if sess.is_animated else Usd.TimeCode.Default()
                    UsdGeom.Mesh(prim).GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(local_np), tc)
                updates.append((sess.prim_path, pts_out))
        return updates

    for prim, sess in sessions:
        changed = sess.apply_brush(brush, cam_basis, cfg, stage)
        if changed and not str(brush.get("tool", "")).endswith("mask"):
            pts_out = sess.get_local_points(return_gpu=return_gpu)
            if write_usd:
                local_np = pts_out.numpy() if hasattr(pts_out, "numpy") else pts_out
                tc = Usd.TimeCode(sess.frame) if sess.is_animated else Usd.TimeCode.Default()
                UsdGeom.Mesh(prim).GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(local_np), tc)
            updates.append((sess.prim_path, pts_out))
    return updates


def export_session_cache_deltas(stage, cfg: dict, frame: float = 0.0):
    """Export encoded sparse deltas for the active layer and masks from live sessions."""
    sessions = _get_or_create_sessions(stage, cfg, frame)
    deltas_out = {}
    masks_out = {}
    active_layer_id = str(cfg.get("activeLayerId") or "")
    for _prim, sess in sessions:
        if sess.active_layer_id:
            active_layer_id = sess.active_layer_id
        local_np = sess.get_local_points(return_gpu=False)
        scale = sess.effective_layer_scale if abs(sess.effective_layer_scale) > 1.0e-4 else 1.0
        delta_np = (local_np - sess.base_local_pts) / scale
        enc = encode_mesh_delta(delta_np)
        if enc is not None:
            deltas_out[sess.prim_path] = enc
        sess.sync_mask_from_gpu()
        m_enc = encode_mesh_mask(sess.mask)
        if m_enc is not None:
            masks_out[sess.prim_path] = m_enc
        sess.dirty = False
        sess.mask_dirty = False
    return active_layer_id, deltas_out, masks_out


def get_cache_mask_vertex_colors(stage, cfg: dict | None, frame: float = 0.0):
    """Return `[(prim_path, colors_np)]` float32 `(N, 3)` RGB vertex colors for mask visualization.

    Unmasked (`0.0`) renders as neutral clay grey `(0.65, 0.67, 0.70)` and masked (`1.0`)
    renders as deep magenta-crimson `(0.80, 0.14, 0.42)` with smooth linear blending.
    """
    from pxr import Usd, UsdGeom

    if not isinstance(cfg, dict) or stage is None:
        return []
    target_query = str(cfg.get("targetQuery") or "")
    prims = _collect_target_meshes(stage, target_query, make_editable=False)
    if not prims:
        return []

    unmasked_rgb = np.array([0.65, 0.67, 0.70], dtype=np.float32)
    masked_rgb = np.array([0.80, 0.14, 0.42], dtype=np.float32)
    delta_rgb = masked_rgb - unmasked_rgb

    mask_map = cfg.get("maskWeights") if isinstance(cfg.get("maskWeights"), dict) else {}
    out = []
    stage_id = id(stage)
    frame_key = round(float(frame), 4)
    for prim in prims:
        prim_path = str(prim.GetPath())
        mesh = UsdGeom.Mesh(prim)
        pts_attr = mesh.GetPointsAttr()
        if not pts_attr:
            continue
        sess = _CACHE_SESSIONS.get((stage_id, prim_path, frame_key))
        if sess is not None:
            sess.sync_mask_from_gpu()
            w = np.clip(sess.mask, 0.0, 1.0).astype(np.float32)
        else:
            sample_times = pts_attr.GetTimeSamples()
            tc = Usd.TimeCode(float(frame)) if sample_times else Usd.TimeCode.Default()
            raw = pts_attr.Get(tc)
            if not raw:
                continue
            w = decode_mesh_mask(mask_map.get(prim_path), len(raw))
        colors = np.ascontiguousarray(unmasked_rgb[None, :] + w[:, None] * delta_rgb[None, :], dtype=np.float32)
        out.append((prim_path, colors))
    return out
