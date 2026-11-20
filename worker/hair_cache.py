"""Simulation cache and compact groom-delta serialization for the Hair node.

Manages:
- Bounded in-memory LRU cache (`_SIM_CACHE`) and compressed `.npz` disk cache
  under `<tempdir>/sublayer_hair_sim_cache` keyed by curve/collider/settings hash.
- Sparse Z-RLE + base64 encoding/decoding of per-vertex local-space groom deltas
  and optional topology overrides (`countsZB64`, `pointsZB64`, `widthsZB64`).
"""

import base64
import hashlib
import json
import os
import tempfile
import zlib
from collections import OrderedDict

_SIM_CACHE: OrderedDict[str, dict[float, object]] = OrderedDict()
_MAX_CACHE_ENTRIES = 16
_MAX_DISK_CACHE_ENTRIES = 16


def _hair_disk_cache_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "sublayer_hair_sim_cache")


def _hair_disk_cache_path(cache_key: str) -> str:
    return os.path.join(_hair_disk_cache_dir(), f"{cache_key}.npz")


def _load_hair_disk_cache(cache_key: str):
    """Load cached simulation frames for `cache_key` from disk into `_SIM_CACHE`."""
    import numpy as np
    from pxr import Vt

    path = _hair_disk_cache_path(cache_key)
    if not os.path.isfile(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            is_static = bool(data["is_static"][0])
            frames = data["frames"]
            pts = data["points"]
        result = {}
        if is_static:
            result[None] = Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(pts[0], dtype=np.float32))
        else:
            for idx, f in enumerate(frames):
                result[float(f)] = Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(pts[idx], dtype=np.float32))
        _SIM_CACHE[cache_key] = result
        while len(_SIM_CACHE) > _MAX_CACHE_ENTRIES:
            _SIM_CACHE.popitem(last=False)
        return result
    except Exception:
        return None


def _save_hair_disk_cache(cache_key: str, frame_results: dict) -> None:
    """Persist simulated frame results to disk and prune oldest cache entries."""
    import numpy as np

    if not frame_results:
        return
    cache_dir = _hair_disk_cache_dir()
    path = _hair_disk_cache_path(cache_key)
    tmp_path = f"{path}.tmp.{os.getpid()}.npz"
    try:
        os.makedirs(cache_dir, exist_ok=True)
        is_static = None in frame_results
        if is_static:
            frames_arr = np.array([0.0], dtype=np.float64)
            pts_arr = np.asarray(frame_results[None], dtype=np.float32)[None, :, :]
        else:
            sorted_keys = sorted(frame_results.keys())
            frames_arr = np.array(sorted_keys, dtype=np.float64)
            pts_arr = np.stack([np.asarray(frame_results[k], dtype=np.float32) for k in sorted_keys], axis=0)
        np.savez(
            tmp_path,
            is_static=np.array([1 if is_static else 0], dtype=np.uint8),
            frames=frames_arr,
            points=pts_arr,
        )
        os.replace(tmp_path, path)
        entries = [
            os.path.join(cache_dir, name)
            for name in os.listdir(cache_dir)
            if name.endswith(".npz") and ".tmp." not in name
        ]
        if len(entries) > _MAX_DISK_CACHE_ENTRIES:
            entries.sort(key=lambda p: os.path.getmtime(p))
            for old_p in entries[:-_MAX_DISK_CACHE_ENTRIES]:
                try:
                    os.remove(old_p)
                except Exception:
                    pass
    except Exception:
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _compute_cache_key(prim_path, cfg, rest_pts_np, counts_np, collider_paths, fps):
    """Compute a deterministic SHA-256 key for a Hair node simulation cache entry."""
    h = hashlib.sha256()
    h.update(b"solver_v=7;")
    h.update(str(prim_path).encode("utf-8"))
    for k in (
        "simMode",
        "settleSteps",
        "startFrame",
        "endFrame",
        "substeps",
        "iterations",
        "stiffness",
        "bendStiffness",
        "damping",
        "gravity",
        "wind",
        "collisionMargin",
        "friction",
        "colliderQuery",
    ):
        h.update(f"{k}={cfg.get(k)};".encode("utf-8"))
    h.update(f"fps={fps};".encode("utf-8"))
    h.update(",".join(collider_paths).encode("utf-8"))
    h.update(counts_np.tobytes())
    h.update(rest_pts_np.tobytes())
    return h.hexdigest()


def _encode_groom_array(array):
    # Zero runs are common in untouched strands. Keep the existing zlib
    # format while avoiding the general-purpose match search on mouse-up.
    encoder = zlib.compressobj(level=1, strategy=zlib.Z_RLE)
    return base64.b64encode(encoder.compress(array) + encoder.flush()).decode("ascii")


def _encode_groom_delta(delta_local_np, curve_count, np, counts_np=None, points_local_np=None, widths_np=None):
    """Encode a (N, 3) float32 local-space groom delta (and optional topology) into a compact dict."""
    arr = np.ascontiguousarray(delta_local_np, dtype="<f4")
    out = {
        "pointCount": int(arr.shape[0]),
        "curveCount": int(curve_count),
    }
    if counts_np is None and len(arr) <= np.iinfo(np.uint32).max:
        changed = np.flatnonzero(np.any(arr != 0.0, axis=1))
        if 0 < len(changed) * 4 < len(arr):
            out["pointIndicesZB64"] = _encode_groom_array(np.ascontiguousarray(changed, dtype="<u4"))
            arr = arr[changed]
    out["deltasZB64"] = _encode_groom_array(arr)
    if counts_np is not None and points_local_np is not None:
        c_arr = np.ascontiguousarray(counts_np, dtype="<i4")
        p_arr = np.ascontiguousarray(points_local_np, dtype="<f4")
        out["countsZB64"] = _encode_groom_array(c_arr)
        out["pointsZB64"] = _encode_groom_array(p_arr)
        if widths_np is not None and len(widths_np) > 0:
            w_arr = np.ascontiguousarray(widths_np, dtype="<f4")
            out["widthsZB64"] = _encode_groom_array(w_arr)
    return out


def _decode_topology_override(entry, np):
    """Decode optional (counts_np, points_local_np, widths_np) when Add/Delete changed topology."""
    if not isinstance(entry, dict):
        return None
    cb64 = entry.get("countsZB64")
    pb64 = entry.get("pointsZB64")
    if not isinstance(cb64, str) or not cb64 or not isinstance(pb64, str) or not pb64:
        return None
    try:
        counts_raw = zlib.decompress(base64.b64decode(cb64.encode("ascii")))
        pts_raw = zlib.decompress(base64.b64decode(pb64.encode("ascii")))
        counts_np = np.ascontiguousarray(np.frombuffer(counts_raw, dtype="<i4"), dtype=np.int32)
        pts_flat = np.frombuffer(pts_raw, dtype="<f4")
        total_verts = int(counts_np.sum()) if len(counts_np) > 0 else 0
        if pts_flat.size != total_verts * 3:
            return None
        pts_np = np.ascontiguousarray(pts_flat.reshape((total_verts, 3)), dtype=np.float32)
        widths_np = None
        wb64 = entry.get("widthsZB64")
        if isinstance(wb64, str) and wb64:
            w_raw = zlib.decompress(base64.b64decode(wb64.encode("ascii")))
            widths_np = np.ascontiguousarray(np.frombuffer(w_raw, dtype="<f4"), dtype=np.float32)
        return counts_np, pts_np, widths_np
    except Exception:
        return None


def _decode_groom_delta(entry, expected_verts, expected_curves, np):
    """Decode a groom delta dict if its topology matches; returns (N, 3) float32 or None."""
    if not isinstance(entry, dict):
        return None
    if int(entry.get("pointCount", -1)) != int(expected_verts):
        return None
    if int(entry.get("curveCount", -1)) != int(expected_curves):
        return None
    b64 = entry.get("deltasZB64")
    if not isinstance(b64, str) or not b64:
        return None
    try:
        raw = zlib.decompress(base64.b64decode(b64.encode("ascii")))
        arr = np.frombuffer(raw, dtype="<f4")
        if "pointIndicesZB64" in entry:
            index_raw = zlib.decompress(base64.b64decode(entry["pointIndicesZB64"], validate=True))
            indices = np.frombuffer(index_raw, dtype="<u4")
            if (
                not indices.size or arr.size != indices.size * 3
                or int(indices[-1]) >= int(expected_verts)
                or np.any(indices[1:] <= indices[:-1])
            ):
                return None
            restored = np.zeros((int(expected_verts), 3), dtype=np.float32)
            restored[indices] = arr.reshape((-1, 3))
            return restored
        if arr.size != int(expected_verts) * 3:
            return None
        return np.ascontiguousarray(arr.reshape((int(expected_verts), 3)), dtype=np.float32)
    except Exception:
        return None


def _groom_signature(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode("utf-8")).hexdigest() if entry is not None else ""
