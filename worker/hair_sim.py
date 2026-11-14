"""GPU-accelerated hair strand simulation for the OrchFlow Hair node.

Uses NVIDIA Warp (ADMM/Jacobi XPBD Discrete Elastic Rod constraints + GPU BVH
mesh collision) when `simEnabled` is True. Imported lazily by `usd_graph.py`
so core app functionality and non-simulated Hair nodes have zero dependency on
Warp or NumPy.
"""

import hashlib
import os
import sys
from collections import OrderedDict

_WARP_READY = False
_SIM_CACHE: OrderedDict[str, dict[float, object]] = OrderedDict()
_MAX_CACHE_ENTRIES = 16
_ACTIVE_SIM_SESSIONS: dict[str, dict[str, object]] = {}


def reset_hair_sessions():
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


def _ensure_warp():
    global _WARP_READY
    sim_pkg_dir = os.environ.get("ORCHFLOW_SIM_PACKAGES", r"I:\AI\OrchFlow-packages\py312")
    if sim_pkg_dir and os.path.isdir(sim_pkg_dir) and sim_pkg_dir not in sys.path:
        sys.path.insert(0, sim_pkg_dir)

    try:
        import numpy as np
        import warp as wp
    except ImportError as exc:
        raise RuntimeError(
            "GPU hair simulation requires 'warp-lang' and 'numpy'. "
            f"Install them into {sim_pkg_dir} or the active Python environment. ({exc})"
        ) from exc

    if not _WARP_READY:
        wp.config.quiet = True
        old_stdout = sys.stdout
        saved_fd = None
        try:
            sys.stdout = sys.stderr
            try:
                saved_fd = os.dup(1)
                os.dup2(2, 1)
            except OSError:
                saved_fd = None
            wp.init()
        finally:
            if saved_fd is not None:
                try:
                    os.dup2(saved_fd, 1)
                    os.close(saved_fd)
                except OSError:
                    pass
            sys.stdout = old_stdout
        _WARP_READY = True

    device = "cuda:0" if wp.is_cuda_available() else "cpu"
    return wp, np, device


def _build_kernels(wp):
    if getattr(wp, "_orchflow_hair_kernels", None) is not None:
        return wp._orchflow_hair_kernels

    @wp.kernel
    def predict_kernel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        anim_rest: wp.array(dtype=wp.vec3),
        ext_accel: wp.vec3,
        damping: float,
        dt: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            p = anim_rest[i]
            pos[i] = p
            prev_pos[i] = p
            vel[i] = wp.vec3(0.0, 0.0, 0.0)
            return

        p = pos[i]
        prev_pos[i] = p
        damp_factor = wp.max(0.0, 1.0 - damping)
        v = vel[i] * damp_factor + ext_accel * dt
        pos[i] = p + v * dt

    @wp.kernel
    def zero_accum_kernel(
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        i = wp.tid()
        delta_pos[i] = wp.vec3(0.0, 0.0, 0.0)
        delta_w[i] = 0.0

    @wp.kernel
    def stretch_constraint_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        seg_v0: wp.array(dtype=int),
        seg_v1: wp.array(dtype=int),
        seg_rest_len: wp.array(dtype=float),
        stiffness: float,
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        s = wp.tid()
        i0 = seg_v0[s]
        i1 = seg_v1[s]
        w0 = inv_mass[i0]
        w1 = inv_mass[i1]
        w_sum = w0 + w1
        if w_sum <= 1e-8:
            return

        p0 = pos[i0]
        p1 = pos[i1]
        diff = p1 - p0
        dist = wp.length(diff)
        if dist <= 1e-8:
            return

        rest = seg_rest_len[s]
        err = dist - rest
        corr = (diff / dist) * (err * stiffness / w_sum)

        if w0 > 0.0:
            wp.atomic_add(delta_pos, i0, corr * w0)
            wp.atomic_add(delta_w, i0, 1.0)
        if w1 > 0.0:
            wp.atomic_add(delta_pos, i1, -corr * w1)
            wp.atomic_add(delta_w, i1, 1.0)

    @wp.kernel
    def bend_constraint_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        bend_v0: wp.array(dtype=int),
        bend_v2: wp.array(dtype=int),
        bend_rest_len: wp.array(dtype=float),
        bend_stiffness: float,
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        b = wp.tid()
        if bend_stiffness <= 1e-6:
            return
        i0 = bend_v0[b]
        i2 = bend_v2[b]
        w0 = inv_mass[i0]
        w2 = inv_mass[i2]
        w_sum = w0 + w2
        if w_sum <= 1e-8:
            return

        p0 = pos[i0]
        p2 = pos[i2]
        diff = p2 - p0
        dist = wp.length(diff)
        if dist <= 1e-8:
            return

        rest = bend_rest_len[b]
        err = dist - rest
        corr = (diff / dist) * (err * bend_stiffness / w_sum)

        if w0 > 0.0:
            wp.atomic_add(delta_pos, i0, corr * w0)
            wp.atomic_add(delta_w, i0, 1.0)
        if w2 > 0.0:
            wp.atomic_add(delta_pos, i2, -corr * w2)
            wp.atomic_add(delta_w, i2, 1.0)

    @wp.kernel
    def apply_accum_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        w = delta_w[i]
        if w > 0.0:
            pos[i] = pos[i] + delta_pos[i] / w

    @wp.kernel
    def collide_mesh_kernel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        mesh_id: wp.uint64,
        margin: float,
        friction: float,
        max_query_dist: float,
        apply_friction: int,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return

        p = pos[i]
        prev_p = prev_pos[i]
        if apply_friction == 2 and wp.length_sq(p - prev_p) <= 1e-12:
            return

        # Continuous collision detection on final substep pass: catch fast motion or open shells
        if apply_friction != 0:
            move = p - prev_p
            move_len = wp.length(move)
            if move_len > 1e-6:
                move_dir = move / move_len
                ray_q = wp.mesh_query_ray(mesh_id, prev_p, move_dir, move_len + margin)
                if ray_q.result and ray_q.t <= move_len + margin:
                    hit_n = wp.mesh_eval_face_normal(mesh_id, ray_q.face)
                    if wp.dot(hit_n, move_dir) > 0.0:
                        hit_n = -hit_n
                    hit_p = prev_p + move_dir * ray_q.t
                    pen = wp.dot((hit_p + hit_n * margin) - p, hit_n)
                    if pen > 0.0:
                        p = p + hit_n * pen

        query = wp.mesh_query_point_sign_normal(mesh_id, p, max_query_dist)
        if query.result:
            closest = wp.mesh_eval_position(mesh_id, query.face, query.u, query.v)
            diff = p - closest
            dist = wp.length(diff)
            signed_dist = dist * query.sign
            if signed_dist < margin:
                face_n = wp.mesh_eval_face_normal(mesh_id, query.face)
                normal = face_n
                if dist > 1e-6:
                    normal = diff / dist
                    if query.sign < 0.0:
                        normal = -normal
                        if wp.dot(normal, face_n) < 0.0:
                            normal = face_n

                new_p = closest + normal * margin
                pos[i] = new_p
                if apply_friction != 0:
                    disp = new_p - prev_p
                    n_comp = wp.dot(disp, normal)
                    t_disp = disp - normal * n_comp
                    prev_pos[i] = new_p - normal * n_comp - t_disp * (1.0 - friction)
                return

        pos[i] = p

    @wp.kernel
    def collide_segments_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        seg_v0: wp.array(dtype=int),
        seg_v1: wp.array(dtype=int),
        mesh_id: wp.uint64,
        margin: float,
        max_query_dist: float,
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        s = wp.tid()
        i0 = seg_v0[s]
        i1 = seg_v1[s]
        w0 = inv_mass[i0]
        w1 = inv_mass[i1]
        w_sum = w0 + w1
        if w_sum <= 1e-8:
            return

        mid = (pos[i0] + pos[i1]) * 0.5
        query = wp.mesh_query_point_sign_normal(mesh_id, mid, max_query_dist)
        if query.result:
            closest = wp.mesh_eval_position(mesh_id, query.face, query.u, query.v)
            diff = mid - closest
            dist = wp.length(diff)
            signed_dist = dist * query.sign
            if signed_dist < margin:
                face_n = wp.mesh_eval_face_normal(mesh_id, query.face)
                normal = face_n
                if dist > 1e-6:
                    normal = diff / dist
                    if query.sign < 0.0:
                        normal = -normal
                        if wp.dot(normal, face_n) < 0.0:
                            normal = face_n
                push = (closest + normal * margin) - mid
                if w0 > 0.0:
                    wp.atomic_add(delta_pos, i0, push * (w0 / w_sum))
                    wp.atomic_add(delta_w, i0, 1.0)
                if w1 > 0.0:
                    wp.atomic_add(delta_pos, i1, push * (w1 / w_sum))
                    wp.atomic_add(delta_w, i1, 1.0)

    @wp.kernel
    def update_vel_kernel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        inv_dt: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            vel[i] = wp.vec3(0.0, 0.0, 0.0)
            return
        vel[i] = (pos[i] - prev_pos[i]) * inv_dt

    @wp.kernel
    def brush_min_depth_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        radius: float,
        min_depth: wp.array(dtype=float),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        to_v = pos[i] - ray_origin
        t_along = wp.dot(to_v, ray_dir)
        if t_along <= 0.0:
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(pos[i] - closest)
        if dist < radius:
            wp.atomic_min(min_depth, 0, t_along)

    @wp.kernel
    def init_grab_weights_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        radius: float,
        min_depth: wp.array(dtype=float),
        grab_w: wp.array(dtype=float),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            grab_w[i] = 0.0
            return
        to_v = pos[i] - ray_origin
        t_along = wp.dot(to_v, ray_dir)
        d_min = min_depth[0]
        if t_along <= 0.0 or t_along > d_min + radius * 1.5:
            grab_w[i] = 0.0
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(pos[i] - closest)
        if dist >= radius:
            grab_w[i] = 0.0
            return
        u = dist / radius
        grab_w[i] = 1.0 - u * u

    @wp.kernel
    def hero_brush_kernel(
        pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        hero_global_idx: wp.array(dtype=int),
        hero_offsets: wp.array(dtype=wp.vec3),
        guide_offsets: wp.array(dtype=int),
        guide_lengths: wp.array(dtype=int),
        cursor_target: wp.vec3,
        strength: float,
        inv_dt: float,
    ):
        g = wp.tid()
        v_idx = hero_global_idx[g]
        if v_idx < 0 or inv_mass[v_idx] <= 0.0:
            return
        target = cursor_target + hero_offsets[g]
        old_p = pos[v_idx]
        w = wp.min(0.55, strength * 0.5)
        delta = (target - old_p) * w
        pos[v_idx] = old_p + delta
        vel[v_idx] = vel[v_idx] * 0.5 + delta * (inv_dt * 0.05)
        g_start = guide_offsets[g]
        g_end = g_start + guide_lengths[g]
        if v_idx - 1 > g_start and inv_mass[v_idx - 1] > 0.0:
            pos[v_idx - 1] = pos[v_idx - 1] + delta * 0.35
            vel[v_idx - 1] = vel[v_idx - 1] * 0.5 + delta * (inv_dt * 0.02)
        if v_idx + 1 < g_end and inv_mass[v_idx + 1] > 0.0:
            pos[v_idx + 1] = pos[v_idx + 1] + delta * 0.35
            vel[v_idx + 1] = vel[v_idx + 1] * 0.5 + delta * (inv_dt * 0.02)

    @wp.kernel
    def strand_ray_dist_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        strand_ids: wp.array(dtype=int),
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        radius: float,
        min_depth: wp.array(dtype=float),
        strand_dist: wp.array(dtype=float),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        to_v = pos[i] - ray_origin
        t_along = wp.dot(to_v, ray_dir)
        d_min = min_depth[0]
        if t_along <= 0.0 or t_along > d_min + radius * 1.5:
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(pos[i] - closest)
        wp.atomic_min(strand_dist, strand_ids[i], dist)

    @wp.kernel
    def vec3_sub_kernel(
        a: wp.array(dtype=wp.vec3),
        b: wp.array(dtype=wp.vec3),
        out: wp.array(dtype=wp.vec3),
    ):
        i = wp.tid()
        out[i] = a[i] - b[i]

    @wp.kernel
    def apply_guide_interp_kernel(
        pos: wp.array(dtype=wp.vec3),
        stroke_start_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        strand_ids: wp.array(dtype=int),
        local_idx: wp.array(dtype=int),
        vert_starts: wp.array(dtype=int),
        vert_counts: wp.array(dtype=int),
        guide_s_ids: wp.array(dtype=int),
        guide_vert_offsets: wp.array(dtype=int),
        guide_lengths: wp.array(dtype=int),
        guide_deltas: wp.array(dtype=wp.vec3),
        guide_roots: wp.array(dtype=wp.vec3),
        guide_mids: wp.array(dtype=wp.vec3),
        guide_tips: wp.array(dtype=wp.vec3),
        guide_count: int,
        radius: float,
        inv_dt: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        s_id = strand_ids[i]
        l_idx = local_idx[i]
        s_start = vert_starts[s_id]
        s_len = vert_counts[s_id]
        r_pos = stroke_start_pos[s_start]
        m_pos = stroke_start_pos[s_start + (s_len // 2)]
        t_pos = stroke_start_pos[s_start + s_len - 1]
        sum_w = float(0.0)
        max_w = float(0.0)
        sum_delta = wp.vec3(0.0, 0.0, 0.0)
        for g in range(guide_count):
            if s_id == guide_s_ids[g]:
                d_exact = guide_deltas[guide_vert_offsets[g] + l_idx]
                pos[i] = pos[i] + d_exact
                vel[i] = vel[i] * 0.5 + d_exact * (inv_dt * 0.15)
                return
            d_root = wp.length(r_pos - guide_roots[g])
            d_mid = wp.length(m_pos - guide_mids[g])
            d_tip = wp.length(t_pos - guide_tips[g])
            d = (d_root + d_mid + d_tip) * 0.33333334
            if d < radius:
                u = d / radius
                w = 1.0 - u * u
                sum_w = sum_w + w
                max_w = wp.max(max_w, w)
                g_idx = guide_vert_offsets[g] + wp.min(l_idx, guide_lengths[g] - 1)
                sum_delta = sum_delta + guide_deltas[g_idx] * w
        if sum_w > 1e-6:
            avg_delta = (sum_delta / sum_w) * max_w
            pos[i] = pos[i] + avg_delta
            vel[i] = vel[i] * 0.25 + avg_delta * (inv_dt * 0.05)

    @wp.kernel
    def world_to_local_kernel(
        world_pos: wp.array(dtype=wp.vec3),
        c0: wp.vec3,
        c1: wp.vec3,
        c2: wp.vec3,
        t: wp.vec3,
        local_pos: wp.array(dtype=wp.vec3),
    ):
        i = wp.tid()
        p = world_pos[i]
        local_pos[i] = c0 * p[0] + c1 * p[1] + c2 * p[2] + t

    kernels = (
        predict_kernel,
        zero_accum_kernel,
        stretch_constraint_kernel,
        bend_constraint_kernel,
        apply_accum_kernel,
        collide_mesh_kernel,
        update_vel_kernel,
        brush_min_depth_kernel,
        init_grab_weights_kernel,
        hero_brush_kernel,
        strand_ray_dist_kernel,
        vec3_sub_kernel,
        apply_guide_interp_kernel,
        world_to_local_kernel,
        collide_segments_kernel,
    )
    wp._orchflow_hair_kernels = kernels
    return kernels


def _points_to_world_np(pts, world_mat, np):
    pts_np = np.asarray(pts, dtype=np.float32)
    if pts_np.size == 0:
        return pts_np.reshape(0, 3)
    m = np.asarray(world_mat, dtype=np.float32)
    return np.ascontiguousarray(pts_np @ m[:3, :3] + m[3, :3], dtype=np.float32)


def _world_np_to_local_vt(pts_np, inv_world_mat, Vt, np):
    m = np.asarray(inv_world_mat, dtype=np.float32)
    local_np = np.ascontiguousarray(pts_np @ m[:3, :3] + m[3, :3], dtype=np.float32)
    return Vt.Vec3fArray.FromNumpy(local_np)


def _triangulate_mesh(mesh, time_code, xform_cache, np):
    pts = mesh.GetPointsAttr().Get(time_code)
    counts = mesh.GetFaceVertexCountsAttr().Get(time_code)
    indices = mesh.GetFaceVertexIndicesAttr().Get(time_code)
    if not pts or not counts or not indices:
        return None, None

    world_mat = xform_cache.GetLocalToWorldTransform(mesh.GetPrim())
    pts_np = _points_to_world_np(pts, world_mat, np)
    counts_np = np.asarray(counts, dtype=np.int32)
    idx_np = np.asarray(indices, dtype=np.int32)
    if counts_np.size == 0 or idx_np.size == 0:
        return None, None

    if np.all(counts_np == 3):
        return pts_np, np.ascontiguousarray(idx_np, dtype=np.int32)
    if np.all(counts_np == 4):
        quads = idx_np.reshape(-1, 4)
        tris = quads[:, [0, 1, 2, 0, 2, 3]].reshape(-1)
        return pts_np, np.ascontiguousarray(tris, dtype=np.int32)

    tris = []
    offset = 0
    for c_int in counts_np.tolist():
        if c_int >= 3:
            v0 = int(idx_np[offset])
            for k in range(1, c_int - 1):
                tris.append(v0)
                tris.append(int(idx_np[offset + k]))
                tris.append(int(idx_np[offset + k + 1]))
        offset += c_int

    if not tris:
        return None, None
    return pts_np, np.asarray(tris, dtype=np.int32)


def _build_merged_collider_mesh(collider_prims, time_code, xform_cache, hair_min, hair_max, np):
    from pxr import UsdGeom

    all_pts = []
    all_tris = []
    vert_offset = 0
    any_animated = False

    for c_prim in collider_prims:
        mesh = UsdGeom.Mesh(c_prim)
        c_pts, c_tris = _triangulate_mesh(mesh, time_code, xform_cache, np)
        if c_pts is None or c_tris is None or len(c_pts) == 0 or len(c_tris) == 0:
            continue
        c_min = c_pts.min(axis=0)
        c_max = c_pts.max(axis=0)
        if np.any(c_max < hair_min) or np.any(c_min > hair_max):
            continue
        all_pts.append(c_pts)
        all_tris.append(c_tris + vert_offset)
        vert_offset += len(c_pts)
        if mesh.GetPointsAttr().ValueMightBeTimeVarying() or mesh.TransformMightBeTimeVarying():
            any_animated = True

    if not all_pts:
        return None, None, False

    return (
        np.ascontiguousarray(np.concatenate(all_pts, axis=0), dtype=np.float32),
        np.ascontiguousarray(np.concatenate(all_tris, axis=0), dtype=np.int32),
        any_animated,
    )


def _compute_cache_key(prim_path, cfg, rest_pts_np, counts_np, collider_paths, fps):
    h = hashlib.sha256()
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
    collide_segments_kernel = kernels[14]

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

    fps = float(stage.GetTimeCodesPerSecond() or 24.0)
    if fps <= 0.0:
        fps = 24.0
    dt_frame = 1.0 / fps
    dt_sub = dt_frame / float(substeps)

    up_axis = UsdGeom.GetStageUpAxis(stage)
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    unit_scale = 1.0 / max(meters_per_unit, 1e-6)
    if up_axis == UsdGeom.Tokens.z:
        grav_vec = wp.vec3(wind[0] * unit_scale, wind[1] * unit_scale, (wind[2] - gravity) * unit_scale)
    else:
        grav_vec = wp.vec3(wind[0] * unit_scale, (wind[1] - gravity) * unit_scale, wind[2] * unit_scale)
    scaled_margin = margin * unit_scale

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
        counts = curves.GetCurveVertexCountsAttr().Get(rest_tc)
        if not counts:
            counts = curves.GetCurveVertexCountsAttr().Get()
        rest_pts = curves.GetPointsAttr().Get(rest_tc)
        if not rest_pts:
            rest_pts = curves.GetPointsAttr().Get()
        if not counts or not rest_pts or len(rest_pts) == 0:
            continue

        counts_np = np.asarray(counts, dtype=np.int32)
        total_verts = int(counts_np.sum())
        if total_verts != len(rest_pts) or total_verts == 0:
            continue

        xform_start = UsdGeom.XformCache(rest_tc)
        rest_world_np = _points_to_world_np(rest_pts, xform_start.GetLocalToWorldTransform(curve_prim), np)

        cache_key = _compute_cache_key(
            curve_prim.GetPath(), cfg, rest_world_np, counts_np, collider_paths, fps
        )
        cached = _SIM_CACHE.get(cache_key)
        if cached is not None:
            _SIM_CACHE.move_to_end(cache_key)
            points_attr = curves.CreatePointsAttr()
            if sim_mode == "static":
                points_attr.Set(cached[None])
            else:
                for frame_f, vt_pts in cached.items():
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
        if n_segs > 0:
            seg_v0_wp = wp.array(seg_v0_np, dtype=int, device=device)
            seg_v1_wp = wp.array(seg_v1_np, dtype=int, device=device)
            seg_len_wp = wp.array(seg_len_np, dtype=float, device=device)

        n_bends = len(bend_v0_np)
        if n_bends > 0:
            bend_v0_wp = wp.array(bend_v0_np, dtype=int, device=device)
            bend_v2_wp = wp.array(bend_v2_np, dtype=int, device=device)
            bend_len_wp = wp.array(bend_len_np, dtype=float, device=device)

        # Build merged collider BVH once, refit only if animated
        max_query_dist = max(1.0 * unit_scale, scaled_margin * 20.0)
        hair_pad = max_query_dist + float(np.max(seg_len_np)) * 2.0 if n_segs > 0 else max_query_dist
        hair_min = rest_world_np.min(axis=0) - hair_pad
        hair_max = rest_world_np.max(axis=0) + hair_pad

        wp_collider = None
        collider_animated = False
        if collider_prims:
            c_pts, c_tris, collider_animated = _build_merged_collider_mesh(
                collider_prims, rest_tc, xform_start, hair_min, hair_max, np
            )
            if c_pts is not None and c_tris is not None:
                wp_collider = wp.Mesh(
                    points=wp.array(c_pts, dtype=wp.vec3, device=device),
                    indices=wp.array(c_tris, dtype=int, device=device),
                )

        curve_animated = bool(
            curves.GetPointsAttr().ValueMightBeTimeVarying() or curves.TransformMightBeTimeVarying()
        )
        points_attr = curves.CreatePointsAttr()
        frame_results: dict[float | None, object] = {}
        damping_sub = 1.0 - (1.0 - damping) ** (1.0 / float(substeps))

        def _step_frame():
            for _ in range(substeps):
                wp.launch(
                    predict_kernel,
                    dim=total_verts,
                    inputs=[pos_wp, prev_pos_wp, vel_wp, inv_mass_wp, anim_rest_wp, grav_vec, damping_sub, dt_sub],
                    device=device,
                )
                for iter_idx in range(iterations):
                    wp.launch(zero_accum_kernel, dim=total_verts, inputs=[delta_pos_wp, delta_w_wp], device=device)
                    if n_segs > 0:
                        wp.launch(
                            stretch_constraint_kernel,
                            dim=n_segs,
                            inputs=[pos_wp, inv_mass_wp, seg_v0_wp, seg_v1_wp, seg_len_wp, stiffness, delta_pos_wp, delta_w_wp],
                            device=device,
                        )
                    if n_bends > 0:
                        wp.launch(
                            bend_constraint_kernel,
                            dim=n_bends,
                            inputs=[pos_wp, inv_mass_wp, bend_v0_wp, bend_v2_wp, bend_len_wp, bend_stiffness, delta_pos_wp, delta_w_wp],
                            device=device,
                        )
                    wp.launch(
                        apply_accum_kernel,
                        dim=total_verts,
                        inputs=[pos_wp, inv_mass_wp, delta_pos_wp, delta_w_wp],
                        device=device,
                    )
                    if wp_collider is not None and (iter_idx % 2 == 1 or iter_idx + 1 == iterations):
                        wp.launch(
                            collide_mesh_kernel,
                            dim=total_verts,
                            inputs=[pos_wp, prev_pos_wp, inv_mass_wp, wp_collider.id, scaled_margin, friction, max_query_dist, 0],
                            device=device,
                        )
                if wp_collider is not None:
                    if n_segs > 0:
                        wp.launch(zero_accum_kernel, dim=total_verts, inputs=[delta_pos_wp, delta_w_wp], device=device)
                        wp.launch(
                            collide_segments_kernel,
                            dim=n_segs,
                            inputs=[pos_wp, inv_mass_wp, seg_v0_wp, seg_v1_wp, wp_collider.id, scaled_margin, max_query_dist, delta_pos_wp, delta_w_wp],
                            device=device,
                        )
                        wp.launch(
                            apply_accum_kernel,
                            dim=total_verts,
                            inputs=[pos_wp, inv_mass_wp, delta_pos_wp, delta_w_wp],
                            device=device,
                        )
                    wp.launch(
                        collide_mesh_kernel,
                        dim=total_verts,
                        inputs=[pos_wp, prev_pos_wp, inv_mass_wp, wp_collider.id, scaled_margin, friction, max_query_dist, 1],
                        device=device,
                    )
                wp.launch(
                    update_vel_kernel,
                    dim=total_verts,
                    inputs=[pos_wp, prev_pos_wp, vel_wp, inv_mass_wp, 1.0 / max(dt_sub, 1e-6)],
                    device=device,
                )

        if sim_mode == "static":
            world_to_local = xform_start.GetLocalToWorldTransform(curve_prim).GetInverse()
            for step_idx in range(settle_steps):
                if step_idx == 0 or (step_idx + 1) % 8 == 0 or step_idx + 1 == settle_steps:
                    _emit_sim_status(f"Hair GPU style: step {step_idx + 1}/{settle_steps}")
                _step_frame()
            sim_world_np = pos_wp.numpy()
            vt_local = _world_np_to_local_vt(sim_world_np, world_to_local, Vt, np)
            points_attr.Set(vt_local)
            frame_results[None] = vt_local
            _ACTIVE_SIM_SESSIONS[str(curve_prim.GetPath())] = {
                "pos_wp": pos_wp,
                "local_wp": wp.zeros(total_verts, dtype=wp.vec3, device=device),
                "prev_pos_wp": prev_pos_wp,
                "vel_wp": vel_wp,
                "inv_mass_wp": inv_mass_wp,
                "anim_rest_wp": anim_rest_wp,
                "total_verts": total_verts,
                "num_strands": len(counts_np),
                "counts_np": counts_np,
                "starts_np": starts,
                "strand_ids_wp": wp.array(strand_ids, dtype=int, device=device),
                "local_idx_wp": wp.array(local_idx, dtype=int, device=device),
                "vert_starts_wp": wp.array(starts, dtype=int, device=device),
                "counts_wp": wp.array(counts_np, dtype=int, device=device),
                "stroke_start_pos_wp": wp.zeros(total_verts, dtype=wp.vec3, device=device),
                "world_to_local": world_to_local,
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
                "guide_cache": None,
            }
        else:
            total_frames = end_frame - start_frame + 1
            for idx_f, frame in enumerate(range(start_frame, end_frame + 1)):
                if idx_f == 0 or (idx_f + 1) % 8 == 0 or frame == end_frame:
                    _emit_sim_status(f"Hair GPU sim: frame {frame}/{end_frame} ({idx_f + 1}/{total_frames})")

                tc = Usd.TimeCode(float(frame))
                xform_cache = UsdGeom.XformCache(tc)
                local_to_world = xform_cache.GetLocalToWorldTransform(curve_prim)
                world_to_local = local_to_world.GetInverse()

                if frame > start_frame:
                    if curve_animated:
                        frame_pts = curves.GetPointsAttr().Get(tc)
                        if frame_pts and len(frame_pts) == total_verts:
                            frame_world_np = _points_to_world_np(frame_pts, local_to_world, np)
                            wp.copy(anim_rest_wp, wp.array(frame_world_np, dtype=wp.vec3, device=device))

                    if wp_collider is not None and collider_animated:
                        c_pts, _, _ = _build_merged_collider_mesh(
                            collider_prims, tc, xform_cache, hair_min, hair_max, np
                        )
                        if c_pts is not None and len(c_pts) == len(wp_collider.points):
                            wp.copy(wp_collider.points, wp.array(c_pts, dtype=wp.vec3, device=device))
                            wp_collider.refit()

                    _step_frame()

                sim_world_np = pos_wp.numpy()
                vt_local = _world_np_to_local_vt(sim_world_np, world_to_local, Vt, np)
                points_attr.Set(vt_local, tc)
                frame_results[float(frame)] = vt_local

        _SIM_CACHE[cache_key] = frame_results
        while len(_SIM_CACHE) > _MAX_CACHE_ENTRIES:
            _SIM_CACHE.popitem(last=False)


def _build_guide_subsession(wp, np, device, sess, guide_s_ids, hero_pos0=None):
    """Build a compact GPU solver session for <= 20 guide strands with per-guide hero verts."""
    counts_np = sess["counts_np"]
    starts_np = sess["starts_np"]
    pos_np = sess["pos_wp"].numpy()
    vel_np = sess["vel_wp"].numpy()
    inv_mass_np = sess["inv_mass_wp"].numpy()

    g_pos_list = []
    g_vel_list = []
    g_inv_list = []
    g_offsets = []
    g_lengths = []
    g_roots = []
    g_mids = []
    g_tips = []
    g_cum_lens = []
    hero_k_list = []
    hero_offsets_list = []
    hero_global_idx_list = []
    g_seg_v0 = []
    g_seg_v1 = []
    g_seg_len = []
    g_bend_v0 = []
    g_bend_v2 = []
    g_bend_len = []

    offset = 0
    for s_id in guide_s_ids:
        s0 = int(starts_np[s_id])
        ln = int(counts_np[s_id])
        if ln <= 1:
            continue
        g_offsets.append(offset)
        g_lengths.append(ln)
        pts_s = pos_np[s0 : s0 + ln]
        g_roots.append(pts_s[0])
        g_mids.append(pts_s[ln // 2])
        g_tips.append(pts_s[ln - 1])
        g_pos_list.append(pts_s)
        g_vel_list.append(vel_np[s0 : s0 + ln])
        g_inv_list.append(inv_mass_np[s0 : s0 + ln])
        cum = np.zeros(ln, dtype=np.float32)
        for k in range(ln - 1):
            g_seg_v0.append(offset + k)
            g_seg_v1.append(offset + k + 1)
            seg_l = float(np.linalg.norm(pts_s[k + 1] - pts_s[k]))
            g_seg_len.append(seg_l)
            cum[k + 1] = cum[k] + seg_l
        g_cum_lens.append(cum)
        if hero_pos0 is not None:
            k_g = 1 + int(np.argmin(np.linalg.norm(pts_s[1:] - hero_pos0, axis=1)))
            h_off = pts_s[k_g] - hero_pos0
        else:
            k_g = max(1, ln // 2)
            h_off = np.zeros(3, dtype=np.float32)
        hero_k_list.append(k_g)
        hero_offsets_list.append(h_off)
        hero_global_idx_list.append(offset + k_g)
        for k in range(ln - 2):
            g_bend_v0.append(offset + k)
            g_bend_v2.append(offset + k + 2)
            g_bend_len.append(float(np.linalg.norm(pts_s[k + 2] - pts_s[k])))
        offset += ln

    if offset == 0 or not g_offsets:
        return None

    g_pos_np = np.concatenate(g_pos_list, axis=0).astype(np.float32)
    g_vel_np = np.concatenate(g_vel_list, axis=0).astype(np.float32)
    g_inv_np = np.concatenate(g_inv_list, axis=0).astype(np.float32)
    hero_offsets_np = np.asarray(hero_offsets_list, dtype=np.float32)
    grab_center = (
        wp.vec3(float(hero_pos0[0]), float(hero_pos0[1]), float(hero_pos0[2]))
        if hero_pos0 is not None
        else wp.vec3(float(g_mids[0][0]), float(g_mids[0][1]), float(g_mids[0][2]))
    )

    return {
        "guide_s_ids_wp": wp.array(np.asarray(guide_s_ids[: len(g_offsets)], dtype=np.int32), dtype=int, device=device),
        "guide_offsets_wp": wp.array(np.asarray(g_offsets, dtype=np.int32), dtype=int, device=device),
        "guide_lengths_wp": wp.array(np.asarray(g_lengths, dtype=np.int32), dtype=int, device=device),
        "guide_roots_wp": wp.array(np.asarray(g_roots, dtype=np.float32), dtype=wp.vec3, device=device),
        "guide_mids_wp": wp.array(np.asarray(g_mids, dtype=np.float32), dtype=wp.vec3, device=device),
        "guide_tips_wp": wp.array(np.asarray(g_tips, dtype=np.float32), dtype=wp.vec3, device=device),
        "guide_count": len(g_offsets),
        "guide_g_offsets": g_offsets,
        "guide_lengths": g_lengths,
        "guide_roots_np": [np.asarray(r, dtype=np.float32) for r in g_roots],
        "guide_cum_lens": g_cum_lens,
        "hero_k": hero_k_list,
        "hero_offsets_np": hero_offsets_np,
        "hero_global_idx_wp": wp.array(np.asarray(hero_global_idx_list, dtype=np.int32), dtype=int, device=device),
        "hero_offsets_wp": wp.array(hero_offsets_np, dtype=wp.vec3, device=device),
        "grab_center": grab_center,
        "grab_ready": True,
        "g_total_verts": offset,
        "g_pos_wp": wp.array(g_pos_np, dtype=wp.vec3, device=device),
        "g_before_wp": wp.zeros(offset, dtype=wp.vec3, device=device),
        "g_delta_wp": wp.zeros(offset, dtype=wp.vec3, device=device),
        "g_prev_pos_wp": wp.array(g_pos_np, dtype=wp.vec3, device=device),
        "g_anim_rest_wp": wp.array(g_pos_np, dtype=wp.vec3, device=device),
        "g_vel_wp": wp.array(g_vel_np, dtype=wp.vec3, device=device),
        "g_inv_mass_wp": wp.array(g_inv_np, dtype=float, device=device),
        "g_delta_pos_wp": wp.zeros(offset, dtype=wp.vec3, device=device),
        "g_delta_w_wp": wp.zeros(offset, dtype=float, device=device),
        "n_segs": len(g_seg_v0),
        "g_seg_v0_wp": wp.array(np.asarray(g_seg_v0, dtype=np.int32), dtype=int, device=device) if g_seg_v0 else None,
        "g_seg_v1_wp": wp.array(np.asarray(g_seg_v1, dtype=np.int32), dtype=int, device=device) if g_seg_v1 else None,
        "g_seg_len_wp": wp.array(np.asarray(g_seg_len, dtype=np.float32), dtype=float, device=device) if g_seg_len else None,
        "n_bends": len(g_bend_v0),
        "g_bend_v0_wp": wp.array(np.asarray(g_bend_v0, dtype=np.int32), dtype=int, device=device) if g_bend_v0 else None,
        "g_bend_v2_wp": wp.array(np.asarray(g_bend_v2, dtype=np.int32), dtype=int, device=device) if g_bend_v2 else None,
        "g_bend_len_wp": wp.array(np.asarray(g_bend_len, dtype=np.float32), dtype=float, device=device) if g_bend_len else None,
    }


def step_hair_brush(stage, hair_cfg, query_matcher, brush, cam_basis, write_usd=True, return_gpu=False):
    """Apply an interactive 3D comb/grab brush to <= 20 guide strands and interpolate neighbors."""
    from pxr import UsdGeom, Vt

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
        brush_min_depth_kernel,
        init_grab_weights_kernel,
        hero_brush_kernel,
        strand_ray_dist_kernel,
        vec3_sub_kernel,
        apply_guide_interp_kernel,
        world_to_local_kernel,
        collide_segments_kernel,
    ) = kernels

    cfg = hair_cfg if isinstance(hair_cfg, dict) else {}
    raw_query = str(cfg.get("targetQuery") or "").strip()

    # Ensure active GPU sessions exist for matching BasisCurves
    missing_session = False
    matching_paths = []
    for prim in stage.Traverse():
        if prim.IsInstanceProxy() or not prim.IsA(UsdGeom.BasisCurves):
            continue
        p_str = str(prim.GetPath())
        if query_matcher(raw_query, p_str):
            matching_paths.append(p_str)
            if p_str not in _ACTIVE_SIM_SESSIONS:
                missing_session = True
    if missing_session:
        apply_hair_simulation(stage, cfg, query_matcher)

    settle_only = bool(brush.get("settleOnly", False))
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

    ray_origin = wp.vec3(float(eye[0]), float(eye[1]), float(eye[2]))
    ray_dir = wp.vec3(rx / rlen, ry / rlen, rz / rlen)
    prev_ray_dir = wp.vec3(prx / prlen, pry / prlen, prz / prlen)
    right_vec = wp.vec3(float(right[0]), float(right[1]), float(right[2]))
    up_vec = wp.vec3(float(up[0]), float(up[1]), float(up[2]))

    radius_m = max(0.01, float(brush.get("radius", 0.15)))
    strength = max(0.0, min(5.0, float(brush.get("strength", 1.0))))
    min_depth_wp = wp.array([1e9], dtype=float, device=device)

    updates = []
    for p_str in matching_paths:
        sess = _ACTIVE_SIM_SESSIONS.get(p_str)
        if sess is None:
            continue
        pos_wp = sess["pos_wp"]
        vel_wp = sess["vel_wp"]
        inv_mass_wp = sess["inv_mass_wp"]
        total_verts = sess["total_verts"]
        unit_scale = sess["unit_scale"]
        dt_sub = sess["dt_sub"]
        world_to_local = sess["world_to_local"]
        scaled_radius = radius_m * unit_scale

        stroke_id = brush.get("strokeId")
        if brush.get("newStroke") or (stroke_id is not None and stroke_id != sess.get("last_stroke_id")):
            sess["guide_cache"] = None
            sess["last_stroke_id"] = stroke_id
            sess["vel_wp"].zero_()
        gc = sess.get("guide_cache")
        if not settle_only and gc is None:
            num_strands = sess["num_strands"]
            wp.copy(min_depth_wp, wp.array([1e9], dtype=float, device=device))
            wp.launch(
                brush_min_depth_kernel,
                dim=total_verts,
                inputs=[pos_wp, inv_mass_wp, ray_origin, prev_ray_dir, scaled_radius, min_depth_wp],
                device=device,
            )
            strand_dist_wp = wp.full(num_strands, 1e9, dtype=float, device=device)
            wp.launch(
                strand_ray_dist_kernel,
                dim=total_verts,
                inputs=[
                    pos_wp,
                    inv_mass_wp,
                    sess["strand_ids_wp"],
                    ray_origin,
                    prev_ray_dir,
                    scaled_radius,
                    min_depth_wp,
                    strand_dist_wp,
                ],
                device=device,
            )
            sd_np = strand_dist_wp.numpy()
            in_rad = np.where(sd_np < scaled_radius)[0]
            if len(in_rad) > 0:
                primary_s = int(in_rad[np.argmin(sd_np[in_rad])])
                pos_np = pos_wp.numpy()
                p_s0 = int(sess["starts_np"][primary_s])
                p_ln = int(sess["counts_np"][primary_s])
                hero_pos0 = pos_np[p_s0 + max(1, p_ln // 2)]
                if p_ln > 1:
                    ro_np = np.array([float(eye[0]), float(eye[1]), float(eye[2])], dtype=np.float32)
                    rd_np = np.array([prx / prlen, pry / prlen, prz / prlen], dtype=np.float32)
                    pts_p = pos_np[p_s0 + 1 : p_s0 + p_ln]
                    to_p = pts_p - ro_np[None, :]
                    t_p = np.sum(to_p * rd_np[None, :], axis=1, keepdims=True)
                    d_p = np.linalg.norm(pts_p - (ro_np[None, :] + rd_np[None, :] * t_p), axis=1)
                    k0 = 1 + int(np.argmin(d_p))
                    hero_pos0 = pos_np[p_s0 + k0]
                guide_s_ids = [primary_s]
                others = in_rad[in_rad != primary_s]
                if len(others) > 0:
                    if len(others) > 19:
                        rng = np.random.default_rng()
                        others = rng.choice(others, size=19, replace=False)
                    guide_s_ids.extend(others.tolist())
                gc = _build_guide_subsession(wp, np, device, sess, guide_s_ids, hero_pos0=hero_pos0)
                if gc is not None:
                    wp.copy(sess["stroke_start_pos_wp"], pos_wp)
                sess["guide_cache"] = gc
            sess["pick_ticks"] = 0
            sess["last_pick_nx"] = nx
            sess["last_pick_ny"] = ny

        if gc is not None:
            g_pos_wp = gc["g_pos_wp"]
            g_before_wp = gc["g_before_wp"]
            g_delta_wp = gc["g_delta_wp"]
            g_prev_pos_wp = gc["g_prev_pos_wp"]
            g_vel_wp = gc["g_vel_wp"]
            g_inv_mass_wp = gc["g_inv_mass_wp"]
            g_total_verts = gc["g_total_verts"]
            wp_collider = sess["wp_collider"]

            wp.copy(g_before_wp, g_pos_wp)
            cursor_target = wp.vec3(0.0, 0.0, 0.0)
            follow_active = False
            if not settle_only and gc["grab_ready"]:
                fwd_dot = max(ray_dir[0] * fwd[0] + ray_dir[1] * fwd[1] + ray_dir[2] * fwd[2], 1e-4)
                t_plane = (
                    (gc["grab_center"][0] - ray_origin[0]) * fwd[0]
                    + (gc["grab_center"][1] - ray_origin[1]) * fwd[1]
                    + (gc["grab_center"][2] - ray_origin[2]) * fwd[2]
                ) / fwd_dot
                cursor_target = wp.vec3(
                    ray_origin[0] + ray_dir[0] * t_plane,
                    ray_origin[1] + ray_dir[1] * t_plane,
                    ray_origin[2] + ray_dir[2] * t_plane,
                )
                ct_np = np.array([cursor_target[0], cursor_target[1], cursor_target[2]], dtype=np.float32)
                hero_k = gc["hero_k"]
                hero_offs = gc["hero_offsets_np"]
                g_offs = gc["guide_g_offsets"]
                g_lens = gc["guide_lengths"]
                g_roots = gc["guide_roots_np"]
                g_cums = gc["guide_cum_lens"]
                changed = False
                max_stretch = 1.02
                for g_idx in range(gc["guide_count"]):
                    k = hero_k[g_idx]
                    if k < 0:
                        continue
                    target_g = ct_np + hero_offs[g_idx]
                    dist_root = float(np.linalg.norm(target_g - g_roots[g_idx]))
                    cum = g_cums[g_idx]
                    ln = g_lens[g_idx]
                    new_k = k
                    while new_k < ln and dist_root > float(cum[new_k]) * max_stretch:
                        new_k += 1
                    if new_k >= ln:
                        hero_k[g_idx] = -1
                        changed = True
                    elif new_k != k:
                        hero_k[g_idx] = new_k
                        changed = True
                if changed:
                    idx_arr = [
                        (-1 if hero_k[g_i] < 0 else g_offs[g_i] + hero_k[g_i])
                        for g_i in range(gc["guide_count"])
                    ]
                    wp.copy(gc["hero_global_idx_wp"], wp.array(np.asarray(idx_arr, dtype=np.int32), dtype=int, device=device))
                follow_active = any(k >= 0 for k in hero_k)

            for _ in range(sess["substeps"]):
                wp.launch(
                    predict_kernel,
                    dim=g_total_verts,
                    inputs=[
                        g_pos_wp,
                        g_prev_pos_wp,
                        g_vel_wp,
                        g_inv_mass_wp,
                        gc["g_anim_rest_wp"],
                        sess["grav_vec"],
                        sess["damping_sub"],
                        dt_sub,
                    ],
                    device=device,
                )
                if follow_active:
                    wp.launch(
                        hero_brush_kernel,
                        dim=gc["guide_count"],
                        inputs=[
                            g_pos_wp,
                            g_vel_wp,
                            g_inv_mass_wp,
                            gc["hero_global_idx_wp"],
                            gc["hero_offsets_wp"],
                            gc["guide_offsets_wp"],
                            gc["guide_lengths_wp"],
                            cursor_target,
                            strength,
                            1.0 / max(dt_sub, 1e-6),
                        ],
                        device=device,
                    )
                for iter_idx in range(sess["iterations"]):
                    wp.launch(
                        zero_accum_kernel,
                        dim=g_total_verts,
                        inputs=[gc["g_delta_pos_wp"], gc["g_delta_w_wp"]],
                        device=device,
                    )
                    if gc["n_segs"] > 0:
                        wp.launch(
                            stretch_constraint_kernel,
                            dim=gc["n_segs"],
                            inputs=[
                                g_pos_wp,
                                g_inv_mass_wp,
                                gc["g_seg_v0_wp"],
                                gc["g_seg_v1_wp"],
                                gc["g_seg_len_wp"],
                                sess["stiffness"],
                                gc["g_delta_pos_wp"],
                                gc["g_delta_w_wp"],
                            ],
                            device=device,
                        )
                    if gc["n_bends"] > 0:
                        wp.launch(
                            bend_constraint_kernel,
                            dim=gc["n_bends"],
                            inputs=[
                                g_pos_wp,
                                g_inv_mass_wp,
                                gc["g_bend_v0_wp"],
                                gc["g_bend_v2_wp"],
                                gc["g_bend_len_wp"],
                                sess["bend_stiffness"],
                                gc["g_delta_pos_wp"],
                                gc["g_delta_w_wp"],
                            ],
                            device=device,
                        )
                    wp.launch(
                        apply_accum_kernel,
                        dim=g_total_verts,
                        inputs=[g_pos_wp, g_inv_mass_wp, gc["g_delta_pos_wp"], gc["g_delta_w_wp"]],
                        device=device,
                    )
                    if wp_collider is not None and (iter_idx % 2 == 1 or iter_idx + 1 == sess["iterations"]):
                        wp.launch(
                            collide_mesh_kernel,
                            dim=g_total_verts,
                            inputs=[
                                g_pos_wp,
                                g_prev_pos_wp,
                                g_inv_mass_wp,
                                wp_collider.id,
                                sess["scaled_margin"],
                                sess["friction"],
                                sess["max_query_dist"],
                                0,
                            ],
                            device=device,
                        )
                if wp_collider is not None:
                    if gc["n_segs"] > 0:
                        wp.launch(
                            zero_accum_kernel,
                            dim=g_total_verts,
                            inputs=[gc["g_delta_pos_wp"], gc["g_delta_w_wp"]],
                            device=device,
                        )
                        wp.launch(
                            collide_segments_kernel,
                            dim=gc["n_segs"],
                            inputs=[
                                g_pos_wp,
                                g_inv_mass_wp,
                                gc["g_seg_v0_wp"],
                                gc["g_seg_v1_wp"],
                                wp_collider.id,
                                sess["scaled_margin"],
                                sess["max_query_dist"],
                                gc["g_delta_pos_wp"],
                                gc["g_delta_w_wp"],
                            ],
                            device=device,
                        )
                        wp.launch(
                            apply_accum_kernel,
                            dim=g_total_verts,
                            inputs=[g_pos_wp, g_inv_mass_wp, gc["g_delta_pos_wp"], gc["g_delta_w_wp"]],
                            device=device,
                        )
                    wp.launch(
                        collide_mesh_kernel,
                        dim=g_total_verts,
                        inputs=[
                            g_pos_wp,
                            g_prev_pos_wp,
                            g_inv_mass_wp,
                            wp_collider.id,
                            sess["scaled_margin"],
                            sess["friction"],
                            sess["max_query_dist"],
                            1,
                        ],
                        device=device,
                    )
                wp.launch(
                    update_vel_kernel,
                    dim=g_total_verts,
                    inputs=[g_pos_wp, g_prev_pos_wp, g_vel_wp, g_inv_mass_wp, 1.0 / max(dt_sub, 1e-6)],
                    device=device,
                )

            wp.copy(sess["prev_pos_wp"], pos_wp)
            wp.launch(vec3_sub_kernel, dim=g_total_verts, inputs=[g_pos_wp, g_before_wp, g_delta_wp], device=device)
            wp.launch(
                apply_guide_interp_kernel,
                dim=total_verts,
                inputs=[
                    pos_wp,
                    sess["stroke_start_pos_wp"],
                    vel_wp,
                    inv_mass_wp,
                    sess["strand_ids_wp"],
                    sess["local_idx_wp"],
                    sess["vert_starts_wp"],
                    sess["counts_wp"],
                    gc["guide_s_ids_wp"],
                    gc["guide_offsets_wp"],
                    gc["guide_lengths_wp"],
                    g_delta_wp,
                    gc["guide_roots_wp"],
                    gc["guide_mids_wp"],
                    gc["guide_tips_wp"],
                    gc["guide_count"],
                    scaled_radius,
                    1.0 / max(dt_sub, 1e-6),
                ],
                device=device,
            )
            if wp_collider is not None:
                wp.launch(
                    collide_mesh_kernel,
                    dim=total_verts,
                    inputs=[
                        pos_wp,
                        sess["prev_pos_wp"],
                        inv_mass_wp,
                        wp_collider.id,
                        sess["scaled_margin"],
                        sess["friction"],
                        sess["max_query_dist"],
                        2,
                    ],
                    device=device,
                )

        local_wp = sess.get("local_wp")
        if return_gpu and not write_usd and local_wp is not None:
            m = np.asarray(world_to_local, dtype=np.float32)
            c0 = wp.vec3(float(m[0, 0]), float(m[0, 1]), float(m[0, 2]))
            c1 = wp.vec3(float(m[1, 0]), float(m[1, 1]), float(m[1, 2]))
            c2 = wp.vec3(float(m[2, 0]), float(m[2, 1]), float(m[2, 2]))
            t_vec = wp.vec3(float(m[3, 0]), float(m[3, 1]), float(m[3, 2]))
            wp.launch(
                world_to_local_kernel,
                dim=total_verts,
                inputs=[pos_wp, c0, c1, c2, t_vec, local_wp],
                device=device,
            )
            wp.synchronize_device(device)
            updates.append((p_str, local_wp))
        else:
            sim_world_np = pos_wp.numpy()
            m = np.asarray(world_to_local, dtype=np.float32)
            local_np = np.ascontiguousarray(sim_world_np @ m[:3, :3] + m[3, :3], dtype=np.float32)
            if write_usd or brush.get("commitUsd"):
                sess["guide_cache"] = None
                sess["vel_wp"].zero_()
                prim = stage.GetPrimAtPath(p_str)
                if prim and prim.IsValid():
                    UsdGeom.BasisCurves(prim).CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(local_np))
            updates.append((p_str, local_np))

    return updates
