"""GPU-accelerated hair strand simulation & interactive grooming for the Hair node.

Uses NVIDIA Warp (ADMM/Jacobi XPBD Discrete Elastic Rod constraints + GPU BVH
mesh collision) when `simEnabled` is True. Imported lazily by `usd_graph.py`
so core app functionality and non-simulated Hair nodes have zero dependency on
Warp or NumPy.

Architecture (split across focused worker modules):
- `hair_sim.py`: Session lifecycle (`_ACTIVE_SIM_SESSIONS`), USD curve discovery,
  `apply_hair_simulation`, `step_hair_brush`, and `export_session_groom_deltas`.
- `hair_kernels.py`: Lazy Warp initialization (`_ensure_warp`) and GPU kernels.
- `hair_cache.py`: In-memory/disk simulation cache and sparse groom-delta codecs.
- `hair_colliders.py`: USD mesh/UsdSkel collider triangulation and space transforms.
- `hair_topology_brushes.py`: `lengthen`, `shorten`, `cut`, `add`, and `delete`
  topology-editing brush handlers.
- `hair_guides.py` / `hair_rods.py` / `hair_contacts.py`: Guide-strand brush
  solver, Discrete Elastic Rod inertia/twist, and BVH contact projection.
"""

import os
import sys

from hair_cache import (
    _MAX_CACHE_ENTRIES,
    _SIM_CACHE,
    _compute_cache_key,
    _decode_groom_delta,
    _decode_topology_override,
    _encode_groom_delta,
    _groom_signature,
    _load_hair_disk_cache,
    _save_hair_disk_cache,
)
from hair_colliders import (
    _build_merged_collider_mesh,
    _build_skel_context,
    _points_to_world_np,
    _transform_might_be_time_varying,
    _triangulate_mesh,
    _world_np_to_local_vt,
)
from hair_kernels import _build_kernels, _ensure_warp
from hair_topology_brushes import (
    _apply_add_or_delete_brush,
    _apply_cut_line_brush,
    _apply_draw_lock_brush,
    _apply_length_or_cut_brush,
    _apply_lengthen_drag_brush,
)

_SCRATCH_BUFFERS: dict[str, object] = {}


_ACTIVE_SIM_SESSIONS: dict[str, dict[str, object]] = {}
_BRUSH_CURVE_INDEX = None


class _BrushCurveIndex:
    def __init__(self, stage):
        from pxr import Sdf, Tf, Usd

        self.stage = stage
        self.paths = None
        self.root_path = Sdf.Path.absoluteRootPath
        self.notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._changed, stage)

    def _changed(self, notice, _sender):
        # Point and curve-count attribute edits leave the set of curve prims
        # intact. Prim and root resyncs can change the composed hierarchy.
        if any(path.IsPrimPath() or path == self.root_path for path in notice.GetResyncedPaths()):
            self.paths = None

    def curve_paths(self):
        from pxr import UsdGeom

        if self.paths is None:
            self.paths = tuple(
                str(prim.GetPath()) for prim in self.stage.Traverse()
                if not prim.IsInstanceProxy() and prim.IsA(UsdGeom.BasisCurves)
            )
        return self.paths


def _brush_curve_paths(stage, raw_query, query_matcher):
    global _BRUSH_CURVE_INDEX
    if _BRUSH_CURVE_INDEX is None or _BRUSH_CURVE_INDEX.stage is not stage:
        if _BRUSH_CURVE_INDEX is not None:
            _BRUSH_CURVE_INDEX.notice.Revoke()
        _BRUSH_CURVE_INDEX = _BrushCurveIndex(stage)
    return tuple(path for path in _BRUSH_CURVE_INDEX.curve_paths() if query_matcher(raw_query, path))


def reset_hair_sessions():
    global _BRUSH_CURVE_INDEX
    if _BRUSH_CURVE_INDEX is not None:
        _BRUSH_CURVE_INDEX.notice.Revoke()
        _BRUSH_CURVE_INDEX = None
    _ACTIVE_SIM_SESSIONS.clear()
    _SIM_CACHE.clear()


def _emit_sim_status(phase: str):
    try:
        main_mod = sys.modules.get('__main__')
        fn = getattr(main_mod, 'report_status', None)
        if callable(fn):
            fn(phase)
            return
        import usd_graph
        usd_graph.report_status(phase)
    except Exception:
        pass




def _session_local_points(sess, np):
    if not sess["total_verts"]:
        return np.zeros((0, 3), dtype=np.float32)
    if sess.get("local_points_current"):
        return sess["local_wp"].numpy()
    world_delta = sess["pos_wp"].numpy() - sess["reference_world_wp"].numpy()
    linear = np.asarray(sess["world_to_local"], dtype=np.float32)[:3, :3]
    local = sess["reference_local_wp"].numpy() + world_delta @ linear
    return np.ascontiguousarray(local, dtype=np.float32)


def export_session_groom_deltas(stage, hair_cfg, query_matcher, *, local_updates=None, acknowledge=False):
    """Return sparse groomDeltas dict for all matching prims in active sessions."""
    from pxr import UsdGeom

    wp, np, _device = _ensure_warp()
    cfg = hair_cfg if isinstance(hair_cfg, dict) else {}
    raw_query = str(cfg.get("targetQuery") or "").strip()
    updated_points = dict(local_updates or ())
    if local_updates is None:
        paths = (str(prim.GetPath()) for prim in stage.Traverse()
                 if prim.IsA(UsdGeom.BasisCurves) and not prim.IsInstanceProxy())
        deltas_out = {}
    else:
        # Brush updates already identify the affected curves. Keep other saved
        # entries intact and avoid another full stage traversal at commit.
        paths = updated_points.keys()
        deltas_out = dict(cfg.get("groomDeltas") or {})
    for p_str in paths:
        if not query_matcher(raw_query, p_str):
            continue
        sess = _ACTIVE_SIM_SESSIONS.get(p_str)
        if sess is None:
            continue
        base_local = sess.get("base_settled_local_np")
        if base_local is None:
            continue
        deltas_out.pop(p_str, None)
        local_np = updated_points.get(p_str)
        if local_np is None:
            local_np = _session_local_points(sess, np)
        if sess.get("topology_dirty"):
            zero_delta = np.zeros_like(local_np, dtype=np.float32)
            deltas_out[p_str] = _encode_groom_delta(
                zero_delta,
                sess["num_strands"],
                np,
                counts_np=sess["counts_np"],
                points_local_np=local_np,
                widths_np=sess.get("widths_np"),
            )
        elif local_np.shape == base_local.shape:
            delta = local_np - base_local
            if delta.size and float(np.max(np.abs(delta))) > 1e-6:
                deltas_out[p_str] = _encode_groom_delta(delta, sess["num_strands"], np)
        if acknowledge:
            sess["synced_groom_sig"] = _groom_signature(deltas_out.get(p_str))
            sess["dirty_groom"] = False
    return deltas_out



def apply_hair_simulation(stage, hair_cfg, query_matcher):
    """Simulate matching `UsdGeom.BasisCurves` prims on the GPU using NVIDIA Warp."""
    from pxr import Usd, UsdGeom, Vt

    cfg = hair_cfg if isinstance(hair_cfg, dict) else {}
    if not bool(cfg.get("simEnabled", False)):
        return

    wp, np, device = _ensure_warp()
    kernels = _build_kernels(wp)
    (
        predict_kernel,
        zero_accum_kernel,
        stretch_constraint_kernel,
        bend_constraint_kernel,
        apply_accum_kernel,
        collide_mesh_kernel,
        update_vel_kernel,
    ) = kernels[:7]
    collide_segments_kernel = kernels[19]
    shape_bend_constraint_kernel = kernels[21]
    vol_grid_accum_kernel = kernels[22]
    vol_grid_repel_kernel = kernels[23]
    enforce_strand_length_kernel = kernels[27]

    raw_query = str(cfg.get("targetQuery") or "").strip()
    sim_mode = str(cfg.get("simMode") or "static").strip().lower()
    if sim_mode not in ("static", "animated"):
        raise ValueError("Hair simMode must be 'static' or 'animated'")
    settle_steps = max(1, min(500, int(cfg.get("settleSteps", 24))))
    collider_query = str(cfg.get("colliderQuery") or "__none__").strip()
    start_frame = int(round(float(cfg.get("startFrame", 1.0))))
    end_frame = int(round(float(cfg.get("endFrame", 48.0))))
    if sim_mode == "animated":
        if end_frame < start_frame:
            raise ValueError("Hair simulation endFrame must be >= startFrame")
        if end_frame - start_frame > 10000:
            raise ValueError("Hair simulation frame range exceeds 10,000 frames")

    substeps = max(1, min(64, int(cfg.get("substeps", 4))))
    iterations = max(1, min(128, int(cfg.get("iterations", 8))))
    stiffness = max(0.0, min(1.0, float(cfg.get("stiffness", 0.95))))
    bend_stiffness = max(0.0, min(1.0, float(cfg.get("bendStiffness", 0.4))))
    damping = max(0.0, min(1.0, float(cfg.get("damping", 0.04))))
    gravity = float(cfg.get("gravity", 9.81))
    wind_raw = cfg.get("wind") or [0.0, 0.0, 0.0]
    wind = (
        float(wind_raw[0]) if len(wind_raw) > 0 else 0.0,
        float(wind_raw[1]) if len(wind_raw) > 1 else 0.0,
        float(wind_raw[2]) if len(wind_raw) > 2 else 0.0,
    )
    margin = max(0.0, float(cfg.get("collisionMargin", 0.005)))
    friction = max(0.0, min(1.0, float(cfg.get("friction", 0.25))))
    sim_scale = max(1e-3, min(10000.0, float(cfg.get("simScale", 1.0) or 1.0)))

    fps = float(stage.GetTimeCodesPerSecond() or 24.0)
    if fps <= 0.0:
        fps = 24.0
    dt_frame = 1.0 / fps
    dt_sub = dt_frame / float(substeps)

    up_axis = UsdGeom.GetStageUpAxis(stage)
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    stage_unit_scale = 1.0 / max(meters_per_unit, 1e-6)

    curve_prims = []
    collider_prims = []
    for prim in stage.Traverse():
        if prim.IsInstanceProxy():
            continue
        if prim.IsA(UsdGeom.BasisCurves) and query_matcher(raw_query, str(prim.GetPath())):
            curve_prims.append(prim)
        elif prim.IsA(UsdGeom.Mesh) and collider_query != "__none__":
            if query_matcher(collider_query, str(prim.GetPath())):
                collider_prims.append(prim)

    # Restore any hair prims created interactively (e.g. Draw Lock on a mesh-only scene)
    # that are persisted in groomDeltas with full topology.
    saved_deltas = cfg.get("groomDeltas") if isinstance(cfg.get("groomDeltas"), dict) else {}
    existing_curve_paths = {str(p.GetPath()) for p in curve_prims}
    for d_path, d_val in saved_deltas.items():
        if d_path in existing_curve_paths or not query_matcher(raw_query, d_path):
            continue
        if _decode_topology_override(d_val, np) is not None:
            from pxr import Gf, Sdf, UsdShade
            new_prim = UsdGeom.BasisCurves.Define(stage, d_path).GetPrim()
            UsdGeom.Xformable(new_prim).SetResetXformStack(True)
            bc_new = UsdGeom.BasisCurves(new_prim)
            bc_new.CreateTypeAttr().Set(UsdGeom.Tokens.cubic)
            bc_new.CreateBasisAttr().Set(UsdGeom.Tokens.bspline)
            bc_new.CreateWrapAttr().Set(UsdGeom.Tokens.pinned)
            bc_new.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
            if not UsdShade.MaterialBindingAPI(new_prim).ComputeBoundMaterial()[0]:
                mat = UsdShade.Material.Define(stage, "/Scene/_OrchFlowCreatedHairMat")
                sh = UsdShade.Shader.Define(stage, "/Scene/_OrchFlowCreatedHairMat/Surface")
                sh.CreateIdAttr("UsdPreviewSurface")
                sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.14, 0.08, 0.05))
                sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
                mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
                UsdShade.MaterialBindingAPI.Apply(new_prim).Bind(mat)
            curve_prims.append(new_prim)
            existing_curve_paths.add(d_path)

    if not curve_prims:
        return

    if sim_mode == "animated":
        if not stage.HasAuthoredTimeCodeRange() or stage.GetStartTimeCode() > start_frame:
            stage.SetStartTimeCode(float(start_frame))
        if not stage.HasAuthoredTimeCodeRange() or stage.GetEndTimeCode() < end_frame:
            stage.SetEndTimeCode(float(end_frame))

    collider_paths = [str(p.GetPath()) for p in collider_prims]
    rest_tc = Usd.TimeCode.Default() if sim_mode == "static" else Usd.TimeCode(start_frame)

    for curve_prim in curve_prims:
        curves = UsdGeom.BasisCurves(curve_prim)
        groom_deltas = cfg.get("groomDeltas") if isinstance(cfg.get("groomDeltas"), dict) else {}
        p_str = str(curve_prim.GetPath())
        raw_prim_delta = groom_deltas.get(p_str)
        prim_groom_sig = _groom_signature(raw_prim_delta)
        topo_override = _decode_topology_override(raw_prim_delta, np)
        if topo_override is not None:
            o_counts, o_pts, o_widths = topo_override
            curves.CreateCurveVertexCountsAttr().Set(Vt.IntArray.FromNumpy(o_counts))
            curves.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(o_pts))
            if o_widths is not None:
                curves.CreateWidthsAttr().Set(Vt.FloatArray.FromNumpy(o_widths))
            counts_np = o_counts
            rest_pts = o_pts
        else:
            counts = curves.GetCurveVertexCountsAttr().Get(rest_tc)
            if not counts:
                counts = curves.GetCurveVertexCountsAttr().Get()
            rest_pts = curves.GetPointsAttr().Get(rest_tc)
            if not rest_pts:
                rest_pts = curves.GetPointsAttr().Get()
            if not counts or not rest_pts or len(rest_pts) == 0:
                continue
            counts_np = np.asarray(counts, dtype=np.int32)

        widths_attr = curves.GetWidthsAttr()
        raw_widths = widths_attr.Get(rest_tc) if widths_attr else None
        if raw_widths is None and widths_attr:
            raw_widths = widths_attr.Get()
        widths_np = np.asarray(raw_widths, dtype=np.float32).copy() if raw_widths and len(raw_widths) > 0 else None
        widths_interp = str(curves.GetWidthsInterpolation() or "vertex")

        total_verts = int(counts_np.sum())
        if total_verts != len(rest_pts) or total_verts == 0:
            continue

        xform_start = UsdGeom.XformCache(rest_tc)
        curve_l2w = xform_start.GetLocalToWorldTransform(curve_prim)
        l2w_np = np.asarray(curve_l2w, dtype=np.float64)
        prim_scale = max(float(np.max(np.linalg.norm(l2w_np[:3, :3], axis=1))), 1e-6)
        unit_scale = stage_unit_scale * prim_scale * sim_scale
        if up_axis == UsdGeom.Tokens.z:
            grav_vec = wp.vec3(wind[0] * unit_scale, wind[1] * unit_scale, (wind[2] - gravity) * unit_scale)
        else:
            grav_vec = wp.vec3(wind[0] * unit_scale, (wind[1] - gravity) * unit_scale, wind[2] * unit_scale)
        scaled_margin = margin * unit_scale
        rest_world_np = _points_to_world_np(rest_pts, curve_l2w, np)

        delta_local_np = (
            None
            if topo_override is not None
            else _decode_groom_delta(raw_prim_delta, total_verts, len(counts_np), np)
        )

        cache_key = _compute_cache_key(
            curve_prim.GetPath(), cfg, rest_world_np, counts_np, collider_paths, fps
        )
        cached = None if topo_override is not None else _SIM_CACHE.get(cache_key)
        if cached is None and topo_override is None:
            cached = _load_hair_disk_cache(cache_key)
        if cached is not None:
            _SIM_CACHE.move_to_end(cache_key)
            points_attr = curves.CreatePointsAttr()
            if sim_mode == "static":
                existing_sess = _ACTIVE_SIM_SESSIONS.get(p_str)
                if (
                    existing_sess is not None
                    and int(existing_sess.get("total_verts", -1)) == total_verts
                    and existing_sess.get("cache_key") == cache_key
                    and existing_sess.get("dirty_groom")
                    and existing_sess.get("synced_groom_sig") == prim_groom_sig
                ):
                    live_local_np = _session_local_points(existing_sess, np)
                    points_attr.Set(Vt.Vec3fArray.FromNumpy(live_local_np))
                    continue
                base_local_np = np.asarray(cached[None], dtype=np.float32)
                if delta_local_np is not None:
                    final_local_np = np.ascontiguousarray(base_local_np + delta_local_np, dtype=np.float32)
                    points_attr.Set(Vt.Vec3fArray.FromNumpy(final_local_np))
                else:
                    final_local_np = base_local_np
                    points_attr.Set(cached[None])
                if (
                    existing_sess is not None
                    and int(existing_sess.get("total_verts", -1)) == total_verts
                    and existing_sess.get("cache_key") == cache_key
                ):
                    final_world_np = _points_to_world_np(
                        final_local_np, xform_start.GetLocalToWorldTransform(curve_prim), np
                    )
                    final_world_wp = wp.array(final_world_np, dtype=wp.vec3, device=device)
                    existing_sess["local_points_current"] = False
                    existing_sess["reference_world_wp"] = final_world_wp
                    existing_sess["reference_local_wp"] = wp.array(final_local_np, dtype=wp.vec3, device=device)
                    wp.copy(existing_sess["pos_wp"], final_world_wp)
                    wp.copy(existing_sess["prev_pos_wp"], final_world_wp)
                    wp.copy(existing_sess["anim_rest_wp"], final_world_wp)
                    existing_sess["vel_wp"].zero_()
                    if delta_local_np is not None:
                        s_v0 = existing_sess.get("seg_v0_np")
                        s_v1 = existing_sess.get("seg_v1_np")
                        if s_v0 is not None and s_v1 is not None and len(s_v0) > 0 and existing_sess.get("seg_len_wp") is not None:
                            new_slen = np.linalg.norm(final_world_np[s_v1] - final_world_np[s_v0], axis=1).astype(np.float32)
                            wp.copy(existing_sess["seg_len_wp"], wp.array(new_slen, dtype=float, device=device))
                        b_v0 = existing_sess.get("bend_v0_np")
                        b_v2 = existing_sess.get("bend_v2_np")
                        if b_v0 is not None and b_v2 is not None and len(b_v0) > 0 and existing_sess.get("bend_len_wp") is not None:
                            new_blen = np.linalg.norm(final_world_np[b_v2] - final_world_np[b_v0], axis=1).astype(np.float32)
                            wp.copy(existing_sess["bend_len_wp"], wp.array(new_blen, dtype=float, device=device))
                    existing_sess["base_settled_local_np"] = base_local_np.copy()
                    existing_sess["cache_key"] = cache_key
                    existing_sess["synced_groom_sig"] = prim_groom_sig
                    existing_sess["dirty_groom"] = False
                    if existing_sess.get("sim_guide_cache") is not None:
                        existing_sess["sim_guide_cache"]["sync_from_full"] = True
                    if existing_sess.get("guide_cache") is not None:
                        existing_sess["guide_cache"]["stroke_ready"] = None
                    continue
            else:
                for frame_f, vt_pts in cached.items():
                    if delta_local_np is not None:
                        frame_local_np = np.ascontiguousarray(
                            np.asarray(vt_pts, dtype=np.float32) + delta_local_np,
                            dtype=np.float32,
                        )
                        points_attr.Set(Vt.Vec3fArray.FromNumpy(frame_local_np), Usd.TimeCode(frame_f))
                    else:
                        points_attr.Set(vt_pts, Usd.TimeCode(frame_f))
                continue

        _emit_sim_status(f"Hair GPU sim: preparing {len(counts_np):,} strands...")

        # Vectorized strand topology construction
        valid_strands = counts_np > 0
        ends = np.cumsum(counts_np)
        starts = ends - counts_np

        inv_mass_np = np.ones(total_verts, dtype=np.float32)
        inv_mass_np[starts[valid_strands]] = 0.0

        # Per-vertex normalized curve parameter t in [0, 1]
        vert_indices = np.arange(total_verts, dtype=np.int32)
        strand_ids = np.repeat(np.arange(len(counts_np), dtype=np.int32), counts_np)
        local_idx = vert_indices - starts[strand_ids]
        denom = np.maximum(1, counts_np[strand_ids] - 1).astype(np.float32)
        root_t_np = (local_idx.astype(np.float32) / denom).astype(np.float32)

        is_seg = local_idx + 1 < counts_np[strand_ids]
        seg_v0_np = vert_indices[is_seg]
        seg_v1_np = seg_v0_np + 1
        seg_len_np = np.linalg.norm(rest_world_np[seg_v1_np] - rest_world_np[seg_v0_np], axis=1).astype(np.float32)

        vert_seg_len = np.zeros(total_verts, dtype=np.float64)
        if len(seg_v1_np) > 0:
            vert_seg_len[seg_v1_np] = seg_len_np
        global_cum = np.cumsum(vert_seg_len, dtype=np.float64)
        cum_len_np = (global_cum - global_cum[starts[strand_ids]]).astype(np.float32)

        is_bend = local_idx + 2 < counts_np[strand_ids]
        bend_v0_np = vert_indices[is_bend]
        bend_v2_np = bend_v0_np + 2
        bend_len_np = np.linalg.norm(rest_world_np[bend_v2_np] - rest_world_np[bend_v0_np], axis=1).astype(np.float32)

        pos_wp = wp.array(rest_world_np, dtype=wp.vec3, device=device)
        prev_pos_wp = wp.array(rest_world_np, dtype=wp.vec3, device=device)
        anim_rest_wp = wp.array(rest_world_np, dtype=wp.vec3, device=device)
        vel_wp = wp.zeros(total_verts, dtype=wp.vec3, device=device)
        inv_mass_wp = wp.array(inv_mass_np, dtype=float, device=device)
        root_t_wp = wp.array(root_t_np, dtype=float, device=device)
        delta_pos_wp = wp.zeros(total_verts, dtype=wp.vec3, device=device)
        delta_w_wp = wp.zeros(total_verts, dtype=float, device=device)

        n_segs = len(seg_v0_np)
        seg_v0_wp = wp.array(seg_v0_np, dtype=int, device=device) if n_segs > 0 else None
        seg_v1_wp = wp.array(seg_v1_np, dtype=int, device=device) if n_segs > 0 else None
        seg_len_wp = wp.array(seg_len_np, dtype=float, device=device) if n_segs > 0 else None

        n_bends = len(bend_v0_np)
        bend_v0_wp = wp.array(bend_v0_np, dtype=int, device=device) if n_bends > 0 else None
        bend_v2_wp = wp.array(bend_v2_np, dtype=int, device=device) if n_bends > 0 else None
        bend_len_wp = wp.array(bend_len_np, dtype=float, device=device) if n_bends > 0 else None

        # Build merged collider BVH once, refit only if animated
        max_strand_len = float(cum_len_np.max()) if len(cum_len_np) > 0 else 0.0
        max_query_dist = max(1.0 * unit_scale, scaled_margin * 20.0, max_strand_len * 2.0)
        hair_pad = max_query_dist + float(np.max(seg_len_np)) * 2.0 if n_segs > 0 else max_query_dist
        hair_min = rest_world_np.min(axis=0) - hair_pad
        hair_max = rest_world_np.max(axis=0) + hair_pad

        skel_ctx = _build_skel_context(stage)
        wp_collider = None
        collider_animated = False
        if collider_prims:
            c_pts, c_tris, collider_animated = _build_merged_collider_mesh(
                collider_prims, rest_tc, xform_start, hair_min, hair_max, np, skel_ctx=skel_ctx
            )
            if c_pts is not None and c_tris is not None:
                wp_collider = wp.Mesh(
                    points=wp.array(c_pts, dtype=wp.vec3, device=device),
                    indices=wp.array(c_tris, dtype=int, device=device),
                )

        curve_xform_animated = _transform_might_be_time_varying(curve_prim)
        curve_animated = bool(
            curves.GetPointsAttr().ValueMightBeTimeVarying() or curve_xform_animated
        )
        points_attr = curves.CreatePointsAttr()
        frame_results: dict[float | None, object] = {}
        damping_sub = 1.0 - (1.0 - damping) ** (1.0 / float(substeps))
        vol_table_size = 65536
        vol_table_mask = vol_table_size - 1
        vol_cell_size = max(scaled_margin * 2.5, 0.012 * unit_scale)
        vol_inv_cell = 1.0 / max(vol_cell_size, 1e-5)
        vol_grid_pos_wp = wp.zeros(vol_table_size, dtype=wp.vec3, device=device)
        vol_grid_count_wp = wp.zeros(vol_table_size, dtype=int, device=device)

        sim_state = {
            "total_verts": total_verts,
            "vert_starts_wp": wp.array(starts, dtype=int, device=device),
            "pos_wp": pos_wp,
            "prev_pos_wp": prev_pos_wp,
            "vel_wp": vel_wp,
            "inv_mass_wp": inv_mass_wp,
            "anim_rest_wp": anim_rest_wp,
            "root_t_wp": root_t_wp,
            "delta_pos_wp": delta_pos_wp,
            "delta_w_wp": delta_w_wp,
            "n_segs": n_segs,
            "seg_v0_wp": seg_v0_wp,
            "seg_v1_wp": seg_v1_wp,
            "seg_len_wp": seg_len_wp,
            "n_bends": n_bends,
            "bend_v0_wp": bend_v0_wp,
            "bend_v2_wp": bend_v2_wp,
            "bend_len_wp": bend_len_wp,
        }

        def _step_frame(
            pre_constraints_fn=None, inv_mass_override=None, use_volume=True,
            max_stretch=None, solve_state=None, dynamic_guides=False,
            self_collision=True, zero_gravity=False, rest_shape_pull=True, overdamped=False,
            bend_scale=1.0, collider_override=None,
        ):
            eff_collider = collider_override if collider_override is not None else wp_collider
            state = sim_state if solve_state is None else solve_state
            s_tv = state["total_verts"]
            if s_tv <= 0:
                return
            s_pos = state["pos_wp"]
            s_prev = state["prev_pos_wp"]
            s_vel = state["vel_wp"]
            s_rest = state["anim_rest_wp"]
            s_rt = state["root_t_wp"]
            s_dp = state["delta_pos_wp"]
            s_dw = state["delta_w_wp"]
            s_nsegs = state["n_segs"]
            s_sv0 = state["seg_v0_wp"]
            s_sv1 = state["seg_v1_wp"]
            s_slen = state["seg_len_wp"]
            s_nbends = state["n_bends"]
            s_bv0 = state["bend_v0_wp"]
            s_bv2 = state["bend_v2_wp"]
            s_blen = state["bend_len_wp"]
            active_im = inv_mass_override if inv_mass_override is not None else state["inv_mass_wp"]
            solve_elastic = state.get("solve_elastic")
            project_guide_contacts = state.get("project_guide_contacts")
            guide_brush = solve_elastic is not None
            # Each guide edge ends at one distinct non-root vertex. The same
            # compact edge list can drive midpoint and point scalp queries.
            scalp_edges = state["active_edges"] if guide_brush else state["vert_starts_wp"]
            scalp_edge_ends = s_sv1 if guide_brush else scalp_edges
            scalp_segment_count = state["active_edge_count"] if guide_brush else s_nsegs
            scalp_point_count = scalp_segment_count if guide_brush else s_tv
            # The guide brush relaxes toward a new pose on each input sample.
            # Retain a small gravity load without carrying inertial velocity.
            step_count = 1 if guide_brush else substeps
            prediction_damping = 1.0 if (overdamped or (guide_brush and not dynamic_guides)) else damping_sub
            step_grav = wp.vec3(0.0, 0.0, 0.0) if zero_gravity else grav_vec
            for _ in range(step_count):
                wp.launch(
                    predict_kernel,
                    dim=s_tv,
                    inputs=[s_pos, s_prev, s_vel, active_im, s_rest, step_grav, prediction_damping, dt_sub],
                    device=device,
                )
                if pre_constraints_fn is not None:
                    pre_constraints_fn()
                if use_volume:
                    vol_grid_pos_wp.zero_()
                    vol_grid_count_wp.zero_()
                    wp.launch(
                        vol_grid_accum_kernel,
                        dim=s_tv,
                        inputs=[s_pos, vol_inv_cell, vol_table_mask, vol_grid_pos_wp, vol_grid_count_wp],
                        device=device,
                    )
                    wp.launch(
                        vol_grid_repel_kernel,
                        dim=s_tv,
                        inputs=[s_pos, active_im, s_rt, vol_inv_cell, vol_cell_size, vol_table_mask, 0.25, vol_grid_pos_wp, vol_grid_count_wp],
                        device=device,
                    )
                eff_bend = bend_stiffness * float(bend_scale)
                if solve_elastic is not None:
                    first_iterations = min(2, max(1, iterations // 2)) if dynamic_guides else max(1, iterations // 2)
                    solve_elastic(stiffness, eff_bend, first_iterations, int(pre_constraints_fn is not None), 0)
                if project_guide_contacts is not None:
                    wp.launch(zero_accum_kernel, dim=s_tv, inputs=[s_dp, s_dw], device=device)
                    guide_margin = min(scaled_margin, float(state.get("max_guide_margin", scaled_margin)))
                    project_guide_contacts(
                        eff_collider.id if eff_collider is not None else 0,
                        guide_margin,
                        max_query_dist,
                        self_collision=self_collision,
                    )
                    second_iterations = min(2, max(1, iterations - first_iterations)) if dynamic_guides else max(1, iterations - first_iterations)
                    solve_elastic(stiffness, eff_bend, second_iterations, 0, 1)
                for iter_idx in range(iterations if solve_elastic is None else 0):
                    wp.launch(zero_accum_kernel, dim=s_tv, inputs=[s_dp, s_dw], device=device)
                    if s_nsegs > 0:
                        wp.launch(
                            stretch_constraint_kernel,
                            dim=s_nsegs,
                            inputs=[s_pos, active_im, s_sv0, s_sv1, s_slen, stiffness, s_dp, s_dw],
                            device=device,
                        )
                        if rest_shape_pull and pre_constraints_fn is None and eff_bend > 1e-6:
                            wp.launch(
                                shape_bend_constraint_kernel,
                                dim=s_nsegs,
                                inputs=[s_pos, active_im, s_rest, s_sv0, s_sv1, eff_bend * 0.25, s_dp, s_dw],
                                device=device,
                            )
                    if s_nbends > 0:
                        wp.launch(
                            bend_constraint_kernel,
                            dim=s_nbends,
                            inputs=[s_pos, active_im, s_bv0, s_bv2, s_blen, eff_bend, s_dp, s_dw],
                            device=device,
                        )
                    wp.launch(
                        apply_accum_kernel,
                        dim=s_tv,
                        inputs=[s_pos, active_im, s_dp, s_dw],
                        device=device,
                    )
                    if eff_collider is not None and (iter_idx % 2 == 1 or iter_idx + 1 == iterations):
                        wp.launch(
                            collide_mesh_kernel,
                            dim=s_tv,
                            inputs=[
                                s_pos, s_prev, active_im, eff_collider.id, scaled_margin, friction, max_query_dist, 0,
                                scalp_edges, scalp_edge_ends, 0,
                            ],
                            device=device,
                        )
                if max_stretch is not None and state.get("cum_len_wp") is not None:
                    wp.launch(
                        enforce_strand_length_kernel,
                        dim=int(state.get("num_strands", len(counts_np))),
                        inputs=[s_pos, active_im, state["vert_starts_wp"], state.get("counts_wp", state.get("guide_counts")), state["cum_len_wp"], max_stretch],
                        device=device,
                    )
                if eff_collider is not None and not guide_brush:
                    if scalp_segment_count > 0:
                        wp.launch(zero_accum_kernel, dim=s_tv, inputs=[s_dp, s_dw], device=device)
                        wp.launch(
                            collide_segments_kernel,
                            dim=scalp_segment_count,
                            inputs=[
                                s_pos, active_im, s_sv0, s_sv1, eff_collider.id, scaled_margin, max_query_dist,
                                s_dp, s_dw, scalp_edges, int(guide_brush),
                            ],
                            device=device,
                        )
                        wp.launch(
                            apply_accum_kernel,
                            dim=s_tv,
                            inputs=[s_pos, active_im, s_dp, s_dw],
                            device=device,
                        )
                    wp.launch(
                        collide_mesh_kernel,
                        dim=scalp_point_count,
                        inputs=[
                            s_pos, s_prev, active_im, eff_collider.id, scaled_margin, friction, max_query_dist, 1,
                            scalp_edges, scalp_edge_ends, int(guide_brush),
                        ],
                        device=device,
                    )
                if not guide_brush or dynamic_guides:
                    wp.launch(
                        update_vel_kernel,
                        dim=s_tv,
                        inputs=[s_pos, s_prev, s_vel, active_im, 1.0 / max(dt_sub, 1e-6)],
                        device=device,
                    )

        strand_ids_wp = wp.array(strand_ids, dtype=int, device=device)
        cum_len_wp = wp.array(cum_len_np, dtype=float, device=device)
        if sim_mode == "static":
            sim_guides = None
            local_to_world = xform_start.GetLocalToWorldTransform(curve_prim)
            world_to_local = local_to_world.GetInverse()
            if topo_override is not None:
                vt_local = Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(rest_pts, dtype=np.float32))
                base_settled_local_np = np.asarray(rest_pts, dtype=np.float32).copy()
            elif cached is not None:
                vt_local = cached[None]
                base_settled_local_np = np.asarray(vt_local, dtype=np.float32).copy()
            else:
                use_admm_guides = len(counts_np) >= 1024 and int(counts_np.min()) >= 2
                sim_guides = None
                if use_admm_guides:
                    from hair_guides import init_sim_guide_cache, step_sim_guides
                    sim_guides = init_sim_guide_cache(
                        wp, device, rest_world_np, counts_np, widths_np, unit_scale,
                        local_to_world, friction, bend_stiffness,
                        strand_ids_wp,
                        inv_mass_wp,
                        cum_len_wp,
                        pos_wp,
                    )
                    sim_starts_wp = sim_state["vert_starts_wp"]
                for step_idx in range(settle_steps):
                    if step_idx == 0 or (step_idx + 1) % 8 == 0 or step_idx + 1 == settle_steps:
                        _emit_sim_status(f"Hair GPU style: step {step_idx + 1}/{settle_steps}")
                    if sim_guides is not None:
                        step_sim_guides(wp, device, sim_guides, _step_frame, strand_ids_wp, sim_starts_wp, pos_wp)
                    else:
                        _step_frame()
                sim_world_np = pos_wp.numpy()
                vt_local = _world_np_to_local_vt(sim_world_np, world_to_local, Vt, np)
                base_settled_local_np = np.asarray(vt_local, dtype=np.float32).copy()
            if topo_override is None:
                frame_results[None] = vt_local
            if delta_local_np is not None or cached is not None or topo_override is not None:
                if delta_local_np is not None:
                    final_local_np = np.ascontiguousarray(base_settled_local_np + delta_local_np, dtype=np.float32)
                    points_attr.Set(Vt.Vec3fArray.FromNumpy(final_local_np))
                else:
                    final_local_np = base_settled_local_np
                final_world_np = _points_to_world_np(final_local_np, local_to_world, np)
                final_world_wp = wp.array(final_world_np, dtype=wp.vec3, device=device)
                wp.copy(pos_wp, final_world_wp)
                wp.copy(prev_pos_wp, final_world_wp)
                wp.copy(anim_rest_wp, final_world_wp)
                if delta_local_np is not None:
                    if n_segs > 0 and seg_len_wp is not None:
                        new_slen = np.linalg.norm(final_world_np[seg_v1_np] - final_world_np[seg_v0_np], axis=1).astype(np.float32)
                        wp.copy(seg_len_wp, wp.array(new_slen, dtype=float, device=device))
                    if n_bends > 0 and bend_len_wp is not None:
                        new_blen = np.linalg.norm(final_world_np[bend_v2_np] - final_world_np[bend_v0_np], axis=1).astype(np.float32)
                        wp.copy(bend_len_wp, wp.array(new_blen, dtype=float, device=device))
            else:
                points_attr.Set(vt_local)
                wp.copy(anim_rest_wp, pos_wp)
                final_local_np = base_settled_local_np
            reference_world_wp = wp.empty_like(pos_wp)
            wp.copy(reference_world_wp, pos_wp)
            sim_state.update({
                "base_settled_local_np": base_settled_local_np,
                "reference_world_wp": reference_world_wp,
                "reference_local_wp": wp.array(final_local_np, dtype=wp.vec3, device=device),
                "local_wp": wp.zeros(total_verts, dtype=wp.vec3, device=device),
                "num_strands": len(counts_np),
                "counts_np": counts_np,
                "starts_np": starts,
                "seg_v0_np": seg_v0_np,
                "seg_v1_np": seg_v1_np,
                "bend_v0_np": bend_v0_np,
                "bend_v2_np": bend_v2_np,
                "strand_ids_wp": strand_ids_wp,
                "local_idx_wp": wp.array(local_idx, dtype=int, device=device),
                "counts_wp": wp.array(counts_np, dtype=int, device=device),
                "cum_len_wp": cum_len_wp,
                "strand_hero_k_wp": wp.full(len(counts_np), -1, dtype=int, device=device),
                "strand_hero_offset_wp": wp.zeros(len(counts_np), dtype=wp.vec3, device=device),
                "strand_surface_target_wp": wp.zeros(len(counts_np), dtype=wp.vec3, device=device),
                "strand_brush_w_wp": wp.zeros(len(counts_np), dtype=float, device=device),
                "active_strand_wp": wp.zeros(len(counts_np), dtype=int, device=device),
                "pinned_strand_wp": wp.zeros(len(counts_np), dtype=int, device=device),
                "brush_inv_mass_wp": wp.zeros(total_verts, dtype=float, device=device),
                "undo_stack": [],
                "sim_guide_cache": sim_guides,
                "grab_ready": False,
                "grab_center": None,
                "prev_cursor_target": None,
                "last_add_center": None,
                "world_to_local": world_to_local,
                "local_to_world": local_to_world,
                "unit_scale": unit_scale,
                "dt_sub": dt_sub,
                "substeps": substeps,
                "iterations": iterations,
                "stiffness": stiffness,
                "bend_stiffness": bend_stiffness,
                "grav_vec": grav_vec,
                "damping_sub": damping_sub,
                "scaled_margin": scaled_margin,
                "friction": friction,
                "max_query_dist": max_query_dist,
                "step_fn": _step_frame,
                "wp_collider": wp_collider,
                "guide_cache": sim_guides,
                "cache_key": cache_key,
                "synced_groom_sig": prim_groom_sig,
                "dirty_groom": False,
                "topology_dirty": topo_override is not None,
                "widths_np": widths_np,
                "widths_interp": widths_interp,
            })
            _ACTIVE_SIM_SESSIONS[p_str] = sim_state
        else:
            use_admm_guides = len(counts_np) >= 1024 and int(counts_np.min()) >= 2
            sim_guides = None
            sim_sids_wp = None
            sim_starts_wp = sim_state["vert_starts_wp"]
            if use_admm_guides:
                from hair_guides import init_sim_guide_cache, step_sim_guides
                sim_sids_wp = wp.array(strand_ids, dtype=int, device=device)
                sim_guides = init_sim_guide_cache(
                    wp, device, rest_world_np, counts_np, widths_np, unit_scale,
                    xform_start.GetLocalToWorldTransform(curve_prim), friction, bend_stiffness,
                    sim_sids_wp, inv_mass_wp, wp.array(cum_len_np, dtype=float, device=device), pos_wp,
                )
            total_frames = end_frame - start_frame + 1
            for idx_f, frame in enumerate(range(start_frame, end_frame + 1)):
                if idx_f == 0 or (idx_f + 1) % 8 == 0 or frame == end_frame:
                    _emit_sim_status(f"Hair GPU sim: frame {frame}/{end_frame} ({idx_f + 1}/{total_frames})")

                tc = Usd.TimeCode(float(frame))
                xform_cache = UsdGeom.XformCache(tc)
                local_to_world = xform_cache.GetLocalToWorldTransform(curve_prim)
                world_to_local = local_to_world.GetInverse()

                if frame > start_frame:
                    if curve_xform_animated:
                        local_to_world = xform_cache.GetLocalToWorldTransform(curve_prim)
                        world_to_local = local_to_world.GetInverse()
                    if curve_animated:
                        frame_pts = curves.GetPointsAttr().Get(tc)
                        if not frame_pts:
                            frame_pts = rest_pts
                        if frame_pts and len(frame_pts) == total_verts:
                            frame_world_np = _points_to_world_np(frame_pts, local_to_world, np)
                            wp.copy(anim_rest_wp, wp.array(frame_world_np, dtype=wp.vec3, device=device))

                    if wp_collider is not None and collider_animated:
                        c_pts, _, _ = _build_merged_collider_mesh(
                            collider_prims, tc, xform_cache, hair_min, hair_max, np, skel_ctx=skel_ctx
                        )
                        if c_pts is not None and len(c_pts) == len(wp_collider.points):
                            wp.copy(wp_collider.points, wp.array(c_pts, dtype=wp.vec3, device=device))
                            wp_collider.refit()

                    if sim_guides is not None:
                        step_sim_guides(wp, device, sim_guides, _step_frame, sim_sids_wp, sim_starts_wp, pos_wp)
                    else:
                        _step_frame()

                sim_world_np = pos_wp.numpy()
                vt_local = _world_np_to_local_vt(sim_world_np, world_to_local, Vt, np)
                if delta_local_np is not None:
                    frame_local_np = np.ascontiguousarray(
                        np.asarray(vt_local, dtype=np.float32) + delta_local_np,
                        dtype=np.float32,
                    )
                    points_attr.Set(Vt.Vec3fArray.FromNumpy(frame_local_np), tc)
                else:
                    points_attr.Set(vt_local, tc)
                frame_results[float(frame)] = vt_local

        _SIM_CACHE[cache_key] = frame_results
        while len(_SIM_CACHE) > _MAX_CACHE_ENTRIES:
            _SIM_CACHE.popitem(last=False)
        _save_hair_disk_cache(cache_key, frame_results)
        _save_hair_disk_cache(cache_key, frame_results)




def step_hair_brush(
    stage, hair_cfg, query_matcher, brush, cam_basis, write_usd=True, return_gpu=False,
    diagnostics=None, uploaded_points=None, unchanged_uploads=None,
):
    """Apply an interactive 3D hair grooming brush (comb/grab/smooth/part/puff) with full GPU XPBD."""
    from pxr import Gf, Usd, UsdGeom, Vt

    wp, np, device = _ensure_warp()
    kernels = _build_kernels(wp)
    (
        _predict_kernel,
        _zero_accum_kernel,
        _stretch_constraint_kernel,
        _bend_constraint_kernel,
        _apply_accum_kernel,
        _collide_mesh_kernel,
        _update_vel_kernel,
        brush_min_depth_kernel,
        init_grab_clump_kernel,
        grab_clump_kernel,
        strand_ray_dist_kernel,
        comb_sweep_kernel,
        smooth_brush_kernel,
        part_brush_kernel,
        puff_brush_kernel,
        clump_brush_kernel,
        mark_active_brush_strands_kernel,
        sync_active_inv_mass_kernel,
        world_to_local_kernel,
        _collide_segments_kernel,
        pin_brush_kernel,
        _shape_bend_constraint_kernel,
        _vol_grid_accum_kernel,
        _vol_grid_repel_kernel,
        surface_comb_kernel,
        _snap_root_to_mesh_kernel,
        ray_mesh_hit_kernel,
        _enforce_strand_length_kernel,
        mask_backfacing_roots_kernel,
        measure_stroke_lengths,
        grab_target_kernel,
    ) = kernels

    cfg = hair_cfg if isinstance(hair_cfg, dict) else {}
    raw_query = str(cfg.get("targetQuery") or "").strip()
    settle_only = bool(brush.get("settleOnly", False))
    tool = str(brush.get("tool") or "comb").lower()

    # Ensure active GPU sessions exist for matching BasisCurves
    matching_paths = _brush_curve_paths(stage, raw_query, query_matcher)
    if (
        not matching_paths
        and not settle_only
        and tool == "drawlock"
        and isinstance(brush.get("drawCurve"), (list, tuple))
        and len(brush["drawCurve"]) >= 2
    ):
        # Bootstrap a new BasisCurves prim on a mesh-only scene when dragging over a 3D object
        from hair_topology_brushes import _build_scene_draw_mesh
        wp_coll = _build_scene_draw_mesh(stage, wp, np, device, Usd, UsdGeom)
        if wp_coll is None:
            return []
        meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
        stage_unit_scale = 1.0 / max(meters_per_unit, 1e-6)
        sim_scale = max(float(cfg.get("simScale", 1.0)), 1e-4)
        unit_scale = stage_unit_scale * sim_scale
        new_path = "/Scene/CreatedHair"
        if not stage.GetPrimAtPath("/Scene").IsValid():
            UsdGeom.Xform.Define(stage, "/Scene")
        UsdGeom.BasisCurves.Define(stage, new_path)
        scaled_margin = float(cfg.get("collisionMargin", 0.004)) * unit_scale
        boot_sess = {
            "_bootstrap_only": True,
            "num_strands": 0,
            "total_verts": 0,
            "counts_np": np.zeros(0, dtype=np.int32),
            "wp_collider": wp_coll,
            "unit_scale": unit_scale,
            "scaled_margin": scaled_margin,
            "max_query_dist": max(0.5 * unit_scale, scaled_margin * 20.0),
        }
        radius = max(float(brush.get("radius", 0.06)), 0.005)
        strength = max(0.05, min(2.0, float(brush.get("strength", 0.65))))
        tip_pct = float(brush.get("drawTipPct", 25.0))
        density = brush.get("drawDensity")
        created = _apply_draw_lock_brush(
            stage, new_path, boot_sess, brush["drawCurve"], radius * unit_scale, strength,
            cam_basis, wp, np, device, Vt, UsdGeom, tip_pct=tip_pct, density=density,
        )
        if not created:
            stage.RemovePrim(new_path)
            return []
        global _BRUSH_CURVE_INDEX
        if _BRUSH_CURVE_INDEX is not None:
            _BRUSH_CURVE_INDEX.paths = None
        apply_hair_simulation(stage, {**cfg, "settleSteps": 0, "gravity": 0.0}, query_matcher)
        sess_new = _ACTIVE_SIM_SESSIONS.get(new_path)
        if sess_new is None:
            return []
        sess_new["topology_dirty"] = True
        sess_new["needs_stage_repopulate"] = True
        sess_new["dirty_groom"] = True
        sess_new["_brush_scene_collider"] = wp_coll
        sess_new["_brush_scene_collider_checked"] = True
        local_np = _session_local_points(sess_new, np)
        return [(new_path, local_np)]

    if any(path not in _ACTIVE_SIM_SESSIONS for path in matching_paths):
        apply_hair_simulation(stage, cfg, query_matcher)

    guarded_shape_tool = tool in ("comb", "surfacecomb", "smooth", "clump")
    # This release contains no pending pointer sample. Save the shape already shown.
    commit_shape = settle_only and bool(brush.get("commitUsd")) and guarded_shape_tool
    nx = float(brush.get("nx", 0.5))
    ny = float(brush.get("ny", 0.5))
    prev_nx = float(brush.get("prevNx", nx))
    prev_ny = float(brush.get("prevNy", ny))
    ndc_dx = nx - prev_nx
    ndc_dy = ny - prev_ny

    tan_half_fov = float(cam_basis.get("tan_half_fov", 0.4142))
    aspect = float(cam_basis.get("aspect", 1.3333))
    eye = cam_basis["origin"]
    fwd = cam_basis["forward"]
    right = cam_basis["right"]
    up = cam_basis["up"]

    # Ray through current cursor (nx, ny in [0, 1] top-left origin) and previous cursor for stroke start
    sx = (nx * 2.0 - 1.0) * tan_half_fov * aspect
    sy = (1.0 - ny * 2.0) * tan_half_fov
    rx = fwd[0] + right[0] * sx + up[0] * sy
    ry = fwd[1] + right[1] * sx + up[1] * sy
    rz = fwd[2] + right[2] * sx + up[2] * sy
    rlen = max((rx * rx + ry * ry + rz * rz) ** 0.5, 1e-8)

    psx = (prev_nx * 2.0 - 1.0) * tan_half_fov * aspect
    psy = (1.0 - prev_ny * 2.0) * tan_half_fov
    prx = fwd[0] + right[0] * psx + up[0] * psy
    pry = fwd[1] + right[1] * psx + up[1] * psy
    prz = fwd[2] + right[2] * psx + up[2] * psy
    prlen = max((prx * prx + pry * pry + prz * prz) ** 0.5, 1e-8)

    base_eye = eye
    base_fwd = fwd
    base_right = right
    base_up = up
    base_cam_basis = cam_basis
    base_ray_origin = wp.vec3(float(eye[0]), float(eye[1]), float(eye[2]))
    base_ray_dir = wp.vec3(rx / rlen, ry / rlen, rz / rlen)
    base_prev_ray_dir = wp.vec3(prx / prlen, pry / prlen, prz / prlen)

    radius_m = max(0.01, float(brush.get("radius", 0.15)))
    strength = max(0.0, min(5.0, float(brush.get("strength", 1.0))))
    min_depth_wp = _SCRATCH_BUFFERS.get("min_depth_wp")
    if min_depth_wp is None:
        min_depth_wp = wp.full(1, 1e9, dtype=float, device=device)
        _SCRATCH_BUFFERS["min_depth_wp"] = min_depth_wp
    else:
        min_depth_wp.fill_(1e9)

    updates = []
    has_gpu_updates = False
    guide_timing_events = [] if diagnostics is not None else None
    # Keep host snapshots alive until their queued copies have completed.
    undo_uploads = []
    xform_brush = UsdGeom.XformCache()
    for p_str in matching_paths:
        sess = _ACTIVE_SIM_SESSIONS.get(p_str)
        if sess is None:
            continue
        if commit_shape and not brush.get("undoStroke") and not sess.get("dirty_groom"):
            continue
        reuse_local = (
            commit_shape and not brush.get("undoStroke")
            and sess.get("local_points_current", False)
        )
        if not reuse_local:
            sess["local_points_current"] = False
        pos_wp = sess["pos_wp"]
        vel_wp = sess["vel_wp"]
        inv_mass_wp = sess["inv_mass_wp"]
        total_verts = sess["total_verts"]
        unit_scale = sess["unit_scale"]
        dt_sub = sess["dt_sub"]
        world_to_local = sess["world_to_local"]
        scaled_radius = radius_m * unit_scale

        eye = base_eye
        fwd = base_fwd
        right = base_right
        up = base_up
        ray_origin = base_ray_origin
        ray_dir = base_ray_dir
        prev_ray_dir = base_prev_ray_dir
        cam_basis = base_cam_basis
        pinned_prim = stage.GetPrimAtPath(p_str)
        hair_l2w = sess.get("local_to_world")
        if pinned_prim and pinned_prim.IsValid() and hair_l2w is not None:
            pinned_l2w = xform_brush.GetLocalToWorldTransform(pinned_prim)
            if pinned_l2w != hair_l2w:
                m_p2h = pinned_l2w.GetInverse() * hair_l2w
                pe = m_p2h.Transform(Gf.Vec3d(float(base_eye[0]), float(base_eye[1]), float(base_eye[2])))
                pf = m_p2h.TransformDir(Gf.Vec3d(float(base_fwd[0]), float(base_fwd[1]), float(base_fwd[2]))).GetNormalized()
                pr = m_p2h.TransformDir(Gf.Vec3d(float(base_right[0]), float(base_right[1]), float(base_right[2]))).GetNormalized()
                pu = m_p2h.TransformDir(Gf.Vec3d(float(base_up[0]), float(base_up[1]), float(base_up[2]))).GetNormalized()
                prd = m_p2h.TransformDir(Gf.Vec3d(float(base_ray_dir[0]), float(base_ray_dir[1]), float(base_ray_dir[2]))).GetNormalized()
                pprd = m_p2h.TransformDir(Gf.Vec3d(float(base_prev_ray_dir[0]), float(base_prev_ray_dir[1]), float(base_prev_ray_dir[2]))).GetNormalized()
                eye = (float(pe[0]), float(pe[1]), float(pe[2]))
                fwd = (float(pf[0]), float(pf[1]), float(pf[2]))
                right = (float(pr[0]), float(pr[1]), float(pr[2]))
                up = (float(pu[0]), float(pu[1]), float(pu[2]))
                ray_origin = wp.vec3(eye[0], eye[1], eye[2])
                ray_dir = wp.vec3(float(prd[0]), float(prd[1]), float(prd[2]))
                prev_ray_dir = wp.vec3(float(pprd[0]), float(pprd[1]), float(pprd[2]))
                cam_basis = {
                    "origin": eye,
                    "forward": fwd,
                    "right": right,
                    "up": up,
                    "tan_half_fov": tan_half_fov,
                    "aspect": aspect,
                }

        def _resolve_brush_collider():
            coll = sess.get("wp_collider") or sess.get("_brush_scene_collider")
            if coll is None and not sess.get("_brush_scene_collider_checked"):
                from hair_topology_brushes import _build_scene_draw_mesh
                coll = _build_scene_draw_mesh(stage, wp, np, device, Usd, UsdGeom)
                sess["_brush_scene_collider_checked"] = True
                if coll is not None:
                    sess["_brush_scene_collider"] = coll
            return coll

        def _snapshot_pre_coll():
            if total_verts <= 0:
                return None
            buf = sess.get("brush_pre_coll_wp")
            if buf is None or buf.shape != pos_wp.shape:
                buf = wp.empty_like(pos_wp)
                sess["brush_pre_coll_wp"] = buf
            wp.copy(buf, pos_wp)
            return buf

        def _enforce_brush_collider(pre_buf, active_im, coll):
            if coll is None or total_verts <= 0 or sess["num_strands"] <= 0:
                return
            from hair_draw_kernels import _build_brush_collider_kernel
            enforce_coll_kernel = _build_brush_collider_kernel(wp)
            src_prev = pre_buf if (pre_buf is not None and pre_buf.shape == pos_wp.shape) else pos_wp
            wp.launch(
                enforce_coll_kernel,
                dim=sess["num_strands"],
                inputs=[
                    src_prev,
                    pos_wp,
                    active_im,
                    sess["vert_starts_wp"],
                    sess["counts_wp"],
                    sess["cum_len_wp"],
                    coll.id,
                    sess["scaled_margin"],
                    sess["max_query_dist"],
                ],
                device=device,
            )

        if brush.get("clearPins"):
            sess["pinned_strand_wp"].zero_()
            continue
        if brush.get("undoStroke"):
            undo_stack = sess.get("undo_stack") or []
            if undo_stack:
                prev_pts = undo_stack.pop()
                undo_uploads.append(prev_pts)
                wp.copy(pos_wp, prev_pts)
                wp.copy(sess["prev_pos_wp"], pos_wp)
                wp.copy(sess["anim_rest_wp"], pos_wp)
                sess["vel_wp"].zero_()
                sess["dirty_groom"] = True
            sess["grab_ready"] = False
            sess["grab_center"] = None
            sess["prev_cursor_target"] = None
            sess["strand_hero_k_wp"].fill_(-1)
            sess["strand_brush_w_wp"].zero_()
            sess["active_strand_wp"].zero_()
            sess["brush_inv_mass_wp"].zero_()
        elif brush.get("smoothAll"):
            if total_verts > 0:
                undo_stack = sess.setdefault("undo_stack", [])
                if len(undo_stack) == 15:
                    snapshot = undo_stack.pop(0)
                else:
                    snapshot = wp.empty_like(pos_wp, device="cpu", pinned=pos_wp.device.is_cuda)
                wp.copy(snapshot, pos_wp)
                undo_stack.append(snapshot)
                from hair_draw_kernels import _build_smooth_all_kernel
                smooth_all_kernel = _build_smooth_all_kernel(wp)
                wp_collider = _resolve_brush_collider()
                coll_id = wp_collider.id if wp_collider is not None else 0
                has_coll = 1 if wp_collider is not None else 0
                wp.launch(
                    smooth_all_kernel,
                    dim=sess["num_strands"],
                    inputs=[
                        pos_wp,
                        inv_mass_wp,
                        sess["pinned_strand_wp"],
                        sess["vert_starts_wp"],
                        sess["counts_wp"],
                        sess["cum_len_wp"],
                        coll_id,
                        has_coll,
                        sess["scaled_margin"],
                        sess["max_query_dist"],
                        strength,
                    ],
                    device=device,
                )
                wp.copy(sess["prev_pos_wp"], pos_wp)
                sess["vel_wp"].zero_()
                sess["dirty_groom"] = True
                if sess.get("guide_cache") is not None:
                    sess["guide_cache"]["stroke_ready"] = None
                if sess.get("sim_guide_cache") is not None:
                    sess["sim_guide_cache"]["sync_from_full"] = True
        elif not commit_shape:
            stroke_id = brush.get("strokeId")
            is_new_stroke = bool(brush.get("newStroke")) or (
                stroke_id is not None and stroke_id != sess.get("last_stroke_id")
            )
            if is_new_stroke:
                sess["last_stroke_id"] = stroke_id
                if not brush.get("livePhysics") and sess.get("sim_guide_cache") is not None:
                    sess["sim_guide_cache"]["sync_from_full"] = True
                if sess.get("guide_cache") is not None:
                    sess["guide_cache"]["stroke_ready"] = None
                sess["vel_wp"].zero_()
                sess["grab_ready"] = False
                sess["grab_center"] = None
                sess["prev_cursor_target"] = None
                sess["strand_hero_k_wp"].fill_(-1)
                sess["strand_brush_w_wp"].zero_()
                sess["active_strand_wp"].zero_()
                sess["brush_inv_mass_wp"].zero_()
                wp.copy(sess["anim_rest_wp"], pos_wp)
                if not settle_only:
                    undo_stack = sess.setdefault("undo_stack", [])
                    if len(undo_stack) == 15:
                        snapshot = undo_stack.pop(0)
                    else:
                        snapshot = wp.empty_like(pos_wp, device="cpu", pinned=pos_wp.device.is_cuda)
                    wp.copy(snapshot, pos_wp)
                    undo_stack.append(snapshot)

            num_strands = sess["num_strands"]
            if (guarded_shape_tool or tool in ("grab", "part", "puff", "physics")) and total_verts > 0:
                arc_lengths = sess.get("stroke_arc_lengths_wp")
                if arc_lengths is None or len(arc_lengths) != total_verts:
                    arc_lengths = wp.empty_like(sess["cum_len_wp"])
                    sess["stroke_arc_lengths_wp"] = arc_lengths
                    measure_lengths = True
                else:
                    measure_lengths = is_new_stroke
                if measure_lengths:
                    wp.launch(
                        measure_stroke_lengths, num_strands,
                        inputs=[pos_wp, sess["vert_starts_wp"], sess["counts_wp"], arc_lengths],
                        device=device,
                    )
            direct_sweep_tool = tool in ("lengthen", "shorten", "cut", "add", "delete", "drawlock")
            sweep_ray_dir = ray_dir if direct_sweep_tool else prev_ray_dir
            direct_edit_done = False
            if brush.get("livePhysics"):
                brush_collider = _resolve_brush_collider()
                pre_coll_buf = _snapshot_pre_coll() if brush_collider is not None else None
                sess["active_strand_wp"].fill_(1)
                wp.launch(
                    sync_active_inv_mass_kernel,
                    dim=total_verts,
                    inputs=[inv_mass_wp, sess["strand_ids_wp"], sess["active_strand_wp"], sess["pinned_strand_wp"], sess["brush_inv_mass_wp"]],
                    device=device,
                )
                if len(sess["counts_np"]) >= 1024 and int(sess["counts_np"].min()) >= 2:
                    from hair_guides import init_sim_guide_cache, step_sim_guides
                    sim_guides = sess.get("sim_guide_cache")
                    if sim_guides is None:
                        sim_guides = init_sim_guide_cache(
                            wp, device, pos_wp.numpy(), sess["counts_np"], sess.get("widths_np"),
                            unit_scale, sess["local_to_world"], sess["friction"], sess["bend_stiffness"],
                            sess["strand_ids_wp"], sess["brush_inv_mass_wp"], sess["cum_len_wp"], pos_wp,
                        )
                        sess["sim_guide_cache"] = sim_guides
                    step_sim_guides(
                        wp, device, sim_guides, sess["step_fn"],
                        sess["strand_ids_wp"], sess["vert_starts_wp"], pos_wp,
                        collider_override=brush_collider,
                    )
                    if diagnostics is not None:
                        diagnostics["totalStrands"] += len(sess["counts_np"])
                        diagnostics["guideStrands"] += sim_guides["num_strands"]
                else:
                    sess["step_fn"](
                        None, use_volume=False, inv_mass_override=sess["brush_inv_mass_wp"],
                        max_stretch=1.02, collider_override=brush_collider,
                    )
                    if diagnostics is not None:
                        diagnostics["totalStrands"] += len(sess["counts_np"])
                        diagnostics["fullSolveStrands"] += len(sess["counts_np"])
                _enforce_brush_collider(pre_coll_buf, sess["brush_inv_mass_wp"], brush_collider)
                sess["dirty_groom"] = True
                direct_edit_done = True
            elif not settle_only and (not sess.get("grab_ready") or direct_sweep_tool):
                d_min_val = 1e9
                if total_verts > 0:
                    strand_ids_wp = sess["strand_ids_wp"]
                    backfacing_wp = sess["active_strand_wp"]
                    mask_backfaces = int(tool in ("comb", "surfacecomb"))
                    if mask_backfaces:
                        # Keep root eligibility fixed for the lifetime of this stroke.
                        backfacing_wp = wp.empty(num_strands, dtype=int, device=device)
                        collider = sess["wp_collider"]
                        wp.launch(
                            mask_backfacing_roots_kernel,
                            dim=num_strands,
                            inputs=[
                                pos_wp, sess["vert_starts_wp"], sess["counts_wp"], ray_origin,
                                collider.id if collider is not None else 0, int(collider is not None),
                                sess["max_query_dist"], backfacing_wp,
                            ],
                            device=device,
                        )
                    min_depth_wp.fill_(1e9)
                    wp.launch(
                        brush_min_depth_kernel,
                        dim=total_verts,
                        inputs=[
                            pos_wp, inv_mass_wp, strand_ids_wp, backfacing_wp, mask_backfaces,
                            ray_origin, sweep_ray_dir, scaled_radius, min_depth_wp,
                        ],
                        device=device,
                    )
                    d_min_val = float(min_depth_wp.numpy()[0])
                if d_min_val >= 1e8 and tool in ("add", "delete", "lengthen", "shorten"):
                    wp_coll = sess.get("wp_collider")
                    if wp_coll is not None:
                        ro_arr = wp.array([ray_origin], dtype=wp.vec3, device=device)
                        rd_arr = wp.array([sweep_ray_dir], dtype=wp.vec3, device=device)
                        out_t = wp.full(1, 1e9, dtype=float, device=device)
                        wp.launch(ray_mesh_hit_kernel, dim=1, inputs=[wp_coll.id, ro_arr, rd_arr, float(sess["max_query_dist"]) * 100.0, out_t], device=device)
                        hit_t = float(out_t.numpy()[0])
                        if hit_t < 1e8:
                            d_min_val = hit_t
                    if d_min_val >= 1e8:
                        if total_verts > 0:
                            mean_pt = np.mean(pos_wp.numpy(), axis=0)
                            d_min_val = max(0.1, float(
                                (mean_pt[0] - ray_origin[0]) * sweep_ray_dir[0]
                                + (mean_pt[1] - ray_origin[1]) * sweep_ray_dir[1]
                                + (mean_pt[2] - ray_origin[2]) * sweep_ray_dir[2]
                            ))
                        else:
                            d_min_val = 2.0 * float(sess["unit_scale"])
                if d_min_val < 1e8:
                    hero_pos0_vec = wp.vec3(
                        ray_origin[0] + sweep_ray_dir[0] * d_min_val,
                        ray_origin[1] + sweep_ray_dir[1] * d_min_val,
                        ray_origin[2] + sweep_ray_dir[2] * d_min_val,
                    )
                    sess["grab_center"] = hero_pos0_vec
                    sess["prev_cursor_target"] = hero_pos0_vec
                    sess["grab_ready"] = True
                    if tool in ("grab", "clump", "surfacecomb", "comb"):
                        strand_dist_wp = wp.full(num_strands, 1e9, dtype=float, device=device)
                        wp.launch(
                            strand_ray_dist_kernel,
                            dim=total_verts,
                            inputs=[
                                pos_wp,
                                inv_mass_wp,
                                strand_ids_wp,
                                backfacing_wp,
                                mask_backfaces,
                                ray_origin,
                                prev_ray_dir,
                                scaled_radius,
                                min_depth_wp,
                                strand_dist_wp,
                            ],
                            device=device,
                        )
                        if tool in ("grab", "surfacecomb", "comb"):
                            wp.launch(
                                init_grab_clump_kernel,
                                dim=num_strands,
                                inputs=[
                                    pos_wp,
                                    sess["vert_starts_wp"],
                                    sess["counts_wp"],
                                    strand_dist_wp,
                                    hero_pos0_vec,
                                    scaled_radius,
                                    sess["strand_hero_k_wp"],
                                    sess["strand_hero_offset_wp"],
                                    sess["strand_surface_target_wp"],
                                    sess["strand_brush_w_wp"],
                                    sess["active_strand_wp"],
                                ],
                                device=device,
                            )
                            wp.launch(
                                sync_active_inv_mass_kernel,
                                dim=total_verts,
                                inputs=[inv_mass_wp, sess["strand_ids_wp"], sess["active_strand_wp"], sess["pinned_strand_wp"], sess["brush_inv_mass_wp"]],
                                device=device,
                            )
                        else:
                            sd_np = strand_dist_wp.numpy()
                            clump_s = int(np.argmin(sd_np))
                            sess["clump_s0"] = int(sess["starts_np"][clump_s])
                            sess["clump_ln"] = int(sess["counts_np"][clump_s])

            pre_constraints_fn = None
            pin_only = tool in ("pin", "unpin")
            cut_line = brush.get("cutLine")
            lengthen_drag = brush.get("lengthenDrag")
            draw_curve = brush.get("drawCurve")
            if direct_edit_done:
                pass
            elif not settle_only and tool == "drawlock" and isinstance(draw_curve, (list, tuple)) and len(draw_curve) >= 2:
                tip_pct = float(brush.get("drawTipPct", 25.0))
                density = brush.get("drawDensity")
                if _apply_draw_lock_brush(
                    stage, p_str, sess, draw_curve, scaled_radius, strength, cam_basis, wp, np, device, Vt, UsdGeom,
                    tip_pct=tip_pct, density=density,
                ):
                    sess["dirty_groom"] = True
                    total_verts = int(sess["total_verts"])
                    pos_wp = sess.get("pos_wp")
                direct_edit_done = True
            elif not settle_only and tool == "cut" and isinstance(cut_line, (list, tuple)) and len(cut_line) == 4:
                _apply_cut_line_brush(
                    stage, p_str, sess, cut_line, cam_basis, wp, np, device, Vt, UsdGeom,
                )
                total_verts = int(sess["total_verts"])
                pos_wp = sess.get("pos_wp")
                direct_edit_done = True
            elif not settle_only and tool == "lengthen" and isinstance(lengthen_drag, (list, tuple)) and len(lengthen_drag) == 4:
                brush_collider = _resolve_brush_collider()
                pre_coll_buf = _snapshot_pre_coll() if brush_collider is not None else None
                if _apply_lengthen_drag_brush(sess, lengthen_drag, scaled_radius, cam_basis, wp, np, device):
                    _enforce_brush_collider(pre_coll_buf, inv_mass_wp, brush_collider)
                    wp.copy(sess["prev_pos_wp"], pos_wp)
                    sess["dirty_groom"] = True
                direct_edit_done = True
            elif not settle_only and sess.get("grab_ready") and sess.get("grab_center") is not None:
                gc_center = sess["grab_center"]
                fwd_dot = max(ray_dir[0] * fwd[0] + ray_dir[1] * fwd[1] + ray_dir[2] * fwd[2], 1e-4)
                t_plane = (
                    (gc_center[0] - ray_origin[0]) * fwd[0]
                    + (gc_center[1] - ray_origin[1]) * fwd[1]
                    + (gc_center[2] - ray_origin[2]) * fwd[2]
                ) / fwd_dot
                cursor_target = wp.vec3(
                    ray_origin[0] + ray_dir[0] * t_plane,
                    ray_origin[1] + ray_dir[1] * t_plane,
                    ray_origin[2] + ray_dir[2] * t_plane,
                )
                prev_ct = sess.get("prev_cursor_target") or cursor_target
                drag_delta = wp.vec3(
                    cursor_target[0] - prev_ct[0],
                    cursor_target[1] - prev_ct[1],
                    cursor_target[2] - prev_ct[2],
                )
                sess["prev_cursor_target"] = cursor_target
                inv_dt = 1.0 / max(dt_sub, 1e-6)
                wp_collider = _resolve_brush_collider()
                coll_id = wp_collider.id if wp_collider is not None else 0
                has_coll = 1 if wp_collider is not None else 0

                if tool in ("smooth", "physics", "clump", "part", "puff", "pin", "unpin", "lengthen", "shorten", "cut", "add", "delete"):
                    if total_verts > 0:
                        min_depth_wp.fill_(1e9)
                        wp.launch(
                            brush_min_depth_kernel,
                            dim=total_verts,
                            inputs=[
                                pos_wp, inv_mass_wp, sess["strand_ids_wp"], sess["active_strand_wp"], 0,
                                ray_origin, ray_dir, scaled_radius, min_depth_wp,
                            ],
                            device=device,
                        )
                        d_cur = float(min_depth_wp.numpy()[0])
                    else:
                        d_cur = 1e9
                    if d_cur < 1e8:
                        brush_center = wp.vec3(
                            ray_origin[0] + ray_dir[0] * d_cur,
                            ray_origin[1] + ray_dir[1] * d_cur,
                            ray_origin[2] + ray_dir[2] * d_cur,
                        )
                    else:
                        brush_center = cursor_target
                    if tool in ("lengthen", "shorten", "cut"):
                        pre_coll_buf = _snapshot_pre_coll() if (wp_collider is not None and tool in ("lengthen", "shorten")) else None
                        if _apply_length_or_cut_brush(sess, tool, brush_center, scaled_radius, strength, wp, np, device, ray_origin=ray_origin, ray_dir=ray_dir):
                            if tool in ("lengthen", "shorten"):
                                _enforce_brush_collider(pre_coll_buf, inv_mass_wp, wp_collider)
                                wp.copy(sess["prev_pos_wp"], pos_wp)
                            sess["dirty_groom"] = True
                        direct_edit_done = True
                    elif tool in ("add", "delete"):
                        _apply_add_or_delete_brush(
                            stage, p_str, sess, tool, brush_center, scaled_radius, strength,
                            ray_origin, ray_dir, is_new_stroke, wp, np, device, Vt, UsdGeom,
                        )
                        total_verts = int(sess["total_verts"])
                        pos_wp = sess.get("pos_wp")
                        if tool == "add" and wp_collider is not None and total_verts > 0:
                            _enforce_brush_collider(None, sess["inv_mass_wp"], wp_collider)
                            wp.copy(sess["prev_pos_wp"], pos_wp)
                        direct_edit_done = True
                    elif pin_only:
                        wp.launch(
                            pin_brush_kernel,
                            dim=num_strands,
                            inputs=[
                                pos_wp,
                                sess["vert_starts_wp"],
                                sess["counts_wp"],
                                ray_origin,
                                ray_dir,
                                min_depth_wp,
                                brush_center,
                                scaled_radius,
                                0,
                                1 if tool == "pin" else 0,
                                sess["pinned_strand_wp"],
                            ],
                            device=device,
                        )
                    else:
                        wp.launch(
                            mark_active_brush_strands_kernel,
                            dim=total_verts,
                            inputs=[
                                pos_wp,
                                inv_mass_wp,
                                sess["strand_ids_wp"],
                                ray_origin,
                                ray_dir,
                                min_depth_wp,
                                brush_center,
                                scaled_radius,
                                2 if tool == "physics" else (1 if tool == "part" else 0),
                                sess["active_strand_wp"],
                            ],
                            device=device,
                        )
                        wp.launch(
                            sync_active_inv_mass_kernel,
                            dim=total_verts,
                            inputs=[inv_mass_wp, sess["strand_ids_wp"], sess["active_strand_wp"], sess["pinned_strand_wp"], sess["brush_inv_mass_wp"]],
                            device=device,
                        )

                if tool in ("smooth", "clump"):
                    brush_source = sess.get("brush_source_wp")
                    if brush_source is None or brush_source.shape != pos_wp.shape:
                        brush_source = wp.empty_like(pos_wp)
                        sess["brush_source_wp"] = brush_source

                if tool == "grab":
                    if len(sess["counts_np"]) >= 1024 and int(sess["counts_np"].min()) >= 2:
                        def _apply_brush(state=sess):
                            direct = state is sess
                            wp.launch(
                                grab_target_kernel,
                                dim=state["num_strands"] if direct else state["active_guide_count"],
                                inputs=[
                                    state["vert_starts_wp"] if direct else state["active_guides"],
                                    state["pos_wp"],
                                    state["vert_starts_wp"],
                                    state["counts_wp"] if direct else state["guide_counts"],
                                    sess["cum_len_wp"] if direct else state["cum_len_wp"],
                                    state["strand_hero_k_wp"],
                                    state["strand_hero_offset_wp"],
                                    state["strand_surface_target_wp"],
                                    state["strand_brush_w_wp"],
                                    coll_id,
                                    has_coll,
                                    sess["scaled_margin"],
                                    sess["max_query_dist"],
                                    cursor_target,
                                    int(direct),
                                ],
                                device=device,
                            )
                        pre_constraints_fn = _apply_brush
                    else:
                        def _apply_brush():
                            wp.launch(
                                grab_clump_kernel,
                                dim=num_strands,
                                inputs=[
                                    pos_wp,
                                    sess["prev_pos_wp"],
                                    vel_wp,
                                    sess["brush_inv_mass_wp"],
                                    sess["vert_starts_wp"],
                                    sess["counts_wp"],
                                    sess["cum_len_wp"],
                                    sess["strand_hero_k_wp"],
                                    sess["strand_hero_offset_wp"],
                                    sess["strand_brush_w_wp"],
                                    coll_id,
                                    has_coll,
                                    sess["scaled_margin"],
                                    sess["max_query_dist"],
                                    cursor_target,
                                    strength,
                                    inv_dt,
                                ],
                                device=device,
                            )
                        pre_constraints_fn = _apply_brush
                elif tool in ("comb", "surfacecomb"):
                    comb_kernel = comb_sweep_kernel if tool == "comb" else surface_comb_kernel
                    step_count = float(sess["substeps"])
                    sub_delta = wp.vec3(
                        drag_delta[0] / step_count, drag_delta[1] / step_count, drag_delta[2] / step_count,
                    )
                    def _apply_brush(state=sess):
                        direct = state is sess
                        wp.launch(
                            comb_kernel,
                            dim=state["num_strands"] if direct else state["active_guide_count"],
                            inputs=[
                                state["vert_starts_wp"] if direct else state["active_guides"],
                                state["pos_wp"],
                                state["vel_wp"],
                                state["brush_inv_mass_wp"],
                                state["vert_starts_wp"],
                                state["strand_hero_k_wp"],
                                state["strand_surface_target_wp"],
                                state["strand_brush_w_wp"],
                                state["cum_len_wp"],
                                coll_id,
                                has_coll,
                                sess["max_query_dist"],
                                sub_delta if direct else drag_delta,
                                scaled_radius,
                                strength,
                                int(direct),
                                state["counts_wp"] if direct else state["guide_counts"],
                                sess["scaled_margin"],
                            ],
                            device=device,
                        )
                    pre_constraints_fn = _apply_brush
                elif tool == "smooth":
                    def _apply_brush():
                        wp.copy(brush_source, pos_wp)
                        wp.launch(
                            smooth_brush_kernel,
                            dim=total_verts,
                            inputs=[
                                pos_wp,
                                brush_source,
                                vel_wp,
                                sess["brush_inv_mass_wp"],
                                sess["vert_starts_wp"],
                                sess["counts_wp"],
                                sess["strand_ids_wp"],
                                sess["local_idx_wp"],
                                sess["stroke_arc_lengths_wp"],
                                ray_origin,
                                ray_dir,
                                min_depth_wp,
                                brush_center,
                                scaled_radius,
                                strength,
                            ],
                            device=device,
                        )
                    pre_constraints_fn = _apply_brush
                elif tool == "clump":
                    clump_s0 = int(sess.get("clump_s0", 0))
                    clump_ln = int(sess.get("clump_ln", 0))
                    def _apply_brush():
                        wp.copy(brush_source, pos_wp)
                        wp.launch(
                            clump_brush_kernel,
                            dim=total_verts,
                            inputs=[
                                pos_wp,
                                brush_source,
                                vel_wp,
                                sess["brush_inv_mass_wp"],
                                sess["counts_wp"],
                                sess["strand_ids_wp"],
                                sess["local_idx_wp"],
                                sess["stroke_arc_lengths_wp"],
                                clump_s0,
                                clump_ln,
                                ray_origin,
                                ray_dir,
                                min_depth_wp,
                                brush_center,
                                scaled_radius,
                                strength,
                                inv_dt,
                            ],
                            device=device,
                        )
                    pre_constraints_fn = _apply_brush
                elif tool == "part":
                    push_step = scaled_radius * 0.08 * strength
                    def _apply_brush(state=sess):
                        im_arr = state["inv_mass_wp"] if state is not sess else sess["brush_inv_mass_wp"]
                        wp.launch(
                            part_brush_kernel,
                            dim=state["total_verts"],
                            inputs=[
                                state["pos_wp"],
                                state["vel_wp"],
                                im_arr,
                                ray_origin,
                                ray_dir,
                                min_depth_wp,
                                scaled_radius,
                                push_step,
                                inv_dt,
                            ],
                            device=device,
                        )
                    pre_constraints_fn = _apply_brush
                elif tool == "puff":
                    push_step = scaled_radius * 0.02 * strength
                    def _apply_brush(state=sess):
                        im_arr = state["inv_mass_wp"] if state is not sess else sess["brush_inv_mass_wp"]
                        wp.launch(
                            puff_brush_kernel,
                            dim=state["total_verts"],
                            inputs=[
                                state["pos_wp"],
                                state["vel_wp"],
                                im_arr,
                                coll_id,
                                has_coll,
                                ray_origin,
                                ray_dir,
                                min_depth_wp,
                                brush_center,
                                scaled_radius,
                                push_step,
                                sess["max_query_dist"],
                                inv_dt,
                            ],
                            device=device,
                        )
                    pre_constraints_fn = _apply_brush

            if pin_only:
                continue
            if direct_edit_done:
                sess["guide_cache"] = None
                if not brush.get("livePhysics"):
                    sess["sim_guide_cache"] = None
            elif not guarded_shape_tool or sess.get("grab_ready"):
                brush_collider = _resolve_brush_collider()
                pre_coll_buf = _snapshot_pre_coll() if brush_collider is not None else None
                raw_vpct = int(brush.get("vertexPct", 100) or 100)
                vertex_stride = 4 if raw_vpct <= 25 else (2 if raw_vpct <= 50 else 1)
                used_guides = False
                if tool in ("comb", "surfacecomb", "grab", "part", "puff", "physics"):
                    from hair_guides import step_guide_brush
                    used_guides = step_guide_brush(
                        wp, device, sess, pre_constraints_fn,
                        is_new_stroke or (tool in ("part", "puff", "physics")),
                        guide_timing_events,
                        dynamic_guides=(tool == "physics"),
                        self_collision=False,
                        zero_gravity=(tool != "physics"),
                        vertex_stride=vertex_stride,
                        bend_scale=(0.15 if tool == "comb" else 1.0),
                        collider_override=brush_collider,
                    )
                max_stretch = None
                if tool in ("comb", "grab", "part", "puff", "physics"):
                    max_stretch = 1.02
                if not used_guides and tool in ("smooth", "clump") and pre_constraints_fn is not None:
                    if vertex_stride > 1 and total_verts > 0:
                        from hair_kernels import _build_subsample_kernels
                        mask_sub_kernel, interp_sub_kernel = _build_subsample_kernels(wp)
                        if tool == "clump":
                            previous_shape = sess.get("brush_previous_wp")
                            if previous_shape is None or previous_shape.shape != pos_wp.shape:
                                previous_shape = wp.empty_like(pos_wp)
                                sess["brush_previous_wp"] = previous_shape
                            wp.copy(previous_shape, pos_wp)
                        else:
                            previous_shape = pos_wp
                        wp.launch(
                            mask_sub_kernel, dim=total_verts,
                            inputs=[sess["brush_inv_mass_wp"], sess["local_idx_wp"], sess["strand_ids_wp"], sess["counts_wp"], vertex_stride],
                            device=device,
                        )
                        pre_constraints_fn()
                        pre_constraints_fn()
                        wp.launch(
                            interp_sub_kernel, dim=num_strands,
                            inputs=[pos_wp, previous_shape, sess["vert_starts_wp"], sess["counts_wp"], sess["active_strand_wp"], 0, vertex_stride, 1 if tool == "smooth" else 0],
                            device=device,
                        )
                    else:
                        pre_constraints_fn()
                        pre_constraints_fn()
                    if tool == "clump" and sess.get("cum_len_wp") is not None and num_strands > 0:
                        wp.launch(
                            _enforce_strand_length_kernel,
                            dim=num_strands,
                            inputs=[pos_wp, sess["brush_inv_mass_wp"], sess["vert_starts_wp"], sess["counts_wp"], sess["cum_len_wp"], 1.02],
                            device=device,
                        )
                    _enforce_brush_collider(pre_coll_buf, sess["brush_inv_mass_wp"], brush_collider)
                    wp.copy(sess["prev_pos_wp"], pos_wp)
                    if sess.get("guide_cache") is not None:
                        sess["guide_cache"]["stroke_ready"] = None
                    if sess.get("sim_guide_cache") is not None:
                        sess["sim_guide_cache"]["sync_from_full"] = True
                elif not used_guides:
                    previous_shape = None
                    if (guarded_shape_tool or vertex_stride > 1) and total_verts > 0:
                        previous_shape = sess.get("brush_previous_wp")
                        if previous_shape is None or previous_shape.shape != pos_wp.shape:
                            previous_shape = wp.empty_like(pos_wp)
                            sess["brush_previous_wp"] = previous_shape
                        wp.copy(previous_shape, pos_wp)
                    if vertex_stride > 1 and total_verts > 0:
                        from hair_kernels import _build_subsample_kernels
                        mask_sub_kernel, interp_sub_kernel = _build_subsample_kernels(wp)
                        wp.launch(
                            mask_sub_kernel, dim=total_verts,
                            inputs=[sess["brush_inv_mass_wp"], sess["local_idx_wp"], sess["strand_ids_wp"], sess["counts_wp"], vertex_stride],
                            device=device,
                        )
                    sess["step_fn"](
                        pre_constraints_fn,
                        inv_mass_override=sess["brush_inv_mass_wp"],
                        use_volume=False,
                        max_stretch=max_stretch,
                        zero_gravity=(tool != "physics"),
                        rest_shape_pull=False,
                        overdamped=(tool in ("grab", "comb", "surfacecomb", "puff", "part", "physics")),
                        bend_scale=(0.15 if tool == "comb" else (0.35 if tool == "grab" else 1.0)),
                        collider_override=brush_collider,
                    )
                    if vertex_stride > 1 and previous_shape is not None and num_strands > 0:
                        from hair_kernels import _build_subsample_kernels
                        _, interp_sub_kernel = _build_subsample_kernels(wp)
                        wp.launch(
                            interp_sub_kernel, dim=num_strands,
                            inputs=[pos_wp, previous_shape, sess["vert_starts_wp"], sess["counts_wp"], sess["active_strand_wp"], 0, vertex_stride, 0],
                            device=device,
                        )
                    if guarded_shape_tool and previous_shape is not None:
                        from hair_guides import bound_full_brush
                        bound_full_brush(wp, device, sess, previous_shape, collider_override=brush_collider)
                        # The guard can reject part of the proposal. Don't
                        # carry velocity from the rejected motion into the next sample.
                        vel_wp.zero_()
                    _enforce_brush_collider(pre_coll_buf, sess["brush_inv_mass_wp"], brush_collider)
                elif used_guides:
                    _enforce_brush_collider(pre_coll_buf, sess["brush_inv_mass_wp"], brush_collider)
                if diagnostics is not None:
                    diagnostics["totalStrands"] += num_strands
                    if used_guides:
                        diagnostics["guideStrands"] += sess["guide_cache"]["num_strands"]
                    else:
                        diagnostics["fullSolveStrands"] += num_strands
                sess["dirty_groom"] = True
            elif not brush.get("commitUsd") or not sess.get("dirty_groom"):
                # The stroke never hit this curve prim. Leave its existing
                # renderer points and saved deltas alone.
                continue

        if total_verts == 0:
            updates.append((p_str, np.zeros((0, 3), dtype=np.float32)))
            continue
        local_wp = sess["local_wp"]
        if not reuse_local:
            transform = np.asarray(world_to_local, dtype=np.float32)
            rows = [wp.vec3(float(row[0]), float(row[1]), float(row[2])) for row in transform[:3, :3]]
            wp.launch(
                world_to_local_kernel,
                dim=total_verts,
                inputs=[pos_wp, sess["reference_world_wp"], sess["reference_local_wp"], *rows, local_wp],
                device=device,
            )
            sess["local_points_current"] = True
        elif unchanged_uploads is not None and uploaded_points and uploaded_points.get(p_str) is local_wp:
            unchanged_uploads.add(p_str)
        if return_gpu and not write_usd and not brush.get("commitUsd"):
            updates.append((p_str, local_wp))
            has_gpu_updates = True
        else:
            local_np = local_wp.numpy()
            if write_usd or brush.get("commitUsd"):
                if not brush.get("livePhysics"):
                    sess["vel_wp"].zero_()
                wp.copy(sess["anim_rest_wp"], pos_wp)
                prim = stage.GetPrimAtPath(p_str)
                if prim and prim.IsValid():
                    UsdGeom.BasisCurves(prim).CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(local_np))
            updates.append((p_str, local_np))

    if has_gpu_updates and wp.get_device(device).is_cuda:
        # RTX consumes these buffers after this call. Only the stream that
        # produced the points needs to finish before the handoff.
        wp.synchronize_stream(wp.get_stream(device))
    if guide_timing_events:
        # The local point readback or the GPU update synchronization above has
        # already waited for the work on this stream.
        diagnostics["guideSolveGpuMs"] = sum(
            wp.get_event_elapsed_time(start, solved, synchronize=False)
            for start, solved, _ in guide_timing_events
        )
        diagnostics["followerGpuMs"] = sum(
            wp.get_event_elapsed_time(solved, reconstructed, synchronize=False)
            for _, solved, reconstructed in guide_timing_events
        )
    return updates


def get_pinned_strand_vertex_colors(stage, hair_cfg, query_matcher, include_colors=True):
    """Return (total_pinned_strands, [(prim_path, float32_colors_np), ...]) for active hair sessions."""
    import numpy as np
    cfg = hair_cfg if isinstance(hair_cfg, dict) else {}
    raw_query = str(cfg.get("targetQuery") or "").strip()
    total_pinned = 0
    color_updates = []
    for p_str in _brush_curve_paths(stage, raw_query, query_matcher):
        sess = _ACTIVE_SIM_SESSIONS.get(p_str)
        if not sess:
            continue
        pinned_np = sess["pinned_strand_wp"].numpy()
        count = int(np.count_nonzero(pinned_np))
        total_pinned += count
        if include_colors:
            sids_np = sess["strand_ids_wp"].numpy()
            mask = pinned_np[sids_np] != 0
            colors_np = np.empty((sess["total_verts"], 3), dtype=np.float32)
            colors_np[:] = (0.14, 0.13, 0.13)
            colors_np[mask] = (1.0, 0.42, 0.04)
            color_updates.append((p_str, np.ascontiguousarray(colors_np, dtype=np.float32)))
    return total_pinned, color_updates


def get_active_hair_points(stage, hair_cfg, query_matcher):
    """Return [(prim_path, local_float32_np), ...] for active hair sim sessions."""
    import numpy as np
    cfg = hair_cfg if isinstance(hair_cfg, dict) else {}
    raw_query = str(cfg.get("targetQuery") or "").strip()
    updates = []
    for p_str in _brush_curve_paths(stage, raw_query, query_matcher):
        sess = _ACTIVE_SIM_SESSIONS.get(p_str)
        if not sess:
            continue
        updates.append((p_str, _session_local_points(sess, np)))
    return updates


def pop_topology_changed(stage, hair_cfg, query_matcher):
    """Return True and clear flag if any active session changed strand count/topology."""
    from pxr import UsdGeom
    cfg = hair_cfg if isinstance(hair_cfg, dict) else {}
    raw_query = str(cfg.get("targetQuery") or "").strip()
    changed = False
    for p_str, sess in _ACTIVE_SIM_SESSIONS.items():
        if not sess.get("needs_stage_repopulate") or not query_matcher(raw_query, p_str):
            continue
        prim = stage.GetPrimAtPath(p_str)
        if not prim or not prim.IsActive() or not prim.IsLoaded() or not prim.IsDefined() or prim.IsAbstract():
            continue
        if not prim.IsInstanceProxy() and prim.IsA(UsdGeom.BasisCurves):
            sess.pop("needs_stage_repopulate")
            changed = True
    return changed
